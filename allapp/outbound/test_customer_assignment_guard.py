import io
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound.serializers import OutboundOrderReadSerializer
from allapp.outbound.views import CustomerViewSet, OutboundOrderViewSet
from allapp.products.models import Product, ProductUom


class CustomerAssignmentGuardTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="CUST-GUARD", name="Customer Guard")
        self.warehouse = Warehouse.objects.create(code="CUST-GUARD-WH", name="Guard WH")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        submit_permission = Permission.objects.get(
            content_type__app_label="outbound",
            codename="submit_outbound_as_owner_buyers",
        )
        self.salesperson = self._salesperson("customer-guard-sales", submit_permission)
        self.other_salesperson = self._salesperson(
            "customer-guard-other", submit_permission
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.salesperson,
            code="CUST-GUARD-OWN",
            name="Own Customer",
        )
        self.other_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.other_salesperson,
            code="CUST-GUARD-OTHER",
            name="Other Salesperson Customer",
        )
        self.cash_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.other_salesperson,
            code="CASH",
            name="Shared Cash Customer",
        )
        uom = ProductUom.objects.create(code="CUST-GUARD-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="CUST-GUARD-SKU",
            sku="CUST-GUARD-SKU",
            name="Guard Product",
            base_uom=uom,
            price=Decimal("10.0000"),
            min_price=Decimal("8.0000"),
        )
        self.factory = APIRequestFactory()
        self.sequence = 0

    def _salesperson(self, username, permission):
        user = get_user_model().objects.create_user(
            username=username,
            password="x",
            owner=self.owner,
        )
        user.user_permissions.add(permission)
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        return user

    def _payload(self, customer):
        return {
            "warehouse_id": self.warehouse.id,
            "customer_id": customer.id,
            "outbound_type": "SALES",
            "contact": "收件人",
            "contact_phone": "13800138000",
            "ship_to": "测试地址",
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": "1.000",
                    "price": "10.0000",
                }
            ],
        }

    def _create(self, customer):
        self.sequence += 1
        request = self.factory.post(
            "/api/outbound/orders/",
            self._payload(customer),
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"customer-guard-{self.sequence:04d}",
        )
        force_authenticate(request, user=self.salesperson)
        return OutboundOrderViewSet.as_view({"post": "create"})(request)

    def test_salesperson_can_create_order_for_own_customer(self):
        response = self._create(self.customer)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            OutboundOrder.objects.get(pk=response.data["id"]).customer_id,
            self.customer.id,
        )

    def test_cross_salesperson_customer_is_rejected_with_uniform_field_error(self):
        response = self._create(self.other_customer)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data["customer_id"][0],
            "客户不存在、未启用或不在当前业务员可用范围内。",
        )
        self.assertFalse(OutboundOrder.objects.exists())

    def test_cross_owner_customer_uses_the_same_non_disclosing_error(self):
        other_owner = Owner.objects.create(code="CUST-OTHER", name="Other Owner")
        cross_owner_customer = Customer.objects.create(
            owner=other_owner,
            salesperson=self.salesperson,
            code="CUST-CROSS-OWNER",
            name="Cross Owner Customer",
        )

        response = self._create(cross_owner_customer)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data["customer_id"][0],
            "客户不存在、未启用或不在当前业务员可用范围内。",
        )
        self.assertFalse(OutboundOrder.objects.exists())

    def test_cash_customer_is_shared_within_the_owner(self):
        response = self._create(self.cash_customer)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            OutboundOrder.objects.get(pk=response.data["id"]).customer_id,
            self.cash_customer.id,
        )

    def test_customer_catalog_includes_shared_cash_but_not_other_regular_customer(self):
        request = self.factory.get("/api/catalog/customers/")
        force_authenticate(request, user=self.salesperson)

        response = CustomerViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        customer_ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.customer.id, customer_ids)
        self.assertIn(self.cash_customer.id, customer_ids)
        self.assertNotIn(self.other_customer.id, customer_ids)

    def test_draft_update_cannot_switch_to_another_salespersons_customer(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            created_by=self.salesperson,
            outbound_type="SALES",
            submit_status="DRAFT",
            approval_status="OWNER_PENDING",
            idempotency_key="customer-guard-update-original",
            idempotency_fingerprint="original",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("1.000"),
            base_price=Decimal("10.0000"),
        )
        payload = self._payload(self.other_customer)
        payload["expected_updated_at"] = order.updated_at.isoformat()
        request = self.factory.put(
            f"/api/outbound/orders/{order.pk}/",
            payload,
            format="json",
        )
        force_authenticate(request, user=self.salesperson)

        response = OutboundOrderViewSet.as_view({"put": "update"})(
            request, pk=order.pk
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("customer_id", response.data)
        order.refresh_from_db()
        self.assertEqual(order.customer_id, self.customer.id)

    def test_order_names_are_serialized_without_per_order_queries(self):
        for _ in range(3):
            OutboundOrder.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                customer=self.customer,
                created_by=self.salesperson,
                outbound_type="SALES",
                submit_status="DRAFT",
                approval_status="OWNER_PENDING",
            )
        request = self.factory.get("/api/outbound/orders/")
        force_authenticate(request, user=self.salesperson)
        scope = AccessScope.for_user(self.salesperson)
        self.salesperson.has_perm("outbound.submit_outbound_as_owner_buyers")
        queryset = OutboundOrderViewSet._optimized_order_queryset().order_by("id")

        with self.assertNumQueries(2):
            data = OutboundOrderReadSerializer(
                queryset,
                many=True,
                context={"request": request, "access_scope": scope},
            ).data

        self.assertEqual(len(data), 3)
        self.assertTrue(all(row["owner_name"] == self.owner.name for row in data))
        self.assertTrue(
            all(row["warehouse_name"] == self.warehouse.name for row in data)
        )
        self.assertTrue(
            all(row["customer_name"] == self.customer.name for row in data)
        )


class CustomerAssignmentAuditCommandTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="CUST-AUDIT", name="Customer Audit")
        self.warehouse = Warehouse.objects.create(code="CUST-AUDIT-WH", name="Audit WH")
        self.creator = get_user_model().objects.create_user(
            username="customer-audit-creator", password="x", owner=self.owner
        )
        self.assignee = get_user_model().objects.create_user(
            username="customer-audit-assignee", password="x", owner=self.owner
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.assignee,
            code="CUST-AUDIT-REGULAR",
            name="Audit Regular Customer",
        )
        self.cash = Customer.objects.create(
            owner=self.owner,
            salesperson=self.assignee,
            code="CASH",
            name="Audit Cash Customer",
        )

    def _order(self, customer, **overrides):
        values = {
            "owner": self.owner,
            "warehouse": self.warehouse,
            "customer": customer,
            "created_by": self.creator,
            "outbound_type": "SALES",
            "submit_status": "DRAFT",
            "approval_status": "OWNER_PENDING",
        }
        values.update(overrides)
        return OutboundOrder.objects.create(**values)

    def test_command_reports_only_editable_non_cash_mismatches_and_fails(self):
        mismatch = self._order(self.customer)
        self._order(self.cash)
        self._order(self.customer, submit_status="SUBMITTED")
        self._order(self.customer, created_by=self.assignee)
        stdout = io.StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_outbound_customer_assignment", stdout=stdout)

        report = json.loads(stdout.getvalue())
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(
            [row["order_id"] for row in report["orders"]], [mismatch.id]
        )

    def test_command_succeeds_when_no_assignment_mismatch_exists(self):
        self._order(self.customer, created_by=self.assignee)
        self._order(self.cash)
        stdout = io.StringIO()

        call_command("audit_outbound_customer_assignment", stdout=stdout)

        report = json.loads(stdout.getvalue())
        self.assertEqual(report, {"finding_count": 0, "orders": []})
