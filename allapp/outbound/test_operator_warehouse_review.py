from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from allapp.accounts.access import AccessScope
from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.accounts.roles import ROLE_GROUP_TEMPLATES
from allapp.baseinfo.models import Supplier
from allapp.inventory.models import InventoryTransaction
from allapp.locations.models import Warehouse
from allapp.tasking.models import TaskAssignment
from allapp.tasking.services import claim_task

from .authz import strict_order_queryset
from .models import OutboundOrder
from .test_warehouse_review import WarehouseReviewFixture
from .warehouse_review import REVIEW_PERMISSION


class OperatorWarehouseReviewTests(WarehouseReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.operator = get_user_model().objects.create_user(username="optional-order-reviewer")
        UserRoleScope.objects.create(
            user=self.operator, role="warehouse_operator", warehouse=self.wh
        )
        self.grant("tasking.claim_task_as_wh_operator")

    def grant(self, permission):
        app, code = permission.split(".")
        self.operator.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=code)
        )
        self.login_operator()

    def login_operator(self):
        self.operator = get_user_model().objects.get(pk=self.operator.pk)
        self.client.force_authenticate(self.operator)

    def test_authorized_operator_confirms_then_picks_but_cannot_self_review(self):
        order = self.prepared(unpriced=True)
        self.grant(REVIEW_PERMISSION)
        profile = self.client.get("/api/auth/profile/").data
        self.assertEqual(profile["user"]["roles"], ["warehouse_operator"])
        self.assertTrue(profile["capabilities"]["can_review_warehouse_outbound"])
        queue = self.client.get("/api/outbound/warehouse-review/", {"search": order.order_no})
        self.assertEqual([row["id"] for row in queue.data["results"]], [order.pk])
        options = self.client.get("/api/outbound/warehouse-review/options/").data
        self.assertEqual(options["warehouses"], [{"id": self.wh.pk, "name": self.wh.name}])
        todos = self.client.get("/api/pda/home-todos/")
        self.assertEqual(todos.status_code, 200, todos.data)
        self.assertTrue(any(item["key"] == "outbound_review" for item in todos.data["items"]))
        self.assertTrue(self.detail(order).data["can_warehouse_confirm"])
        result = self.act(order, "confirm")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["approved_by_warehouse"], self.operator.pk)
        self.assertEqual(self.act(order, "confirm").status_code, 409)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.order.warehouse_confirm", actor=self.operator
            ).count(),
            1,
        )
        task = self.task(order)
        self.assertEqual(task.status, "RELEASED")
        claim_task(task, by_user=self.operator, allowed_wh_ids={self.wh.pk})
        scanned = self.client.post(
            f"/api/pda/pick-tasks/{task.pk}/scan/",
            {"barcode": self.product.sku, "qty": "2.125"},
            format="json",
        )
        self.assertEqual(scanned.status_code, 200, scanned.data)
        submitted = self.client.post(f"/api/pda/pick-tasks/{task.pk}/create-review-task/")
        self.assertEqual(submitted.status_code, 200, submitted.data)
        denied = self.client.post(f"/api/pda/pick-tasks/{task.pk}/post/")
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertFalse(
            InventoryTransaction.objects.filter(tx_type="ISSUE", src_id=task.pk).exists()
        )
        self.wh_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="taskconfirm_as_wh_manager"
            )
        )
        self.client.force_authenticate(self.wh_manager)
        posted = self.client.post(f"/api/pda/pick-tasks/{task.pk}/post/")
        self.assertEqual(posted.status_code, 200, posted.data)

    def test_review_is_opt_in_and_revocation_blocks_reads_and_writes(self):
        order = self.prepared()
        self.login_operator()
        self.assertNotIn(REVIEW_PERMISSION, ROLE_GROUP_TEMPLATES["warehouse_operator"].permissions)
        self.assertEqual(self.detail(order).status_code, 403)
        self.assertEqual(self.act(order, "confirm").status_code, 403)
        self.assertEqual(self.act(order, "reject", reason="退回").status_code, 403)
        self.grant(REVIEW_PERMISSION)
        self.assertEqual(self.detail(order).status_code, 200)
        self.operator.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label="outbound", codename="approve_outbound_as_wh_manager"
            )
        )
        self.login_operator()
        self.assertFalse(
            self.client.get("/api/auth/profile/").data["capabilities"][
                "can_review_warehouse_outbound"
            ]
        )
        self.assertEqual(self.detail(order).status_code, 403)
        self.assertEqual(self.act(order, "confirm").status_code, 403)
        self.assertEqual(self.act(order, "reject", reason="退回").status_code, 403)

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_operator_review_stays_in_warehouse_and_does_not_expand_pick_ownership(self):
        order = self.prepared()
        self.grant(REVIEW_PERMISSION)
        foreign = self.make_order(warehouse=Warehouse.objects.create(code="OPREV2", name="异仓"))
        self.assertEqual(self.detail(foreign).status_code, 404)
        self.assertEqual(self.act(foreign, "confirm").status_code, 404)
        self.assertEqual(self.act(foreign, "reject", reason="跨仓").status_code, 404)
        self.assertEqual(self.act(order, "confirm").status_code, 200)
        task = self.task(order)
        TaskAssignment.objects.create(task=task, assignee=self.wh_manager)
        self.assertEqual(self.client.get(f"/api/pda/pick-tasks/{task.pk}/").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/api/pda/pick-tasks/{task.pk}/scan/", {"barcode": self.product.sku, "qty": 1}
            ).status_code,
            404,
        )
        self.assertEqual(self.detail(order).status_code, 200)

    def test_rejection_requires_reason_and_records_actual_operator(self):
        order = self.prepared()
        self.grant(REVIEW_PERMISSION)
        self.assertEqual(self.act(order, "reject", reason="").status_code, 400)
        result = self.act(order, "reject", reason="收货地址需核对")
        self.assertEqual(result.status_code, 200, result.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "WHS_REJECTED")
        self.assertEqual(order.approved_by_warehouse_id, self.operator.pk)
        self.assertEqual(self.task(order).status, "CANCELLED")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="outbound.order.warehouse_reject", actor=self.operator
            ).exists()
        )

    def test_optional_review_does_not_expose_pos_returns_or_remove_assisted_access(self):
        standard = self.prepared()
        pos = self.make_order(memo="[POS]旧收银单")
        returned = self.make_order(
            outbound_type="SUPPLIER_RETURN",
            customer=None,
            supplier=Supplier.objects.create(owner=self.owner, code="OPREVSUP", name="供应商"),
        )
        assisted = self.make_order(processing_mode="WAREHOUSE_ASSISTED", assisted_by=self.operator)
        self.grant(REVIEW_PERMISSION)
        for hidden in (pos, returned, assisted):
            self.assertEqual(self.detail(hidden).status_code, 404)
            self.assertEqual(self.client.get(f"/api/outbound/orders/{hidden.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/outbound/orders/{standard.pk}/").status_code, 200)
        self.grant("outbound.process_warehouse_assisted_outbound")
        self.assertEqual(self.client.get(f"/api/outbound/orders/{assisted.pk}/").status_code, 200)
        visible = set(
            strict_order_queryset(OutboundOrder.objects.all(), self.operator).values_list(
                "pk", flat=True
            )
        )
        self.assertIn(standard.pk, visible)
        self.assertIn(assisted.pk, visible)
        self.assertNotIn(pos.pk, visible)
        self.assertNotIn(returned.pk, visible)
        self.assertEqual(AccessScope.for_user(self.operator).roles, {"warehouse_operator"})

    @override_settings(WMS_ACCESS_SCOPE_LEGACY_FALLBACK=True)
    def test_review_grant_does_not_enable_a_legacy_unscoped_operator(self):
        order = self.prepared()
        self.grant(REVIEW_PERMISSION)
        self.operator.role_scopes.all().delete()
        self.operator.warehouse = self.wh
        self.operator.save(update_fields=["warehouse"])
        self.login_operator()
        self.assertFalse(
            self.client.get("/api/auth/profile/").data["capabilities"][
                "can_review_warehouse_outbound"
            ]
        )
        self.assertEqual(self.detail(order).status_code, 403)
