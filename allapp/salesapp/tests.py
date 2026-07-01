from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.baseinfo.models import Customer, Owner
from allapp.inventory.models import InventoryDetail, InventorySummary
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import (
    Brand,
    Product,
    ProductCategory,
    ProductPackage,
    ProductUom,
)
from allapp.tasking.models import WmsTask

from .models import (
    Channel,
    CustomerChannel,
    MiniCustomerAddress,
    MiniProgramUser,
    PriceItem,
    PriceList,
    Promotion,
    PromotionDiscountStep,
    PromotionSpecialPrice,
    SaleMiniAfterSaleRequest,
    SaleMiniBanner,
    SaleMiniCart,
    SaleMiniCartItem,
    SaleMiniCoupon,
    SaleMiniCouponTemplate,
    SaleMiniDistributionRecord,
    SaleMiniOrderAdjustment,
    SaleMiniOrderMapping,
    SaleMiniPayment,
    SaleMiniPaymentEvent,
    SaleMiniPointLedger,
    SaleMiniRefund,
    SaleProductConfig,
    SalesOrder,
    SalesOrderLine,
)
from .services_salemini_adjustments import (
    confirm_adjustments,
    confirm_distribution,
    point_balance,
)

User = get_user_model()


class SalesMobileApiTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="SMA", name="Sales Mobile A")
        self.other_owner = Owner.objects.create(code="SMB", name="Sales Mobile B")
        self.user = User.objects.create_user(
            username="seller", password="pw", owner=self.owner
        )
        self.other_user = User.objects.create_user(
            username="other", password="pw", owner=self.other_owner
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="C001",
            name="第一终端",
        )
        self.uom = ProductUom.objects.create(code="EA", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="P001",
            sku="P001",
            name="测试商品",
            base_uom=self.uom,
            price=Decimal("12.50"),
            expiry_control=False,
            is_active=True,
        )
        self.other_product = Product.objects.create(
            owner=self.other_owner,
            code="P999",
            sku="P999",
            name="其他货主商品",
            base_uom=self.uom,
            price=Decimal("99.00"),
            expiry_control=False,
            is_active=True,
        )
        InventorySummary.objects.create(
            owner=self.owner,
            product=self.product,
            base_unit="EA",
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            available_qty=Decimal("10.0000"),
            is_active=True,
        )
        InventorySummary.objects.create(
            owner=self.other_owner,
            product=self.other_product,
            base_unit="EA",
            onhand_qty=Decimal("8.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            available_qty=Decimal("8.0000"),
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_catalog_is_owner_scoped_and_returns_server_price_and_stock(self):
        response = self.client.get(
            "/api/sales/mobile/catalog/",
            {"customer_id": self.customer.id, "search": "P"},
        )

        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.product.id)
        self.assertEqual(rows[0]["unit_price"], "12.5000")
        self.assertEqual(rows[0]["available_qty"], "10.000")

    def test_catalog_prices_package_uom_from_base_unit_price(self):
        carton = ProductUom.objects.create(code="CTN", name="箱")
        ProductPackage.objects.create(
            product=self.product,
            uom=carton,
            qty_in_base=12,
            is_sales_default=True,
        )

        response = self.client.get(
            "/api/sales/mobile/catalog/",
            {"customer_id": self.customer.id, "search": "P001"},
        )

        self.assertEqual(response.status_code, 200)
        product = response.data["results"][0]
        self.assertEqual(product["order_uom"], "CTN")
        self.assertEqual(product["qty_in_base"], "12.000")
        self.assertEqual(product["unit_price"], "150.0000")

    def test_create_order_reprices_package_uom_from_base_unit_price(self):
        carton = ProductUom.objects.create(code="CTN", name="箱")
        ProductPackage.objects.create(
            product=self.product,
            uom=carton,
            qty_in_base=12,
            is_sales_default=True,
        )

        response = self.client.post(
            "/api/sales/mobile/orders/",
            {
                "customer_id": self.customer.id,
                "submit": True,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "order_uom": "CTN",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("stock", response.data)

        InventorySummary.objects.filter(owner=self.owner, product=self.product).update(
            onhand_qty=Decimal("20.0000"),
            available_qty=Decimal("20.0000"),
        )
        response = self.client.post(
            "/api/sales/mobile/orders/",
            {
                "customer_id": self.customer.id,
                "submit": True,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "order_uom": "CTN",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["total_amount"], "150.00")
        self.assertEqual(response.data["lines"][0]["unit_price"], "150.0000")

    def test_catalog_ignores_channel_price_when_customer_has_no_channel(self):
        channel = Channel.objects.create(owner=self.owner, code="KA", name="KA")
        price_list = PriceList.objects.create(
            owner=self.owner,
            code="KA-ONLY",
            name="KA 专属价",
            channel=channel,
            effective_from=date(2020, 1, 1),
            is_default=False,
        )
        PriceItem.objects.create(
            owner=self.owner,
            price_list=price_list,
            product=self.product,
            price=Decimal("5.0000"),
        )

        response = self.client.get(
            "/api/sales/mobile/catalog/",
            {"customer_id": self.customer.id, "search": "P001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["unit_price"], "12.5000")

    def test_catalog_uses_channel_pricelist_before_default_pricelist(self):
        channel = Channel.objects.create(owner=self.owner, code="RT", name="零售")
        CustomerChannel.objects.create(
            owner=self.owner, customer=self.customer, channel=channel
        )
        default_list = PriceList.objects.create(
            owner=self.owner,
            code="DEFAULT",
            name="通用默认价",
            effective_from=date(2020, 1, 1),
            is_default=True,
        )
        channel_list = PriceList.objects.create(
            owner=self.owner,
            code="RT",
            name="零售渠道价",
            channel=channel,
            effective_from=date(2020, 1, 1),
            is_default=False,
        )
        PriceItem.objects.create(
            owner=self.owner,
            price_list=default_list,
            product=self.product,
            price=Decimal("30.0000"),
        )
        PriceItem.objects.create(
            owner=self.owner,
            price_list=channel_list,
            product=self.product,
            price=Decimal("20.0000"),
        )

        response = self.client.get(
            "/api/sales/mobile/catalog/",
            {"customer_id": self.customer.id, "search": "P001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["unit_price"], "20.0000")

    def test_catalog_ignores_promotion_special_price_for_other_customer(self):
        other_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="C002",
            name="第二终端",
        )
        promotion = Promotion.objects.create(
            owner=self.owner,
            code="C002-SP",
            name="第二终端特价",
            promo_type=Promotion.PromoType.SPECIAL_PRICE,
            customer=other_customer,
            effective_from=date(2020, 1, 1),
        )
        PromotionSpecialPrice.objects.create(
            owner=self.owner,
            promotion=promotion,
            product=self.product,
            special_price=Decimal("1.0000"),
        )

        response = self.client.get(
            "/api/sales/mobile/catalog/",
            {"customer_id": self.customer.id, "search": "P001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["unit_price"], "12.5000")

    def test_create_order_calculates_amount_from_server_price(self):
        response = self.client.post(
            "/api/sales/mobile/orders/",
            {
                "customer_id": self.customer.id,
                "submit": True,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "order_uom": "EA",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], SalesOrder.Status.SUBMITTED)
        self.assertEqual(response.data["total_amount"], "25.00")

        order = SalesOrder.objects.get(id=response.data["id"])
        line = SalesOrderLine.objects.get(order=order)
        self.assertEqual(order.owner, self.owner)
        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.total_amount, Decimal("25.00"))
        self.assertEqual(line.unit_price, Decimal("12.5000"))
        self.assertEqual(line.line_amount, Decimal("25.00"))

    def test_create_order_rejects_insufficient_stock(self):
        response = self.client.post(
            "/api/sales/mobile/orders/",
            {
                "customer_id": self.customer.id,
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "11.000",
                        "order_uom": "EA",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("stock", response.data)
        self.assertFalse(SalesOrder.objects.exists())


class SaleMiniApiTests(TestCase):
    def assertPublicProductPayloadHidesInternalFields(self, payload):
        for field in ("owner_id", "owner", "owner_name"):
            self.assertNotIn(field, payload)
        for field in ("code", "sku", "barcodes", "base_unit_price", "qty_in_base"):
            self.assertNotIn(field, payload)

    def assertPublicTaxonomyPayloadHidesInternalFields(self, payload):
        for field in ("code", "owner_id", "owner", "owner_name"):
            self.assertNotIn(field, payload)

    def setUp(self):
        self.owner = Owner.objects.create(code="SMINI", name="Sale Mini Owner")
        self.warehouse = Warehouse.objects.create(
            code="WHSMI", name="Sale Mini Warehouse"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWSMI",
            name="Sale Mini Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWSMI-01-01-01",
            name="Sale Mini Location",
        )
        self.user = User.objects.create_user(
            username="mini-buyer",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="MC001",
            name="小程序客户",
        )
        self.buyer = MiniProgramUser.objects.create(
            owner=self.owner,
            user=self.user,
            customer=self.customer,
            nickname="采购员",
        )
        self.uom = ProductUom.objects.create(code="EA-MINI", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="MP001",
            sku="MP001",
            name="小程序上架商品",
            base_uom=self.uom,
            price=Decimal("12.50"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.hidden_product = Product.objects.create(
            owner=self.owner,
            code="MP999",
            sku="MP999",
            name="未上架商品",
            base_uom=self.uom,
            price=Decimal("99.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=self.product,
            is_listed=True,
            sale_price=Decimal("9.5000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
            stock_display=SaleProductConfig.StockDisplay.EXACT,
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_sale_mini_order(self, *, payment_method="OFFLINE", extra=None):
        payload = {
            "payment_method": payment_method,
            "contact": "张三",
            "contact_phone": "13800000000",
            "ship_to": "上海市测试路 1 号",
            "delivery_method": "OWN_TRUCK",
            "lines": [
                {
                    "product_id": self.product.id,
                    "qty": "2.000",
                    "order_uom": "EA-MINI",
                }
            ],
        }
        if extra:
            payload.update(extra)
        response = self.client.post(
            "/api/sale-mini/orders/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        return response

    def _create_discount_step(self, *, threshold="10.00", discount="3.00"):
        promo = Promotion.objects.create(
            owner=self.owner,
            code=f"MINI-FULL-{discount}",
            name="小程序满减",
            promo_type=Promotion.PromoType.DISCOUNT_STEP,
            effective_from=date(2020, 1, 1),
        )
        return PromotionDiscountStep.objects.create(
            owner=self.owner,
            promotion=promo,
            threshold_amount=Decimal(threshold),
            discount_amount=Decimal(discount),
        )

    def _create_coupon(self, *, threshold="10.00", discount="4.00"):
        template = SaleMiniCouponTemplate.objects.create(
            owner=self.owner,
            code=f"MINI-COUPON-{discount}",
            title="小程序优惠券",
            threshold_amount=Decimal(threshold),
            discount_amount=Decimal(discount),
            effective_from=date(2020, 1, 1),
        )
        return SaleMiniCoupon.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            template=template,
            coupon_no=f"COUPON-{discount}",
        )

    def _earn_points(self, points=500):
        return SaleMiniPointLedger.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            tx_no=f"POINT-EARN-{points}",
            tx_type=SaleMiniPointLedger.TxType.EARN,
            points_delta=points,
            note="测试积分",
        )

    def _create_referrer(self):
        ref_user = User.objects.create_user(
            username="mini-referrer",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        return MiniProgramUser.objects.create(
            owner=self.owner,
            user=ref_user,
            customer=self.customer,
            nickname="推荐人",
        )

    def _create_other_owner_sale_binding(self):
        other_owner = Owner.objects.create(code="SMINI-X", name="Sale Mini Other")
        other_customer = Customer.objects.create(
            owner=other_owner,
            salesperson=self.user,
            code="MC-X",
            name="跨商家客户",
        )
        other_buyer = MiniProgramUser.objects.create(
            owner=other_owner,
            user=self.user,
            customer=other_customer,
            nickname="跨商家采购员",
        )
        other_product = Product.objects.create(
            owner=other_owner,
            code="MP-X",
            sku="MP-X",
            name="跨商家商品",
            base_uom=self.uom,
            price=Decimal("6.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=other_owner,
            product=other_product,
            is_listed=True,
            sale_price=Decimal("5.0000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )
        InventoryDetail.objects.create(
            owner=other_owner,
            product=other_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("6.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        return other_owner, other_customer, other_buyer, other_product

    def test_products_only_return_listed_goods_with_server_stock_and_price(self):
        response = self.client.get(
            "/api/sale-mini/products/",
            {"search": "小程序上架"},
        )
        code_response = self.client.get(
            "/api/sale-mini/products/",
            {"search": "MP001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(code_response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.product.id)
        self.assertPublicProductPayloadHidesInternalFields(rows[0])
        self.assertEqual(rows[0]["price"], "9.5000")
        self.assertEqual(rows[0]["stock"]["available_qty"], "10.000")
        self.assertEqual(code_response.data["results"], [])

    def test_saleable_stock_requires_complete_tracking_fields(self):
        tracked_product = Product.objects.create(
            owner=self.owner,
            code="MP-TRACK",
            sku="MP-TRACK",
            name="批次效期商品",
            base_uom=self.uom,
            price=Decimal("18.00"),
            batch_control=True,
            expiry_control=True,
            expiry_basis=Product.ExpiryBasis.MFG,
            shelf_life_days=365,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=tracked_product,
            is_listed=True,
            sale_price=Decimal("18.0000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
            stock_display=SaleProductConfig.StockDisplay.EXACT,
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        public_client = APIClient()

        response = public_client.get(
            "/api/sale-mini/products/",
            {"search": "批次效期"},
        )
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock"]["available_qty"], "0.000")
        self.assertEqual(rows[0]["stock"]["status"], "OUT")

        stocked_response = public_client.get(
            "/api/sale-mini/products/",
            {"search": "批次效期", "only_stock": "1"},
        )
        self.assertEqual(stocked_response.status_code, 200)
        self.assertEqual(stocked_response.data["results"], [])

        preview = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": tracked_product.id,
                        "qty": "1.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data["ok"])
        self.assertEqual(preview.data["lines"][0]["available_qty"], "0.000")
        self.assertIn("库存不足", preview.data["lines"][0]["message"])

        detail.batch_no = "LOT-202606"
        detail.production_date = date(2026, 6, 1)
        detail.expiry_date = date(2027, 6, 1)
        detail.save(update_fields=["batch_no", "production_date", "expiry_date"])

        response = public_client.get(
            "/api/sale-mini/products/",
            {"search": "批次效期"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["stock"]["available_qty"], "5.000")

        preview = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": tracked_product.id,
                        "qty": "1.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["ok"])
        self.assertEqual(preview.data["lines"][0]["available_qty"], "5.000")

    def test_public_products_return_listed_goods_from_all_owners(self):
        other_owner = Owner.objects.create(code="SMINI2", name="Sale Mini Owner 2")
        other_user = User.objects.create_user(
            username="mini-owner-2",
            password="pw",
            owner=other_owner,
            warehouse=self.warehouse,
        )
        Product.objects.create(
            owner=other_owner,
            code="MP-HIDDEN-2",
            sku="MP-HIDDEN-2",
            name="其他货主未上架商品",
            base_uom=self.uom,
            price=Decimal("30.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        other_product = Product.objects.create(
            owner=other_owner,
            code="MP002",
            sku="MP002",
            name="其他货主上架商品",
            base_uom=self.uom,
            price=Decimal("22.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=other_owner,
            product=other_product,
            is_listed=True,
            is_hot=True,
            sale_price=Decimal("21.0000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )
        InventoryDetail.objects.create(
            owner=other_owner,
            product=other_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("7.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        Customer.objects.create(
            owner=other_owner,
            salesperson=other_user,
            code="MC-OTHER",
            name="其他货主客户",
        )
        public_client = APIClient()

        response = public_client.get(
            "/api/sale-mini/products/",
            {"search": "上架商品"},
        )

        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(
            {row["id"] for row in rows}, {self.product.id, other_product.id}
        )
        by_id = {row["id"]: row for row in rows}
        self.assertPublicProductPayloadHidesInternalFields(by_id[self.product.id])
        self.assertPublicProductPayloadHidesInternalFields(by_id[other_product.id])
        self.assertEqual(by_id[other_product.id]["price"], "21.0000")
        self.assertEqual(by_id[other_product.id]["stock"]["available_qty"], "7.000")

    def test_public_products_search_matches_brand_across_all_owners(self):
        shared_brand = Brand.objects.create(code="BR-UNITY", name="统一优选")
        hidden_product = Product.objects.create(
            owner=self.owner,
            code="MP-BRAND-HIDDEN",
            sku="MP-BRAND-HIDDEN",
            name="不可售测试品",
            brand=shared_brand,
            base_uom=self.uom,
            price=Decimal("99.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        self.product.name = "日用清洁套装"
        self.product.brand = shared_brand
        self.product.save(update_fields=["name", "brand", "updated_at"])
        other_owner, _other_customer, _other_buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        other_product.name = "厨房补给套装"
        other_product.brand = shared_brand
        other_product.save(update_fields=["name", "brand", "updated_at"])
        public_client = APIClient()

        by_name = public_client.get(
            "/api/sale-mini/products/",
            {"search": "统一优选"},
        )
        by_internal_code = public_client.get(
            "/api/sale-mini/products/",
            {"search": "BR-UNITY"},
        )

        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(by_internal_code.status_code, 200)
        self.assertEqual(
            {row["id"] for row in by_name.data["results"]},
            {self.product.id, other_product.id},
        )
        self.assertEqual(by_internal_code.data["results"], [])
        self.assertNotIn(
            hidden_product.id,
            {row["id"] for row in by_name.data["results"]},
        )
        by_id = {row["id"]: row for row in by_name.data["results"]}
        self.assertPublicProductPayloadHidesInternalFields(by_id[self.product.id])
        self.assertPublicProductPayloadHidesInternalFields(by_id[other_product.id])

    def test_product_detail_respects_owner_and_config_context(self):
        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)
        public_client = APIClient()

        response = public_client.get(
            f"/api/sale-mini/products/{self.product.id}/",
            {"owner_id": self.owner.id, "config_id": config.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.product.id)
        self.assertPublicProductPayloadHidesInternalFields(response.data)
        self.assertEqual(response.data["config_id"], config.id)
        self.assertEqual(response.data["price"], "9.5000")

        config_only = public_client.get(
            f"/api/sale-mini/products/{self.product.id}/",
            {"config_id": config.id},
        )

        self.assertEqual(config_only.status_code, 200)
        self.assertEqual(config_only.data["id"], self.product.id)
        self.assertPublicProductPayloadHidesInternalFields(config_only.data)
        self.assertEqual(config_only.data["config_id"], config.id)

        other_owner = Owner.objects.create(code="SMINI-DTL", name="其他详情商家")
        ignored_owner = public_client.get(
            f"/api/sale-mini/products/{self.product.id}/",
            {"owner_id": other_owner.id},
        )

        self.assertEqual(ignored_owner.status_code, 200)
        self.assertEqual(ignored_owner.data["id"], self.product.id)
        self.assertPublicProductPayloadHidesInternalFields(ignored_owner.data)

    def test_pickup_order_uses_pickup_fulfillment_without_ship_to_address(self):
        response = self._create_sale_mini_order(
            extra={
                "delivery_method": "PICKUP",
                "contact": "李四",
                "contact_phone": "13900000000",
                "ship_to": "",
            }
        )

        order = OutboundOrder.objects.get(id=response.data["id"])
        self.assertEqual(order.delivery_method, "PICKUP")
        self.assertEqual(order.contact, "李四")
        self.assertEqual(order.contact_phone, "13900000000")
        self.assertEqual(order.ship_to, "客户自提")
        self.assertNotIn(self.owner.name, response.data["ship_to"])

    def test_order_payload_lines_include_sale_config_context_for_reorder(self):
        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)

        response = self._create_sale_mini_order()

        line = response.data["lines"][0]
        self.assertEqual(line["owner_id"], self.owner.id)
        self.assertEqual(line["config_id"], config.id)
        self.assertEqual(line["product_id"], self.product.id)
        self.assertEqual(line["order_uom"], "EA-MINI")

    def test_public_home_hides_merchants_and_ignores_owner_browse_filter(self):
        category = ProductCategory.objects.create(code="MINI-CAT", name="小程序分类")
        self.product.category = category
        self.product.save(update_fields=["category", "updated_at"])
        SaleProductConfig.objects.filter(
            owner=self.owner,
            product=self.product,
        ).update(is_hot=True, is_recommended=True)
        other_owner = Owner.objects.create(code="SMINI3", name="Sale Mini Owner 3")
        hidden_owner = Owner.objects.create(code="SMINI4", name="Sale Mini Owner 4")
        other_product = Product.objects.create(
            owner=other_owner,
            code="MP003",
            sku="MP003",
            name="其他商家商品",
            category=category,
            base_uom=self.uom,
            price=Decimal("18.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        Product.objects.create(
            owner=hidden_owner,
            code="MP-HIDDEN-4",
            sku="MP-HIDDEN-4",
            name="未上架商家商品",
            category=category,
            base_uom=self.uom,
            price=Decimal("19.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=other_owner,
            product=other_product,
            is_listed=True,
            is_hot=True,
            is_recommended=True,
            sale_price=Decimal("16.0000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )
        SaleMiniBanner.objects.create(
            owner=other_owner,
            title="统一商城活动",
            image_url="https://example.com/banner.png",
            link_type="PRODUCT",
            link_value=str(other_product.id),
        )
        public_client = APIClient()

        home = public_client.get("/api/sale-mini/home/")
        merchants = public_client.get("/api/sale-mini/merchants/")
        products = public_client.get(
            "/api/sale-mini/products/",
            {"owner_id": other_owner.id},
        )
        categories = public_client.get(
            "/api/sale-mini/categories/",
            {"owner_id": other_owner.id},
        )

        self.assertEqual(home.status_code, 200)
        self.assertEqual(merchants.status_code, 404)
        self.assertEqual(products.status_code, 200)
        self.assertEqual(categories.status_code, 200)
        self.assertNotIn("merchants", home.data)
        self.assertEqual(len(home.data["banners"]), 1)
        self.assertNotIn("owner_id", home.data["banners"][0])
        for row in home.data["categories"]:
            self.assertPublicTaxonomyPayloadHidesInternalFields(row)
        self.assertEqual(
            {row["id"] for row in home.data["hot_products"]},
            {self.product.id, other_product.id},
        )
        self.assertEqual(
            {row["id"] for row in home.data["recommend_products"]},
            {self.product.id, other_product.id},
        )
        for row in home.data["hot_products"]:
            self.assertPublicProductPayloadHidesInternalFields(row)
        for row in home.data["recommend_products"]:
            self.assertPublicProductPayloadHidesInternalFields(row)
        self.assertEqual(
            {row["id"] for row in products.data["results"]},
            {self.product.id, other_product.id},
        )
        for row in products.data["results"]:
            self.assertPublicProductPayloadHidesInternalFields(row)
        self.assertEqual(categories.data[0]["id"], category.id)
        self.assertPublicTaxonomyPayloadHidesInternalFields(categories.data[0])

    def test_public_brands_only_return_listed_goods_and_respect_filters(self):
        category = ProductCategory.objects.create(
            code="MINI-BRAND-CAT", name="品牌分类"
        )
        listed_brand = Brand.objects.create(code="BR-LISTED", name="上架品牌")
        other_listed_brand = Brand.objects.create(
            code="BR-LISTED-2", name="跨货主上架品牌"
        )
        hidden_brand = Brand.objects.create(code="BR-HIDDEN", name="未上架品牌")
        self.product.category = category
        self.product.brand = listed_brand
        self.product.save(update_fields=["category", "brand", "updated_at"])
        self.hidden_product.category = category
        self.hidden_product.brand = hidden_brand
        self.hidden_product.save(update_fields=["category", "brand", "updated_at"])
        other_owner, _other_customer, _other_buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        other_product.category = category
        other_product.brand = other_listed_brand
        other_product.save(update_fields=["category", "brand", "updated_at"])
        public_client = APIClient()

        brands = public_client.get("/api/sale-mini/brands/")
        filtered = public_client.get(
            "/api/sale-mini/brands/",
            {"owner_id": self.owner.id, "category_id": category.id},
        )
        products = public_client.get(
            "/api/sale-mini/products/",
            {"brand_id": listed_brand.id},
        )

        self.assertEqual(brands.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(products.status_code, 200)
        self.assertEqual(
            {row["id"] for row in brands.data},
            {listed_brand.id, other_listed_brand.id},
        )
        self.assertEqual(
            {row["id"] for row in filtered.data},
            {listed_brand.id, other_listed_brand.id},
        )
        by_id = {row["id"]: row for row in filtered.data}
        for row in brands.data:
            self.assertPublicTaxonomyPayloadHidesInternalFields(row)
        for row in filtered.data:
            self.assertPublicTaxonomyPayloadHidesInternalFields(row)
        self.assertEqual(by_id[listed_brand.id]["product_count"], 1)
        self.assertEqual(by_id[other_listed_brand.id]["product_count"], 1)
        self.assertEqual(
            {row["id"] for row in products.data["results"]},
            {self.product.id},
        )

    @patch("allapp.salesapp.salemini_api._wechat_code_to_session")
    def test_wechat_login_returns_jwt_for_bound_openid(self, mock_session):
        self.buyer.openid = "wx-open-001"
        self.buyer.unionid = "wx-union-001"
        self.buyer.save(update_fields=["openid", "unionid"])
        mock_session.return_value = {
            "openid": "wx-open-001",
            "unionid": "wx-union-001",
        }
        client = APIClient()

        response = client.post(
            "/api/sale-mini/auth/wechat-login/",
            {"code": "login-code"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["customer"]["id"], self.customer.id)
        self.assertNotIn("owner", response.data)
        self.assertNotIn("warehouse", response.data)
        self.assertNotIn("code", response.data["customer"])
        self.assertNotIn("name", response.data["customer"])
        binding = response.data["bindings"][0]
        self.assertEqual(binding["owner"]["id"], self.owner.id)
        self.assertNotIn("code", binding["customer"])
        self.assertNotIn("name", binding["customer"])

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me_response = client.get("/api/sale-mini/me/")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data["customer"]["id"], self.customer.id)
        self.assertNotIn("owner", me_response.data)
        self.assertNotIn("warehouse", me_response.data)
        self.assertNotIn("code", me_response.data["customer"])
        self.assertNotIn("name", me_response.data["customer"])

    @patch("allapp.salesapp.salemini_api._wechat_code_to_session")
    def test_wechat_login_binds_openid_from_existing_unionid(self, mock_session):
        self.buyer.unionid = "wx-union-002"
        self.buyer.save(update_fields=["unionid"])
        mock_session.return_value = {
            "openid": "wx-open-002",
            "unionid": "wx-union-002",
        }
        client = APIClient()

        response = client.post(
            "/api/sale-mini/auth/wechat-login/",
            {"code": "login-code", "nickname": "微信采购员"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.openid, "wx-open-002")
        self.assertEqual(self.buyer.nickname, "微信采购员")

    @patch("allapp.salesapp.salemini_api._wechat_code_to_session")
    def test_wechat_login_rejects_unbound_openid(self, mock_session):
        mock_session.return_value = {"openid": "wx-missing"}
        client = APIClient()

        response = client.post(
            "/api/sale-mini/auth/wechat-login/",
            {"code": "login-code"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("购买权限", str(response.data))

    @patch("allapp.salesapp.salemini_api._wechat_code_to_session")
    def test_wechat_login_rejects_duplicate_openid_binding(self, mock_session):
        self.buyer.openid = "wx-duplicate"
        self.buyer.save(update_fields=["openid"])
        other_user = User.objects.create_user(
            username="mini-buyer-2",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        other_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=other_user,
            code="MC002",
            name="小程序客户二",
        )
        MiniProgramUser.objects.create(
            owner=self.owner,
            user=other_user,
            customer=other_customer,
            openid="wx-duplicate",
        )
        mock_session.return_value = {"openid": "wx-duplicate"}
        client = APIClient()

        response = client.post(
            "/api/sale-mini/auth/wechat-login/",
            {"code": "login-code"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("多个购买权限记录", str(response.data))

    @patch("allapp.salesapp.salemini_api._wechat_code_to_session")
    def test_wechat_login_rejects_buyer_user_without_warehouse(self, mock_session):
        no_warehouse_user = User.objects.create_user(
            username="mini-no-warehouse",
            password="pw",
            owner=self.owner,
        )
        no_warehouse_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=no_warehouse_user,
            code="MC003",
            name="无仓库客户",
        )
        MiniProgramUser.objects.create(
            owner=self.owner,
            user=no_warehouse_user,
            customer=no_warehouse_customer,
            openid="wx-no-warehouse",
        )
        mock_session.return_value = {"openid": "wx-no-warehouse"}
        client = APIClient()

        response = client.post(
            "/api/sale-mini/auth/wechat-login/",
            {"code": "login-code"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("商城履约配置", str(response.data))

    def test_preview_recalculates_amount_and_rejects_shortage(self):
        response = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ok"])
        self.assertEqual(response.data["total_amount"], "19.00")
        self.assertEqual(response.data["lines"][0]["base_unit_price"], "9.5000")

        response = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "11.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ok"])
        self.assertIn("库存不足", response.data["lines"][0]["message"])

    def test_discount_adjustment_does_not_pollute_outbound_line_price(self):
        self._create_discount_step(threshold="10.00", discount="3.00")

        preview = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.data["goods_amount"], "19.00")
        self.assertEqual(preview.data["adjustment_amount"], "-3.00")
        self.assertEqual(preview.data["payable_amount"], "16.00")
        self.assertEqual(preview.data["total_amount"], "16.00")

        response = self._create_sale_mini_order()
        order = OutboundOrder.objects.get(id=response.data["id"])
        mapping = SaleMiniOrderMapping.objects.get(outbound_order=order)
        line = OutboundOrderLine.objects.get(order=order)
        adjustment = SaleMiniOrderAdjustment.objects.get(mapping=mapping)

        self.assertEqual(response.data["total_amount"], "16.00")
        self.assertEqual(order.final_order_amount, Decimal("19.00"))
        self.assertEqual(mapping.goods_amount, Decimal("19.00"))
        self.assertEqual(mapping.adjustment_amount, Decimal("-3.00"))
        self.assertEqual(mapping.payable_amount, Decimal("16.00"))
        self.assertEqual(line.base_price, Decimal("9.5000"))
        self.assertEqual(line.final_line_amount, Decimal("19.00"))
        self.assertEqual(
            adjustment.adjustment_type,
            SaleMiniOrderAdjustment.AdjustmentType.DISCOUNT_STEP,
        )
        self.assertEqual(adjustment.status, SaleMiniOrderAdjustment.Status.CONFIRMED)
        self.assertEqual(adjustment.amount, Decimal("-3.00"))

    def test_wechat_coupon_and_points_lock_then_release_on_cancel(self):
        coupon = self._create_coupon(discount="4.00")
        self._earn_points(500)

        response = self._create_sale_mini_order(
            payment_method="WECHAT",
            extra={"coupon_id": coupon.id, "points": 100},
        )

        mapping = SaleMiniOrderMapping.objects.get(id=response.data["mapping_id"])
        coupon.refresh_from_db()
        self.assertEqual(response.data["goods_amount"], "19.00")
        self.assertEqual(response.data["adjustment_amount"], "-5.00")
        self.assertEqual(response.data["payable_amount"], "14.00")
        self.assertEqual(coupon.status, SaleMiniCoupon.Status.LOCKED)
        self.assertEqual(coupon.locked_mapping, mapping)
        self.assertEqual(
            point_balance(self.owner, self.customer, self.buyer), (400, 100)
        )
        self.assertEqual(
            set(mapping.adjustments.values_list("status", flat=True)),
            {SaleMiniOrderAdjustment.Status.LOCKED},
        )

        cancel = self.client.post(
            f"/api/sale-mini/orders/{response.data['id']}/cancel/",
            {},
            format="json",
        )

        self.assertEqual(cancel.status_code, 200)
        mapping.refresh_from_db()
        coupon.refresh_from_db()
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.CANCELLED
        )
        self.assertEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertEqual(coupon.status, SaleMiniCoupon.Status.AVAILABLE)
        self.assertIsNone(coupon.locked_mapping)
        self.assertEqual(point_balance(self.owner, self.customer, self.buyer), (500, 0))
        self.assertEqual(
            set(mapping.adjustments.values_list("status", flat=True)),
            {SaleMiniOrderAdjustment.Status.RELEASED},
        )
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))

    def test_coupon_and_point_api_returns_current_buyer_benefits(self):
        coupon = self._create_coupon(discount="4.00")
        global_coupon = self._create_coupon(discount="2.00")
        global_coupon.coupon_no = "COUPON-GLOBAL"
        global_coupon.buyer_user = None
        global_coupon.save(update_fields=["coupon_no", "buyer_user"])
        other_user = User.objects.create_user(
            username="mini-other-benefit",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        other_customer = Customer.objects.create(
            owner=self.owner,
            salesperson=other_user,
            code="MC-BENEFIT",
            name="其他权益客户",
        )
        other_buyer = MiniProgramUser.objects.create(
            owner=self.owner,
            user=other_user,
            customer=other_customer,
            nickname="其他买家",
        )
        template = global_coupon.template
        SaleMiniCoupon.objects.create(
            owner=self.owner,
            customer=other_customer,
            buyer_user=other_buyer,
            template=template,
            coupon_no="COUPON-OTHER",
        )
        self._earn_points(500)
        other_owner, other_customer, other_buyer, _other_product = (
            self._create_other_owner_sale_binding()
        )
        other_template = SaleMiniCouponTemplate.objects.create(
            owner=other_owner,
            code="MINI-COUPON-OTHER-OWNER",
            title="跨绑定优惠券",
            threshold_amount=Decimal("10.00"),
            discount_amount=Decimal("3.00"),
            effective_from=date.today() - timedelta(days=1),
        )
        SaleMiniCoupon.objects.create(
            owner=other_owner,
            customer=other_customer,
            buyer_user=other_buyer,
            template=other_template,
            coupon_no="COUPON-OTHER-OWNER",
        )
        SaleMiniPointLedger.objects.create(
            owner=other_owner,
            customer=other_customer,
            buyer_user=other_buyer,
            tx_no="POINT-EARN-OTHER-OWNER",
            tx_type=SaleMiniPointLedger.TxType.EARN,
            points_delta=300,
        )

        coupons = self.client.get(
            "/api/sale-mini/coupons/",
            {"order_amount": "19.00"},
        )
        points = self.client.get("/api/sale-mini/points/")
        scoped_coupons = self.client.get(
            "/api/sale-mini/coupons/",
            {"owner_id": self.owner.id, "order_amount": "19.00"},
        )
        scoped_points = self.client.get(
            "/api/sale-mini/points/",
            {"owner_id": self.owner.id},
        )

        self.assertEqual(coupons.status_code, 200)
        self.assertEqual(points.status_code, 200)
        self.assertEqual(scoped_coupons.status_code, 200)
        self.assertEqual(scoped_points.status_code, 200)
        self.assertEqual(
            {row["coupon_no"] for row in coupons.data},
            {coupon.coupon_no, "COUPON-GLOBAL", "COUPON-OTHER-OWNER"},
        )
        self.assertEqual(
            {row["coupon_no"] for row in scoped_coupons.data},
            {coupon.coupon_no, "COUPON-GLOBAL"},
        )
        self.assertTrue(all(row["usable"] for row in coupons.data))
        self.assertEqual(points.data["points"], 800)
        self.assertEqual(points.data["frozen"], 0)
        self.assertEqual(points.data["exchange_rate"], "100.00")
        self.assertEqual(scoped_points.data["points"], 500)
        self.assertEqual(scoped_points.data["frozen"], 0)

    def test_address_api_returns_all_bound_addresses_without_owner_filter(self):
        other_owner, other_customer, other_buyer, _other_product = (
            self._create_other_owner_sale_binding()
        )
        first_address = MiniCustomerAddress.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            contact="张三",
            phone="13800000000",
            province="浙江",
            city="杭州",
            district="西湖",
            detail="一号仓",
            is_default=True,
        )
        second_address = MiniCustomerAddress.objects.create(
            owner=other_owner,
            customer=other_customer,
            buyer_user=other_buyer,
            contact="李四",
            phone="13900000000",
            province="上海",
            city="上海",
            district="浦东",
            detail="二号仓",
            is_default=True,
        )

        all_addresses = self.client.get("/api/sale-mini/addresses/")
        scoped_addresses = self.client.get(
            "/api/sale-mini/addresses/",
            {"owner_id": self.owner.id},
        )

        self.assertEqual(all_addresses.status_code, 200)
        self.assertEqual(scoped_addresses.status_code, 200)
        self.assertEqual(
            {row["id"] for row in all_addresses.data},
            {first_address.id, second_address.id},
        )
        self.assertEqual(
            {row["owner_id"] for row in all_addresses.data},
            {self.owner.id, other_owner.id},
        )
        self.assertEqual(
            {row["id"] for row in scoped_addresses.data},
            {first_address.id},
        )

    def test_server_cart_persists_reprices_and_reports_stock_errors(self):
        config = SaleProductConfig.objects.get(owner=self.owner, product=self.product)
        response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ok"])
        self.assertEqual(response.data["line_count"], 1)
        self.assertEqual(response.data["total_amount"], "19.00")
        self.assertEqual(response.data["items"][0]["config_id"], config.id)
        self.assertEqual(response.data["items"][0]["unit_price"], "9.5000")
        cart_id = response.data["id"]
        item_id = response.data["items"][0]["item_id"]
        self.assertTrue(
            SaleMiniCartItem.objects.filter(
                cart_id=cart_id,
                product=self.product,
                order_uom="EA-MINI",
                qty=Decimal("2.000"),
            ).exists()
        )

        SaleProductConfig.objects.filter(product=self.product).update(
            sale_price=Decimal("8.0000")
        )
        response = self.client.get("/api/sale-mini/cart/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_amount"], "16.00")
        self.assertEqual(response.data["items"][0]["config_id"], config.id)
        self.assertEqual(response.data["items"][0]["unit_price"], "8.0000")

        response = self.client.post(
            "/api/sale-mini/cart/update/",
            {"item_id": item_id, "qty": "11.000"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ok"])
        self.assertIn("库存不足", response.data["items"][0]["message"])

        by_cart_id = self.client.get("/api/sale-mini/cart/", {"cart_id": cart_id})

        self.assertEqual(by_cart_id.status_code, 200)
        self.assertEqual(by_cart_id.data["cart_id"], cart_id)
        self.assertEqual(by_cart_id.data["owner_id"], self.owner.id)
        self.assertEqual(by_cart_id.data["line_count"], 1)
        self.assertEqual(by_cart_id.data["items"][0]["item_id"], item_id)

        response = self.client.post(
            "/api/sale-mini/cart/remove/",
            {"item_id": item_id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["line_count"], 0)
        self.assertFalse(SaleMiniCartItem.objects.filter(cart_id=cart_id).exists())

    def test_cart_groups_items_by_owner_for_multi_owner_buyer(self):
        other_owner, _customer, _buyer, other_product = (
            self._create_other_owner_sale_binding()
        )

        first = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        second = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": other_product.id,
                "qty": "1.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        cart = self.client.get("/api/sale-mini/cart/")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(cart.status_code, 200)
        self.assertEqual(cart.data["line_count"], 2)
        self.assertEqual(
            {group["owner_id"] for group in cart.data["groups"]},
            {self.owner.id, other_owner.id},
        )
        self.assertEqual(
            SaleMiniCart.objects.filter(buyer_user__user=self.user).count(), 2
        )

    def test_multi_owner_preview_returns_combined_packages(self):
        other_owner, _customer, _buyer, other_product = (
            self._create_other_owner_sale_binding()
        )

        response = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "order_uom": "EA-MINI",
                    },
                    {
                        "product_id": other_product.id,
                        "qty": "1.000",
                        "order_uom": "EA-MINI",
                    },
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ok"])
        self.assertTrue(response.data["is_combined"])
        self.assertEqual(response.data["goods_amount"], "14.50")
        self.assertEqual(response.data["payable_amount"], "14.50")
        self.assertEqual(response.data["line_count"], 2)
        self.assertEqual(
            {group["owner_id"] for group in response.data["groups"]},
            {self.owner.id, other_owner.id},
        )
        self.assertEqual(
            {line["product_id"] for line in response.data["lines"]},
            {self.product.id, other_product.id},
        )

    def test_multi_owner_checkout_splits_orders_and_keeps_inventory_accurate(self):
        other_owner, other_customer, other_buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        first_cart = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        second_cart = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": other_product.id,
                "qty": "1.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        self.assertEqual(first_cart.status_code, 200)
        self.assertEqual(second_cart.status_code, 200)

        response = self.client.post(
            "/api/sale-mini/orders/",
            {
                "cart_ids": [first_cart.data["cart_id"], second_cart.data["cart_id"]],
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "payment_method": "OFFLINE",
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    },
                    {
                        "product_id": other_product.id,
                        "qty": "1.000",
                        "order_uom": "EA-MINI",
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["is_combined"])
        self.assertEqual(response.data["order_count"], 2)
        self.assertEqual(response.data["goods_amount"], "24.00")
        self.assertEqual(response.data["payable_amount"], "24.00")
        self.assertEqual(response.data["payment_status"], "OFFLINE")

        order_ids = [row["id"] for row in response.data["orders"]]
        orders = {
            order.owner_id: order
            for order in OutboundOrder.objects.filter(id__in=order_ids)
        }
        self.assertEqual(set(orders), {self.owner.id, other_owner.id})
        self.assertEqual(orders[self.owner.id].customer, self.customer)
        self.assertEqual(orders[other_owner.id].customer, other_customer)
        self.assertEqual(
            orders[self.owner.id].final_order_amount,
            Decimal("19.00"),
        )
        self.assertEqual(
            orders[other_owner.id].final_order_amount,
            Decimal("5.00"),
        )

        mappings = {
            mapping.owner_id: mapping
            for mapping in SaleMiniOrderMapping.objects.filter(
                outbound_order_id__in=order_ids
            )
        }
        self.assertEqual(mappings[self.owner.id].buyer_user, self.buyer)
        self.assertEqual(mappings[other_owner.id].buyer_user, other_buyer)
        self.assertEqual(mappings[self.owner.id].payable_amount, Decimal("19.00"))
        self.assertEqual(mappings[other_owner.id].payable_amount, Decimal("5.00"))
        batch_sources = {mapping.source for mapping in mappings.values()}
        self.assertEqual(len(batch_sources), 1)
        self.assertTrue(next(iter(batch_sources)).startswith("sale-mini-batch-"))

        self_line = OutboundOrderLine.objects.get(order=orders[self.owner.id])
        other_line = OutboundOrderLine.objects.get(order=orders[other_owner.id])
        self.assertEqual(self_line.base_qty, Decimal("2.000"))
        self.assertEqual(other_line.base_qty, Decimal("1.000"))

        self_detail = InventoryDetail.objects.get(product=self.product)
        other_detail = InventoryDetail.objects.get(product=other_product)
        self.assertEqual(self_detail.allocated_qty, Decimal("2.0000"))
        self.assertEqual(self_detail.available_qty, Decimal("8.0000"))
        self.assertEqual(other_detail.allocated_qty, Decimal("1.0000"))
        self.assertEqual(other_detail.available_qty, Decimal("5.0000"))

        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.PICK,
                source_pk__in=[str(order_id) for order_id in order_ids],
            ).count(),
            2,
        )
        self.assertFalse(
            SaleMiniCartItem.objects.filter(
                cart_id__in=[first_cart.data["cart_id"], second_cart.data["cart_id"]]
            ).exists()
        )

        order_list = self.client.get("/api/sale-mini/orders/")
        self.assertEqual(order_list.status_code, 200)
        self.assertEqual(len(order_list.data["results"]), 1)
        public_order = order_list.data["results"][0]
        self.assertTrue(public_order["is_combined"])
        self.assertEqual(public_order["order_count"], 2)
        self.assertEqual(public_order["line_count"], 2)
        self.assertEqual(public_order["payable_amount"], "24.00")
        self.assertTrue(public_order["order_no"].startswith("SC"))

        order_detail = self.client.get(f"/api/sale-mini/orders/{response.data['id']}/")
        self.assertEqual(order_detail.status_code, 200)
        self.assertTrue(order_detail.data["is_combined"])
        self.assertEqual(order_detail.data["order_count"], 2)
        self.assertEqual(order_detail.data["line_count"], 2)
        self.assertEqual(
            {line["product_id"] for line in order_detail.data["lines"]},
            {self.product.id, other_product.id},
        )

        cancel = self.client.post(f"/api/sale-mini/orders/{response.data['id']}/cancel/")
        self.assertEqual(cancel.status_code, 200)
        self.assertTrue(cancel.data["is_combined"])
        self.assertEqual(cancel.data["status"], "CANCELLED")
        self.assertEqual(cancel.data["order_count"], 2)
        self_detail.refresh_from_db()
        other_detail.refresh_from_db()
        self.assertEqual(self_detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(self_detail.available_qty, Decimal("10.0000"))
        self.assertEqual(other_detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(other_detail.available_qty, Decimal("6.0000"))

    def test_cart_and_checkout_auto_create_internal_binding_for_new_owner(self):
        other_owner = Owner.objects.create(code="SMINI-AUTO", name="Sale Mini Auto")
        other_product = Product.objects.create(
            owner=other_owner,
            code="MP-AUTO",
            sku="MP-AUTO",
            name="自动绑定商品",
            base_uom=self.uom,
            price=Decimal("11.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=other_owner,
            product=other_product,
            is_listed=True,
            sale_price=Decimal("10.0000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
        )
        InventoryDetail.objects.create(
            owner=other_owner,
            product=other_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )

        cart_response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": other_product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )

        self.assertEqual(cart_response.status_code, 200)
        buyer = MiniProgramUser.objects.get(owner=other_owner, user=self.user)
        self.assertEqual(buyer.customer.owner, other_owner)
        self.assertEqual(buyer.customer.salesperson, self.user)
        self.assertTrue(buyer.customer.code.startswith("MINI-U"))
        self.assertEqual(cart_response.data["owner_id"], other_owner.id)

        order_response = self.client.post(
            "/api/sale-mini/orders/",
            {
                "owner_id": other_owner.id,
                "cart_id": cart_response.data["cart_id"],
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": other_product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(order_response.status_code, 201)
        order = OutboundOrder.objects.get(id=order_response.data["id"])
        mapping = SaleMiniOrderMapping.objects.get(outbound_order=order)
        self.assertEqual(order.owner, other_owner)
        self.assertEqual(order.customer, buyer.customer)
        self.assertEqual(mapping.buyer_user, buyer)

    def test_checkout_can_target_other_owner_binding(self):
        other_owner, other_customer, other_buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        cart_response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": other_product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        self.assertEqual(cart_response.status_code, 200)

        response = self.client.post(
            "/api/sale-mini/orders/",
            {
                "owner_id": other_owner.id,
                "cart_id": cart_response.data["cart_id"],
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": other_product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = OutboundOrder.objects.get(id=response.data["id"])
        mapping = SaleMiniOrderMapping.objects.get(outbound_order=order)
        self.assertEqual(order.owner, other_owner)
        self.assertEqual(order.customer, other_customer)
        self.assertEqual(mapping.buyer_user, other_buyer)
        self.assertEqual(response.data["owner_id"], other_owner.id)
        self.assertFalse(
            SaleMiniCartItem.objects.filter(
                cart_id=cart_response.data["cart_id"]
            ).exists()
        )

    def test_order_and_after_sale_lists_ignore_owner_filter_for_unified_mall(self):
        first_order = self._create_sale_mini_order()
        other_owner, other_customer, other_buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        cart_response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": other_product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        self.assertEqual(cart_response.status_code, 200)
        second_order = self.client.post(
            "/api/sale-mini/orders/",
            {
                "owner_id": other_owner.id,
                "cart_id": cart_response.data["cart_id"],
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": other_product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(second_order.status_code, 201)
        first_mapping = SaleMiniOrderMapping.objects.get(
            id=first_order.data["mapping_id"]
        )
        second_mapping = SaleMiniOrderMapping.objects.get(
            id=second_order.data["mapping_id"]
        )
        SaleMiniAfterSaleRequest.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=first_mapping,
            request_no="AS-UNIFIED-1",
            request_type=SaleMiniAfterSaleRequest.RequestType.REFUND,
            amount=Decimal("1.00"),
            requested_at=timezone.now(),
        )
        SaleMiniAfterSaleRequest.objects.create(
            owner=other_owner,
            customer=other_customer,
            buyer_user=other_buyer,
            mapping=second_mapping,
            request_no="AS-UNIFIED-2",
            request_type=SaleMiniAfterSaleRequest.RequestType.REFUND,
            amount=Decimal("2.00"),
            requested_at=timezone.now(),
        )

        orders = self.client.get(
            "/api/sale-mini/orders/",
            {"owner_id": other_owner.id},
        )
        after_sales = self.client.get(
            "/api/sale-mini/after-sales/",
            {"owner_id": other_owner.id},
        )

        self.assertEqual(orders.status_code, 200)
        self.assertEqual(after_sales.status_code, 200)
        order_ids = {row["id"] for row in orders.data["results"]}
        self.assertIn(first_order.data["id"], order_ids)
        self.assertIn(second_order.data["id"], order_ids)
        self.assertEqual(
            {row["request_no"] for row in after_sales.data},
            {"AS-UNIFIED-1", "AS-UNIFIED-2"},
        )

    def test_server_cart_rejects_unlisted_product_and_conflicting_uom(self):
        response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.hidden_product.id,
                "qty": "1.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            SaleMiniCartItem.objects.filter(product=self.hidden_product).exists()
        )

        carton = ProductUom.objects.create(code="CTN-MINI", name="箱")
        ProductPackage.objects.create(
            product=self.product,
            uom=carton,
            qty_in_base=Decimal("6.000"),
        )
        response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "1.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "1.000",
                "order_uom": "CTN-MINI",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("已按 EA-MINI 加入购物车", str(response.data))
        cart = SaleMiniCart.objects.get(customer=self.customer)
        self.assertEqual(cart.items.count(), 1)

    def test_server_cart_rejects_mismatched_sale_product_config(self):
        _other_owner, _other_customer, _other_buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        wrong_config = SaleProductConfig.objects.get(product=other_product)

        response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "owner_id": self.owner.id,
                "config_id": wrong_config.id,
                "product_id": self.product.id,
                "qty": "1.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            SaleMiniCartItem.objects.filter(
                cart__customer=self.customer,
                product=self.product,
            ).exists()
        )

    def test_checkout_with_cart_id_clears_matching_server_cart_items(self):
        response = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        cart_id = response.data["id"]
        self.assertEqual(SaleMiniCartItem.objects.filter(cart_id=cart_id).count(), 1)

        response = self.client.post(
            "/api/sale-mini/orders/",
            {
                "cart_id": cart_id,
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(SaleMiniCartItem.objects.filter(cart_id=cart_id).exists())

    def test_create_order_generates_outbound_and_allocates_inventory(self):
        response = self.client.post(
            "/api/sale-mini/orders/",
            {
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "WAIT_SHIP")
        self.assertEqual(response.data["status_name"], "待发货")
        self.assertEqual(response.data["total_amount"], "19.00")
        self.assertEqual(response.data["customer"], {"id": self.customer.id})
        self.assertEqual(response.data["warehouse"], {"id": self.warehouse.id})

        order = OutboundOrder.objects.get(id=response.data["id"])
        mapping = SaleMiniOrderMapping.objects.get(outbound_order=order)
        line = OutboundOrderLine.objects.get(order=order)
        self.assertEqual(mapping.customer, self.customer)
        self.assertEqual(order.src_bill_no, f"SALE-MINI-{order.id}")
        self.assertEqual(order.outbound_type, "SALES")
        self.assertEqual(order.submit_status, "SUBMITTED")
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        self.assertEqual(order.final_order_amount, Decimal("19.00"))
        self.assertEqual(line.base_qty, Decimal("2.000"))
        self.assertEqual(line.base_price, Decimal("9.5000"))

        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(detail.allocated_qty, Decimal("2.0000"))
        self.assertEqual(detail.available_qty, Decimal("8.0000"))

        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model=order._meta.model_name,
            source_pk=str(order.pk),
        )
        self.assertEqual(task.status, "RESERVED")
        self.assertEqual(task.lines.count(), 1)

    def test_checkout_rejects_second_order_after_available_stock_is_allocated(self):
        InventoryDetail.objects.filter(product=self.product).update(
            onhand_qty=Decimal("2.0000"),
            available_qty=Decimal("2.0000"),
            allocated_qty=Decimal("0.0000"),
        )

        first = self._create_sale_mini_order()
        second = self.client.post(
            "/api/sale-mini/orders/",
            {
                "contact": "李四",
                "contact_phone": "13900000000",
                "ship_to": "上海市测试路 2 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)
        self.assertIn("库存不足", str(second.data))
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(detail.allocated_qty, Decimal("2.0000"))
        self.assertEqual(detail.available_qty, Decimal("0.0000"))

    @patch("allapp.salesapp.salemini_api.create_jsapi_prepay")
    def test_wechat_prepay_creates_payment_and_returns_client_params(self, mock_prepay):
        self.buyer.openid = "wx-open-pay"
        self.buyer.save(update_fields=["openid"])
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        self.assertEqual(order_response.data["payment_status"], "UNPAID")
        self.assertTrue(order_response.data["pay_deadline_at"])
        mock_prepay.return_value = (
            "prepay-test",
            {"prepay_id": "prepay-test"},
            {
                "timeStamp": "1700000000",
                "nonceStr": "nonce",
                "package": "prepay_id=prepay-test",
                "signType": "RSA",
                "paySign": "signed",
            },
        )

        response = self.client.post(
            "/api/sale-mini/payments/wechat/prepay/",
            {"order_id": order_response.data["id"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["paid"])
        self.assertEqual(
            response.data["pay_params"]["package"], "prepay_id=prepay-test"
        )
        payment = SaleMiniPayment.objects.get(
            mapping_id=order_response.data["mapping_id"]
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.PREPAY)
        self.assertEqual(payment.amount, Decimal("19.00"))
        self.assertEqual(payment.amount_cents, 1900)
        self.assertEqual(payment.prepay_id, "prepay-test")

    @patch("allapp.salesapp.salemini_api.create_jsapi_prepay")
    def test_wechat_prepay_uses_payable_amount_after_adjustment(self, mock_prepay):
        self._create_discount_step(threshold="10.00", discount="3.00")
        self.buyer.openid = "wx-open-pay-discount"
        self.buyer.save(update_fields=["openid"])
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        self.assertEqual(order_response.data["goods_amount"], "19.00")
        self.assertEqual(order_response.data["payable_amount"], "16.00")
        mock_prepay.return_value = (
            "prepay-discount",
            {"prepay_id": "prepay-discount"},
            {
                "timeStamp": "1700000000",
                "nonceStr": "nonce",
                "package": "prepay_id=prepay-discount",
                "signType": "RSA",
                "paySign": "signed",
            },
        )

        response = self.client.post(
            "/api/sale-mini/payments/wechat/prepay/",
            {"order_id": order_response.data["id"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payment = SaleMiniPayment.objects.get(
            mapping_id=order_response.data["mapping_id"]
        )
        self.assertEqual(payment.amount, Decimal("16.00"))
        self.assertEqual(payment.amount_cents, 1600)

    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_wechat_payment_callback_marks_paid_idempotently(
        self, mock_verify, mock_decrypt
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-CALLBACK-1",
            out_trade_no="SMT-CALLBACK-1",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "out_trade_no": payment.out_trade_no,
            "transaction_id": "wx-transaction-1",
            "trade_state": "SUCCESS",
            "amount": {"total": 1900, "currency": "CNY"},
        }
        payload = {
            "id": "evt-pay-1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            payload,
            format="json",
        )
        duplicate = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        payment.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(payment.status, SaleMiniPayment.Status.PAID)
        self.assertEqual(payment.transaction_id, "wx-transaction-1")
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.PAID
        )
        self.assertEqual(
            SaleMiniPaymentEvent.objects.filter(event_id="evt-pay-1").count(), 1
        )

    @override_settings(SALE_MINI_DISTRIBUTION_COMMISSION_RATE="0.10")
    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_wechat_payment_callback_confirms_adjustments_and_distribution(
        self, mock_verify, mock_decrypt
    ):
        coupon = self._create_coupon(discount="4.00")
        self._earn_points(500)
        referrer = self._create_referrer()
        order_response = self._create_sale_mini_order(
            payment_method="WECHAT",
            extra={
                "coupon_id": coupon.id,
                "points": 100,
                "referrer_buyer_id": referrer.id,
            },
        )
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-CALLBACK-ADJ",
            out_trade_no="SMT-CALLBACK-ADJ",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("14.00"),
            amount_cents=1400,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "out_trade_no": payment.out_trade_no,
            "transaction_id": "wx-transaction-adj",
            "trade_state": "SUCCESS",
            "amount": {"total": 1400, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            {"id": "evt-pay-adj", "event_type": "TRANSACTION.SUCCESS", "resource": {}},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        coupon.refresh_from_db()
        distribution = SaleMiniDistributionRecord.objects.get(mapping=mapping)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.PAID
        )
        self.assertEqual(coupon.status, SaleMiniCoupon.Status.USED)
        self.assertEqual(coupon.used_mapping, mapping)
        self.assertIsNotNone(coupon.used_at)
        self.assertEqual(point_balance(self.owner, self.customer, self.buyer), (400, 0))
        self.assertEqual(
            set(mapping.adjustments.values_list("status", flat=True)),
            {SaleMiniOrderAdjustment.Status.CONFIRMED},
        )
        self.assertIsNotNone(distribution.confirmed_at)
        self.assertEqual(distribution.base_amount, Decimal("14.00"))
        self.assertEqual(distribution.commission_amount, Decimal("1.40"))

    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_wechat_payment_callback_rejects_amount_mismatch(
        self, mock_verify, mock_decrypt
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-CALLBACK-2",
            out_trade_no="SMT-CALLBACK-2",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "out_trade_no": payment.out_trade_no,
            "transaction_id": "wx-transaction-2",
            "trade_state": "SUCCESS",
            "amount": {"total": 1800, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            {"id": "evt-pay-bad", "event_type": "TRANSACTION.SUCCESS", "resource": {}},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(payment.status, SaleMiniPayment.Status.PREPAY)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.UNPAID
        )
        event = SaleMiniPaymentEvent.objects.get(event_id="evt-pay-bad")
        self.assertEqual(
            event.process_status, SaleMiniPaymentEvent.ProcessStatus.FAILED
        )

    @patch("allapp.salesapp.salemini_api.request_refund")
    def test_wechat_refund_request_cancels_order_and_releases_inventory(
        self, mock_refund
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
        mapping.paid_at = timezone.now()
        mapping.save(update_fields=["payment_status", "paid_at"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-REFUND-1",
            out_trade_no="SMT-REFUND-1",
            transaction_id="wx-paid-1",
            status=SaleMiniPayment.Status.PAID,
            amount=Decimal("19.00"),
            amount_cents=1900,
            paid_at=timezone.now(),
        )
        mock_refund.return_value = (
            {"out_refund_no": "SMRF-1"},
            {"refund_id": "wx-refund-1", "status": "PROCESSING"},
        )

        response = self.client.post(
            "/api/sale-mini/payments/wechat/refund/",
            {"order_id": order_response.data["id"], "reason": "不想买了"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        payment.refresh_from_db()
        refund = SaleMiniRefund.objects.get(payment=payment)
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.REFUNDING
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.REFUNDING)
        self.assertEqual(refund.status, SaleMiniRefund.Status.PROCESSING)
        self.assertEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))

    def test_after_sale_request_is_created_after_warehouse_work_started(self):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
        mapping.paid_at = timezone.now()
        mapping.save(update_fields=["payment_status", "paid_at"])
        order = mapping.outbound_order
        order.approval_status = "WHS_APPROVED"
        order.save(update_fields=["approval_status", "updated_at"])

        refund = self.client.post(
            "/api/sale-mini/payments/wechat/refund/",
            {"order_id": order.id, "reason": "已开始作业"},
            format="json",
        )
        response = self.client.post(
            "/api/sale-mini/after-sales/",
            {"order_id": order.id, "request_type": "REFUND", "reason": "已开始作业"},
            format="json",
        )
        duplicate = self.client.post(
            "/api/sale-mini/after-sales/",
            {"order_id": order.id, "request_type": "REFUND", "reason": "重复申请"},
            format="json",
        )

        self.assertEqual(refund.status_code, 400)
        self.assertIn("备货阶段", str(refund.data))
        self.assertIn("售后", str(refund.data))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        request_row = SaleMiniAfterSaleRequest.objects.get(mapping=mapping)
        self.assertEqual(request_row.status, SaleMiniAfterSaleRequest.Status.PENDING)
        self.assertEqual(request_row.amount, Decimal("19.00"))
        self.assertEqual(request_row.reason, "已开始作业")

    @override_settings(SALE_MINI_DISTRIBUTION_COMMISSION_RATE="0.10")
    @patch("allapp.salesapp.salemini_api.request_refund")
    def test_successful_refund_reverses_confirmed_adjustments_and_distribution(
        self, mock_refund
    ):
        coupon = self._create_coupon(discount="4.00")
        self._earn_points(500)
        referrer = self._create_referrer()
        order_response = self._create_sale_mini_order(
            payment_method="WECHAT",
            extra={
                "coupon_id": coupon.id,
                "points": 100,
                "referrer_buyer_id": referrer.id,
            },
        )
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        confirm_adjustments(mapping)
        confirm_distribution(mapping)
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
        mapping.paid_at = timezone.now()
        mapping.save(update_fields=["payment_status", "paid_at"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-REFUND-SUCCESS",
            out_trade_no="SMT-REFUND-SUCCESS",
            transaction_id="wx-paid-success",
            status=SaleMiniPayment.Status.PAID,
            amount=Decimal("14.00"),
            amount_cents=1400,
            paid_at=timezone.now(),
        )
        mock_refund.return_value = (
            {"out_refund_no": "SMRF-SUCCESS"},
            {"refund_id": "wx-refund-success", "status": "SUCCESS"},
        )

        response = self.client.post(
            "/api/sale-mini/payments/wechat/refund/",
            {"order_id": order_response.data["id"], "reason": "测试退款"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        payment.refresh_from_db()
        coupon.refresh_from_db()
        distribution = SaleMiniDistributionRecord.objects.get(mapping=mapping)
        refund = SaleMiniRefund.objects.get(payment=payment)
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.REFUNDED
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.REFUNDED)
        self.assertEqual(refund.status, SaleMiniRefund.Status.SUCCESS)
        self.assertIsNotNone(refund.success_at)
        self.assertEqual(coupon.status, SaleMiniCoupon.Status.AVAILABLE)
        self.assertIsNone(coupon.used_mapping)
        self.assertEqual(point_balance(self.owner, self.customer, self.buyer), (500, 0))
        self.assertEqual(
            distribution.status, SaleMiniDistributionRecord.Status.REVERSED
        )
        self.assertEqual(
            set(mapping.adjustments.values_list("status", flat=True)),
            {SaleMiniOrderAdjustment.Status.REVERSED},
        )
        self.assertEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))

    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_wechat_refund_callback_marks_refunded(self, mock_verify, mock_decrypt):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDING
        mapping.save(update_fields=["payment_status"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-REFUND-CB",
            out_trade_no="SMT-REFUND-CB",
            status=SaleMiniPayment.Status.REFUNDING,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        refund = SaleMiniRefund.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            payment=payment,
            refund_no="SMR-CB",
            out_refund_no="SMRF-CB",
            status=SaleMiniRefund.Status.PROCESSING,
            amount=Decimal("19.00"),
            amount_cents=1900,
            total_amount_cents=1900,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "out_trade_no": payment.out_trade_no,
            "out_refund_no": refund.out_refund_no,
            "refund_id": "wx-refund-cb",
            "refund_status": "SUCCESS",
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/refund-callback/",
            {"id": "evt-refund-1", "event_type": "REFUND.SUCCESS", "resource": {}},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        payment.refresh_from_db()
        refund.refresh_from_db()
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.REFUNDED
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.REFUNDED)
        self.assertEqual(refund.status, SaleMiniRefund.Status.SUCCESS)

    def test_expire_unpaid_wechat_order_releases_inventory(self):
        coupon = self._create_coupon(discount="4.00")
        self._earn_points(500)
        order_response = self._create_sale_mini_order(
            payment_method="WECHAT",
            extra={"coupon_id": coupon.id, "points": 100},
        )
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.pay_deadline_at = timezone.now() - timedelta(minutes=1)
        mapping.save(update_fields=["pay_deadline_at"])

        out = StringIO()
        call_command("expire_sale_mini_orders", stdout=out)

        mapping.refresh_from_db()
        coupon.refresh_from_db()
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertIn("expired=1", out.getvalue())
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.CANCELLED
        )
        self.assertEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertEqual(coupon.status, SaleMiniCoupon.Status.AVAILABLE)
        self.assertEqual(point_balance(self.owner, self.customer, self.buyer), (500, 0))
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))
