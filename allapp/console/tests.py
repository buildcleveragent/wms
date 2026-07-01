from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Brand, Product, ProductCategory, ProductUom
from allapp.salesapp.models import SaleProductConfig


class SaleMiniProductListingConsoleTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="SLC", name="商城运营货主")
        self.category = ProductCategory.objects.create(code="SLC-CAT", name="饮品")
        self.brand = Brand.objects.create(code="SLC-BRAND", name="测试品牌")
        self.uom = ProductUom.objects.create(code="SLC-EA", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="SLC001",
            sku="SLC001",
            name="可上架商品",
            category=self.category,
            brand=self.brand,
            base_uom=self.uom,
            price=Decimal("12.50"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.no_price_product = Product.objects.create(
            owner=self.owner,
            code="SLC002",
            sku="SLC002",
            name="缺价格商品",
            category=self.category,
            brand=self.brand,
            base_uom=self.uom,
            price=None,
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.warehouse = Warehouse.objects.create(code="SLCWH", name="商城运营仓")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SLCSW",
            name="商城运营子仓",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SLCSW-01-01-01",
            name="商城运营库位",
        )
        self.inventory = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("7.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        self.user = get_user_model().objects.create_superuser(
            username="catalog-admin",
            email="catalog@example.com",
            password="pw",
        )
        self.client.force_login(self.user)
        self.url = reverse("console:sale_mini_product_listing")

    def test_listing_page_shows_unconfigured_products_and_filters(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "可上架商品")
        self.assertContains(response, "缺价格商品")
        self.assertContains(response, "未创建商城配置")

        stock_response = self.client.get(self.url, {"stock": "in"})
        self.assertContains(stock_response, "可上架商品")
        self.assertNotContains(stock_response, "缺价格商品")

        missing_price_response = self.client.get(self.url, {"price": "missing"})
        self.assertContains(missing_price_response, "缺价格商品")
        self.assertNotContains(missing_price_response, "可上架商品")

    def test_bulk_list_creates_config_and_public_product_without_changing_inventory(self):
        before_available = InventoryDetail.objects.get(pk=self.inventory.pk).available_qty

        response = self.client.post(
            self.url,
            {
                "bulk_action": "list",
                "product_ids": [str(self.product.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)
        self.assertTrue(config.is_listed)
        self.assertTrue(config.is_active)
        self.assertEqual(config.sale_price, Decimal("12.50"))
        self.assertEqual(
            InventoryDetail.objects.get(pk=self.inventory.pk).available_qty,
            before_available,
        )

        public_response = self.client.get("/api/sale-mini/products/")
        self.assertEqual(public_response.status_code, 200)
        rows = public_response.json()["results"]
        self.assertEqual({row["id"] for row in rows}, {self.product.id})

    def test_bulk_list_rejects_missing_price_and_keeps_product_unlisted(self):
        response = self.client.post(
            self.url,
            {
                "bulk_action": "list",
                "product_ids": [str(self.no_price_product.id)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未设置商品价格")
        config = SaleProductConfig.objects.get(
            owner=self.owner,
            product=self.no_price_product,
        )
        self.assertFalse(config.is_listed)

        public_response = self.client.get("/api/sale-mini/products/")
        self.assertEqual(public_response.json()["results"], [])

    def test_bulk_price_and_badge_updates_only_sale_product_config(self):
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=self.product,
            sale_price=Decimal("12.5000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )
        before_available = InventoryDetail.objects.get(pk=self.inventory.pk).available_qty

        self.client.post(
            self.url,
            {
                "bulk_action": "set_sale_price",
                "product_ids": [str(self.product.id)],
                "sale_price": "8.88",
            },
        )
        self.client.post(
            self.url,
            {
                "bulk_action": "set_badges",
                "product_ids": [str(self.product.id)],
                "is_recommended": "1",
                "is_hot": "1",
            },
        )

        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)
        self.assertEqual(config.sale_price, Decimal("8.88"))
        self.assertTrue(config.is_recommended)
        self.assertTrue(config.is_hot)
        self.assertFalse(config.is_new)
        self.assertEqual(
            InventoryDetail.objects.get(pk=self.inventory.pk).available_qty,
            before_available,
        )
