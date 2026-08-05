from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventorySummary
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

        Product.objects.bulk_create(
            [
                Product(
                    owner=cls.owner,
                    code=f"PAG{index:03d}",
                    sku=f"PAG{index:03d}",
                    name=f"Inventory Product {index:03d}",
                    base_uom=cls.uom,
                )
                for index in range(55)
            ]
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
