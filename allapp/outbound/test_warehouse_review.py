from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Supplier
from allapp.locations.models import Warehouse
from allapp.tasking.models import ReplenishTaskExtra, WmsTask

from .models import OutboundOrder
from .services import approve_owner_order
from .test_reorder import ReorderFixture


class WarehouseReviewFixture(ReorderFixture):
    def setUp(self):
        super().setUp()
        self.wh_manager = get_user_model().objects.create_user(username="review-wh")
        UserRoleScope.objects.create(
            user=self.wh_manager, role="warehouse_manager", warehouse=self.wh
        )
        self.wh_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound", codename="approve_outbound_as_wh_manager"
            )
        )

    def prepared(self, *, unpriced=False):
        if unpriced:
            type(self.product).objects.filter(pk=self.product.pk).update(price=None)
        self.client.force_authenticate(self.sales)
        result = self.create()
        self.assertEqual(result.status_code, 201, result.data)
        order = OutboundOrder.objects.get(pk=result.data["id"])
        order = approve_owner_order(order, by_user=self.manager)
        self.client.force_authenticate(self.wh_manager)
        return order

    def act(self, order, operation, **extra):
        return self.client.post(
            f"/api/outbound/orders/{order.pk}/warehouse-{operation}/",
            {"expected_updated_at": order.updated_at.isoformat(), **extra},
            format="json",
        )

    def detail(self, order):
        return self.client.get(f"/api/outbound/warehouse-review/{order.pk}/")

    def task(self, order):
        return WmsTask.objects.get(task_type="PICK", source_pk=str(order.pk))


