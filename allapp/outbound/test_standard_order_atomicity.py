from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.views import OutboundOrderViewSet
from allapp.products.models import Product, ProductUom


class StandardOrderAtomicityTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="ATOMOWN", name="Atomicity Owner")
        self.warehouse = Warehouse.objects.create(code="ATOMWH", name="Atomicity WH")
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.user = get_user_model().objects.create_user(
            username="atomicity-salesperson",
            password="x",
            owner=self.owner,
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="submit_outbound_as_owner_buyers",
            )
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="ATOMCUSTOMER",
            name="Atomicity Customer",
        )
        uom = ProductUom.objects.create(code="ATOMPC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="ATOMSKU",
            sku="ATOMSKU",
            name="Atomicity Product",
            base_uom=uom,
            price=Decimal("10.00"),
            min_price=Decimal("1.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.factory = APIRequestFactory()
        self.view = OutboundOrderViewSet.as_view({"post": "create"})
        self.key_number = 0

    def item(self, *, qty="1.000", price="10.0000"):
        row = {"product_id": self.product.id, "qty": qty}
        if price is not ...:
            row["price"] = price
        return row

    def create(self, items, *, outbound_type="SALES"):
        self.key_number += 1
        request = self.factory.post(
            "/api/outbound/orders/",
            {
                "warehouse_id": self.warehouse.id,
                "customer_id": self.customer.id,
                "outbound_type": outbound_type,
                "items": items,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"atomic-order-{self.key_number:04d}",
        )
        force_authenticate(request, user=self.user)
        return self.view(request)

    def assert_no_creation_side_effects(self):
        self.assertFalse(OutboundOrder.objects.exists())
        self.assertFalse(OutboundOrderLine.objects.exists())
        self.assertFalse(
            AuditEvent.objects.filter(action="outbound.order.create").exists()
        )

    def test_zero_negative_and_oversized_quantities_are_rejected_before_write(self):
        for qty in ("0.000", "-0.001", "123456789012.000"):
            with self.subTest(qty=qty):
                response = self.create([self.item(qty=qty)])
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("qty", response.data["items"][0])

        self.assert_no_creation_side_effects()

    def test_missing_zero_and_negative_sales_prices_are_rejected_before_write(self):
        for price in (..., "0.0000", "-0.0001"):
            with self.subTest(price=price):
                response = self.create([self.item(price=price)])
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("price", response.data["items"][0])

        self.assert_no_creation_side_effects()

    def test_invalid_second_input_line_rejects_the_entire_order(self):
        response = self.create(
            [
                self.item(qty="1.000"),
                self.item(qty="0.000"),
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("qty", response.data["items"][1])
        self.assert_no_creation_side_effects()

    def test_second_line_model_validation_error_is_mapped_and_rolls_back(self):
        original_save = OutboundOrderLine.save
        save_count = 0

        def fail_second_line(instance, *args, **kwargs):
            nonlocal save_count
            save_count += 1
            if save_count == 2:
                raise DjangoValidationError({"base_qty": "injected invalid quantity"})
            return original_save(instance, *args, **kwargs)

        with mock.patch.object(OutboundOrderLine, "save", new=fail_second_line):
            response = self.create([self.item(), self.item(qty="2.000")])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("qty", response.data["items"][1])
        self.assert_no_creation_side_effects()

    def test_unknown_second_line_exception_propagates_but_rolls_back(self):
        original_save = OutboundOrderLine.save
        save_count = 0

        def fail_second_line(instance, *args, **kwargs):
            nonlocal save_count
            save_count += 1
            if save_count == 2:
                raise RuntimeError("injected storage failure")
            return original_save(instance, *args, **kwargs)

        with mock.patch.object(OutboundOrderLine, "save", new=fail_second_line):
            with self.assertRaisesRegex(RuntimeError, "injected storage failure"):
                self.create([self.item(), self.item(qty="2.000")])

        self.assert_no_creation_side_effects()

    def test_audit_failure_rolls_back_header_and_all_lines(self):
        with mock.patch(
            "allapp.outbound.views.record_audit_event",
            side_effect=RuntimeError("injected audit failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected audit failure"):
                self.create([self.item(), self.item(qty="2.000")])

        self.assert_no_creation_side_effects()

    def test_valid_multiline_order_commits_header_lines_numbers_and_audit(self):
        response = self.create(
            [
                self.item(qty="1.000", price="10.0000"),
                self.item(qty="2.000", price="9.0000"),
            ]
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        lines = list(order.lines.order_by("line_no"))
        self.assertEqual([line.line_no for line in lines], [10, 20])
        self.assertEqual(
            [(line.base_qty, line.base_price) for line in lines],
            [
                (Decimal("1.000"), Decimal("10.0000")),
                (Decimal("2.000"), Decimal("9.0000")),
            ],
        )
        self.assertEqual(order.next_line_no, 30)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.order.create",
                object_id=str(order.pk),
            ).count(),
            1,
        )

    def test_non_sales_order_keeps_optional_zero_price_semantics(self):
        response = self.create(
            [self.item(price=...)],
            outbound_type="OTHER_OUT",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            OutboundOrder.objects.get(pk=response.data["id"]).lines.get().base_price,
            Decimal("0.0000"),
        )
