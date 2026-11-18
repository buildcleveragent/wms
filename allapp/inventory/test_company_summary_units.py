"""Company inventory preserves snapshot units and scoped aggregate quantities."""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom

from .models import InventoryDetail
from .views import CompanyInventorySummaryViewSet


class CompanySummaryUnitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unit = ProductUom.objects.create(code="CS-KG", name="千克")
        cls.old_unit = ProductUom.objects.create(code="CS-BOX", name="箱", is_active=False)
        cls.owner = Owner.objects.create(code="CS-O", name="库存查询货主")
        cls.other_owner = Owner.objects.create(code="CS-OTHER", name="其他货主")
        cls.product = Product.objects.create(
            owner=cls.owner, code="CS-P", name="测试商品", base_uom=cls.unit
        )
        cls.other_product = Product.objects.create(
            owner=cls.other_owner, code="CS-OTHER", name="其他商品", base_uom=cls.unit
        )
        cls.locations = []
        for index in range(2):
            warehouse = Warehouse.objects.create(code=f"CS-W{index}", name=f"仓库{index}")
            sub = Subwarehouse.objects.create(warehouse=warehouse, code=f"CSS{index}", name="子仓")
            cls.locations.append(
                Location.objects.create(
                    warehouse=warehouse, subwarehouse=sub, code=f"CSS{index}-01-01-01", name="库位"
                )
            )
            OwnerWarehouseBinding.objects.create(owner=cls.owner, warehouse=warehouse)
        cls.stocks = []
        for location, product, qty in [
            (cls.locations[0], cls.product, "2.5"),
            (cls.locations[1], cls.product, "3.75"),
            (cls.locations[0], cls.other_product, "6"),
        ]:
            cls.stocks.append(
                InventoryDetail.objects.create(
                    owner=product.owner,
                    product=product,
                    warehouse=location.warehouse,
                    subwarehouse=location.subwarehouse,
                    location=location,
                    onhand_qty=Decimal(qty),
                    available_qty=Decimal(qty),
                )
            )
        cls.admin = get_user_model().objects.create_superuser(username="cs-admin", password="test")
        cls.user = get_user_model().objects.create_user(username="cs-operator")
        UserRoleScope.objects.create(
            user=cls.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=cls.locations[0].warehouse,
        )
        cls.user.user_permissions.add(
            Permission.objects.get(codename="access_warehouse_operations")
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def response(self, mode="warehouse", **params):
        response = self.client.get("/api/inventory/company-summary/", {"mode": mode, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_names_and_aggregates_in_both_modes(self):
        warehouse = self.response(owner_id=self.owner.pk)
        self.assertEqual(warehouse["count"], 2)
        for row in warehouse["results"]:
            self.assertEqual(row["base_unit"], self.unit.code)
            self.assertEqual(row["base_unit_name"], "千克")
        all_rows = self.response("all", owner_id=self.owner.pk)["results"]
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(Decimal(all_rows[0]["onhand_qty"]), Decimal("6.25"))
        self.assertEqual(Decimal(all_rows[0]["available_qty"]), Decimal("6.25"))
        self.assertEqual(all_rows[0]["base_unit_name"], "千克")

    def test_historical_and_unknown_units_do_not_merge_or_use_current_product_unit(self):
        InventoryDetail.objects.filter(pk=self.stocks[0].pk).update(base_unit=self.old_unit.code)
        InventoryDetail.objects.filter(pk=self.stocks[1].pk).update(base_unit="CS-UNKNOWN")
        for mode in ("warehouse", "all"):
            with self.subTest(mode=mode):
                rows = self.response(mode, owner_id=self.owner.pk)["results"]
                self.assertEqual(len(rows), 2)
                by_unit = {row["base_unit"]: row for row in rows}
                self.assertEqual(by_unit[self.old_unit.code]["base_unit_name"], "箱")
                self.assertIsNone(by_unit["CS-UNKNOWN"]["base_unit_name"])
                self.assertEqual(Decimal(by_unit[self.old_unit.code]["onhand_qty"]), Decimal("2.5"))

    def test_owner_warehouse_search_and_pagination(self):
        data = self.response(
            owner_id=self.owner.pk,
            warehouse_id=self.locations[1].warehouse_id,
            search="测试商品",
            page_size=10,
        )
        self.assertEqual(data["count"], 1)
        self.assertEqual(Decimal(data["results"][0]["onhand_qty"]), Decimal("3.75"))
        self.assertEqual(self.response(owner_id=self.other_owner.pk)["count"], 1)
        self.assertEqual(self.response(search="不存在")["count"], 0)
        self.assertEqual(len(self.response(page_size=1)["results"]), 1)

    def test_warehouse_scope_and_denied_warehouse(self):
        self.client.force_authenticate(self.user)
        for mode in ("warehouse", "all"):
            rows = self.response(mode, owner_id=self.owner.pk)["results"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(Decimal(rows[0]["onhand_qty"]), Decimal("2.5"))
        denied = self.client.get(
            "/api/inventory/company-summary/",
            {
                "warehouse_id": self.locations[1].warehouse_id,
            },
        )
        self.assertEqual(denied.status_code, 403)
        unscoped = get_user_model().objects.create_user(username="cs-no-scope")
        self.client.force_authenticate(unscoped)
        self.assertEqual(self.client.get("/api/inventory/company-summary/").status_code, 403)

    def test_unit_resolution_is_one_aggregate_query(self):
        view = CompanyInventorySummaryViewSet()
        view.request = SimpleNamespace(user=self.admin, query_params={"mode": "all"})
        queryset = view.get_queryset()
        with self.assertNumQueries(1):
            rows = list(queryset)
            self.assertTrue(all(row["base_unit_name"] == "千克" for row in rows))

    def test_warehouse_options_include_empty_warehouses_and_respect_scope(self):
        empty = Warehouse.objects.create(code="CS-EMPTY", name="暂无库存仓库")
        url = "/api/inventory/company-summary/warehouse-options/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn({"id": empty.pk, "code": empty.code, "name": empty.name}, response.data)
        self.assertTrue(
            {location.warehouse_id for location in self.locations}.issubset(
                {row["id"] for row in response.data}
            )
        )
        self.client.force_authenticate(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            [
                {
                    "id": self.locations[0].warehouse_id,
                    "code": self.locations[0].warehouse.code,
                    "name": self.locations[0].warehouse.name,
                }
            ],
        )

    def test_warehouse_options_require_inventory_permission(self):
        url = "/api/inventory/company-summary/warehouse-options/"
        denied = get_user_model().objects.create_user(username="cs-options-denied")
        UserRoleScope.objects.create(
            user=denied,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.locations[0].warehouse,
        )
        self.client.force_authenticate(denied)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(url).status_code, (401, 403))
