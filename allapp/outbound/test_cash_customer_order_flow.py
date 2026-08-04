from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound.views import CustomerViewSet, OutboundOrderViewSet
from allapp.products.models import Product, ProductUom


class CashCustomerOrderFlowTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="CASHFLOW", name="Cash Flow Owner")
        self.warehouse = Warehouse.objects.create(code="CASHFLOWWH", name="Cash Flow WH")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.user = get_user_model().objects.create_user(
            username="cash-flow-salesperson",
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
        self.cash_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CASH",
            name="散客",
        )
        self.named_drop_ship_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="REGULAR",
            name="一件代发合作客户",
        )
        uom = ProductUom.objects.create(code="CASHFLOWPC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="CASHFLOWSKU",
            sku="CASHFLOWSKU",
            name="Cash Flow Product",
            base_uom=uom,
            price=Decimal("10.00"),
            min_price=Decimal("1.00"),
            volume=Decimal("0.100000"),
            batch_control=False,
            expiry_control=False,
        )
        self.factory = APIRequestFactory()
        self.order_view = OutboundOrderViewSet.as_view({"post": "create"})
        self.key_number = 0

    def order_payload(self, customer, **overrides):
        payload = {
            "warehouse_id": self.warehouse.id,
            "customer_id": customer.id,
            "outbound_type": "SALES",
            "contact": "张三",
            "contact_phone": "13800138000",
            "ship_to": "测试路1号",
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": "1.000",
                    "price": "10.0000",
                }
            ],
        }
        payload.update(overrides)
        return payload

    def create_order(self, payload):
        self.key_number += 1
        request = self.factory.post(
            "/api/outbound/orders/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"cash-flow-{self.key_number:04d}",
        )
        force_authenticate(request, user=self.user)
        return self.order_view(request)

    def test_customer_catalog_returns_cash_code_when_name_has_no_drop_ship_text(self):
        view = CustomerViewSet.as_view({"get": "list"})
        request = self.factory.get("/api/catalog/customers/")
        force_authenticate(request, user=self.user)

        response = view(request)

        self.assertEqual(response.status_code, 200, response.data)
        cash_row = next(
            row for row in response.data["results"] if row["id"] == self.cash_customer.id
        )
        self.assertEqual(
            cash_row,
            {"id": self.cash_customer.id, "code": "CASH", "name": "散客"},
        )

    def test_cash_customer_requires_each_receiver_field_without_writing_order(self):
        for field in ("contact", "contact_phone", "ship_to"):
            with self.subTest(field=field):
                response = self.create_order(
                    self.order_payload(self.cash_customer, **{field: ""})
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)

        self.assertFalse(OutboundOrder.objects.exists())

    def test_cash_customer_with_complete_receiver_data_creates_order(self):
        response = self.create_order(self.order_payload(self.cash_customer))

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.assertEqual(order.customer_id, self.cash_customer.id)
        self.assertEqual(order.contact, "张三")
        self.assertEqual(order.contact_phone, "13800138000")
        self.assertEqual(order.ship_to, "测试路1号")

    def test_non_cash_customer_name_does_not_trigger_receiver_requirements(self):
        response = self.create_order(
            self.order_payload(
                self.named_drop_ship_customer,
                contact="",
                contact_phone="",
                ship_to="",
            )
        )

        self.assertEqual(response.status_code, 201, response.data)
        order = OutboundOrder.objects.get(pk=response.data["id"])
        self.assertEqual(order.customer_id, self.named_drop_ship_customer.id)
