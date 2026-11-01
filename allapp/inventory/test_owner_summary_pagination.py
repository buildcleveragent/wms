from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventorySummary
from allapp.inventory.serializers import OwnerInventorySummarySerializer
from allapp.inventory.views import OwnerInventorySummaryViewSet
from allapp.products.models import Product, ProductUom


class OwnerInventorySummaryPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(name="Inventory Page Owner", code="INVPG")
        cls.other_owner = Owner.objects.create(name="Other Inventory Owner", code="INVOT")
        cls.uom = ProductUom.objects.create(code="IPCS", name="件")
        cls.user = get_user_model().objects.create_user(
            username="inventory-page-owner",
            password="test-password",
            owner=cls.owner,
        )
        UserRoleScope.objects.create(
            user=cls.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=cls.owner,
        )

        cls.user.user_permissions.add(Permission.objects.get(codename="access_owner_sales"))
        for index in range(55):
            Product.objects.create(
                owner=cls.owner,
                code=f"PAG{index:03d}",
                name=f"Inventory Product {index:03d}",
                base_uom=cls.uom,
            )
        products = list(Product.objects.filter(owner=cls.owner).order_by("code"))
        InventorySummary.objects.bulk_create(
            [
                InventorySummary(
                    owner=cls.owner,
                    product=product,
                    base_unit=cls.uom.code,
                )
                for product in products
            ]
        )
        cls.summary_ids = set(
            InventorySummary.objects.filter(owner=cls.owner).values_list("id", flat=True)
        )

        foreign_product = Product.objects.create(
            owner=cls.other_owner,
            code="FOREIGN001",
            sku="FOREIGN001",
            name="Foreign Inventory Product",
            base_uom=cls.uom,
        )
        InventorySummary.objects.create(
            owner=cls.other_owner,
            product=foreign_product,
            base_unit=cls.uom.code,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_two_pages_return_all_owner_rows_without_duplicates(self):
        first = self.client.get("/api/inventory/summary/", {"page": 1, "page_size": 50})
        second = self.client.get("/api/inventory/summary/", {"page": 2, "page_size": 50})

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["count"], 55)
        self.assertEqual(second.data["count"], 55)
        self.assertEqual(len(first.data["results"]), 50)
        self.assertEqual(len(second.data["results"]), 5)
        self.assertIsNotNone(first.data["next"])
        self.assertIsNone(second.data["next"])

        first_ids = {row["id"] for row in first.data["results"]}
        second_ids = {row["id"] for row in second.data["results"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(first_ids | second_ids, self.summary_ids)

    def test_search_keeps_owner_scope_and_accurate_count(self):
        response = self.client.get(
            "/api/inventory/summary/",
            {"search": "PAG054", "page": 1, "page_size": 50},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["product_code"], "PAG054")

    def test_summary_returns_unit_name_without_changing_code(self):
        response = self.client.get("/api/inventory/summary/", {"page_size": 50})

        self.assertEqual(response.status_code, 200)
        for row in response.data["results"]:
            self.assertEqual(row["base_unit"], "IPCS")
            self.assertEqual(row["base_unit_name"], "件")

    def test_unit_name_matches_inventory_snapshot_not_current_product_unit(self):
        ProductUom.objects.create(code="OLD-BOX", name="箱")
        summary_id = min(self.summary_ids)
        InventorySummary.objects.filter(pk=summary_id).update(base_unit="OLD-BOX")

        response = self.client.get("/api/inventory/summary/")

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.data["results"] if row["id"] == summary_id)
        self.assertEqual(row["base_unit"], "OLD-BOX")
        self.assertEqual(row["base_unit_name"], "箱")

    def test_unknown_snapshot_unit_retains_code_and_has_no_guessed_name(self):
        summary_id = min(self.summary_ids)
        InventorySummary.objects.filter(pk=summary_id).update(base_unit="UNKNOWN")

        response = self.client.get("/api/inventory/summary/")

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.data["results"] if row["id"] == summary_id)
        self.assertEqual(row["base_unit"], "UNKNOWN")
        self.assertIsNone(row["base_unit_name"])

    def test_serializing_a_page_does_not_query_units_per_row(self):
        view = OwnerInventorySummaryViewSet()
        view.request = SimpleNamespace(user=self.user, query_params={})
        queryset = view.get_queryset()[:50]

        with self.assertNumQueries(1):
            rows = OwnerInventorySummarySerializer(queryset, many=True).data

        self.assertEqual(len(rows), 50)
        self.assertTrue(all(row["base_unit_name"] == "件" for row in rows))
