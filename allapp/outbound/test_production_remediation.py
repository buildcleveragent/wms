from decimal import Decimal
from unittest import mock

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound import services as outbound_services
from allapp.outbound.admin import OutboundOrderAdmin
from allapp.outbound.authz import apply_legacy_scope, strict_order_queryset
from allapp.outbound.models import OutboundOrder
from allapp.outbound.views import (
    CustomerViewSet,
    OutboundOrderViewSet,
    OwnerViewSet,
    ProductViewSet,
)
from allapp.products.models import Product, ProductUom
from allapp.tasking import services as task_services
from allapp.tasking.models import TaskAssignment, WmsTask, WmsTaskLine
from allapp.tasking.views import WmsTaskViewSet


def permission(app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


class OutboundProductionRemediationTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Remediation Owner", code="REMED-OWN")
        self.warehouse = Warehouse.objects.create(
            code="REMED-WH",
            name="Remediation Warehouse",
        )
        self.other_warehouse = Warehouse.objects.create(
            code="REMED-WH-2",
            name="Other Remediation Warehouse",
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="RMSW",
            name="Remediation Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="RMSW-01-01-01",
            name="Remediation Location",
        )
        self.uom = ProductUom.objects.create(
            code="REMED-PC",
            name="件",
            is_active=True,
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="REMED-SKU",
            sku="REMED-SKU",
            name="Remediation Product",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.salesperson = get_user_model().objects.create_user(
            username="remediation-sales",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.other_salesperson = get_user_model().objects.create_user(
            username="remediation-sales-other",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        submit_permission = permission(
            "outbound", "submit_outbound_as_owner_buyers"
        )
        self.salesperson.user_permissions.add(submit_permission)
        self.other_salesperson.user_permissions.add(submit_permission)
        UserRoleScope.objects.create(
            user=self.salesperson,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        UserRoleScope.objects.create(
            user=self.other_salesperson,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="REMED-CUST",
            name="Remediation Customer",
        )

    def _order(self, *, created_by=None, qty="3.000", pack_requirement="NONE"):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            submit_status="SUBMITTED",
            approval_status="OWNER_PENDING",
            created_by=created_by or self.salesperson,
        )
        line = order.lines.create(
            product=self.product,
            base_uom=self.uom,
            base_qty=Decimal(qty),
            base_price=Decimal("10.0000"),
            pack_requirement=pack_requirement,
            created_by=created_by or self.salesperson,
        )
        return order, line

    def _inventory(self, qty="10.0000"):
        return InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            product=self.product,
            onhand_qty=Decimal(qty),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )

    def test_salesperson_without_legacy_warehouse_can_select_warehouse(self):
        self.salesperson.warehouse = None
        self.salesperson.save(update_fields=["warehouse"])
        request = APIRequestFactory().post(
            "/api/outbound/orders/",
            {
                "warehouse_id": self.warehouse.pk,
                "customer_id": self.customer.pk,
                "src_bill_no": "REMED-NO-LEGACY-WH",
                "items": [
                    {
                        "product_id": self.product.pk,
                        "qty": "3.000",
                        "price": "10.0000",
                    }
                ],
            },
            format="json",
        )
        force_authenticate(request, user=self.salesperson)

        response = OutboundOrderViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.assertEqual(order.owner_id, self.owner.pk)
        self.assertEqual(order.warehouse_id, self.warehouse.pk)

    @override_settings(USE_TZ=False, TIME_ZONE="Asia/Shanghai")
    def test_offset_etd_is_persisted_as_local_business_time(self):
        request = APIRequestFactory().post(
            "/api/outbound/orders/",
            {
                "warehouse_id": self.warehouse.pk,
                "customer_id": self.customer.pk,
                "src_bill_no": "REMED-LOCAL-ETD",
                "etd": "2026-07-18T16:00:00+08:00",
                "items": [
                    {
                        "product_id": self.product.pk,
                        "qty": "3.000",
                        "price": "10.0000",
                    }
                ],
            },
            format="json",
        )
        force_authenticate(request, user=self.salesperson)

        response = OutboundOrderViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.assertEqual(order.etd.hour, 16)

    def test_task_number_filter_does_not_compare_text_collations(self):
        order, _ = self._order()
        task = WmsTask.objects.create(
            task_no="REMED-FILTER-TASK",
            task_type=WmsTask.TaskType.PICK,
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="outbound",
            source_model="outboundorder",
            source_pk=str(order.pk),
        )
        request = APIRequestFactory().get(
            "/api/outbound/orders/",
            {"task_no": task.task_no},
        )
        force_authenticate(request, user=self.salesperson)

        response = OutboundOrderViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data["results"]], [order.pk])

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_value_is_telemetry_only_and_never_expands_scope(self):
        own_order, _ = self._order(created_by=self.salesperson)
        other_order, _ = self._order(created_by=self.other_salesperson)
        base = OutboundOrder.objects.all()
        strict = strict_order_queryset(base, self.salesperson)

        effective = apply_legacy_scope(
            base_qs=base,
            scoped_qs=strict,
            user=self.salesperson,
            endpoint="test.shadow.enforced",
        )

        self.assertEqual(list(effective.values_list("id", flat=True)), [own_order.id])
        self.assertNotIn(other_order.id, effective.values_list("id", flat=True))

    def test_owner_salesperson_sees_only_orders_they_created(self):
        own_order, _ = self._order(created_by=self.salesperson)
        self._order(created_by=self.other_salesperson)

        visible = strict_order_queryset(OutboundOrder.objects.all(), self.salesperson)

        self.assertEqual(list(visible.values_list("id", flat=True)), [own_order.id])

    def test_owner_manager_sees_all_orders_for_their_owner(self):
        first, _ = self._order(created_by=self.salesperson)
        second, _ = self._order(created_by=self.other_salesperson)
        manager = get_user_model().objects.create_user(
            username="remediation-owner-manager",
            password="x",
            owner=self.owner,
        )
        manager.user_permissions.add(
            permission("outbound", "approve_outbound_as_owner_manager")
        )
        UserRoleScope.objects.create(
            user=manager,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )

        visible = strict_order_queryset(OutboundOrder.objects.all(), manager)

        self.assertEqual(
            set(visible.values_list("id", flat=True)),
            {first.id, second.id},
        )

    def test_customer_catalog_separates_salesperson_and_owner_manager(self):
        other_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.other_salesperson,
            code="REMED-CUST-OTHER",
            name="Other Salesperson Customer",
        )
        manager = get_user_model().objects.create_user(
            username="remediation-catalog-manager",
            password="x",
            owner=self.owner,
        )
        manager.user_permissions.add(
            permission("outbound", "approve_outbound_as_owner_manager")
        )
        UserRoleScope.objects.create(
            user=manager,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        factory = APIRequestFactory()
        view = CustomerViewSet.as_view({"get": "list"})

        sales_request = factory.get("/api/outbound/customers/")
        force_authenticate(sales_request, user=self.salesperson)
        sales_response = view(sales_request)
        manager_request = factory.get("/api/outbound/customers/")
        force_authenticate(manager_request, user=manager)
        manager_response = view(manager_request)

        self.assertEqual(sales_response.status_code, 200)
        self.assertEqual(
            {row["id"] for row in sales_response.data["results"]},
            {self.customer.id},
        )
        self.assertEqual(
            {row["id"] for row in manager_response.data["results"]},
            {self.customer.id, other_customer.id},
        )

    def test_warehouse_catalog_uses_authorized_warehouse_associations(self):
        self._inventory()
        other_owner = Owner.objects.create(name="Other Catalog Owner", code="REMOTHER")
        WmsTask.objects.create(
            task_no="REMED-OTHER-WH-TASK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
            owner=other_owner,
            warehouse=self.other_warehouse,
        )
        operator = get_user_model().objects.create_user(
            username="remediation-operator-catalog",
            password="x",
            warehouse=self.warehouse,
        )
        operator.user_permissions.add(
            permission("tasking", "claim_task_as_wh_operator")
        )
        UserRoleScope.objects.create(
            user=operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        factory = APIRequestFactory()
        owner_view = OwnerViewSet.as_view({"get": "list"})
        owner_request = factory.get("/api/outbound/owners/")
        force_authenticate(owner_request, user=operator)
        owner_response = owner_view(owner_request)

        product_view = ProductViewSet.as_view({"get": "list"})
        product_request = factory.get(
            "/api/outbound/products/", {"owner": other_owner.id}
        )
        force_authenticate(product_request, user=operator)
        product_response = product_view(product_request)

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(
            {row["id"] for row in owner_response.data["results"]},
            {self.owner.id},
        )
        self.assertEqual(product_response.status_code, 403)

    def test_warehouse_operator_sees_personal_tasks_and_unclaimed_pool_only(self):
        operator = get_user_model().objects.create_user(
            username="remediation-task-operator",
            password="x",
            warehouse=self.warehouse,
        )
        coworker = get_user_model().objects.create_user(
            username="remediation-task-coworker",
            password="x",
            warehouse=self.warehouse,
        )
        operator.user_permissions.add(
            permission("tasking", "claim_task_as_wh_operator")
        )
        UserRoleScope.objects.create(
            user=operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )

        own_task = WmsTask.objects.create(
            task_no="REMED-OWN-TASK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.IN_PROGRESS,
            owner=self.owner,
            warehouse=self.warehouse,
            created_by=operator,
        )
        pool_task = WmsTask.objects.create(
            task_no="REMED-POOL-TASK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
            created_by=coworker,
        )
        assigned_to_other = WmsTask.objects.create(
            task_no="REMED-OTHER-TASK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
            created_by=coworker,
        )
        TaskAssignment.objects.create(
            task=assigned_to_other,
            assignee=coworker,
        )
        hidden_in_progress = WmsTask.objects.create(
            task_no="REMED-HIDDEN-TASK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.IN_PROGRESS,
            owner=self.owner,
            warehouse=self.warehouse,
            created_by=coworker,
        )
        view = WmsTaskViewSet()
        raw_request = APIRequestFactory().get("/api/tasks/")
        raw_request.user = operator
        view.request = raw_request

        visible_ids = set(view.get_queryset().values_list("id", flat=True))

        self.assertEqual(visible_ids, {own_task.id, pool_task.id})
        self.assertNotIn(assigned_to_other.id, visible_ids)
        self.assertNotIn(hidden_in_progress.id, visible_ids)

    def test_short_allocation_cannot_be_released(self):
        order, _ = self._order(qty="5.000")
        self._inventory(qty="2.0000")
        order.owner_approve(by_user=self.salesperson, allow_backorder=True)
        order.approval_status = "WHS_APPROVED"
        order.save(update_fields=["approval_status"])

        with self.assertRaises(ValidationError) as exc:
            outbound_services.promote_reserved_pick(
                order,
                by_user=self.salesperson,
            )

        self.assertIn("缺口", str(exc.exception))

    def test_cancel_releases_inventory_and_retains_cancelled_task_lines(self):
        order, _ = self._order()
        inventory = self._inventory()
        order.owner_approve(by_user=self.salesperson, allow_backorder=False)

        cancelled = outbound_services.cancel_order(
            order,
            by_user=self.salesperson,
        )

        inventory.refresh_from_db()
        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model="outboundorder",
            source_pk=str(order.id),
        )
        self.assertEqual(cancelled.approval_status, "CANCELLED")
        self.assertEqual(inventory.allocated_qty, Decimal("0.0000"))
        self.assertEqual(inventory.available_qty, Decimal("10.0000"))
        self.assertEqual(task.status, WmsTask.Status.CANCELLED)
        self.assertTrue(task.lines.exists())
        self.assertFalse(
            task.lines.exclude(status=WmsTaskLine.Status.CANCELLED).exists()
        )

    def test_started_pick_blocks_cancel_and_preserves_allocation(self):
        order, _ = self._order()
        inventory = self._inventory()
        order.owner_approve(by_user=self.salesperson, allow_backorder=False)
        order.approval_status = "WHS_APPROVED"
        order.save(update_fields=["approval_status"])
        task = outbound_services.promote_reserved_pick(
            order,
            by_user=self.salesperson,
        )
        task.status = WmsTask.Status.IN_PROGRESS
        task.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            outbound_services.cancel_order(order, by_user=self.salesperson)

        inventory.refresh_from_db()
        self.assertEqual(inventory.allocated_qty, Decimal("3.0000"))

    def test_real_review_pack_dispatch_chain_closes_standard_order(self):
        order, order_line = self._order(pack_requirement="BOX")
        pick = WmsTask.objects.create(
            task_no="REMED-PICK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="outbound",
            source_model="outboundorder",
            source_pk=str(order.id),
        )
        WmsTaskLine.objects.create(
            task=pick,
            product=self.product,
            from_location=self.location,
            qty_plan=Decimal("3.000"),
            qty_done=Decimal("3.000"),
            status=WmsTaskLine.Status.COMPLETED,
            src_model="OutboundOrderLine",
            src_id=order_line.id,
        )

        review = outbound_services.create_review_task_for_pick(
            pick,
            by_user=self.salesperson,
        )
        replay = outbound_services.create_review_task_for_pick(
            pick,
            by_user=self.salesperson,
        )
        self.assertEqual(review.id, replay.id)
        self.assertEqual(review.task_type, WmsTask.TaskType.REVIEW)

        outbound_services.approve_review_task_for_pick(
            pick,
            by_user=self.other_salesperson,
        )
        pick.refresh_from_db()
        pick.posting_status = WmsTask.PostingStatus.POSTED
        pick.posted_by = self.other_salesperson
        pick.save(update_fields=["posting_status", "posted_by"])
        created = outbound_services.finalize_review_after_pick_post(
            review,
            by_user=self.other_salesperson,
        )
        pack = created["pack_task"]
        self.assertEqual(pack.status, WmsTask.Status.RELEASED)

        pack.lines.update(qty_done=Decimal("3.000"))
        request = RequestFactory().post("/api/tasks/complete/")
        request.user = self.other_salesperson
        task_services.task_complete(request=request, task=pack)
        dispatch = WmsTask.objects.get(
            task_type=WmsTask.TaskType.DISPATCH,
            source_model="outboundorder",
            source_pk=str(order.id),
        )
        dispatch.lines.update(qty_done=Decimal("3.000"))
        task_services.task_complete(request=request, task=dispatch)

        order.refresh_from_db()
        self.assertTrue(order.is_closed)
        self.assertEqual(order.close_reason, "全部发运完成")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="outbound.pack.complete", object_id=str(pack.id)
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="outbound.dispatch.complete", object_id=str(dispatch.id)
            ).exists()
        )


