from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Warehouse
from allapp.tasking.models import TaskScanLog

from .test_physical_review import PhysicalReviewFixture


class PickReallocationTests(PhysicalReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.wh_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="taskconfirm_as_wh_manager"
            )
        )

    def replacement_stock(self, code, qty):
        loc = Location.objects.create(warehouse=self.wh, subwarehouse=self.sub, code=code)
        return InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.wh,
            location=loc,
            onhand_qty=Decimal(qty),
        )

    def request_data(self, line, **changes):
        return {
            "line_id": line.pk,
            "reason": "库位冻结，已核对现场",
            "expected_qty": str(line.qty_done),
            "expected_revision": line.scan_logs.filter(status="OK")
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
            or 0,
            "physical_confirmed": True,
            **changes,
        }

    def reallocate(self, task, payload, actor):
        self.client.force_authenticate(actor)
        return self.client.post(
            reverse("task-reallocate-sale-line", args=[task.pk]), payload, format="json"
        )

    def test_frozen_scanned_layer_requires_manager_physical_confirmation_and_new_review(self):
        task = self.ready_pick()
        line = task.lines.get()
        old_scan = TaskScanLog.objects.get(task_line=line)
        self.loc.is_frozen = True
        self.loc.save(update_fields=["is_frozen"])
        self.assertEqual(self.post_review(task).status_code, 400)
        self.assertFalse(
            InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).exists()
        )
        new = self.replacement_stock("REORDERS-02-01-01", "3")
        payload = self.request_data(line)
        self.assertIn(self.reallocate(task, payload, self.picker).status_code, [403, 404])
        self.assertEqual(
            self.reallocate(
                task, {**payload, "physical_confirmed": False}, self.wh_manager
            ).status_code,
            400,
        )
        self.assertEqual(
            self.reallocate(task, {**payload, "expected_revision": 0}, self.wh_manager).status_code,
            400,
        )
        other_wh = Warehouse.objects.create(code="RM-FOREIGN", name="其他仓库")
        foreign = get_user_model().objects.create_user(username="remote-manager")
        UserRoleScope.objects.create(user=foreign, role="warehouse_manager", warehouse=other_wh)
        foreign.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="taskconfirm_as_wh_manager"
            )
        )
        self.assertIn(self.reallocate(task, payload, foreign).status_code, (403, 404))
        browser = Client()
        browser.force_login(self.wh_manager)
        detail = browser.get(reverse("tasking_console:task_detail", args=[task.pk]))
        self.assertContains(detail, "重配失效库存")
        self.assertContains(detail, f'name="expected_revision" value="{old_scan.pk}"')
        self.client.force_authenticate(self.wh_manager)
        self.assertEqual(
            self.client.get(reverse("task-reallocate-sale-line", args=[task.pk])).data["lines"][0][
                "expected_revision"
            ],
            old_scan.pk,
        )
        result = self.reallocate(task, payload, self.wh_manager)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(result.data["idempotent"])
        from django.core.exceptions import ValidationError
        from allapp.tasking.plugins.handlers import get_posting_handler

        with self.assertRaises(ValidationError):
            get_posting_handler().handle(task=task, by_user=self.reviewer)
        task.refresh_from_db()
        self.assertEqual(task.posting_status, "NOT_READY")
        from allapp.tasking.services import save_receiving_snapshot
        from rest_framework.exceptions import ValidationError as APIValidationError

        with self.assertRaises(APIValidationError):
            save_receiving_snapshot(line.pk, [], self.picker)
        self.assertTrue(self.reallocate(task, payload, self.wh_manager).data["idempotent"])
        old_scan.refresh_from_db()
        self.assertEqual(old_scan.status, "IGNORED")
        self.assertEqual(old_scan.qty_base_delta, Decimal("2.125"))
        self.review.refresh_from_db()
        self.assertEqual(
            (self.review.status, self.review.review_status, self.review.posting_status),
            ("CANCELLED", "NONE", "NONE"),
        )
        line.refresh_from_db()
        history = line.plan_meta["sale_reallocation"]["previous_review"]
        self.assertEqual(
            (history["id"], history["status"], history["review_status"], history["approved_by_id"]),
            (self.review.pk, "COMPLETED", "APPROVED", self.reviewer.pk),
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.pick.reallocate", object_id=str(task.pk)
            ).count(),
            1,
        )
        self.client.force_authenticate(self.picker)
        scanned = self.client.post(
            f"/api/pda/pick-tasks/{task.pk}/scan/",
            {"barcode": self.product.sku, "qty": "2.125", "location_id": new.location_id},
            format="json",
        )
        self.assertEqual(scanned.status_code, 200, scanned.data)
        review = self.client.post(f"/api/pda/pick-tasks/{task.pk}/create-review-task/")
        self.assertEqual(review.status_code, 200, review.data)
        self.assertNotEqual(review.data["review_task_id"], self.review.pk)
        self.client.force_authenticate(self.reviewer)
        posted = self.post_review(task)
        self.assertEqual(posted.status_code, 200, posted.data)
        self.assertEqual(
            list(
                InventoryTransaction.objects.filter(
                    src_model="WmsTask", src_id=task.pk
                ).values_list("location_id", "qty_delta")
            ),
            [(new.location_id, Decimal("-2.125"))],
        )
        new.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(new.onhand_qty, Decimal(".875"))
        self.assertEqual(self.stock.onhand_qty, Decimal("10"))

    def test_partial_reallocation_keeps_other_picked_line_and_rejects_old_scans(self):
        from allapp.outbound.services import confirm_warehouse_order
        from allapp.tasking.models import TaskAssignment

        self.stock.onhand_qty = Decimal("4")
        self.stock.save()
        other = self.replacement_stock("REORDERS-02-01-03", "1.125")
        order = self.prepared()
        _, task = confirm_warehouse_order(order, by_user=self.wh_manager)
        first = task.lines.get(from_location=self.loc)
        second = task.lines.get(from_location_id=other.location_id)
        operator = self.operator("partial-operator")
        TaskAssignment.objects.create(task=task, assignee=operator)
        self.client.force_authenticate(operator)

        def scan(location, qty, seq):
            return self.client.post(
                f"/api/pda/pick-tasks/{task.pk}/scan/",
                {
                    "barcode": self.product.sku,
                    "qty": qty,
                    "location_id": location,
                    "client_seq": seq,
                },
                format="json",
            )

        self.assertEqual(scan(self.loc.pk, ".5", "partial-first").status_code, 200)
        self.assertEqual(scan(other.location_id, "1.125", "partial-second").status_code, 200)
        self.loc.is_frozen = True
        self.loc.save(update_fields=["is_frozen"])
        self.assertEqual(scan(self.loc.pk, ".5", "invalid").status_code, 400)
        manual = self.client.post(
            f"/api/pda/pick-tasks/{task.pk}/adjust-line-qty/",
            {"line_id": first.pk, "final_qty_done": "0"},
            format="json",
        )
        self.assertEqual(manual.status_code, 400)
        first.refresh_from_db()
        new = self.replacement_stock("REORDERS-02-01-04", "2")
        payload = self.request_data(first)
        outsider = self.operator("other-operator")
        self.assertIn(self.reallocate(task, payload, outsider).status_code, (403, 404))
        result = self.reallocate(task, payload, self.wh_manager)
        self.assertEqual(result.status_code, 200, result.data)
        self.client.force_authenticate(operator)
        replay = scan(self.loc.pk, ".5", "partial-first")
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        self.assertEqual(TaskScanLog.objects.filter(task=task).count(), 2)
        active = self.client.get(f"/api/pda/pick-tasks/{task.pk}/lines/")
        self.assertEqual(
            {row["id"] for row in active.data},
            {second.pk, result.data["replacement_lines"][0]["line_id"]},
        )
        second.refresh_from_db()
        self.assertEqual(second.qty_done, Decimal("1.125"))
        self.assertEqual(TaskScanLog.objects.get(task_line=second).status, "OK")
        replacement = task.lines.get(from_location_id=new.location_id)
        self.assertEqual((replacement.qty_done, replacement.qty_plan), (0, Decimal("1")))

    def test_never_scanned_line_uses_zero_revision_from_manager_endpoint(self):
        from allapp.outbound.services import confirm_warehouse_order

        order = self.prepared()
        _, task = confirm_warehouse_order(order, by_user=self.wh_manager)
        line = task.lines.get()
        self.loc.is_frozen = True
        self.loc.save(update_fields=["is_frozen"])
        self.replacement_stock("REORDERS-02-01-08", "3")
        url = reverse("task-reallocate-sale-line", args=[task.pk])
        row = self.client.get(url).data["lines"][0]
        self.assertEqual(
            (row["line_id"], row["expected_revision"], Decimal(row["expected_qty"])),
            (line.pk, 0, 0),
        )
        result = self.reallocate(task, {**row, "reason": "冻结库位换货位"}, self.wh_manager)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(
            self.reallocate(task, {**row, "reason": "冻结库位换货位"}, self.wh_manager).data[
                "idempotent"
            ]
        )

    def test_equal_quantity_after_manual_adjustment_invalidates_old_confirmation(self):
        from allapp.outbound.services import confirm_warehouse_order
        from allapp.tasking.services import adjust_pick_line_qty

        order = self.prepared()
        _, task = confirm_warehouse_order(order, by_user=self.wh_manager)
        line = task.lines.get()
        operator = self.operator("adjusted-operator")
        first = adjust_pick_line_qty(task.pk, line.pk, "0.5", by_user=operator, client_seq="first")
        adjust_pick_line_qty(task.pk, line.pk, "1", by_user=operator, client_seq="increment")
        last = adjust_pick_line_qty(task.pk, line.pk, "0.5", by_user=operator, client_seq="back")
        self.loc.is_frozen = True
        self.loc.save(update_fields=["is_frozen"])
        self.replacement_stock("REORDERS-02-01-09", "3")
        row = self.client.get(reverse("task-reallocate-sale-line", args=[task.pk])).data["lines"][0]
        self.assertEqual(row["expected_revision"], last["scan_id"])
        payload = {**row, "reason": "现场复核", "physical_confirmed": True}
        stale = self.reallocate(
            task, {**payload, "expected_revision": first["scan_id"]}, self.wh_manager
        )
        self.assertEqual(stale.status_code, 400, stale.data)
        self.assertEqual(TaskScanLog.objects.filter(task_line=line, status="OK").count(), 3)
        self.assertEqual(self.reallocate(task, payload, self.wh_manager).status_code, 200)

    def test_lot_barcode_can_pick_later_reserved_line_in_same_location(self):
        from unittest.mock import patch
        from allapp.outbound.services import confirm_warehouse_order
        from allapp.tasking.models import TaskAssignment

        self.stock.onhand_qty = Decimal("4")
        self.stock.batch_no = "FIRST"
        self.stock.save()
        alternate = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.wh,
            location=self.loc,
            batch_no="SECOND",
            onhand_qty=Decimal("1.125"),
        )
        order = self.prepared()
        _, task = confirm_warehouse_order(order, by_user=self.wh_manager)
        operator = self.operator("batch-picker")
        TaskAssignment.objects.create(task=task, assignee=operator)
        self.client.force_authenticate(operator)
        shown = self.client.get(f"/api/pda/pick-tasks/{task.pk}/lines/")
        self.assertCountEqual(
            [row["source_inventory"]["batch_no"] for row in shown.data], ["FIRST", "SECOND"]
        )
        with patch(
            "allapp.tasking.services._resolver",
            return_value=lambda *_: {
                "product_id": self.product.pk,
                "pack_qty": 1,
                "code_type": "UNIT",
                "lot_no": "SECOND",
            },
        ):
            response = self.client.post(
                f"/api/pda/pick-tasks/{task.pk}/scan/",
                {"barcode": "SECOND", "qty": "1.125", "client_seq": "second-first"},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        line = task.lines.get(pk=response.data["line_id"])
        self.assertEqual(line.plan_meta["inventory_detail_id"], alternate.pk)
        self.assertEqual(TaskScanLog.objects.get(task_line=line).lot_no, "SECOND")

    def test_expiry_after_approval_rechecks_final_post_and_allows_explicit_reallocation(self):
        from datetime import timedelta
        from django.utils import timezone
        from unittest.mock import patch

        today = timezone.now().date()
        self.product.expiry_control = True
        self.product.expiry_basis = "MFG"
        self.product.shelf_life_days = 30
        self.product.expiry_warning_days = 5
        self.product.save()
        self.stock.production_date = today - timedelta(days=30)
        self.stock.expiry_date = today
        self.stock.save()
        task = self.ready_pick()
        line = task.lines.get()
        alt = self.replacement_stock("REORDERS-02-01-06", "3")
        alt.production_date = today
        alt.expiry_date = today + timedelta(days=30)
        alt.save()
        tomorrow = timezone.now() + timedelta(days=1)
        with patch("allapp.inventory.sale_eligibility.timezone", wraps=timezone) as clock:
            clock.now.return_value = tomorrow
            failed = self.post_review(task)
            self.assertEqual(failed.status_code, 400, failed.data)
            self.assertFalse(
                InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).exists()
            )
            response = self.reallocate(task, self.request_data(line), self.wh_manager)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["replacement_lines"][0]["inventory_detail_id"], alt.pk)
            self.assertEqual(TaskScanLog.objects.get(task_line=line).status, "IGNORED")

    def test_shortage_rolls_back_and_valid_line_cannot_be_reallocated(self):
        task = self.ready_pick()
        line = task.lines.get()
        payload = self.request_data(line)
        self.assertEqual(self.reallocate(task, payload, self.wh_manager).status_code, 400)
        self.loc.is_disabled = True
        self.loc.save(update_fields=["is_disabled"])
        replacement = self.replacement_stock("REORDERS-02-01-02", "1")
        failed = self.reallocate(task, payload, self.wh_manager)
        self.assertEqual(failed.status_code, 400, failed.data)
        self.assertIn("替代库存不足", str(failed.data))
        replacement.refresh_from_db()
        self.assertEqual(replacement.allocated_qty, 0)
        self.assertEqual(task.lines.count(), 1)
        self.assertEqual(TaskScanLog.objects.get(task_line=line).status, "OK")
        self.review.refresh_from_db()
        self.assertNotEqual(self.review.status, "CANCELLED")


