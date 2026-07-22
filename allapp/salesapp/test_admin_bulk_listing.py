from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductCategory, ProductUom
from allapp.salesapp.models import SaleProductConfig


class SaleProductConfigAdminBulkOwnerTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="ABULK", name="批量上架货主")
        self.category = ProductCategory.objects.create(code="ABULK-CAT", name="可售分类")
        self.uom = ProductUom.objects.create(code="ABULK-EA", name="件")
        self.product_a = Product.objects.create(
            owner=self.owner,
            code="ABULK-001",
            sku="ABULK-001",
            name="批量商品 A",
            category=self.category,
            base_uom=self.uom,
            price=Decimal("10.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.product_b = Product.objects.create(
            owner=self.owner,
            code="ABULK-002",
            sku="ABULK-002",
            name="批量商品 B",
            category=self.category,
            base_uom=self.uom,
            price=Decimal("20.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.no_price_product = Product.objects.create(
            owner=self.owner,
            code="ABULK-003",
            sku="ABULK-003",
            name="缺价格商品",
            category=self.category,
            base_uom=self.uom,
            price=None,
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.no_category_product = Product.objects.create(
            owner=self.owner,
            code="ABULK-004",
            sku="ABULK-004",
            name="缺分类商品",
            base_uom=self.uom,
            price=Decimal("30.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.warehouse = Warehouse.objects.create(code="ABULKWH", name="批量上架仓")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="ABULKSW",
            name="批量上架子仓",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="ABULKSW-01-01-01",
            name="批量上架库位",
        )
        self.inventory = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product_a,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("9.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        self.user = get_user_model().objects.create_superuser(
            username="sale-config-admin",
            email="sale-config-admin@example.com",
            password="pw",
        )
        self.client.force_login(self.user)
        self.changelist_url = reverse("admin:salesapp_saleproductconfig_changelist")
        self.bulk_url = reverse("admin:salesapp_saleproductconfig_bulk_owner_list")

    def bulk_payload(self, operation="list", **overrides):
        payload = {
            "owner": str(self.owner.id),
            "operation": operation,
            "only_active_products": "on",
            "skip_missing_price": "on",
            "sync_sale_price": "on",
            "stock_display": SaleProductConfig.StockDisplay.STATUS,
            "min_order_qty": "1.000",
            "multiple_qty": "1.000",
            "max_order_qty": "",
        }
        payload.update(overrides)
        return payload

    def test_changelist_links_owner_bulk_listing_page(self):
        response = self.client.get(self.changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "按货主批量上架")
        self.assertContains(response, self.bulk_url)

    def test_owner_bulk_listing_creates_and_lists_valid_products_only(self):
        before_available = InventoryDetail.objects.get(pk=self.inventory.pk).available_qty

        response = self.client.post(self.bulk_url, self.bulk_payload(), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "批量处理完成")
        self.assertContains(response, "创建 2 个")
        self.assertContains(response, "上架 2 个")
        self.assertContains(response, "跳过 2 个")
        configs = {
            config.product_id: config
            for config in SaleProductConfig.objects.filter(owner=self.owner)
        }
        self.assertEqual(set(configs), {self.product_a.id, self.product_b.id})
        self.assertTrue(configs[self.product_a.id].is_listed)
        self.assertTrue(configs[self.product_b.id].is_listed)
        self.assertEqual(configs[self.product_a.id].sale_price, Decimal("10.00"))
        self.assertEqual(configs[self.product_b.id].sale_price, Decimal("20.00"))
        self.assertEqual(
            InventoryDetail.objects.get(pk=self.inventory.pk).available_qty,
            before_available,
        )

        public_response = self.client.get("/api/sale-mini/products/")
        rows = public_response.json()["results"]
        self.assertEqual({row["id"] for row in rows}, {self.product_a.id, self.product_b.id})

    def test_owner_bulk_listing_preserves_existing_single_product_price_by_default(self):
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=self.product_a,
            sale_price=Decimal("6.6600"),
            is_listed=False,
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )

        response = self.client.post(self.bulk_url, self.bulk_payload(), follow=True)

        self.assertEqual(response.status_code, 200)
        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product_a)
        self.assertTrue(config.is_listed)
        self.assertEqual(config.sale_price, Decimal("6.6600"))

    def test_owner_bulk_unlist_keeps_configs_and_hides_public_catalog(self):
        for product in (self.product_a, self.product_b):
            SaleProductConfig.objects.create(
                owner=self.owner,
                product=product,
                sale_price=product.price,
                is_listed=True,
                min_order_qty=Decimal("1.000"),
                multiple_qty=Decimal("1.000"),
            )

        response = self.client.post(
            self.bulk_url,
            self.bulk_payload(operation="unlist"),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            SaleProductConfig.objects.filter(owner=self.owner, is_listed=True).exists()
        )
        self.assertEqual(SaleProductConfig.objects.filter(owner=self.owner).count(), 2)
        public_response = self.client.get("/api/sale-mini/products/")
        self.assertEqual(public_response.json()["results"], [])