class WarehouseReviewTests(WarehouseReviewFixture, TestCase):
    def test_profile_queue_detail_and_scoped_options(self):
        order = self.prepared()
        profile = self.client.get("/api/auth/profile/")
        self.assertTrue(profile.data["capabilities"]["can_review_warehouse_outbound"])
        response = self.client.get(
            "/api/outbound/warehouse-review/", {"search": order.order_no, "stage": "pending"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data["results"]], [order.pk])
        detail = self.detail(order)
        self.assertTrue(detail.data["can_warehouse_confirm"])
        self.assertTrue(detail.data["can_warehouse_reject"])
        self.assertEqual(Decimal(detail.data["lines"][0]["shortage_qty"]), 0)
        choices = self.client.get("/api/outbound/warehouse-review/options/").data
        self.assertEqual(choices["warehouses"], [{"id": self.wh.pk, "name": self.wh.name}])

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_scope_and_permission_are_enforced_even_in_shadow_mode(self):
        order = self.prepared()
        other_wh = Warehouse.objects.create(code="REVIEW2", name="其他仓库")
        other = self.make_order(warehouse=other_wh)
        self.assertEqual(self.detail(other).status_code, 404)
        self.assertEqual(self.act(other, "confirm").status_code, 404)
        self.assertEqual(self.act(other, "reject", reason="越权").status_code, 404)
        response = self.client.get("/api/outbound/warehouse-review/", {"warehouse_id": other_wh.pk})
        self.assertEqual(response.data["count"], 0)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.detail(order).status_code, 403)
        self.assertEqual(self.act(order, "confirm").status_code, 403)
        self.wh_manager.user_permissions.clear()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.wh_manager.pk))
        self.assertEqual(self.detail(order).status_code, 403)

    def test_excludes_pos_assisted_and_returns(self):
        self.client.force_authenticate(self.wh_manager)
        pos = self.make_order(memo="[POS]历史收银")
        returned = self.make_order(
            outbound_type="SUPPLIER_RETURN",
            customer=None,
            supplier=Supplier.objects.create(owner=self.owner, code="REVIEW-SUP", name="供应商"),
        )
        assisted = self.make_order(
            processing_mode="WAREHOUSE_ASSISTED",
            assisted_by=self.wh_manager,
            assistance_reason="测试代办",
        )
        for order in (pos, returned, assisted):
            self.assertEqual(self.detail(order).status_code, 404)
            self.assertEqual(self.act(order, "confirm").status_code, 404)

    def test_unpriced_confirmation_releases_once_and_records_reviewer(self):
        order = self.prepared(unpriced=True)
        result = self.act(order, "confirm")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["approval_status"], "WHS_APPROVED")
        self.assertEqual(result.data["amount_status"], "UNPRICED")
        self.assertEqual(result.data["approved_by_warehouse"], self.wh_manager.pk)
        self.assertEqual(self.task(order).status, "RELEASED")
        self.assertEqual(self.act(order, "confirm").status_code, 409)
        self.assertEqual(
            AuditEvent.objects.filter(action="outbound.order.warehouse_confirm").count(), 1
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.onhand_qty, 10)

    def test_reject_releases_inventory_and_owner_can_revise_and_resubmit(self):
        order = self.prepared(unpriced=True)
        response = self.act(order, "reject", reason="  配送地址需确认  ")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["warehouse_reject_reason"], "配送地址需确认")
        self.assertEqual(response.data["approval_status"], "WHS_REJECTED")
        self.assertEqual(self.task(order).status, "CANCELLED")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.allocated_qty, 1)
        self.assertEqual(self.act(order, "reject", reason="再次退回").status_code, 409)
        self.client.force_authenticate(self.sales)
        read = self.client.get(f"/api/outbound/orders/{order.pk}/")
        self.assertEqual(read.data["warehouse_reject_reason"], "配送地址需确认")
        withdrawn = self.client.post(f"/api/outbound/orders/{order.pk}/withdraw/")
        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        resubmitted = self.client.post(f"/api/outbound/orders/{order.pk}/submit/")
        self.assertEqual(resubmitted.status_code, 200, resubmitted.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_PENDING")
        order = approve_owner_order(order, by_user=self.manager)
        self.client.force_authenticate(self.wh_manager)
        self.assertEqual(self.act(order, "confirm").status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.warehouse_reject_reason, "")

    def test_rejection_requires_reason_and_version(self):
        order = self.prepared()
        for reason in ("", "   ", "x" * 501):
            self.assertEqual(self.act(order, "reject", reason=reason).status_code, 400)
        response = self.client.post(
            f"/api/outbound/orders/{order.pk}/warehouse-reject/",
            {"reason": "需修改"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")

    def test_started_pick_cannot_be_confirmed_or_rejected(self):
        order = self.prepared()
        task = self.task(order)
        WmsTask.objects.filter(pk=task.pk).update(status="IN_PROGRESS")
        detail = self.detail(order)
        self.assertFalse(detail.data["can_warehouse_confirm"])
        self.assertFalse(detail.data["can_warehouse_reject"])
        self.assertEqual(self.act(order, "confirm").status_code, 400)
        self.assertEqual(self.act(order, "reject", reason="不能退回").status_code, 400)

    def test_audit_failure_rolls_back_rejection_and_allocations(self):
        order = self.prepared()
        with patch("allapp.outbound.views.record_audit_event", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError):
                self.act(order, "reject", reason="测试回滚")
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        self.assertEqual(order.warehouse_reject_reason, "")
        self.assertEqual(self.task(order).status, "RESERVED")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.allocated_qty, Decimal("3.125"))

    def test_reject_preserves_shared_replenishment_and_cancels_unshared(self):
        order = self.prepared()
        for shared in (False, True):
            task = WmsTask.objects.create(
                task_no=f"REVIEW-REPLEN-{int(shared)}",
                owner=self.owner,
                warehouse=self.wh,
                task_type="REPLEN",
                status="READY",
                source_model="OutboundOrder",
                source_pk=str(order.pk),
            )
            extra = ReplenishTaskExtra.objects.create(
                task=task,
                trigger="DEMAND",
                demand_order_ids=[order.pk, self.order.pk] if shared else [order.pk],
            )
            if shared:
                shared_task, shared_extra = task, extra
            else:
                unshared_task = task
        response = self.act(order, "reject", reason="取消本单需求")
        self.assertEqual(response.status_code, 200, response.data)
        shared_task.refresh_from_db()
        shared_extra.refresh_from_db()
        unshared_task.refresh_from_db()
        self.assertEqual(shared_task.status, "READY")
        self.assertEqual(shared_extra.demand_order_ids, [self.order.pk])
        self.assertEqual(unshared_task.status, "CANCELLED")

    @override_settings(REPLENISHMENT_DEMAND_ENABLED=True)
    def test_shortfall_enters_waiting_instead_of_claiming_release(self):
        order = self.prepared()
        # Existing allocation is insufficient for a larger approved demand.
        order.lines.update(base_qty=Decimal("5"))
        with patch("allapp.tasking.replenishment.create_demand_tasks") as create:
            response = self.act(order, "confirm")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["approval_status"], "WHS_PENDING")
        self.assertTrue(response.data["replenishment_wait"]["shortages"])
        create.assert_called_once()
        self.assertEqual(self.task(order).status, "RESERVED")


class WarehouseReviewConcurrencyTests(WarehouseReviewFixture, TransactionTestCase):
    def test_concurrent_confirm_and_reject_have_one_winner(self):
        order = self.prepared(unpriced=True)
        barrier = Barrier(2)

        def perform(operation):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.wh_manager.pk))
                barrier.wait(timeout=10)
                response = client.post(
                    f"/api/outbound/orders/{order.pk}/warehouse-{operation}/",
                    {"expected_updated_at": order.updated_at.isoformat(), "reason": "并发退回"},
                    format="json",
                )
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(perform, ["confirm", "reject"]))
        self.assertEqual(sorted(results), [200, 409])
        order.refresh_from_db()
        self.assertIn(order.approval_status, {"WHS_APPROVED", "WHS_REJECTED"})
        self.assertEqual(
            AuditEvent.objects.filter(
                action__in=["outbound.order.warehouse_confirm", "outbound.order.warehouse_reject"]
            ).count(),
            1,
        )
        self.stock.refresh_from_db()
        self.assertEqual(
            self.stock.allocated_qty,
            Decimal("1") if order.approval_status == "WHS_REJECTED" else Decimal("3.125"),
        )