class ReallocationConcurrencyTests(PhysicalReviewFixture, TransactionTestCase):
    def test_same_manager_retry_reallocates_once(self):
        task = self.ready_pick()
        line = task.lines.get()
        self.loc.is_frozen = True
        self.loc.save(update_fields=["is_frozen"])
        other = Location.objects.create(
            warehouse=self.wh, subwarehouse=self.sub, code="REORDERS-02-01-05"
        )
        replacement = InventoryDetail.objects.create(
            owner=self.owner, product=self.product, warehouse=self.wh, location=other, onhand_qty=3
        )
        self.wh_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="taskconfirm_as_wh_manager"
            )
        )
        barrier = Barrier(2)
        payload = {
            "line_id": line.pk,
            "reason": "冻结库位重新分配",
            "physical_confirmed": True,
            "expected_qty": str(line.qty_done),
            "expected_revision": line.scan_logs.filter(status="OK")
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first()
            or 0,
        }

        def request(_):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.wh_manager.pk))
                barrier.wait(timeout=10)
                res = client.post(
                    reverse("task-reallocate-sale-line", args=[task.pk]), payload, format="json"
                )
                return res.status_code, res.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(request, range(2)))
        self.assertEqual([r[0] for r in responses], [200, 200], responses)
        self.assertCountEqual([r[1]["idempotent"] for r in responses], [True, False])
        self.assertEqual(task.lines.exclude(status="CANCELLED").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="outbound.pick.reallocate").count(), 1)
        replacement.refresh_from_db()
        self.assertEqual(replacement.allocated_qty, Decimal("2.125"))
