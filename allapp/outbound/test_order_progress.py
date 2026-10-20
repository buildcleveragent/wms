"""Evidence, tenant isolation and read-only contracts for owner progress."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound.progress import build_order_progress
from allapp.outbound.views import OutboundOrderViewSet
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskStatusLog, WmsTask, WmsTaskLine


class OrderProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="PROG", name="进度货主")
        cls.warehouse = Warehouse.objects.create(code="PROG", name="进度仓库")
        OwnerWarehouseBinding.objects.create(owner=cls.owner, warehouse=cls.warehouse)
        cls.user = get_user_model().objects.create_user(
            username="progress-sales", password="x", owner=cls.owner
        )
        cls.user.user_permissions.add(
            Permission.objects.get(codename="submit_outbound_as_owner_buyers")
        )
        UserRoleScope.objects.create(user=cls.user, role="owner_salesperson", owner=cls.owner)
        cls.customer = Customer.objects.create(
            owner=cls.owner, salesperson=cls.user, code="PROG", name="客户"
        )
        cls.unit = ProductUom.objects.create(code="PROG", name="瓶")
        cls.product = Product.objects.create(
            owner=cls.owner, code="PROG", sku="PROG", name="商品", base_uom=cls.unit, price=10
        )

    def setUp(self):
        self.order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            created_by=self.user,
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        self.line = self.order.lines.create(product=self.product, base_qty=10, base_price=10)
        self.order.refresh_from_db()

    def task(self, kind="PICK", status="RELEASED", qty=0, parent=None, **kwargs):
        task = WmsTask.objects.create(
            task_no=f"PROG-{WmsTask.all_objects.count() + 1}",
            task_type=kind,
            owner=self.owner,
            warehouse=self.warehouse,
            status=status,
            source_app="tasking" if parent else "outbound",
            source_model="WmsTask" if parent else "OutboundOrder",
            source_pk=str(parent.pk if parent else self.order.pk),
            review_status="APPROVED" if status == "COMPLETED" else "NOT_READY",
            posting_status="POSTED" if status == "COMPLETED" else "NOT_READY",
            **kwargs,
        )
        WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            qty_plan=10,
            qty_done=qty,
            src_model="OutboundOrderLine",
            src_id=self.line.pk,
        )
        return task

    def result(self):
        self.order.refresh_from_db()
        return build_order_progress(self.order)

    def set_order(self, **kwargs):
        OutboundOrder.objects.filter(pk=self.order.pk).update(**kwargs)

    def call(self, user=None):
        request = APIRequestFactory().get(f"/api/outbound/orders/{self.order.pk}/progress/")
        force_authenticate(request, user=user or self.user)
        return OutboundOrderViewSet.as_view({"get": "progress"})(request, pk=self.order.pk)

    def test_standard_milestones_and_actual_shipment_only(self):
        self.set_order(approval_status="OWNER_PENDING")
        self.assertEqual(self.result()["current_status"], "OWNER_PENDING")
        self.set_order(approval_status="OWNER_APPROVED")
        self.assertEqual(self.result()["current_status"], "WAREHOUSE_PENDING")
        self.set_order(approval_status="WHS_APPROVED")
        pick = self.task()
        self.assertEqual(self.result()["current_status"], "AWAITING_PICK")
        WmsTask.objects.filter(pk=pick.pk).update(status="IN_PROGRESS")
        self.assertEqual(self.result()["current_status"], "PICKING")
        WmsTask.objects.filter(pk=pick.pk).update(
            status="COMPLETED", review_status="APPROVED", posting_status="POSTED"
        )
        self.assertEqual(self.result()["current_status"], "AWAITING_REVIEW")
        self.task("REVIEW", "COMPLETED", 10, parent=pick)
        self.assertEqual(self.result()["current_status"], "AWAITING_DISPATCH")
        self.assertEqual(Decimal(self.result()["quantities"][0]["shipped_qty"]), 0)
        self.task("DISPATCH", "COMPLETED", 10)
        self.assertEqual(self.result()["current_status"], "SHIPPED")

    def test_partial_dispatch_pending_work_and_overship(self):
        first = self.task("DISPATCH", "COMPLETED", 4)
        second = self.task("DISPATCH", "RELEASED", 6)
        result = self.result()
        self.assertEqual(result["current_status"], "PARTIALLY_SHIPPED")
        self.assertEqual(Decimal(result["quantities"][0]["remaining_qty"]), 6)
        WmsTask.objects.filter(pk=second.pk).update(
            status="COMPLETED", review_status="APPROVED", posting_status="POSTED"
        )
        self.assertEqual(self.result()["current_status"], "SHIPPED")
        first.lines.update(qty_done=8)
        result = self.result()
        self.assertEqual(Decimal(result["quantities"][0]["shipped_qty"]), 14)
        self.assertEqual(Decimal(result["quantities"][0]["remaining_qty"]), 0)
        self.assertIn("over_shipped", [e["code"] for e in result["exceptions"]])
        self.task("DISPATCH", "RELEASED", 0)
        self.assertEqual(self.result()["current_status"], "PARTIALLY_SHIPPED")

    def test_multiple_pick_tasks_and_unapproved_review(self):
        self.task("PICK", "COMPLETED", 5)
        self.task("PICK", "IN_PROGRESS", 2)
        self.assertEqual(self.result()["current_status"], "PICKING")
        review = self.task("REVIEW", "COMPLETED", 5)
        WmsTask.objects.filter(pk=review.pk).update(
            review_status="PENDING", posting_status="NOT_READY"
        )
        self.assertNotEqual(self.result()["current_status"], "AWAITING_DISPATCH")
        stage = next(s for s in self.result()["stages"] if s["code"] == "REVIEW")
        self.assertNotEqual(stage["state"], "completed")

    def test_cancelled_deleted_tasks_and_lines_do_not_count(self):
        self.task("DISPATCH", "CANCELLED", 100)
        deleted = self.task("DISPATCH", "COMPLETED", 100)
        WmsTask.objects.filter(pk=deleted.pk).update(is_deleted=True)
        shipped = self.task("DISPATCH", "COMPLETED", 3)
        WmsTaskLine.objects.create(
            task=shipped, product=self.product, qty_done=100, status="CANCELLED"
        )
        WmsTaskLine.objects.create(
            task=shipped, product=self.product, qty_done=100, is_deleted=True
        )
        self.assertEqual(Decimal(self.result()["quantities"][0]["shipped_qty"]), 3)

    def test_duplicate_products_and_mixed_units(self):
        self.order.lines.create(product=self.product, base_qty=2, base_price=10)
        unit = ProductUom.objects.create(code="PROG-BAG", name="袋")
        product = Product.objects.create(
            owner=self.owner,
            code="PROG-BAG",
            sku="PROG-BAG",
            name="袋装商品",
            base_uom=unit,
            price=10,
        )
        self.order.lines.create(product=product, base_qty=3, base_price=10)
        result = self.result()
        self.assertEqual(len(result["quantities"]), 2)
        self.assertEqual(Decimal(result["quantities"][0]["planned_qty"]), 12)
        self.assertEqual(result["quantities"][1]["unit"], "袋")

    def test_descendant_tasks_and_source_line_chain(self):
        pick = self.task("PICK", "COMPLETED", 10)
        review = self.task("REVIEW", "COMPLETED", 10, parent=pick)
        dispatch = self.task("DISPATCH", "COMPLETED", 10, parent=review)
        review.lines.update(src_model="WmsTaskLine", src_id=pick.lines.get().pk)
        dispatch.lines.update(src_model="tasking.WmsTaskLine", src_id=review.lines.get().pk)
        self.assertEqual(self.result()["current_status"], "SHIPPED")
        review.lines.update(src_id=dispatch.lines.get().pk)
        self.assertEqual(self.result()["current_status"], "DATA_MISMATCH")

    def test_wrong_source_and_product_never_claim_completion(self):
        task = self.task("DISPATCH", "COMPLETED", 10)
        task.lines.update(src_id=self.line.pk + 99999)
        result = self.result()
        self.assertEqual(result["current_status"], "DATA_MISMATCH")
        self.assertIsNone(result["quantities"][0]["shipped_qty"])

    def test_task_scope_and_source_app_are_bounded(self):
        task = self.task("DISPATCH", "COMPLETED", 10)
        other = Owner.objects.create(code="OTHER-PROG", name="其他货主")
        WmsTask.objects.filter(pk=task.pk).update(owner=other)
        self.assertEqual(Decimal(self.result()["quantities"][0]["shipped_qty"]), 0)
        WmsTask.objects.filter(pk=task.pk).update(owner=self.owner, source_app="inbound")
        self.assertEqual(Decimal(self.result()["quantities"][0]["shipped_qty"]), 0)
        wh = Warehouse.objects.create(code="OTHER-PROG", name="其他仓库")
        WmsTask.objects.filter(pk=task.pk).update(warehouse=wh, source_app="outbound")
        self.assertEqual(Decimal(self.result()["quantities"][0]["shipped_qty"]), 0)

    def test_missing_times_and_log_fallback(self):
        task = self.task("PICK", "COMPLETED", 10)
        result = self.result()
        self.assertIsNone(next(s for s in result["stages"] if s["code"] == "PICK")["completed_at"])
        TaskStatusLog.objects.create(task=task, old_status="IN_PROGRESS", new_status="COMPLETED")
        result = self.result()
        stage = next(s for s in result["stages"] if s["code"] == "PICK")
        self.assertTrue(stage["completed_at"].endswith("+08:00"))
        self.assertTrue(result["as_of"].endswith("+08:00"))
        self.task("PICK", "COMPLETED", 1)
        self.assertIsNone(
            next(s for s in self.result()["stages"] if s["code"] == "PICK")["completed_at"]
        )

    def test_resubmission_does_not_reuse_old_approval_time(self):
        record_audit_event(
            action="outbound.order.owner_reject",
            module="outbound",
            obj=self.order,
            metadata={"reason": "修改数量"},
        )
        record_audit_event(action="outbound.order.submit", module="outbound", obj=self.order)
        self.set_order(
            approval_status="OWNER_PENDING",
            owner_reject_reason="修改数量",
            approved_at_ownermanager=timezone.now() - timedelta(days=1),
        )
        result = self.result()
        self.assertEqual(result["current_status"], "OWNER_PENDING")
        self.assertIsNone(result["stages"][1]["completed_at"])
        self.assertFalse(result["exceptions"])
        self.assertEqual(result["history"][0]["message"], "修改数量")
        self.assertIsNotNone(result["stages"][0]["completed_at"])

    def test_failure_notes_do_not_expose_technical_details(self):
        task = self.task("PICK", "COMPLETED", 10)
        WmsTask.objects.filter(pk=task.pk).update(
            posting_status="FAILED", posting_note="SECRET mysql password traceback"
        )
        result = self.result()
        self.assertEqual(result["current_status"], "EXCEPTION")
        self.assertNotIn("SECRET", str(result))
        self.assertIn("posting_failed", [e["code"] for e in result["exceptions"]])

    def test_cancelled_and_abnormally_closed_orders(self):
        self.set_order(approval_status="CANCELLED", close_reason="客户取消")
        self.assertEqual(self.result()["current_status"], "CANCELLED")
        self.assertEqual(self.result()["exceptions"][0]["message"], "客户取消")
        self.set_order(approval_status="WHS_APPROVED", is_closed=True, close_reason="异常关闭")
        self.assertEqual(self.result()["current_status"], "CLOSED")

    def test_assisted_completion_is_not_shipment(self):
        self.task("PICK", "COMPLETED", 10)
        self.set_order(
            processing_mode="WAREHOUSE_ASSISTED", is_closed=True, close_reason="仓库代办出库完成"
        )
        result = self.result()
        self.assertEqual(result["current_status"], "ASSISTED_COMPLETED")
        self.assertIsNone(result["quantities"][0]["shipped_qty"])
        self.assertEqual(result["stages"][-1]["state"], "not_applicable")

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_api_strict_permissions_even_in_shadow_mode(self):
        self.assertEqual(self.call().status_code, 200)
        other = get_user_model().objects.create_user(username="other-progress", owner=self.owner)
        other.user_permissions.add(
            Permission.objects.get(codename="submit_outbound_as_owner_buyers")
        )
        UserRoleScope.objects.create(user=other, role="owner_salesperson", owner=self.owner)
        self.assertEqual(self.call(other).status_code, 404)
        other_owner = Owner.objects.create(code="PROG-OTHER", name="其他")
        UserRoleScope.objects.filter(user=other).update(owner=other_owner)
        self.assertEqual(self.call(other).status_code, 404)

    def test_read_only_and_query_count_does_not_grow_with_lines(self):
        self.task("DISPATCH", "COMPLETED", 10)
        with CaptureQueriesContext(connection) as first:
            result = self.result()
        for i in range(4):
            self.order.lines.create(product=self.product, base_qty=1, base_price=10)
        with CaptureQueriesContext(connection) as second:
            self.result()
        self.assertEqual(len(first), len(second))
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in first))
        self.assertEqual(Decimal(result["quantities"][0]["shipped_qty"]), 10)
        self.assertEqual(self.call().data["quantities"], self.call().data["quantities"])
        self.assertEqual(self.call()["Cache-Control"], "no-store")

    def test_short_pick_review_difference_and_replenishment(self):
        from allapp.tasking.models import PickLineExtra, ReplenishTaskExtra, ReviewLineExtra

        pick = self.task("PICK", "IN_PROGRESS", 3)
        # Bulk insert records already produced by execution; avoid invoking workflow hooks.
        PickLineExtra.objects.bulk_create(
            [PickLineExtra(line=pick.lines.get(), qty_short=7, short_reason="缺货")]
        )
        review = self.task("REVIEW", "IN_PROGRESS", 2)
        ReviewLineExtra.objects.bulk_create(
            [
                ReviewLineExtra(
                    line=review.lines.get(), qty_discrepancy_plan=8, discrepancy_reason="数量差异"
                )
            ]
        )
        replen = self.task("REPLEN", "RELEASED")
        WmsTask.objects.filter(pk=replen.pk).update(source_model="", source_pk="")
        ReplenishTaskExtra.objects.bulk_create(
            [ReplenishTaskExtra(task=replen, demand_order_ids=[self.order.pk])]
        )
        codes = {e["code"] for e in self.result()["exceptions"]}
        self.assertTrue({"short_pick", "review_difference", "replenishment_wait"}.issubset(codes))

    def test_packing_is_optional_and_review_rejection_is_visible(self):
        pick = self.task("PICK", "COMPLETED", 10)
        review = self.task("REVIEW", "COMPLETED", 10, parent=pick)
        self.assertNotIn("PACK", [s["code"] for s in self.result()["stages"]])
        self.task("PACK", "IN_PROGRESS", 5)
        self.assertEqual(self.result()["current_status"], "PACKING")
        WmsTask.objects.filter(pk=review.pk).update(
            review_status="REJECTED", posting_status="NOT_READY", approval_note="数量需复核"
        )
        self.assertEqual(self.result()["current_status"], "EXCEPTION")
        self.assertIn("数量需复核", str(self.result()["exceptions"]))

    def test_dispatch_unit_mismatch_and_zero_shipment(self):
        from allapp.tasking.models import DispatchLineExtra

        task = self.task("DISPATCH", "COMPLETED", 0)
        self.assertNotEqual(self.result()["stages"][-1]["state"], "completed")
        self.assertIn("short_shipped", [e["code"] for e in self.result()["exceptions"]])
        unit = ProductUom.objects.create(code="PROG-BOX", name="箱")
        DispatchLineExtra.objects.bulk_create(
            [DispatchLineExtra(line=task.lines.get(), base_uom=unit)]
        )
        result = self.result()
        self.assertEqual(result["current_status"], "DATA_MISMATCH")
        self.assertEqual(result["stages"][-1]["state"], "exception")

    def test_pending_work_keeps_stage_partial_even_after_full_quantity(self):
        self.task("DISPATCH", "COMPLETED", 10)
        self.task("DISPATCH", "RELEASED", 0)
        self.assertEqual(self.result()["current_status"], "PARTIALLY_SHIPPED")
        self.assertEqual(self.result()["stages"][-1]["state"], "partial")
        self.assertIsNone(self.result()["stages"][-1]["completed_at"])

    def test_warehouse_scope_permissions(self):
        user = get_user_model().objects.create_user(username="progress-wh")
        user.user_permissions.add(Permission.objects.get(codename="view_outboundorder"))
        other = Warehouse.objects.create(code="PROG-W2", name="其他仓库")
        scope = UserRoleScope.objects.create(user=user, role="warehouse_manager", warehouse=other)
        self.assertEqual(self.call(user).status_code, 404)
        UserRoleScope.objects.filter(pk=scope.pk).update(warehouse=self.warehouse)
        self.assertEqual(self.call(user).status_code, 200)

    def test_workflow_transition_is_observed_on_next_read(self):
        task = self.task("DISPATCH", "RELEASED", 10)
        first = self.call().data
        WmsTask.objects.filter(pk=task.pk).update(
            status="COMPLETED", review_status="APPROVED", posting_status="POSTED"
        )
        second = self.call().data
        self.assertNotEqual(first["current_status"], "SHIPPED")
        self.assertEqual(second["current_status"], "SHIPPED")
        self.assertEqual(Decimal(second["quantities"][0]["shipped_qty"]), 10)

    def test_shared_replenishment_does_not_expose_other_order_descendants(self):
        from allapp.tasking.models import ReplenishTaskExtra

        replen = self.task("REPLEN", "RELEASED")
        ReplenishTaskExtra.objects.bulk_create(
            [ReplenishTaskExtra(task=replen, demand_order_ids=[self.order.pk, self.order.pk + 1])]
        )
        self.task("DISPATCH", "COMPLETED", 10, parent=replen)
        result = self.result()
        self.assertEqual(Decimal(result["quantities"][0]["shipped_qty"]), 0)
        self.assertNotEqual(result["current_status"], "SHIPPED")

    def test_cancellation_preserves_recorded_approvals_but_no_active_approval(self):
        record_audit_event(action="outbound.order.owner_approve", module="outbound", obj=self.order)
        self.set_order(approval_status="CANCELLED", close_reason="取消")
        result = self.result()
        self.assertEqual(result["stages"][1]["state"], "completed")
        self.assertEqual(result["stages"][2]["state"], "pending")

    def test_exception_time_and_warehouse_reason_use_current_audit_evidence(self):
        record_audit_event(
            action="outbound.order.warehouse_reject",
            module="outbound",
            obj=self.order,
            metadata={"reason": "仓库暂停接单", "private": "not exposed"},
        )
        self.set_order(approval_status="WHS_REJECTED")
        result = self.result()
        exception = result["exceptions"][0]
        self.assertEqual(exception["message"], "仓库暂停接单")
        self.assertIsNotNone(exception["occurred_at"])
        self.assertNotIn("not exposed", str(result))
        record_audit_event(action="outbound.order.cancel", module="outbound", obj=self.order)
        self.set_order(approval_status="CANCELLED", close_reason="客户取消")
        self.assertIsNotNone(self.result()["exceptions"][0]["occurred_at"])
