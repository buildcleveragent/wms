from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.enums import PricingStatus
from allapp.outbound.models import OutboundOrder
from allapp.outbound.views import OutboundOrderViewSet
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask


class OutboundSalePriceGuardTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="PRICE-OWN", name="Price Owner")
        self.warehouse = Warehouse.objects.create(code="PRICE-WH", name="Price WH")
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.uom = ProductUom.objects.create(code="PRICE-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="PRICE-SKU",
            sku="PRICE-SKU",
            name="Price Product",
            base_uom=self.uom,
            price=Decimal("100.00"),
            min_price=Decimal("1.00"),
            max_discount=Decimal("20.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.other_product = Product.objects.create(
            owner=self.owner,
            code="PRICE-SKU-2",
            sku="PRICE-SKU-2",
            name="Other Price Product",
            base_uom=self.uom,
            price=Decimal("50.00"),
            min_price=Decimal("40.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.salesperson = get_user_model().objects.create_user(
            username="price-salesperson",
            password="x",
            owner=self.owner,
        )
        self.salesperson.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="submit_outbound_as_owner_buyers",
            )
        )
        UserRoleScope.objects.create(
            user=self.salesperson,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.manager = get_user_model().objects.create_user(
            username="price-manager",
            password="x",
            owner=self.owner,
        )
        self.manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="approve_outbound_as_owner_manager",
            )
        )
        UserRoleScope.objects.create(
            user=self.manager,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="PRICE-CUSTOMER",
            name="Price Customer",
        )
        self.factory = APIRequestFactory()

    def create_order(self, items):
        request = self.factory.post(
            "/api/outbound/orders/",
            {
                "warehouse_id": self.warehouse.id,
                "customer_id": self.customer.id,
                "items": items,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="sale-price-guard-0001",
        )
        force_authenticate(request, user=self.salesperson)
        return OutboundOrderViewSet.as_view({"post": "create"})(request)

    def approve_order(self, order):
        request = self.factory.post(
            f"/api/outbound/orders/{order.pk}/owner-approve/",
            {},
            format="json",
        )
        force_authenticate(request, user=self.manager)
        return OutboundOrderViewSet.as_view({"post": "owner_approve"})(
            request,
            pk=order.pk,
        )

    def item(self, price, *, product=None):
        row = {
            "product_id": (product or self.product).id,
            "qty": "1.000",
        }
        if price is not ...:
            row["price"] = price
        return row

    def test_exact_discount_floor_can_be_created(self):
        response = self.create_order([self.item("80.0000")])

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.assertEqual(order.lines.get().base_price, Decimal("80.0000"))

    def test_price_below_discount_floor_is_rejected_without_order(self):
        response = self.create_order([self.item("79.9999")])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("items", response.data)
        self.assertEqual(OutboundOrder.objects.count(), 0)

    def test_zero_negative_and_missing_prices_are_rejected(self):
        for price in ("0.0000", "-0.0001", ...):
            with self.subTest(price=price):
                response = self.create_order([self.item(price)])
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(OutboundOrder.objects.count(), 0)

    def test_one_invalid_line_rejects_the_whole_order(self):
        response = self.create_order(
            [
                self.item("80.0000"),
                self.item("39.9999", product=self.other_product),
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(OutboundOrder.objects.count(), 0)

    def test_client_price_rule_fields_cannot_bypass_server_rule(self):
        item = self.item("1.0000")
        item.update({"orig_price": "1.00", "min_price": "0", "max_discount": "100"})

        response = self.create_order([item])

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(OutboundOrder.objects.count(), 0)

    def test_approval_rechecks_price_before_freezing_or_allocating(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            outbound_type="SALES",
            submit_status="SUBMITTED",
            approval_status="OWNER_PENDING",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("1.000"),
            base_price=Decimal("79.9999"),
        )

        response = self.approve_order(order)

        self.assertEqual(response.status_code, 400, response.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_PENDING")
        self.assertEqual(order.pricing_status, PricingStatus.PENDING)
        self.assertEqual(order.final_order_amount, Decimal("0.00"))
        self.assertFalse(WmsTask.objects.filter(source_pk=str(order.pk)).exists())

    def test_approval_uses_latest_product_price_rule(self):
        response = self.create_order([self.item("80.0000")])
        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.product.max_discount = Decimal("10.00")
        self.product.save(update_fields=["max_discount", "updated_at"])

        response = self.approve_order(order)

        self.assertEqual(response.status_code, 400, response.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_PENDING")

    def test_corrected_price_can_be_approved(self):
        response = self.create_order([self.item("80.0000")])
        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])

        with mock.patch("allapp.outbound.services.allocate_inventory") as allocate:
            response = self.approve_order(order)

        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        self.assertEqual(order.pricing_status, PricingStatus.CONFIRMED)
        self.assertEqual(order.final_order_amount, Decimal("80.00"))
        allocate.assert_called_once()

    def test_assisted_order_keeps_its_existing_zero_price_rule(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            outbound_type="SALES",
            processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
            submit_status="SUBMITTED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("1.000"),
            base_price=Decimal("0.0000"),
        )

        with mock.patch("allapp.outbound.services.allocate_inventory"):
            order.owner_approve(by_user=self.manager, allow_backorder=True)

        order.refresh_from_db()
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        self.assertEqual(order.final_order_amount, Decimal("0.00"))
