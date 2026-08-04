from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import get_resolver, path, resolve
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Warehouse

from .checks import (
    _iter_registered_routes,
    check_legacy_sales_api_not_registered,
)
from .models import Channel, PriceList, SalesOrder


User = get_user_model()


RETIRED_RESOURCE_PATHS = (
    "biz-orgs",
    "salespersons",
    "channels",
    "customer-channels",
    "customer-product-policies",
    "channel-product-policies",
    "price-groups",
    "price-lists",
    "price-items",
    "customer-special-prices",
    "price-memories",
    "promotions",
    "promotion-gift-items",
    "promotion-discount-steps",
    "promotion-special-prices",
    "sales-orders",
    "sales-order-lines",
    "visit-plans",
    "attendance",
    "visit-records",
    "gps-points",
    "photo-types",
    "visit-photos",
    "credit-policies",
    "ar-ledgers",
    "expense-advances",
    "expense-writeoffs",
    "merch-plans",
    "merch-agreements",
    "merch-audits",
    "rebate-payouts",
    "mobile/home",
    "mobile/customers",
    "mobile/catalog",
    "mobile/quote",
    "mobile/orders",
    "mobile/orders/1",
    "mobile/orders/1/submit",
)


class LegacySalesApiRouteContractTests(SimpleTestCase):
    def test_no_legacy_sales_route_is_registered(self):
        routes = list(_iter_registered_routes(get_resolver().url_patterns))
        self.assertFalse(
            [route for route in routes if route.lstrip("^").startswith("api/sales/")]
        )

    def test_sale_mini_route_remains_registered(self):
        match = resolve("/api/sale-mini/products/")
        self.assertIsNotNone(match.func)

    def test_system_check_blocks_accidental_legacy_prefix_registration(self):
        fake_resolver = SimpleNamespace(
            url_patterns=[path("api/sales/channels/", lambda request: None)]
        )
        with patch(
            "allapp.salesapp.checks.get_resolver",
            return_value=fake_resolver,
        ):
            errors = check_legacy_sales_api_not_registered(None)
        self.assertEqual([error.id for error in errors], ["salesapp.E008"])

    def test_system_check_does_not_block_sale_mini(self):
        fake_resolver = SimpleNamespace(
            url_patterns=[path("api/sale-mini/products/", lambda request: None)]
        )
        with patch(
            "allapp.salesapp.checks.get_resolver",
            return_value=fake_resolver,
        ):
            self.assertEqual(check_legacy_sales_api_not_registered(None), [])

    def test_retired_transport_modules_are_removed(self):
        app_dir = Path(settings.BASE_DIR) / "allapp" / "salesapp"
        for filename in ("urls.py", "views.py", "serializers.py", "mobile_api.py"):
            self.assertFalse((app_dir / filename).exists(), filename)


class LegacySalesApiHttpContractTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="LAPI", name="Legacy API")
        self.other_owner = Owner.objects.create(
            code="LAPI2",
            name="Legacy API Other",
        )
        self.warehouse = Warehouse.objects.create(
            code="LAPI-WH",
            name="Legacy API Warehouse",
        )
        self.customer_salesperson = User.objects.create_user(
            username="legacy-customer-salesperson",
            password="pw",
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.customer_salesperson,
            code="LEGACY-API-CUSTOMER",
            name="Legacy API Customer",
        )
        self.channel = Channel.objects.create(
            owner=self.owner,
            code="LEGACY-API-CHANNEL",
            name="Original channel",
        )
        self.order = SalesOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            order_date="2026-08-04",
            status=SalesOrder.Status.SUBMITTED,
        )

    def _scoped_user(self, username, role):
        kwargs = {}
        scope_kwargs = {"role": role}
        if role in UserRoleScope.OWNER_ROLES:
            kwargs["owner"] = self.owner
            scope_kwargs["owner"] = self.owner
        else:
            kwargs["warehouse"] = self.warehouse
            scope_kwargs["warehouse"] = self.warehouse
        user = User.objects.create_user(username=username, password="pw", **kwargs)
        UserRoleScope.objects.create(user=user, **scope_kwargs)
        return user

    def test_all_identity_classes_receive_404(self):
        users = [
            None,
            self._scoped_user("legacy-owner-manager", UserRoleScope.Role.OWNER_MANAGER),
            self._scoped_user(
                "legacy-owner-salesperson",
                UserRoleScope.Role.OWNER_SALESPERSON,
            ),
            self._scoped_user(
                "legacy-warehouse-operator",
                UserRoleScope.Role.WAREHOUSE_OPERATOR,
            ),
            self._scoped_user(
                "legacy-warehouse-manager",
                UserRoleScope.Role.WAREHOUSE_MANAGER,
            ),
            self._scoped_user(
                "legacy-warehouse-boss",
                UserRoleScope.Role.WAREHOUSE_BOSS,
            ),
            User.objects.create_user(
                username="legacy-owner-binding-only",
                password="pw",
                owner=self.owner,
            ),
            User.objects.create_superuser(
                username="legacy-superuser",
                email="legacy-superuser@example.com",
                password="pw",
            ),
        ]
        for user in users:
            with self.subTest(user=getattr(user, "username", "anonymous")):
                client = APIClient()
                if user is not None:
                    client.force_authenticate(user)
                response = client.get("/api/sales/price-lists/")
                self.assertEqual(response.status_code, 404)

    def test_every_retired_resource_family_and_api_root_return_404(self):
        client = APIClient()
        client.force_authenticate(
            self._scoped_user(
                "legacy-route-probe",
                UserRoleScope.Role.OWNER_MANAGER,
            )
        )
        for resource in RETIRED_RESOURCE_PATHS:
            with self.subTest(resource=resource):
                self.assertEqual(
                    client.get(f"/api/sales/{resource}/").status_code,
                    404,
                )
        self.assertEqual(client.get("/api/sales/").status_code, 404)

    def test_retired_mutations_and_review_actions_have_no_side_effects(self):
        client = APIClient()
        client.force_authenticate(
            self._scoped_user(
                "legacy-mutation-probe",
                UserRoleScope.Role.OWNER_SALESPERSON,
            )
        )
        requests = (
            (
                "post",
                "/api/sales/channels/",
                {
                    "owner": self.other_owner.id,
                    "code": "ATTACK",
                    "name": "Attack",
                    "is_deleted": True,
                    "deleted_by": 1,
                },
            ),
            (
                "put",
                f"/api/sales/channels/{self.channel.id}/",
                {"owner": self.other_owner.id, "code": "CHANGED", "name": "Changed"},
            ),
            (
                "patch",
                f"/api/sales/channels/{self.channel.id}/",
                {"is_active": False, "is_deleted": True},
            ),
            ("delete", f"/api/sales/channels/{self.channel.id}/", None),
            (
                "post",
                "/api/sales/price-lists/",
                {
                    "owner": self.other_owner.id,
                    "code": "ATTACK-PRICE",
                    "name": "Attack Price",
                    "effective_from": "2026-08-04",
                },
            ),
            (
                "post",
                f"/api/sales/sales-orders/{self.order.id}/approve/",
                {},
            ),
            (
                "post",
                f"/api/sales/sales-orders/{self.order.id}/reject/",
                {},
            ),
            (
                "post",
                "/api/sales/sales-orders/batch_approve/",
                {"ids": [self.order.id]},
            ),
            (
                "post",
                "/api/sales/mobile/orders/",
                {
                    "customer_id": self.customer.id,
                    "items": [{"product_id": 1, "qty": "1"}],
                },
            ),
        )
        channel_count = Channel.all_objects.count()
        order_count = SalesOrder.all_objects.count()
        price_list_count = PriceList.all_objects.count()
        for method, url, payload in requests:
            with self.subTest(method=method, url=url):
                response = getattr(client, method)(url, payload or {}, format="json")
                self.assertEqual(response.status_code, 404)

        self.channel.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(Channel.all_objects.count(), channel_count)
        self.assertEqual(SalesOrder.all_objects.count(), order_count)
        self.assertEqual(PriceList.all_objects.count(), price_list_count)
        self.assertEqual(self.channel.code, "LEGACY-API-CHANNEL")
        self.assertEqual(self.channel.name, "Original channel")
        self.assertTrue(self.channel.is_active)
        self.assertFalse(self.channel.is_deleted)
        self.assertEqual(self.order.status, SalesOrder.Status.SUBMITTED)

    def test_sale_mini_public_catalog_remains_available(self):
        response = APIClient().get("/api/sale-mini/products/")
        self.assertEqual(response.status_code, 200, response.data)