class OutboundAdminHardeningTests(TestCase):
    """Regression coverage for Admin-only shortcuts around the standard flow."""

    def setUp(self):
        self.owner = Owner.objects.create(name="Admin Owner", code="OA-OWN")
        self.other_owner = Owner.objects.create(
            name="Other Admin Owner", code="OA-OWN-2"
        )
        self.warehouse = Warehouse.objects.create(
            code="OA-WH", name="Admin Warehouse"
        )
        self.other_warehouse = Warehouse.objects.create(
            code="OA-WH-2", name="Other Admin Warehouse"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="OASW",
            name="Admin Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="OASW-01-01-01",
            name="Admin Location",
        )
        self.uom = ProductUom.objects.create(code="OA-PC", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="OA-SKU",
            sku="OA-SKU",
            name="Admin Product",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.owner_manager = self._user(
            "oa-owner-manager",
            owner=self.owner,
            permission_code="approve_outbound_as_owner_manager",
            role=UserRoleScope.Role.OWNER_MANAGER,
        )
        self.warehouse_manager = self._user(
            "oa-warehouse-manager",
            warehouse=self.warehouse,
            permission_code="approve_outbound_as_wh_manager",
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
        )
        self.other_salesperson = get_user_model().objects.create_user(
            username="oa-other-salesperson",
            password="x",
            owner=self.other_owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.owner_manager,
            code="OA-CUST",
            name="Admin Customer",
        )
        self.other_customer = Customer.objects.create(
            owner=self.other_owner,
            salesperson=self.other_salesperson,
            code="OA-CUST-2",
            name="Other Admin Customer",
        )
        self.order_admin = OutboundOrderAdmin(OutboundOrder, django_admin.site)

    def _user(self, username, *, owner=None, warehouse=None, permission_code, role):
        user = get_user_model().objects.create_user(
            username=username,
            password="x",
            owner=owner,
            warehouse=warehouse,
            is_staff=True,
        )
        user.user_permissions.add(permission("outbound", permission_code))
        UserRoleScope.objects.create(
            user=user,
            role=role,
            owner=owner if role in UserRoleScope.OWNER_ROLES else None,
            warehouse=warehouse if role in UserRoleScope.WAREHOUSE_ROLES else None,
        )
        return user

    def _order(
        self,
        *,
        owner=None,
        warehouse=None,
        customer=None,
        submit_status="SUBMITTED",
        is_closed=False,
    ):
        owner = owner or self.owner
        warehouse = warehouse or self.warehouse
        customer = customer or self.customer
        order = OutboundOrder.objects.create(
            owner=owner,
            warehouse=warehouse,
            customer=customer,
            submit_status=submit_status,
            approval_status="OWNER_PENDING",
            created_by=self.owner_manager,
            is_closed=is_closed,
            close_reason="fixture closed" if is_closed else None,
        )
        if owner == self.owner:
            order.lines.create(
                product=self.product,
                base_uom=self.uom,
                base_qty=Decimal("3.000"),
                base_price=Decimal("10.0000"),
                created_by=self.owner_manager,
            )
        return order

    @staticmethod
    def _request(user):
        request = RequestFactory().post("/admin/outbound/outboundorder/")
        request.user = user
        return request

    def test_save_model_requires_explicit_final_owner_and_warehouse_scope(self):
        order = self._order()
        request = self._request(self.owner_manager)

        order.memo = "in owner scope"
        self.order_admin.save_model(request, order, form=mock.Mock(), change=True)
        order.refresh_from_db()
        self.assertEqual(order.memo, "in owner scope")

        order.owner = self.other_owner
        order.customer = self.other_customer
        with self.assertRaises(PermissionDenied):
            self.order_admin.save_model(request, order, form=mock.Mock(), change=True)

        warehouse_scoped_order = OutboundOrder.objects.get(pk=order.pk)
        warehouse_scoped_order.warehouse = self.other_warehouse
        with self.assertRaises(PermissionDenied):
            self.order_admin.save_model(
                self._request(self.warehouse_manager),
                warehouse_scoped_order,
                form=mock.Mock(),
                change=True,
            )

    @override_settings(WMS_ACCESS_SCOPE_LEGACY_FALLBACK=True)
    def test_save_model_rejects_legacy_scope_even_when_read_fallback_is_enabled(self):
        order = self._order()
        legacy_manager = get_user_model().objects.create_user(
            username="oa-legacy-manager",
            password="x",
            owner=self.owner,
            is_staff=True,
        )
        legacy_manager.user_permissions.add(
            permission("outbound", "approve_outbound_as_owner_manager")
        )
        order.memo = "must not be saved"

        with self.assertRaises(PermissionDenied):
            self.order_admin.save_model(
                self._request(legacy_manager), order, form=mock.Mock(), change=True
            )

    def test_admin_cannot_directly_close_or_reopen_an_order(self):
        order = self._order()
        request = self._request(self.owner_manager)

        self.assertNotIn("action_close", self.order_admin.actions)
        self.assertNotIn("action_reopen", self.order_admin.actions)
        with self.assertRaises(PermissionDenied):
            self.order_admin.action_close(request, OutboundOrder.objects.filter(pk=order.pk))
        with self.assertRaises(PermissionDenied):
            self.order_admin.action_reopen(request, OutboundOrder.objects.filter(pk=order.pk))

        order.is_closed = True
        order.close_reason = "manual closure"
        with self.assertRaises(PermissionDenied):
            self.order_admin.save_model(request, order, form=mock.Mock(), change=True)
        order.refresh_from_db()
        self.assertFalse(order.is_closed)

    def test_warehouse_manager_cannot_use_legacy_combined_owner_approval_release(self):
        order = self._order()
        request = self._request(self.warehouse_manager)

        self.assertNotIn("action_wh_full_approve_and_release", self.order_admin.actions)
        with self.assertRaises(PermissionDenied):
            self.order_admin.action_wh_full_approve_and_release(
                request, OutboundOrder.objects.filter(pk=order.pk)
            )
        with self.assertRaises(ValidationError):
            outbound_services.approve_and_release_order(
                order, by_user=self.warehouse_manager, allow_backorder=False
            )

        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_PENDING")
        self.assertFalse(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PICK,
                source_pk=str(order.pk),
            ).exists()
        )

    def test_warehouse_manager_confirms_owner_approved_order_via_api(self):
        order = self._order()
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            product=self.product,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        order.owner_approve(by_user=self.owner_manager, allow_backorder=False)
        request = APIRequestFactory().post(
            f"/api/outbound/orders/{order.pk}/warehouse-confirm/",
            {},
            format="json",
        )
        force_authenticate(request, user=self.warehouse_manager)

        response = OutboundOrderViewSet.as_view(
            {"post": "warehouse_confirm"}
        )(request, pk=order.pk)

        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "WHS_APPROVED")
        pick = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model="outboundorder",
            source_pk=str(order.pk),
        )
        self.assertEqual(pick.status, WmsTask.Status.RELEASED)

    def test_owner_approval_requires_submitted_open_order_and_owner_scope(self):
        draft = self._order(submit_status="DRAFT")
        closed = self._order(is_closed=True)
        other = self._order(
            owner=self.other_owner,
            customer=self.other_customer,
        )
        request = self._request(self.owner_manager)

        with mock.patch.object(self.order_admin, "message_user"):
            self.order_admin.action_owner_approve(
                request,
                OutboundOrder.objects.filter(pk__in=[draft.pk, closed.pk, other.pk]),
            )

        for order in (draft, closed, other):
            order.refresh_from_db()
            self.assertEqual(order.approval_status, "OWNER_PENDING")
            self.assertFalse(
                WmsTask.objects.filter(source_pk=str(order.pk)).exists()
            )

    def test_owner_approval_succeeds_only_for_submitted_open_in_scope_order(self):
        order = self._order()
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            product=self.product,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )

        with mock.patch.object(self.order_admin, "message_user"):
            self.order_admin.action_owner_approve(
                self._request(self.owner_manager),
                OutboundOrder.objects.filter(pk=order.pk),
            )

        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        self.assertTrue(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PICK,
                source_pk=str(order.pk),
            ).exists()
        )
