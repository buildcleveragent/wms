from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.views import (
    ProductViewSet,
    ReceiveProductViewSet,
    _product_carton_info,
)
from allapp.products.models import Product, ProductPackage, ProductUom


class ProductCartonInfoUnitTests(SimpleTestCase):
    class Packages:
        def __init__(self, packages):
            self.packages = packages

        def all(self):
            return self.packages

    def _product(self, *, replenish_uom_id=None, code=None, packages=()):
        return SimpleNamespace(
            replenish_uom_id=replenish_uom_id,
            replenish_uom=SimpleNamespace(code=code) if code else None,
            packages=self.Packages(packages),
        )

    def test_uses_qty_in_base_from_the_matching_prefetched_package(self):
        product = self._product(
            replenish_uom_id=2,
            code="BOX",
            packages=(
                SimpleNamespace(uom_id=1, qty_in_base=6),
                SimpleNamespace(uom_id=2, qty_in_base=24),
            ),
        )

        self.assertEqual(_product_carton_info(product), ("BOX", 24))

    def test_missing_package_keeps_unit_code_and_returns_null_conversion(self):
        product = self._product(replenish_uom_id=2, code="BOX")

        self.assertEqual(_product_carton_info(product), ("BOX", None))

    def test_product_package_query_compiles_with_the_real_conversion_field(self):
        query = ProductPackage.objects.only("id", "uom_id", "qty_in_base").query

        self.assertIn("qty_in_base", str(query))


class ProductCatalogPackageConversionTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Catalog Package Owner", code="CATPKG")
        self.warehouse = Warehouse.objects.create(
            code="CATPKGWH",
            name="Catalog Package Warehouse",
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="CATPKG",
            name="Catalog Package Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="CATPKG-01-01-01",
            name="Catalog Package Location",
        )
        self.base_uom = ProductUom.objects.create(code="CATPC", name="件")
        self.carton_uom = ProductUom.objects.create(code="CATBOX", name="箱")
        self.user = get_user_model().objects.create_user(
            username="catalog-package-salesperson",
            password="x",
            owner=self.owner,
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.factory = APIRequestFactory()

        self.plain_product = self._product("CAT-PLAIN", "Plain Product")
        self.plain_product.min_price = Decimal("8.1234")
        self.plain_product.max_discount = Decimal("10.00")
        self.plain_product.save(update_fields=["min_price", "max_discount", "updated_at"])
        self.carton_product = self._product(
            "CAT-CARTON",
            "Carton Product",
            replenish_uom=self.carton_uom,
        )
        self.other_carton_product = self._product(
            "CAT-CARTON-OTHER",
            "Other Carton Product",
            replenish_uom=self.carton_uom,
        )
        self.missing_package_product = self._product(
            "CAT-MISSING",
            "Missing Package Product",
            replenish_uom=self.carton_uom,
        )
        ProductPackage.objects.create(
            product=self.carton_product,
            uom=self.carton_uom,
            qty_in_base=12,
        )
        ProductPackage.objects.create(
            product=self.other_carton_product,
            uom=self.carton_uom,
            qty_in_base=24,
        )
        for product in (
            self.plain_product,
            self.carton_product,
            self.other_carton_product,
            self.missing_package_product,
        ):
            InventoryDetail.objects.create(
                owner=self.owner,
                product=product,
                warehouse=self.warehouse,
                subwarehouse=self.subwarehouse,
                location=self.location,
                base_unit=self.base_uom.code,
                onhand_qty=Decimal("10.0000"),
            )

    def _product(self, code, name, *, replenish_uom=None):
        return Product.objects.create(
            owner=self.owner,
            code=code,
            name=name,
            base_uom=self.base_uom,
            replenish_uom=replenish_uom,
            price=Decimal("10.00"),
        )

    def _list(self, viewset, path, params=None):
        request = self.factory.get(path, params or {})
        force_authenticate(request, user=self.user)
        return viewset.as_view({"get": "list"})(request)

    def _assert_package_conversions(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        products = {row["id"]: row for row in response.data["results"]}
        self.assertEqual(
            set(products),
            {
                self.plain_product.id,
                self.carton_product.id,
                self.other_carton_product.id,
                self.missing_package_product.id,
            },
        )
        self.assertIsNone(products[self.plain_product.id]["carton_unit"])
        self.assertIsNone(products[self.plain_product.id]["carton_conv"])
        self.assertEqual(
            products[self.plain_product.id]["minimum_sale_price"], "9.0000"
        )
        self.assertEqual(products[self.carton_product.id]["carton_unit"], "CATBOX")
        self.assertEqual(products[self.carton_product.id]["carton_conv"], 12)
        self.assertEqual(
            products[self.other_carton_product.id]["carton_conv"],
            24,
        )
        self.assertEqual(
            products[self.missing_package_product.id]["carton_unit"],
            "CATBOX",
        )
        self.assertIsNone(
            products[self.missing_package_product.id]["carton_conv"]
        )

    def test_product_catalog_uses_qty_in_base_without_breaking_mixed_page(self):
        response = self._list(
            ProductViewSet,
            "/api/catalog/products/",
            {"warehouse_id": self.warehouse.id},
        )

        self._assert_package_conversions(response)

    def test_receive_product_catalog_uses_the_same_safe_conversion(self):
        response = self._list(
            ReceiveProductViewSet,
            "/api/catalog/receive_products/",
            {"owner": self.owner.id},
        )

        self._assert_package_conversions(response)

    def test_zero_stock_products_are_filtered_before_count_and_page_slice(self):
        self._product("PAGE-ZERO", "Page Zero")
        stocked = self._product("PAGE-STOCK", "Page Stock")
        InventoryDetail.objects.create(
            owner=self.owner,
            product=stocked,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=self.location,
            base_unit=self.base_uom.code,
            onhand_qty=Decimal("5.0000"),
        )

        response = self._list(
            ProductViewSet,
            "/api/catalog/products/",
            {
                "warehouse_id": self.warehouse.id,
                "search": "PAGE-",
                "page_size": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual([row["id"] for row in response.data["results"]], [stocked.id])
        self.assertIsNone(response.data["next"])
