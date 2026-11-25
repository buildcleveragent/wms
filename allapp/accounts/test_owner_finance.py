"""Owner finance: real HTTP credentials, all-owner reads and immutable business data."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from allapp.baseinfo.models import Customer
from allapp.billing.models import Bill, BillingPeriod
from allapp.inbound import arrival_services, customer_return_services
from allapp.inbound.arrival_api import actions as arrival_actions
from allapp.inbound.customer_return_api import actions as return_actions
from allapp.inbound.customer_return_models import CustomerReturn
from allapp.inventory import test_owner_history as history_fixtures
from allapp.outbound.models import OutboundOrder

from .access import AccessScope
from .models import UserRoleScope
from .role_memberships import sync_user_role_membership
from .roles import ROLE_GROUP_TEMPLATES


class OwnerFinanceTests(TestCase):
    tx = history_fixtures.OwnerHistoryTests.tx

    @classmethod
    def setUpTestData(cls):
        history_fixtures.OwnerHistoryTests.setUpTestData.__func__(cls)
        call_command("sync_wms_role_groups", stdout=StringIO())
        cls.user.groups.add(Group.objects.get(name="WMS::货主管理员"))
        cls.finance = get_user_model().objects.create_user(
            username="owner-finance", owner=cls.owner, password="FinanceTesting-2026!"
        )
        UserRoleScope.objects.create(user=cls.finance, role="owner_finance", owner=cls.owner)
        sync_user_role_membership(cls.finance)
        cls.sales = get_user_model().objects.create_user(username="finance-sales", owner=cls.owner)
        UserRoleScope.objects.create(user=cls.sales, role="owner_salesperson", owner=cls.owner)
        cls.sales_order = OutboundOrder.objects.create(
            owner=cls.owner, warehouse=cls.warehouse, customer=cls.customer, created_by=cls.sales
        )
        OutboundOrder.objects.filter(pk=cls.sales_order.pk).update(
            submit_status="SUBMITTED", approval_status="OWNER_PENDING"
        )
        cls.sales2 = get_user_model().objects.create_user(
            username="finance-sales-2", owner=cls.owner
        )
        UserRoleScope.objects.create(user=cls.sales2, role="owner_salesperson", owner=cls.owner)
        OutboundOrder.objects.filter(pk=cls.outbound.pk).update(created_by=cls.sales2)
        cls.foreign_order = OutboundOrder.objects.create(
            owner=cls.other,
            warehouse=cls.warehouse,
            customer=Customer.objects.create(
                owner=cls.other, salesperson=cls.user, code="FIN-X", name="其他客户"
            ),
            created_by=cls.user,
        )
        cls.customer_return = CustomerReturn.objects.create(
            number="FINANCE-RETURN",
            owner=cls.owner,
            warehouse=cls.warehouse,
            original_order=cls.sales_order,
            created_by=cls.sales,
        )
        cls.period = BillingPeriod.objects.create(
            owner=cls.owner,
            warehouse=cls.warehouse,
            label="2026-11",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )
        cls.bill = Bill.objects.create(
            owner=cls.owner,
            warehouse=cls.warehouse,
            period=cls.period,
            invoice_no="FINANCE-BILL",
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
        )

    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        return client

    def test_role_template_scope_profile_and_constraints(self):
        scope = AccessScope.for_user(self.finance)
        self.assertTrue(scope.is_valid)
        self.assertEqual(scope.owner_ids, {self.owner.pk})
        template = ROLE_GROUP_TEMPLATES["owner_finance"]
        self.assertFalse(
            any(
                p.split(".")[1].startswith(("add_", "change_", "delete_", "approve_", "submit_"))
                for p in template.permissions
            )
        )
        data = self.client_for(self.finance).get("/api/auth/profile/").data
        self.assertEqual(data["user"]["roles"], ["owner_finance"])
        self.assertTrue(data["capabilities"]["owner_read_only"])
        self.assertTrue(data["capabilities"]["can_view_owner_operations"])
        self.assertTrue(data["capabilities"]["can_export_owner_inventory"])
        self.assertFalse(data["capabilities"]["can_manage_owner_inventory_alerts"])
        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(
                user=self.finance, role="owner_finance", warehouse=self.warehouse
            )
        with self.assertRaises(ValidationError):
            UserRoleScope.objects.create(user=self.finance, role="owner_manager", owner=self.owner)

    def test_reads_match_manager_and_include_colleagues(self):
        finance, manager = self.client_for(self.finance), self.client_for(self.user)
        for url in (
            "/api/outbound/orders/",
            "/api/inbound/orders/",
            "/api/catalog/customers/",
            "/api/inbound/customer-returns/",
            "/api/billing/periods/",
            "/api/billing/bills/",
        ):
            with self.subTest(url=url):
                actual, expected = finance.get(url), manager.get(url)
                self.assertEqual(actual.status_code, 200, getattr(actual, "data", None))
                self.assertEqual(expected.status_code, 200)
                actual_rows = (
                    actual.data if isinstance(actual.data, list) else actual.data["results"]
                )
                expected_rows = (
                    expected.data if isinstance(expected.data, list) else expected.data["results"]
                )
                self.assertEqual(
                    [r["id"] for r in actual_rows],
                    [r["id"] for r in expected_rows],
                )
        orders = finance.get("/api/outbound/orders/").data["results"]
        self.assertEqual({o["id"] for o in orders}, {self.outbound.pk, self.sales_order.pk})
        for order in orders:
            for field in ("can_edit", "can_reorder", "can_withdraw", "can_owner_review"):
                self.assertFalse(order[field])
        self.assertEqual(
            finance.get(f"/api/outbound/orders/{self.foreign_order.pk}/").status_code, 404
        )
        self.assertEqual(
            finance.get(f"/api/outbound/customers/{self.customer.pk}/addresses/").status_code, 200
        )
        workbench = finance.get("/api/outbound/owner-workbench/")
        self.assertEqual(workbench.status_code, 200, workbench.data)
        self.assertEqual(workbench.data["scope_label"], "本货主全部订单")
        self.assertIn("pending", workbench.data["available_categories"])
        returned = finance.get(f"/api/inbound/customer-returns/{self.customer_return.pk}/")
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.data["allowed_actions"], [])
        bill = finance.get(f"/api/billing/bills/{self.bill.pk}/")
        self.assertEqual(bill.status_code, 200)
        self.assertEqual(Decimal(bill.data["total"]), 100)

    def test_statistics_details_and_exports_are_full_owner_and_read_only(self):
        tx = self.tx(100, "2026-11-02T10:00:00")
        self.tx(-30, "2026-11-03T10:00:00", src_id=self.sales_order.pk)
        finance, manager = self.client_for(self.finance), self.client_for(self.user)
        root = "/api/inventory/owner-history/statistics/"
        with CaptureQueriesContext(connection) as queries:
            actual = finance.get(root + "summary/")
            expected = manager.get(root + "summary/")
            self.assertEqual(actual.status_code, 200)
            self.assertEqual(actual.data["totals"], expected.data["totals"])
            self.assertEqual(actual.data["access_scope"], "owner")
            for chart_type in ("trend", "ranking"):
                query = {"chart": chart_type, "direction": "inbound"}
                chart = finance.get(root + "chart/", query)
                self.assertEqual(chart.status_code, 200)
                self.assertEqual(
                    chart.data["results"], manager.get(root + "chart/", query).data["results"]
                )
                self.assertEqual(Decimal(chart.data["results"][0]["value"]), 100)
            detail = finance.get(root + f"events/ledger-{tx.pk}/")
            self.assertEqual(detail.status_code, 200)
        writes = [
            q["sql"]
            for q in queries
            if q["sql"].lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT"))
        ]
        self.assertFalse(any("inventory_inventory" in sql.lower() for sql in writes))
        for url in (root + "export/?view=products", "/api/outbound/orders/owner-export/"):
            response = finance.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b"".join(response.streaming_content).startswith(b"PK"))
        response = finance.post("/api/reports/v2/operations/exports/", {}, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertEqual(
            finance.post(
                "/api/reports/v2/operations/exports/", {"owner": self.other.pk}, format="json"
            ).status_code,
            403,
        )

    def test_jwt_business_writes_denied_even_with_direct_grants(self):
        self.finance.user_permissions.set(Permission.objects.all())
        client = self.client_for(self.finance)
        before = list(OutboundOrder.objects.values())
        endpoints = [
            ("post", "/api/outbound/orders/"),
            ("patch", f"/api/outbound/orders/{self.outbound.pk}/"),
            ("delete", f"/api/outbound/orders/{self.outbound.pk}/"),
            ("post", f"/api/outbound/orders/{self.outbound.pk}/owner-approve/"),
            ("post", f"/api/outbound/orders/{self.outbound.pk}/cancel/"),
            ("post", "/api/inbound/orders/"),
            ("post", "/api/inbound/customer-returns/"),
            ("post", "/api/inbound/receive_without_order/import_confirm/"),
            ("post", f"/api/outbound/customers/{self.customer.pk}/addresses/"),
            ("patch", "/api/inventory/owner-alerts/settings/"),
            ("post", "/api/billing/periods/"),
        ]
        for method, url in endpoints:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 403)
                self.assertIn("只读", response.json()["detail"])
        self.assertEqual(list(OutboundOrder.objects.values()), before)
        self.assertEqual(arrival_actions(self.finance, self.inbound), [])
        self.assertEqual(return_actions(None, self.finance), [])

    def test_session_write_barrier_and_role_changes(self):
        client = APIClient()
        client.force_login(self.finance)
        self.assertEqual(client.post("/api/outbound/orders/", {}).status_code, 403)
        # The same JWT must immediately pick up the new finance role.
        client = self.client_for(self.sales)
        UserRoleScope.objects.filter(user=self.sales).update(role="owner_finance")
        self.assertEqual(client.post("/api/outbound/orders/", {}).status_code, 403)

    def test_authentication_password_and_print_ticket_remain_available(self):
        client = APIClient()
        login = client.post(
            "/api/auth/login/",
            {
                "username": self.finance.username,
                "password": "FinanceTesting-2026!",
            },
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        token = RefreshToken.for_user(self.finance)
        response = client.post("/api/auth/refresh/", {"refresh": str(token)}, format="json")
        self.assertEqual(response.status_code, 200)
        logout = client.post("/api/auth/logout/", {"refresh": login.data["refresh"]}, format="json")
        self.assertEqual(logout.status_code, 204)
        response = client.post(
            "/api/print-tickets/", {"path": "/api/pda/pick-tasks/1/print/"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        response = client.post(
            "/api/auth/password/change/",
            {
                "old_password": "FinanceTesting-2026!",
                "new_password1": "FinanceChanged-2026!",
                "new_password2": "FinanceChanged-2026!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.finance.refresh_from_db()
        self.assertTrue(self.finance.check_password("FinanceChanged-2026!"))

    def test_group_sync_preserves_existing_manager_customizations(self):
        group = Group.objects.get(name="WMS::货主管理员")
        group.permissions.remove(Permission.objects.get(codename="export_operations"))
        call_command("sync_wms_role_groups", stdout=StringIO())
        self.assertFalse(group.permissions.filter(codename="export_operations").exists())
        self.assertTrue(
            Group.objects.get(name="WMS::货主财务")
            .permissions.filter(codename="export_operations")
            .exists()
        )

    def test_service_commands_reject_finance_before_any_business_work(self):
        self.finance.user_permissions.set(Permission.objects.all())
        with self.assertRaises(PermissionDenied):
            arrival_services.command(
                self.finance, {}, "note", [], lambda: self.fail("must not run")
            )
        with self.assertRaises(PermissionDenied):
            customer_return_services.command(self.finance, "note", {}, self.customer_return.pk)

    def test_manager_can_still_write_and_finance_can_read_the_result(self):
        url = f"/api/outbound/customers/{self.customer.pk}/addresses/"
        response = self.client_for(self.user).post(
            url,
            {"contact": "收件人", "phone": "13800000000", "address": "财务回归测试地址"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="finance-manager-address-1",
        )
        self.assertEqual(response.status_code, 201, response.data)
        rows = self.client_for(self.finance).get(url).data["results"]
        self.assertEqual([row["id"] for row in rows], [response.data["id"]])
