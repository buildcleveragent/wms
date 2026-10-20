from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from allapp.accounts.models import UserRoleScope
from allapp.core.choices import ZoneType
from allapp.locations.models import Location
from allapp.products.models import ProductPackage
from allapp.tasking.models import ReplenishmentPolicy, TaskAssignment, WmsTask
from allapp.tasking.replenishment import record_replenishment
from allapp.tasking.services import _run_posting_handler

from .admin import OutboundOrderAdmin
from .models import OutboundOrder
from .services import approve_owner_order
from .test_warehouse_review import WarehouseReviewFixture


class WarehouseReviewContractTests(WarehouseReviewFixture, TestCase):
    @override_settings(WMS_ACCESS_SCOPE_LEGACY_FALLBACK=True)
    def test_legacy_role_binding_does_not_enable_warehouse_review(self):
        order = self.prepared()
        UserRoleScope.objects.filter(user=self.wh_manager).delete()
        self.wh_manager.warehouse = self.wh
        self.wh_manager.save(update_fields=["warehouse"])
        group, _ = Group.objects.get_or_create(name="WMS::仓库管理员")
        self.wh_manager.groups.add(group)
        self.client.force_authenticate(get_user_model().objects.get(pk=self.wh_manager.pk))
        profile = self.client.get("/api/auth/profile/")
        self.assertFalse(profile.data["capabilities"]["can_review_warehouse_outbound"])
        self.assertEqual(self.detail(order).status_code, 403)
        self.assertEqual(self.act(order, "confirm").status_code, 403)
        self.assertEqual(self.act(order, "reject", reason="旧权限不应授权").status_code, 403)

    @override_settings(REPLENISHMENT_DEMAND_ENABLED=True)
    def test_shared_replenishment_resumes_remaining_order_after_rejection(self):
        pick = Location.objects.create(
            warehouse=self.wh,
            subwarehouse=self.sub,
            code="REORDERS-01-01-02",
            name="拣选位",
            zone_type=ZoneType.PICK,
        )
        Location.objects.filter(pk=self.loc.pk).update(zone_type=ZoneType.STORAGE)
        self.loc.refresh_from_db()
        self.stock.location = self.loc
        self.stock.allocated_qty = 0
        self.stock.locked_qty = 0
        self.stock.damaged_qty = 0
        self.stock.save()
        ProductPackage.objects.create(product=self.product, uom=self.uom, qty_in_base=1)
        ReplenishmentPolicy.objects.create(
            owner=self.owner,
            warehouse=self.wh,
            product=self.product,
            target_location=pick,
            min_qty=0,
            target_qty=3,
            replenish_uom=self.uom,
            source_zone_type=ZoneType.STORAGE,
            demand_enabled=True,
            auto_release=True,
        )
        order = self.prepared(unpriced=True)
        response = self.act(order, "confirm")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["approval_status"], "WHS_PENDING")
        replenish = WmsTask.objects.get(task_type="REPLEN", source_pk=str(order.pk))
        self.assertEqual(response.data["replenishment_wait"]["task_nos"], [replenish.task_no])
        self.client.force_authenticate(self.sales)
        second = self.create(self.payload(qty="1"), key="shared-replen-second")
        self.assertEqual(second.status_code, 201, second.data)
        remaining = approve_owner_order(
            OutboundOrder.objects.get(pk=second.data["id"]), by_user=self.manager
        )
        self.client.force_authenticate(self.wh_manager)
        self.assertEqual(self.act(remaining, "confirm").status_code, 200)
        self.assertEqual(WmsTask.objects.filter(task_type="REPLEN").count(), 1)
        order.refresh_from_db()
        self.assertEqual(self.act(order, "reject", reason="本单地址需修改").status_code, 200)
        replenish.refresh_from_db()
        self.assertEqual(replenish.status, "RELEASED")
        self.assertEqual(replenish.replenishtaskextra.demand_order_ids, [remaining.pk])
        TaskAssignment.objects.create(
            task=replenish, assignee=self.wh_manager, accepted_at=timezone.now()
        )
        replenish.status = "IN_PROGRESS"
        replenish.started_at = timezone.now()
        replenish.save(update_fields=["status", "started_at", "updated_at"])
        replenish.lines.update(status="IN_PROGRESS")
        line = replenish.lines.get()
        recorded = record_replenishment(
            task_id=replenish.pk,
            line_id=line.pk,
            request_id="warehouse-review-replen-1",
            from_location_code=self.loc.code,
            to_location_code=pick.code,
            product_code=self.product.code,
            qty=line.qty_plan,
            by_user=self.wh_manager,
        )
        self.assertTrue(recorded["posting_required"])
        _run_posting_handler(replenish.pk, by_user=self.wh_manager, note="仓库确认补货验证")
        order.refresh_from_db()
        remaining.refresh_from_db()
        self.assertEqual(order.approval_status, "WHS_REJECTED")
        self.assertEqual(remaining.approval_status, "WHS_APPROVED")
        self.assertEqual(self.task(remaining).status, "RELEASED")
        self.assertFalse(remaining.lines.get().is_priced)

    def test_permission_alone_cannot_impersonate_warehouse_role(self):
        order = self.prepared()
        self.manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound", codename="approve_outbound_as_wh_manager"
            )
        )
        self.client.force_authenticate(get_user_model().objects.get(pk=self.manager.pk))
        profile = self.client.get("/api/auth/profile/")
        self.assertFalse(profile.data["capabilities"]["can_review_warehouse_outbound"])
        self.assertEqual(self.detail(order).status_code, 403)
        self.assertEqual(self.act(order, "confirm").status_code, 403)
        self.assertEqual(self.act(order, "reject", reason="伪造角色").status_code, 403)

    def test_withdraw_capability_is_only_for_original_salesperson(self):
        order = self.prepared()
        self.assertEqual(self.act(order, "reject", reason="地址待核对").status_code, 200)
        self.client.force_authenticate(self.sales)
        original = self.client.get(f"/api/outbound/orders/{order.pk}/")
        self.assertTrue(original.data["can_withdraw"])
        self.client.force_authenticate(self.wh_manager)
        warehouse = self.client.get(f"/api/outbound/orders/{order.pk}/")
        self.assertFalse(warehouse.data["can_withdraw"])
        self.client.force_authenticate(self.other)
        self.assertIn(
            self.client.post(f"/api/outbound/orders/{order.pk}/withdraw/").status_code, (403, 404)
        )

    def test_admin_requires_reason_and_uses_the_same_rejection_service(self):
        order = self.prepared()
        model_admin = OutboundOrderAdmin(OutboundOrder, admin.site)
        request = RequestFactory().post("/admin/outbound/outboundorder/", {})
        request.user = self.wh_manager
        response = model_admin.action_whs_reject(request, OutboundOrder.objects.filter(pk=order.pk))
        self.assertEqual(response.status_code, 200)
        self.assertIn("warehouse_reject.html", response.template_name)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        request = RequestFactory().post(
            "/admin/outbound/outboundorder/", {"apply": "1", "reason": "后台核对地址"}
        )
        request.user = self.wh_manager
        with patch.object(model_admin, "message_user"):
            model_admin.action_whs_reject(request, OutboundOrder.objects.filter(pk=order.pk))
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "WHS_REJECTED")
        self.assertEqual(order.warehouse_reject_reason, "后台核对地址")
        self.assertEqual(self.task(order).status, "CANCELLED")
