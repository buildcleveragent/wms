from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connections
from django.http import Http404
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory import daily_close_review as review
from allapp.inventory import daily_close_storage as storage
from allapp.inventory.daily_close import opening_candidate
from allapp.inventory.models import (
    InventoryCloseAttachment,
    InventoryClosePilot,
    InventoryDailyClose,
    InventoryOpeningVersion,
)
from allapp.inventory.snapshot_services import trusted_inventory_baseline
from allapp.locations.models import Warehouse
from allapp.outbound.testing import stock_for_owner_order
from allapp.products.models import Product, ProductUom
from allapp.reports.models import AlertCase
from allapp.reports.views_boss_p1 import AlertCaseActionApi
from allapp.tasking import counting
from allapp.tasking.models import TaskScanLog

ARCHIVE = [{"type": "external", "number": "ARCH-17"}]


def signed_copy():
    stream = BytesIO()
    Image.new("RGB", (2, 2)).save(stream, format="PNG")
    return SimpleUploadedFile("签署明细.png", stream.getvalue(), content_type="image/png")


def reviewer_account(name, role, *, warehouse=None, owner=None, permissions=()):
    user = get_user_model().objects.create_user(username=name)
    UserRoleScope.objects.create(user=user, role=role, owner=owner, warehouse=warehouse)
    for permission in permissions:
        app, code = permission.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=code)
        )
    return user


def create_close():
    owner = Owner.objects.create(code="DC-O", name="日结货主")
    warehouse = Warehouse.objects.create(code="DC-W", name="日结仓")
    OwnerWarehouseBinding.objects.create(owner=owner, warehouse=warehouse)
    day = timezone.now().date() - timedelta(days=2)
    InventoryClosePilot.objects.create(warehouse=warehouse, enabled=True, start_date=day)
    return InventoryDailyClose.objects.create(
        owner=owner,
        warehouse=warehouse,
        business_date=day,
        quality="MISSING",
        review_state="WAREHOUSE",
    )


class ReviewFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.close = create_close()
        cls.manager = reviewer_account(
            "close-manager",
            "warehouse_manager",
            warehouse=cls.close.warehouse,
            permissions=(review.REVIEW, review.SIGN, "reports.manage_alert_cases"),
        )
        cls.signer = reviewer_account(
            "close-signer",
            "warehouse_operator",
            warehouse=cls.close.warehouse,
            permissions=(review.SIGN,),
        )
        cls.finance = reviewer_account(
            "close-report-reader",
            "owner_finance",
            owner=cls.close.owner,
            permissions=(review.SIGN, review.REVIEW, review.VIEW),
        )

    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        overrides = override_settings(PRIVATE_CLOSE_ROOT=str(self.root))
        overrides.enable()
        self.addCleanup(overrides.disable)

    def submit(self, files=()):
        return review.submit_review(
            self.manager,
            self.close.pk,
            version=self.close.revision,
            note="核对在库差额",
            references=ARCHIVE,
            files=files,
        )

    def action(self, case_id, action, **payload):
        req = APIRequestFactory().patch("/", payload, format="json")
        force_authenticate(req, user=self.manager)
        return AlertCaseActionApi.as_view()(req, pk=case_id, action=action)

    def test_double_sign_return_resubmit_stale_and_alert_reopen(self):
        close = self.submit()
        self.assertEqual(close.case.status, "IN_PROGRESS")
        with self.assertRaises(PermissionDenied):
            review.decide_review(
                self.manager, close.pk, version=close.revision, approve=True, note="签核"
            )
        with self.assertRaises(ValidationError):
            self.submit()
        close = review.decide_review(
            self.signer, close.pk, version=close.revision, approve=False, note="补充盘点"
        )
        close = review.submit_review(
            self.manager, close.pk, version=close.revision, note="已补齐", references=ARCHIVE
        )
        with self.assertRaises(ValidationError):
            review.decide_review(
                self.signer, close.pk, version=close.revision, approve=True, note="缺少计费影响"
            )
        close = review.decide_review(
            self.signer,
            close.pk,
            version=close.revision,
            approve=True,
            billing_impact="NO_CHANGE",
            note="核对账期无需调整",
        )
        self.assertEqual(
            (close.quality, close.review_state, close.case.status), ("MISSING", "CLOSED", "CLOSED")
        )
        decision = close.reviews.get(action="FINANCE_APPROVE")
        self.assertEqual(decision.references[0]["value"], "NO_CHANGE")
        self.assertEqual(close.reviews.count(), 4)
        self.assertFalse(
            trusted_inventory_baseline(
                close.business_date, owner_id=close.owner_id, warehouse_id=close.warehouse_id
            )
        )
        close.review_state = "WAREHOUSE"
        close.save(update_fields=["review_state"])
        review.sync_close_alert(close)
        review.sync_close_alert(close)
        self.assertEqual(AlertCase.objects.filter(alert_type=review.ALERT_TYPE).count(), 1)
        self.assertEqual(close.case.history.filter(action="REOPEN").count(), 1)

    def test_source_owned_case_and_scoped_assignment(self):
        close = self.submit()
        self.assertEqual(
            self.action(close.case_id, "assignment", assignee=self.signer.pk).status_code, 200
        )
        self.assertEqual(
            self.action(close.case_id, "assignment", assignee=self.finance.pk).status_code, 409
        )
        AlertCase.objects.filter(pk=close.case_id).update(status="RESOLVED")
        self.assertEqual(self.action(close.case_id, "close").status_code, 409)
        self.assertEqual(self.action(close.case_id, "acknowledge").status_code, 409)
        close.case.refresh_from_db()
        self.assertEqual(close.case.assignee_id, self.signer.pk)
        self.assertEqual(close.case.status, "RESOLVED")

    def test_read_scope_includes_only_own_finance_and_private_downloads_require_warehouse(self):
        close = self.submit(files=[signed_copy()])
        item = InventoryCloseAttachment.objects.get(review__close=close)
        response = review.download_evidence(self.signer, item.pk)
        # Direct service call: close the file without signalling the end of a Django
        # request, which would close the surrounding MySQL TestCase transaction.
        with closing(response.file_to_stream):
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertEqual(b"".join(response.streaming_content), signed_copy().read())
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(review.visible_closes(self.finance).filter(pk=close.pk).exists())
        with self.assertRaises(PermissionDenied):
            review.download_evidence(self.finance, item.pk)
        with self.assertRaises(PermissionDenied):
            review.decide_review(
                self.finance, close.pk, version=close.revision, approve=True, note="越权"
            )
        other_wh = Warehouse.objects.create(code="DC-F", name="外仓")
        foreign = reviewer_account(
            "close-foreign", "warehouse_manager", warehouse=other_wh, permissions=(review.REVIEW,)
        )
        with self.assertRaises(Http404):
            review.download_evidence(foreign, item.pk)
        self.assertFalse(review.visible_closes(foreign).exists())
        other_owner = Owner.objects.create(code="DC-X", name="外货主")
        self.assertFalse(
            review.visible_closes(
                reviewer_account(
                    "close-other-owner",
                    "owner_finance",
                    owner=other_owner,
                    permissions=(review.VIEW,),
                )
            ).exists()
        )

    def test_invalid_source_or_database_failure_rolls_back_written_attachment(self):
        with self.assertRaises(ValidationError):
            review.submit_review(
                self.manager,
                self.close.pk,
                version=0,
                note="查询",
                references=[{"type": "count", "id": 999999}],
                files=[signed_copy()],
            )
        with patch.object(
            InventoryCloseAttachment.objects, "create", side_effect=RuntimeError("failed")
        ):
            with self.assertRaises(RuntimeError):
                self.submit(files=[signed_copy()])
        self.assertFalse(any(f.is_file() for f in self.root.rglob("*")))
        self.close.refresh_from_db()
        self.assertEqual((self.close.revision, self.close.review_state), (0, "WAREHOUSE"))
        self.assertFalse(self.close.reviews.exists())

    def test_signed_opening_reconstructs_unknown_holds_with_evidence(self):
        close = self.close
        uom = ProductUom.objects.create(code="DCPCS", name="件")
        product = Product.objects.create(
            owner=close.owner, code="DC-S", sku="DC-S", name="独立期初商品", base_uom=uom
        )
        stock_for_owner_order(product, close.warehouse, quantity="10")
        with patch("allapp.inventory.daily_close._load_hold_balances", return_value=None):
            rows = opening_candidate(close, allow_unknown_holds=True)
            self.assertIsNone(rows[0]["allocated_qty"])

            def submit(rows, *, files=(), complete=True, references=ARCHIVE):
                return review.submit_opening(
                    self.manager,
                    close.pk,
                    version=close.revision,
                    rows=rows,
                    complete=complete,
                    references=references,
                    note="已与日终签字结存逐项核对",
                    files=files,
                )

            with self.assertRaises(ValidationError):
                submit(rows, files=[signed_copy()])
            rows[0].update(allocated_qty="2", damaged_qty="1", locked_qty="0")
            for invalid in ([], rows + rows, [{**rows[0], "onhand_qty": "11"}]):
                with self.assertRaises(ValidationError):
                    submit(invalid, files=[signed_copy()])
            with self.assertRaises(ValidationError):
                submit(rows)
            with self.assertRaises(ValidationError):
                submit(rows, files=[signed_copy()], complete=False)
            close = submit(rows, files=[signed_copy()])
            with self.assertRaises(PermissionDenied):
                review.decide_opening(
                    self.manager, close.pk, version=close.revision, approve=True, note="签核"
                )
            close = review.decide_opening(
                self.signer, close.pk, version=close.revision, approve=False, note="补签"
            )
            self.assertEqual(close.opening_versions.get().status, "RETURNED")
            close = submit(rows, files=[signed_copy()])
            close = review.decide_opening(
                self.signer,
                close.pk,
                version=close.revision,
                approve=True,
                billing_impact="FOLLOW_UP",
                note="账期已核对",
            )
            self.assertTrue(
                trusted_inventory_baseline(
                    close.business_date, owner_id=close.owner_id, warehouse_id=close.warehouse_id
                )
            )
        self.assertEqual(close.quality, "MISSING")
        versions = list(close.opening_versions.order_by("version"))
        self.assertEqual([v.status for v in versions], ["RETURNED", "APPROVED"])
        self.assertEqual(versions[1].rows[0]["available_qty"], "7.0000")
        self.assertEqual(versions[1].approved_by, self.signer)
        with self.assertRaises(ValidationError):
            versions[1].save(update_fields=["rows"])

    def test_posted_full_count_is_eligible_but_partial_count_is_not(self):
        close = self.close
        counter = get_user_model().objects.create_superuser(username="dc-counter")
        uom = ProductUom.objects.create(code="DCUNT", name="件")
        product = Product.objects.create(
            owner=close.owner, code="DCC", sku="DCC", name="实盘商品", base_uom=uom
        )
        detail = stock_for_owner_order(product, close.warehouse, quantity="4")

        def count(**scope):
            task, _, _ = counting.create_count_task(
                owner_id=close.owner_id,
                warehouse_id=close.warehouse_id,
                created_by=counter,
                **scope,
            )
            counting.release_count_task(task.pk, by_user=counter)
            counting.record_count(
                task.pk,
                line_id=task.lines.get().pk,
                qty_counted="4",
                client_seq=f"close-count-{task.pk}",
                by_user=counter,
            )
            result = counting.submit_count_task(task.pk, by_user=counter)
            self.assertEqual(result["outcome"], "AUTO_POSTED_NO_DIFF")
            return task

        measured = datetime.combine(close.business_date, time(15))
        with patch("django.utils.timezone.now", return_value=measured):
            partial = count(scope="LOC", location_id=detail.location_id)
            full = count(scope="ALL")
        with patch("allapp.inventory.daily_close._load_hold_balances", return_value={}):
            rows = opening_candidate(close)

            def submit(task):
                return review.submit_opening(
                    self.manager,
                    close.pk,
                    rows=rows,
                    complete=True,
                    version=close.revision,
                    references=[{"type": "count", "id": task.pk}],
                    note="核对盘点至日终流水及占用",
                )

            with self.assertRaises(ValidationError):
                submit(partial)
            scans = TaskScanLog.objects.filter(task=full)
            scans.update(posted_at=None)
            with self.assertRaises(ValidationError):
                submit(full)
            scans.update(posted_at=measured)
            close = submit(full)
            close = review.decide_opening(
                self.signer,
                close.pk,
                version=close.revision,
                approve=True,
                billing_impact="NO_CHANGE",
                note="库存及账单均已复核",
            )
        self.assertEqual(close.opening_versions.get().status, "APPROVED")

    def test_missing_original_or_changed_balance_prevents_approval(self):
        close = review.submit_opening(
            self.manager,
            self.close.pk,
            version=0,
            rows=[],
            complete=True,
            references=ARCHIVE,
            note="签字空仓记录",
            files=[signed_copy()],
        )
        item = InventoryCloseAttachment.objects.get(review__close=close)
        storage.evidence_path(item.storage_key).unlink()
        with self.assertRaises(ValidationError):
            review.decide_opening(
                self.signer, close.pk, version=close.revision, approve=True, note="签核"
            )
        self.assertFalse(InventoryOpeningVersion.objects.filter(status="APPROVED").exists())


