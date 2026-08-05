from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.count_views import CountLineSerializer
from allapp.tasking.admin import CountWizardForm
from allapp.tasking.counting import (
    approve_count_task,
    assert_inventory_not_count_locked,
    claim_count_task,
    create_count_task,
    post_count_task,
    record_count,
    reject_count_task,
    release_count_task,
    submit_count_task,
)
from allapp.tasking.models import CountLineExtra, CountScopeLock, TaskScanLog, WmsTask


class CountProductionClosureTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="CNTOWNER", name="盘点货主")
        self.warehouse = Warehouse.objects.create(code="COUNT-WH", name="盘点仓")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse, code="COUNTSW", name="盘点子仓"
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="COUNTSW-01-01-01",
            name="盘点库位",
        )
        self.uom = ProductUom.objects.create(code="COUNT-EA", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="COUNT-SKU",
            sku="COUNT-SKU",
            name="盘点商品",
            base_uom=self.uom,
            price=Decimal("1.00"),
        )
        self.detail = InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            product=self.product,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=0,
            locked_qty=0,
            damaged_qty=0,
        )
        self.manager = get_user_model().objects.create_superuser(
            username="count-manager", email="count@example.com", password="x"
        )

    def create_and_release(self):
        task, created, truncated = create_count_task(
            created_by=self.manager,
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            scope="LOC",
            location_id=self.location.id,
        )
        self.assertEqual(created, 1)
        self.assertFalse(truncated)
        return release_count_task(task.id, by_user=self.manager)

    @override_settings(COUNT_MAX_TIMES=2)
    def test_no_difference_auto_posts_and_releases_scope(self):
        task = self.create_and_release()
        line = task.lines.get()
        record_count(
            task.id,
            line_id=line.id,
            qty_counted="5",
            client_seq="no-diff-1",
            by_user=self.manager,
        )

        result = submit_count_task(task.id, by_user=self.manager)

        task.refresh_from_db()
        self.assertEqual(result["outcome"], "AUTO_POSTED_NO_DIFF")
        self.assertEqual(task.status, WmsTask.Status.COMPLETED)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertFalse(
            CountScopeLock.objects.filter(released_at__isnull=True).exists()
        )

    @override_settings(COUNT_MAX_TIMES=1)
    def test_zero_physical_count_is_counted_and_waits_for_approval(self):
        task = self.create_and_release()
        line = task.lines.get()
        record_count(
            task.id,
            line_id=line.id,
            qty_counted="0",
            client_seq="zero-count-1",
            by_user=self.manager,
        )
        result = submit_count_task(task.id, by_user=self.manager)

        extra = CountLineExtra.objects.get(line=line)
        task.refresh_from_db()
        self.assertEqual(extra.count_status, "COUNTED")
        self.assertEqual(extra.qty_diff, Decimal("-5.0000"))
        self.assertEqual(result["outcome"], "PENDING_APPROVAL")
        self.assertEqual(task.review_status, WmsTask.ReviewStatus.PENDING)
        self.assertTrue(
            CountScopeLock.objects.filter(released_at__isnull=True).exists()
        )

    @override_settings(COUNT_MAX_TIMES=2)
    def test_difference_creates_released_recount_and_keeps_lock(self):
        task = self.create_and_release()
        claim_count_task(task.id, by_user=self.manager)
        line = task.lines.get()
        record_count(
            task.id,
            line_id=line.id,
            qty_counted="4",
            client_seq="recount-1",
            by_user=self.manager,
        )
        result = submit_count_task(task.id, by_user=self.manager)

        recount = WmsTask.objects.get(pk=result["next_task_id"])
        task.refresh_from_db()
        self.assertEqual(result["outcome"], "RECOUNT_RELEASED")
        self.assertEqual(task.review_status, WmsTask.ReviewStatus.NEED_RECOUNT)
        self.assertEqual(recount.status, WmsTask.Status.RELEASED)
        self.assertEqual(recount.assignments.get().assignee_id, self.manager.id)
        self.assertTrue(
            CountScopeLock.objects.filter(
                active_task=recount, released_at__isnull=True
            ).exists()
        )

    def test_scope_lock_blocks_other_inventory_changes(self):
        task = self.create_and_release()
        with self.assertRaises(ValidationError):
            assert_inventory_not_count_locked(
                owner_id=self.owner.id,
                warehouse_id=self.warehouse.id,
                location_id=self.location.id,
                product_id=self.product.id,
                batch_no="",
            )
        assert_inventory_not_count_locked(
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            location_id=self.location.id,
            product_id=self.product.id,
            batch_no="",
            task=task,
        )

    def test_overlapping_count_task_cannot_be_released(self):
        first = self.create_and_release()
        second, _created, _truncated = create_count_task(
            created_by=self.manager,
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            scope="LOC",
            location_id=self.location.id,
        )

        with self.assertRaisesRegex(ValidationError, first.task_no):
            release_count_task(second.id, by_user=self.manager)

    @override_settings(COUNT_MAX_TIMES=1)
    def test_approved_difference_posts_inventory_and_releases_lock(self):
        task = self.create_and_release()
        record_count(
            task.id,
            line_id=task.lines.get().id,
            qty_counted="4",
            client_seq="approve-diff",
            by_user=self.manager,
        )
        submit_count_task(task.id, by_user=self.manager)
        approve_count_task(task.id, by_user=self.manager, note="差异确认")
        post_count_task(task.id, by_user=self.manager)

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, Decimal("4.0000"))
        self.assertFalse(
            CountScopeLock.objects.filter(released_at__isnull=True).exists()
        )

    @override_settings(COUNT_MAX_TIMES=1)
    def test_rejected_difference_does_not_change_inventory_and_releases_lock(self):
        task = self.create_and_release()
        record_count(
            task.id,
            line_id=task.lines.get().id,
            qty_counted="4",
            client_seq="reject-diff",
            by_user=self.manager,
        )
        submit_count_task(task.id, by_user=self.manager)
        reject_count_task(task.id, by_user=self.manager, note="重新检查范围")

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, Decimal("5.0000"))
        self.assertFalse(
            CountScopeLock.objects.filter(released_at__isnull=True).exists()
        )

    def test_auto_post_failure_is_retryable_and_keeps_lock(self):
        task = self.create_and_release()
        record_count(
            task.id,
            line_id=task.lines.get().id,
            qty_counted="5",
            client_seq="failed-auto-post",
            by_user=self.manager,
        )
        with patch(
            "allapp.inventory.services.post_task",
            side_effect=ValidationError("模拟过账失败"),
        ):
            result = submit_count_task(task.id, by_user=self.manager)

        task.refresh_from_db()
        self.assertEqual(result["outcome"], "POSTING_FAILED")
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.FAILED)
        self.assertTrue(
            CountScopeLock.objects.filter(released_at__isnull=True).exists()
        )

        post_count_task(task.id, by_user=self.manager)
        task.refresh_from_db()
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertFalse(
            CountScopeLock.objects.filter(released_at__isnull=True).exists()
        )

    def test_blind_line_hides_book_quantity_from_operator(self):
        task = self.create_and_release()
        line = task.lines.select_related("task", "product", "from_location").get()
        operator = get_user_model().objects.create_user(
            username="count-operator", password="x"
        )
        request = APIRequestFactory().get("/")
        request.user = operator

        data = CountLineSerializer(line, context={"request": request}).data

        self.assertNotIn("qty_book", data)
        self.assertNotIn("qty_diff", data)

    def test_count_wizard_uses_canonical_field_names_and_has_no_lpn_filter(self):
        form = CountWizardForm(user=self.manager)

        self.assertIn("task_remark", form.fields)
        self.assertIn("exclude_zero_onhand", form.fields)
        self.assertIn("subwarehouse", form.fields)
        self.assertNotIn("lpn", form.fields)

    def test_record_retry_is_idempotent_and_new_value_replaces_snapshot(self):
        task = self.create_and_release()
        line = task.lines.get()
        first = record_count(
            task.id,
            line_id=line.id,
            qty_counted="5",
            client_seq="same-request",
            by_user=self.manager,
        )
        retry = record_count(
            task.id,
            line_id=line.id,
            qty_counted="5",
            client_seq="same-request",
            by_user=self.manager,
        )
        record_count(
            task.id,
            line_id=line.id,
            qty_counted="0",
            client_seq="changed-request",
            by_user=self.manager,
        )

        self.assertFalse(first["idempotent"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(
            TaskScanLog.objects.filter(
                task=task, status=TaskScanLog.ScanStatus.OK
            ).count(),
            1,
        )
        self.assertEqual(
            TaskScanLog.objects.filter(
                task=task, status=TaskScanLog.ScanStatus.IGNORED
            ).count(),
            1,
        )

    def test_operator_api_lists_claims_and_records_blind_zero_count(self):
        task = self.create_and_release()
        operator = get_user_model().objects.create_user(
            username="count-api-operator", password="x"
        )
        operator.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="claim_task_as_wh_operator"
            )
        )
        UserRoleScope.objects.create(
            user=operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        client = APIClient()
        client.force_authenticate(operator)

        listed = client.get("/api/pda/count-tasks/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([row["id"] for row in listed.json()], [task.id])
        lines = client.get(f"/api/pda/count-tasks/{task.id}/lines/")
        self.assertEqual(lines.status_code, 200)
        self.assertNotIn("qty_book", lines.json()[0])
        self.assertNotIn("qty_diff", lines.json()[0])
        self.assertEqual(
            client.post(f"/api/pda/count-tasks/{task.id}/claim/", {}).status_code,
            200,
        )
        recorded = client.post(
            f"/api/pda/count-tasks/{task.id}/record/",
            {
                "line_id": task.lines.get().id,
                "qty_counted": "0",
                "client_seq": "api-zero-count",
            },
            format="json",
        )
        self.assertEqual(recorded.status_code, 200)
        self.assertEqual(recorded.json()["qty_counted"], "0.0000")
