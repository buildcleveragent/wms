from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound import services as outbound_services
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.views import OutboundOrderViewSet
from allapp.products.models import Product, ProductUom


class OwnerOrderReviewStateMachineTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="FLOW-OWN", name="Flow Owner")
        self.warehouse = Warehouse.objects.create(code="FLOW-WH", name="Flow WH")
        self.other_warehouse = Warehouse.objects.create(
            code="FLOW-WH-2", name="Flow WH 2"
        )
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        OwnerWarehouseBinding.objects.create(
            owner=self.owner, warehouse=self.other_warehouse
        )
        submit_perm = Permission.objects.get(
            content_type__app_label="outbound",
            codename="submit_outbound_as_owner_buyers",
        )
        approve_perm = Permission.objects.get(
            content_type__app_label="outbound",
            codename="approve_outbound_as_owner_manager",
        )
        self.salesperson = get_user_model().objects.create_user(
            username="flow-sales", password="x", owner=self.owner
        )
        self.salesperson.user_permissions.add(submit_perm)
        UserRoleScope.objects.create(
            user=self.salesperson,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.other_salesperson = get_user_model().objects.create_user(
            username="flow-sales-2", password="x", owner=self.owner
        )
        self.other_salesperson.user_permissions.add(submit_perm)
        UserRoleScope.objects.create(
            user=self.other_salesperson,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.manager = get_user_model().objects.create_user(
            username="flow-manager", password="x", owner=self.owner
        )
        self.manager.user_permissions.add(approve_perm)
        UserRoleScope.objects.create(
            user=self.manager,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="FLOW-CUSTOMER",
            name="Flow Customer",
        )
        self.customer_2 = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="FLOW-CUSTOMER-2",
            name="Flow Customer 2",
        )
        uom = ProductUom.objects.create(code="FLOW-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="FLOW-SKU",
            sku="FLOW-SKU",
            name="Flow Product",
            base_uom=uom,
            price=Decimal("10.00"),
            min_price=Decimal("1.00"),
        )
        self.factory = APIRequestFactory()

    def make_order(self, *, submit="SUBMITTED", approval="OWNER_PENDING"):
        sequence = OutboundOrder.all_objects.count() + 1
        order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            created_by=self.salesperson,
            outbound_type="SALES",
            submit_status=submit,
            approval_status=approval,
            owner_reject_reason=("fix" if approval == "OWNER_REJECTED" else ""),
            idempotency_key=f"flow-order-key-{sequence:04d}",
            idempotency_fingerprint="original",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("1.000"),
            base_price=Decimal("10.0000"),
        )
        return order

    def call(self, action, method, order, *, user, data=None):
        path = f"/api/outbound/orders/{order.pk}/{action}/"
        request = getattr(self.factory, method)(path, data or {}, format="json")
        force_authenticate(request, user=user)
        return OutboundOrderViewSet.as_view({method: action})(request, pk=order.pk)

    def update_payload(
        self,
        *,
        order,
        customer=None,
        warehouse=None,
        qty="2.000",
        expected_updated_at=None,
    ):
        return {
            "expected_updated_at": expected_updated_at or order.updated_at.isoformat(),
            "warehouse_id": (warehouse or self.other_warehouse).id,
            "customer_id": (customer or self.customer_2).id,
            "outbound_type": "SALES",
            "remark": "corrected",
            "src_bill_no": "FLOW-SRC-1",
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": qty,
                    "price": "9.0000",
                }
            ],
        }

    def test_withdrawn_or_rejected_draft_cannot_be_approved(self):
        for approval, reason in (("OWNER_PENDING", ""), ("OWNER_REJECTED", "fix")):
            with self.subTest(approval=approval):
                order = self.make_order(submit="DRAFT", approval="OWNER_PENDING")
                if approval == "OWNER_REJECTED":
                    order.owner_reject_reason = reason
                    order.approval_status = approval
                    order.save(
                        update_fields=[
                            "approval_status",
                            "owner_reject_reason",
                            "updated_at",
                        ]
                    )
                with mock.patch(
                    "allapp.outbound.services.allocate_inventory"
                ) as allocate:
                    response = self.call(
                        "owner_approve", "post", order, user=self.manager
                    )
                self.assertEqual(response.status_code, 400, response.data)
                allocate.assert_not_called()
                order.refresh_from_db()
                self.assertEqual(order.submit_status, "DRAFT")

    def test_draft_order_cannot_be_confirmed_by_warehouse(self):
        order = self.make_order(submit="DRAFT", approval="OWNER_APPROVED")
        with (
            mock.patch("allapp.outbound.services.promote_reserved_pick") as promote,
            self.assertRaises(ValidationError),
        ):
            outbound_services.confirm_warehouse_order(
                order,
                by_user=self.manager,
            )
        promote.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")

    def test_reject_requires_reason_and_returns_order_to_draft(self):
        order = self.make_order()
        for reason in (None, "   "):
            response = self.call(
                "owner_reject",
                "post",
                order,
                user=self.manager,
                data={"reason": reason},
            )
            self.assertEqual(response.status_code, 400, response.data)
        response = self.call(
            "owner_reject",
            "post",
            order,
            user=self.manager,
            data={"reason": "  请修正数量  "},
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.submit_status, "DRAFT")
        self.assertEqual(order.approval_status, "OWNER_REJECTED")
        self.assertEqual(order.owner_reject_reason, "请修正数量")
        self.assertTrue(
            AuditEvent.objects.filter(
                action="outbound.order.owner_reject", object_id=str(order.pk)
            ).exists()
        )

    def test_database_rejects_rejected_status_without_persisted_reason(self):
        order = self.make_order()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OutboundOrder.objects.filter(pk=order.pk).update(
                submit_status="DRAFT",
                approval_status="OWNER_REJECTED",
                owner_reject_reason="",
            )

    def test_only_creator_can_update_and_valid_update_replaces_lines(self):
        order = self.make_order(submit="DRAFT", approval="OWNER_PENDING")
        old_line_id = order.lines.get().id
        denied = self.call(
            "update",
            "put",
            order,
            user=self.other_salesperson,
            data=self.update_payload(order=order),
        )
        # The strict queryset intentionally hides another salesperson's order.
        self.assertEqual(denied.status_code, 404, denied.data)

        response = self.call(
            "update",
            "put",
            order,
            user=self.salesperson,
            data=self.update_payload(order=order),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["changed"])
        self.assertTrue(response.data["updated_at"])
        order.refresh_from_db()
        self.assertEqual(order.warehouse_id, self.other_warehouse.id)
        self.assertEqual(order.customer_id, self.customer_2.id)
        self.assertEqual(order.lines.get().base_qty, Decimal("2.000"))
        self.assertTrue(OutboundOrderLine.all_objects.get(pk=old_line_id).is_deleted)
        catalog_order = OutboundOrderViewSet._optimized_order_queryset().get(
            pk=order.pk
        )
        self.assertEqual(catalog_order.catalog_total_qty, Decimal("2.000"))
        self.assertEqual(catalog_order.catalog_total_amount, Decimal("18.0000"))

        active_line_id = order.lines.get().id
        repeated = self.call(
            "update",
            "put",
            order,
            user=self.salesperson,
            data=self.update_payload(order=order),
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertFalse(repeated.data["changed"])
        self.assertEqual(order.lines.get().id, active_line_id)

    def test_edit_context_hydrates_authorized_draft_only(self):
        order = self.make_order(submit="DRAFT", approval="OWNER_PENDING")
        request = self.factory.get(f"/api/outbound/orders/{order.pk}/edit-context/")
        force_authenticate(request, user=self.salesperson)
        response = OutboundOrderViewSet.as_view({"get": "edit_context"})(
            request, pk=order.pk
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["warehouse"]["id"], self.warehouse.id)
        self.assertEqual(response.data["customer"]["code"], self.customer.code)
        self.assertEqual(response.data["items"][0]["product_id"], self.product.id)
        self.assertEqual(response.data["items"][0]["qty"], Decimal("1.000"))

    def test_stale_update_returns_409_without_mutating_order_lines_or_audit(self):
        order = self.make_order(submit="DRAFT", approval="OWNER_PENDING")
        stale_version = order.updated_at.isoformat()
        first = self.call(
            "update",
            "put",
            order,
            user=self.salesperson,
            data=self.update_payload(
                order=order,
                qty="2.000",
                expected_updated_at=stale_version,
            ),
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertNotEqual(first.data["updated_at"], stale_version)

        order.refresh_from_db()
        active_line_ids = list(order.lines.values_list("id", flat=True))
        all_line_count = OutboundOrderLine.all_objects.filter(order=order).count()
        audit_count = AuditEvent.objects.filter(
            action="outbound.order.update_draft",
            object_id=str(order.pk),
        ).count()

        stale = self.call(
            "update",
            "put",
            order,
            user=self.salesperson,
            data=self.update_payload(
                order=order,
                qty="3.000",
                expected_updated_at=stale_version,
            ),
        )
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertEqual(stale.data["code"], "stale_order_edit")
        self.assertEqual(
            stale.data["detail"],
            "订单已被其他会话修改，请重新加载。",
        )
        self.assertEqual(stale.data["current_updated_at"], first.data["updated_at"])

        order.refresh_from_db()
        self.assertEqual(order.lines.get().base_qty, Decimal("2.000"))
        self.assertEqual(
            list(order.lines.values_list("id", flat=True)), active_line_ids
        )
        self.assertEqual(
            OutboundOrderLine.all_objects.filter(order=order).count(),
            all_line_count,
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.order.update_draft",
                object_id=str(order.pk),
            ).count(),
            audit_count,
        )

    def test_invalid_update_and_audit_failure_roll_back_the_whole_order(self):
        order = self.make_order(submit="DRAFT", approval="OWNER_REJECTED")
        order.owner_reject_reason = "fix"
        order.save(update_fields=["owner_reject_reason", "updated_at"])
        original_line_id = order.lines.get().id
        invalid = self.call(
            "update",
            "put",
            order,
            user=self.salesperson,
            data=self.update_payload(order=order, qty="0"),
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(order.lines.get().id, original_line_id)

        with mock.patch(
            "allapp.outbound.views.record_audit_event",
            side_effect=RuntimeError("audit down"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit down"):
                self.call(
                    "update",
                    "put",
                    order,
                    user=self.salesperson,
                    data=self.update_payload(order=order),
                )
        order.refresh_from_db()
        self.assertEqual(order.warehouse_id, self.warehouse.id)
        self.assertEqual(order.lines.get().id, original_line_id)

    def test_save_and_resubmit_preserves_latest_reason_then_can_approve(self):
        order = self.make_order()
        rejected = self.call(
            "owner_reject",
            "post",
            order,
            user=self.manager,
            data={"reason": "请调整客户"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        # Rejection changes the optimistic-lock version. Editing starts from
        # the freshly reloaded order, as the owner client does after entering
        # the rejected draft again.
        order.refresh_from_db()
        updated = self.call(
            "update",
            "put",
            order,
            user=self.salesperson,
            data=self.update_payload(order=order),
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        submitted = self.call("submit", "post", order, user=self.salesperson)
        self.assertEqual(submitted.status_code, 200, submitted.data)
        order.refresh_from_db()
        self.assertEqual(
            (order.submit_status, order.approval_status),
            ("SUBMITTED", "OWNER_PENDING"),
        )
        self.assertEqual(order.owner_reject_reason, "请调整客户")

        with mock.patch("allapp.outbound.services.allocate_inventory"):
            approved = self.call("owner_approve", "post", order, user=self.manager)
        self.assertEqual(approved.status_code, 200, approved.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")

    def test_approval_audit_failure_rolls_back_status_and_pricing(self):
        order = self.make_order()
        with (
            mock.patch("allapp.outbound.services.allocate_inventory"),
            mock.patch(
                "allapp.outbound.views.record_audit_event",
                side_effect=RuntimeError("audit down"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit down"):
                self.call("owner_approve", "post", order, user=self.manager)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_PENDING")
        self.assertEqual(order.pricing_status, "PENDING")