class StorageRules(SimpleTestCase):
    def test_public_roots_links_and_invalid_content_are_rejected(self):
        with override_settings(
            PRIVATE_CLOSE_ROOT=Path("/tmp") / "public" / "invoices", MEDIA_ROOT="/tmp/public"
        ):
            with self.assertRaises(ImproperlyConfigured):
                storage.private_close_root()
        with TemporaryDirectory() as root, override_settings(PRIVATE_CLOSE_ROOT=root):
            with self.assertRaises(ValidationError):
                storage.evidence_path("../proof.pdf")
            directory = Path(root) / "42"
            directory.symlink_to("/tmp", target_is_directory=True)
            with self.assertRaises(ValidationError):
                storage.evidence_path("42/" + "a" * 32 + ".pdf")
            with self.assertRaises(ValidationError):
                storage.prepare_files([SimpleUploadedFile("bad.png", b"wrong data")])
            with self.assertRaises(ValidationError):
                storage.prepare_files([SimpleUploadedFile("bad.pdf", b"wrong data")])
            self.assertEqual(storage.prepare_files([signed_copy()])[0].extension, "png")


class ReviewConcurrentTests(TransactionTestCase):
    def test_same_revision_has_one_successful_submit(self):
        close = create_close()
        manager = reviewer_account(
            "close-concurrent",
            "warehouse_manager",
            warehouse=close.warehouse,
            permissions=(review.REVIEW,),
        )
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=manager.pk)
                barrier.wait(timeout=10)
                try:
                    review.submit_review(
                        user, close.pk, version=close.revision, note="并发复核", references=ARCHIVE
                    )
                    return True
                except ValidationError:
                    return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(close.reviews.count(), 1)
