from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from allapp.baseinfo.models import Customer, Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound import services as outbound_services
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import (
    Brand,
    Product,
    ProductCategory,
    ProductPackage,
    ProductUom,
)
from allapp.tasking.models import WmsTask, WmsTaskLine

from .models import (
    MiniCustomerAddress,
    MiniProgramUser,
    Promotion,
    PromotionDiscountStep,
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
    SaleMiniProductReview,
    SaleMiniProductReviewImage,
    SaleMiniRefund,
    SaleProductConfig,
)
from .services_salemini_adjustments import (
    confirm_adjustments,
    confirm_distribution,
    point_balance,
)
from .services_salemini_payments import get_or_create_full_refund
from .salemini_api import _prepare_wechat_prepay

User = get_user_model()


class SaleMiniCatalogBootstrapCommandTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="SMBC", name="Sale Mini Bootstrap")
        self.uom = ProductUom.objects.create(code="SMBC-EA", name="件")
        self.category = ProductCategory.objects.create(
            code="SMBC-CAT",
            name="Bootstrap Category",
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="SMBC-P1",
            sku="SMBC-P1",
            name="Bootstrap Product",
            category=self.category,
            base_uom=self.uom,
            price=Decimal("6.50"),
            is_active=True,
        )

    def test_bootstrap_catalog_dry_run_reports_missing_without_creating_config(self):
        out = StringIO()

        call_command("bootstrap_sale_mini_catalog", stdout=out)

        self.assertIn("missing_configs=1", out.getvalue())
        self.assertFalse(
            SaleProductConfig.objects.filter(product=self.product).exists()
        )

    def test_bootstrap_catalog_requires_owner_code_when_listing_and_creates_config(
        self,
    ):
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_sale_mini_catalog", "--apply", "--listed", stdout=StringIO()
            )

        out = StringIO()
        call_command(
            "bootstrap_sale_mini_catalog",
            "--owner-code",
            self.owner.code,
            "--apply",
            "--listed",
            stdout=out,
        )

        config = SaleProductConfig.objects.get(product=self.product)
        self.assertEqual(config.owner_id, self.owner.id)
        self.assertEqual(config.sale_price, Decimal("6.50"))
        self.assertTrue(config.is_listed)
        self.assertIn("created=1 listed=true", out.getvalue())


@override_settings(
    WECHAT_MINI_APPID="wx-test-app",
    WECHAT_PAY_MCH_ID="1900000001",
    WECHAT_PAY_NOTIFY_URL="https://pay.example.test/callback/",
    WECHAT_PAY_REFUND_NOTIFY_URL="https://pay.example.test/refund-callback/",
)
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
        self.category = ProductCategory.objects.create(
            code="MINI-DEFAULT-CAT", name="默认大类"
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="MP001",
            sku="MP001",
            name="小程序上架商品",
            category=self.category,
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
            category=self.category,
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

    def _completed_order_line(self):
        response = self._create_sale_mini_order()
        order = OutboundOrder.objects.get(pk=response.data["id"])
        order.is_closed = True
        order.close_reason = "测试订单已完成"
        order.save(update_fields=["is_closed", "close_reason", "updated_at"])
        mapping = SaleMiniOrderMapping.objects.get(outbound_order=order)
        return mapping, order.lines.get()

    def _review_draft(self, line, **overrides):
        payload = {
            "order_line_id": line.id,
            "quality_score": 5,
            "delivery_score": 4,
            "overall_score": 5,
            "content": "商品质量很好，配送及时。",
            "is_anonymous": True,
        }
        payload.update(overrides)
        return self.client.post(
            "/api/sale-mini/reviews/drafts/", payload, format="json"
        )

    def _review_image(self, name="review.png"):
        content = BytesIO()
        Image.new("RGB", (24, 18), color=(22, 119, 255)).save(content, format="PNG")
        return SimpleUploadedFile(name, content.getvalue(), content_type="image/png")

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
            category=self.category,
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

    def test_review_requires_completed_owned_purchase_and_valid_payment(self):
        order_response = self._create_sale_mini_order()
        order = OutboundOrder.objects.get(pk=order_response.data["id"])
        line = order.lines.get()

        not_completed = self._review_draft(line)
        self.assertEqual(not_completed.status_code, 400)
        self.assertIn("订单完成后", str(not_completed.data))

        order.is_closed = True
        order.close_reason = "测试订单已完成"
        order.save(update_fields=["is_closed", "close_reason", "updated_at"])
        mapping = SaleMiniOrderMapping.objects.get(outbound_order=order)
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDED
        mapping.save(update_fields=["payment_status", "updated_at"])
        refunded = self._review_draft(line)
        self.assertEqual(refunded.status_code, 400)
        self.assertIn("付款状态", str(refunded.data))

        other_user = User.objects.create_user(
            username="review-other",
            password="pw",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        MiniProgramUser.objects.create(
            owner=self.owner,
            user=other_user,
            customer=self.customer,
            nickname="其他买家",
        )
        self.client.force_authenticate(other_user)
        forbidden = self._review_draft(line)
        self.assertEqual(forbidden.status_code, 403)

    def test_review_draft_is_idempotent_and_order_payload_tracks_status(self):
        mapping, line = self._completed_order_line()
        first = self._review_draft(line)
        second = self._review_draft(line, content="更新后的评价")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(
            SaleMiniProductReview.objects.filter(order_line=line).count(), 1
        )
        detail = self.client.get(f"/api/sale-mini/orders/{mapping.outbound_order_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.data["lines"][0]["review"]["eligible"])
        self.assertEqual(detail.data["lines"][0]["review"]["status"], "DRAFT")

    def test_pending_review_is_private_until_published_and_can_be_hidden(self):
        mapping, line = self._completed_order_line()
        draft = self._review_draft(line, is_anonymous=False)
        submit = self.client.post(
            f"/api/sale-mini/reviews/{draft.data['id']}/submit/", {}, format="json"
        )
        config = SaleProductConfig.objects.get(product=self.product)

        self.assertEqual(submit.status_code, 200)
        self.assertEqual(submit.data["status"], "PENDING")
        hidden_list = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/reviews/",
            {"config_id": config.id},
        )
        self.assertEqual(hidden_list.status_code, 200)
        self.assertEqual(hidden_list.data["count"], 0)

        review = SaleMiniProductReview.objects.get(pk=draft.data["id"])
        review.status = SaleMiniProductReview.Status.PUBLISHED
        review.published_at = timezone.now()
        review.save(update_fields=["status", "published_at", "updated_at"])
        public_list = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/reviews/",
            {"config_id": config.id},
        )
        product_detail = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/",
            {"config_id": config.id},
        )
        self.assertEqual(public_list.data["count"], 1)
        self.assertEqual(public_list.data["results"][0]["display_name"], "采购员")
        self.assertNotIn("buyer_user", public_list.data["results"][0])
        self.assertNotIn("order_line_id", public_list.data["results"][0])
        self.assertEqual(product_detail.data["review_summary"]["count"], 1)
        self.assertEqual(product_detail.data["review_preview"]["id"], review.id)

        review.status = SaleMiniProductReview.Status.HIDDEN
        review.save(update_fields=["status", "updated_at"])
        hidden_again = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/reviews/",
            {"config_id": config.id},
        )
        self.assertEqual(hidden_again.data["count"], 0)

    def test_review_summary_averages_filters_and_anonymous_privacy_are_exact(self):
        config = SaleProductConfig.objects.get(product=self.product)
        published = []
        for score, anonymous in ((5, True), (3, False)):
            _mapping, line = self._completed_order_line()
            draft = self._review_draft(
                line,
                quality_score=score,
                delivery_score=score,
                overall_score=score,
                is_anonymous=anonymous,
            )
            row = SaleMiniProductReview.objects.get(pk=draft.data["id"])
            row.status = SaleMiniProductReview.Status.PUBLISHED
            row.published_at = timezone.now()
            row.save(update_fields=["status", "published_at", "updated_at"])
            published.append(row)

        response = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/reviews/",
            {"config_id": config.id},
        )
        five_star = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/reviews/",
            {"config_id": config.id, "score": 5},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["count"], 2)
        self.assertEqual(response.data["summary"]["average_overall"], "4.0")
        self.assertEqual(response.data["summary"]["score_counts"]["5"], 1)
        self.assertEqual(response.data["summary"]["score_counts"]["3"], 1)
        self.assertEqual(five_star.data["count"], 1)
        anonymous = next(row for row in response.data["results"] if row["is_anonymous"])
        self.assertEqual(anonymous["display_name"], "匿名用户")
        self.assertEqual(anonymous["avatar_url"], "")

    @override_settings(MEDIA_ROOT="/tmp/wms-sale-mini-review-tests")
    def test_review_image_upload_validates_content_limit_and_owner(self):
        _mapping, line = self._completed_order_line()
        draft = self._review_draft(line)
        review_id = draft.data["id"]

        invalid = self.client.post(
            f"/api/sale-mini/reviews/{review_id}/images/",
            {"image": SimpleUploadedFile("bad.jpg", b"not-an-image", "image/jpeg")},
            format="multipart",
        )
        self.assertEqual(invalid.status_code, 400)

        for index in range(6):
            upload = self.client.post(
                f"/api/sale-mini/reviews/{review_id}/images/",
                {"image": self._review_image(f"review-{index}.png")},
                format="multipart",
            )
            self.assertEqual(upload.status_code, 201)
        self.assertEqual(
            SaleMiniProductReviewImage.objects.filter(review_id=review_id).count(), 6
        )
        seventh = self.client.post(
            f"/api/sale-mini/reviews/{review_id}/images/",
            {"image": self._review_image("seventh.png")},
            format="multipart",
        )
        self.assertEqual(seventh.status_code, 400)

        image_id = (
            SaleMiniProductReviewImage.objects.filter(review_id=review_id).first().id
        )
        removed = self.client.delete(
            f"/api/sale-mini/reviews/{review_id}/images/{image_id}/"
        )
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(
            SaleMiniProductReviewImage.objects.filter(review_id=review_id).count(), 5
        )

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
        self.assertEqual(rows[0]["stock"]["display"], "10.000 件")
        self.assertEqual(rows[0]["stock"]["base_uom_name"], "件")
        self.assertEqual(rows[0]["order_uom"], "EA-MINI")
        self.assertEqual(rows[0]["order_uom_name"], "件")
        self.assertEqual(code_response.data["results"], [])

    def test_quantity_rules_are_hidden_and_not_enforced_by_default(self):
        config = SaleProductConfig.objects.get(
            owner=self.owner,
            product=self.product,
        )
        config.min_order_qty = Decimal("2.000")
        config.multiple_qty = Decimal("3.000")
        config.enable_qty_rules = False
        config.save(
            update_fields=[
                "enable_qty_rules",
                "min_order_qty",
                "multiple_qty",
                "updated_at",
            ]
        )

        detail = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/",
            {"config_id": config.id},
        )
        preview = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "0.500",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )
        cart = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "config_id": config.id,
                "product_id": self.product.id,
                "qty": "0.500",
                "order_uom": "EA-MINI",
            },
            format="json",
        )

        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.data["rules"]["enabled"])
        self.assertEqual(detail.data["rules"]["min_order_qty"], "1.000")
        self.assertEqual(detail.data["rules"]["multiple_qty"], "1.000")
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["ok"])
        self.assertEqual(cart.status_code, 200)
        self.assertFalse(cart.data["lines"][0]["rules"]["enabled"])
        self.assertEqual(cart.data["lines"][0]["order_uom_name"], "件")

    def test_enabled_quantity_rules_match_frontend_increment_sequence(self):
        config = SaleProductConfig.objects.get(
            owner=self.owner,
            product=self.product,
        )
        config.enable_qty_rules = True
        config.min_order_qty = Decimal("2.000")
        config.multiple_qty = Decimal("3.000")
        config.save(
            update_fields=[
                "enable_qty_rules",
                "min_order_qty",
                "multiple_qty",
                "updated_at",
            ]
        )

        detail = self.client.get(
            f"/api/sale-mini/products/{self.product.id}/",
            {"config_id": config.id},
        )
        valid = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "5.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )
        invalid = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "3.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )
        cart = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "config_id": config.id,
                "product_id": self.product.id,
                "qty": "5.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        order = self.client.post(
            "/api/sale-mini/orders/",
            {
                "payment_method": "OFFLINE",
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": self.product.id,
                        "qty": "5.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["rules"]["enabled"])
        self.assertEqual(detail.data["rules"]["min_order_qty"], "2.000")
        self.assertEqual(detail.data["rules"]["multiple_qty"], "3.000")
        self.assertTrue(valid.data["ok"])
        self.assertTrue(valid.data["lines"][0]["rules"]["enabled"])
        self.assertFalse(invalid.data["ok"])
        self.assertIn("递增", invalid.data["lines"][0]["message"])
        self.assertEqual(cart.status_code, 200)
        self.assertTrue(cart.data["lines"][0]["rules"]["enabled"])
        self.assertEqual(order.status_code, 201)
        self.assertEqual(order.data["lines"][0]["order_uom_name"], "件")
        order_line = OutboundOrderLine.objects.get(order_id=order.data["id"])
        self.assertEqual(order_line.base_qty, Decimal("5.000"))
        inventory = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(inventory.allocated_qty, Decimal("5.0000"))
        self.assertEqual(inventory.available_qty, Decimal("5.0000"))

    def test_saleable_stock_counts_incomplete_tracking_fields_and_allocates(self):
        tracked_product = Product.objects.create(
            owner=self.owner,
            code="MP-TRACK",
            sku="MP-TRACK",
            name="批次效期商品",
            category=self.category,
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
        incomplete_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("2.0000"),
            locked_qty=Decimal("1.0000"),
            damaged_qty=Decimal("1.0000"),
            base_unit=self.uom.code,
        )
        complete_location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWSMI-01-01-02",
            name="Sale Mini Complete Tracking Location",
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=complete_location,
            batch_no="LOT-202606",
            production_date=date(2026, 6, 1),
            expiry_date=date(2027, 6, 1),
            onhand_qty=Decimal("2.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        inactive_location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWSMI-01-01-03",
            name="Sale Mini Inactive Inventory Location",
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=inactive_location,
            onhand_qty=Decimal("9.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
            is_active=False,
        )
        public_client = APIClient()

        response = public_client.get(
            "/api/sale-mini/products/",
            {"search": "批次效期"},
        )
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock"]["available_qty"], "8.000")
        self.assertEqual(rows[0]["stock"]["status"], "IN")

        detail_response = public_client.get(
            f"/api/sale-mini/products/{tracked_product.id}/"
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["stock"]["available_qty"], "8.000")

        stocked_response = public_client.get(
            "/api/sale-mini/products/",
            {"search": "批次效期", "only_stock": "1"},
        )
        self.assertEqual(stocked_response.status_code, 200)
        self.assertEqual(len(stocked_response.data["results"]), 1)
        self.assertEqual(
            stocked_response.data["results"][0]["stock"]["available_qty"],
            "8.000",
        )

        preview = self.client.post(
            "/api/sale-mini/orders/preview/",
            {
                "lines": [
                    {
                        "product_id": tracked_product.id,
                        "qty": "4.000",
                        "order_uom": "EA-MINI",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["ok"])
        self.assertEqual(preview.data["lines"][0]["available_qty"], "8.000")

        order_response = self.client.post(
            "/api/sale-mini/orders/",
            {
                "payment_method": "OFFLINE",
                "contact": "张三",
                "contact_phone": "13800000000",
                "ship_to": "上海市测试路 1 号",
                "delivery_method": "OWN_TRUCK",
                "lines": [
                    {
                        "product_id": tracked_product.id,
                        "qty": "4.000",
                        "order_uom": "EA-MINI",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(order_response.status_code, 201)
        incomplete_detail.refresh_from_db()
        self.assertEqual(incomplete_detail.onhand_qty, Decimal("10.0000"))
        self.assertEqual(incomplete_detail.allocated_qty, Decimal("6.0000"))
        self.assertEqual(incomplete_detail.locked_qty, Decimal("1.0000"))
        self.assertEqual(incomplete_detail.damaged_qty, Decimal("1.0000"))
        self.assertEqual(incomplete_detail.available_qty, Decimal("2.0000"))
        self.assertEqual(
            incomplete_detail.onhand_qty,
            incomplete_detail.allocated_qty
            + incomplete_detail.locked_qty
            + incomplete_detail.damaged_qty
            + incomplete_detail.available_qty,
        )
        pick_line = WmsTaskLine.objects.get(
            task__task_type=WmsTask.TaskType.PICK,
            product=tracked_product,
        )
        self.assertEqual(pick_line.qty_plan, Decimal("4.0000"))
        self.assertEqual(
            pick_line.plan_meta["inventory_detail_id"], incomplete_detail.id
        )

    def test_saleable_stock_counts_serial_controlled_inventory_without_serial(self):
        serial_product = Product.objects.create(
            owner=self.owner,
            code="MP-SERIAL-GAP",
            sku="MP-SERIAL-GAP",
            name="缺序列号商品",
            category=self.category,
            base_uom=self.uom,
            price=Decimal("28.00"),
            batch_control=False,
            expiry_control=False,
            serial_control=True,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=serial_product,
            is_listed=True,
            sale_price=Decimal("28.0000"),
            min_order_qty=Decimal("1.000"),
            multiple_qty=Decimal("1.000"),
            stock_display=SaleProductConfig.StockDisplay.EXACT,
        )
        serial_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=serial_product,
            warehouse=self.warehouse,
            location=self.location,
            serial_no="SERIAL-GAP-1",
            onhand_qty=Decimal("1.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        InventoryDetail.objects.filter(pk=serial_detail.pk).update(
            serial_no="",
            serial_no_norm=None,
        )

        response = APIClient().get(
            "/api/sale-mini/products/",
            {"search": "缺序列号", "only_stock": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["stock"]["available_qty"], "1.000")

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
            category=self.category,
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
            {row["id"] for row in home.data["categories"]},
            {self.category.id, category.id},
        )
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
        category_rows = {row["id"]: row for row in categories.data}
        self.assertEqual(category_rows[category.id]["product_count"], 2)
        self.assertEqual(category_rows[self.category.id]["product_count"], 0)
        self.assertPublicTaxonomyPayloadHidesInternalFields(category_rows[category.id])

    def test_public_product_tags_are_strict_and_paginated(self):
        base_config = SaleProductConfig.objects.get(
            owner=self.owner,
            product=self.product,
        )
        base_config.is_hot = True
        base_config.save(update_fields=["is_hot", "updated_at"])

        def create_tagged_product(code, **flags):
            product = Product.objects.create(
                owner=self.owner,
                code=code,
                sku=code,
                name=f"标签商品 {code}",
                category=self.category,
                base_uom=self.uom,
                price=Decimal("8.00"),
                expiry_control=False,
                batch_control=False,
                is_active=True,
            )
            SaleProductConfig.objects.create(
                owner=self.owner,
                product=product,
                is_listed=True,
                sale_price=Decimal("8.0000"),
                **flags,
            )
            return product

        second_hot = create_tagged_product("MP-HOT-2", is_hot=True)
        new_product = create_tagged_product("MP-NEW-1", is_new=True)
        recommended_product = create_tagged_product("MP-REC-1", is_recommended=True)
        public_client = APIClient()

        hot = public_client.get(
            "/api/sale-mini/products/", {"tag": "hot", "page_size": 1}
        )
        new = public_client.get("/api/sale-mini/products/", {"tag": "new"})
        recommended = public_client.get(
            "/api/sale-mini/products/", {"tag": "recommended"}
        )
        invalid = public_client.get(
            "/api/sale-mini/products/", {"tag": "not-supported"}
        )

        self.assertEqual(hot.status_code, 200)
        self.assertEqual(hot.data["count"], 2)
        self.assertEqual(len(hot.data["results"]), 1)
        self.assertTrue(hot.data["next"])
        self.assertIn(
            hot.data["results"][0]["id"],
            {self.product.id, second_hot.id},
        )
        self.assertEqual(new.status_code, 200)
        self.assertEqual({row["id"] for row in new.data["results"]}, {new_product.id})
        self.assertEqual(recommended.status_code, 200)
        self.assertEqual(
            {row["id"] for row in recommended.data["results"]},
            {recommended_product.id},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_public_home_returns_every_listed_root_category(self):
        expected_ids = {self.category.id}
        for index in range(13):
            category = ProductCategory.objects.create(
                code=f"MINI-HOME-{index:02d}",
                name=f"首页大类 {index:02d}",
                sort_order=index + 1,
            )
            product = Product.objects.create(
                owner=self.owner,
                code=f"MP-HOME-{index:02d}",
                sku=f"MP-HOME-{index:02d}",
                name=f"首页分类商品 {index:02d}",
                category=category,
                base_uom=self.uom,
                price=Decimal("6.00"),
                expiry_control=False,
                batch_control=False,
                is_active=True,
            )
            SaleProductConfig.objects.create(
                owner=self.owner,
                product=product,
                is_listed=True,
                sale_price=Decimal("6.0000"),
            )
            expected_ids.add(category.id)

        response = APIClient().get("/api/sale-mini/home/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["id"] for row in response.data["categories"]}, expected_ids
        )

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

    def test_public_category_tree_aggregates_subtrees_and_filters_descendants(self):
        middle = ProductCategory.objects.create(
            code="MINI-FRUIT", name="水果", parent=self.category, sort_order=1
        )
        small = ProductCategory.objects.create(
            code="MINI-BERRY", name="莓果", parent=middle, sort_order=1
        )
        self.product.category = small
        self.product.save(update_fields=["category", "updated_at"])
        _owner, _customer, _buyer, other_product = (
            self._create_other_owner_sale_binding()
        )
        other_product.category = middle
        other_product.save(update_fields=["category", "updated_at"])
        public_client = APIClient()

        categories = public_client.get("/api/sale-mini/categories/")
        root_products = public_client.get(
            "/api/sale-mini/products/", {"category_id": self.category.id}
        )
        small_products = public_client.get(
            "/api/sale-mini/products/", {"category_id": small.id}
        )

        self.assertEqual(categories.status_code, 200)
        by_id = {row["id"]: row for row in categories.data}
        self.assertEqual(by_id[self.category.id]["product_count"], 2)
        self.assertEqual(by_id[middle.id]["product_count"], 2)
        self.assertEqual(by_id[small.id]["product_count"], 1)
        self.assertEqual(by_id[small.id]["level_name"], "小类")
        self.assertEqual(by_id[small.id]["path"], "默认大类 > 水果 > 莓果")
        self.assertTrue(by_id[self.category.id]["has_children"])
        self.assertEqual(
            {row["id"] for row in root_products.data["results"]},
            {self.product.id, other_product.id},
        )
        self.assertEqual(
            {row["id"] for row in small_products.data["results"]},
            {self.product.id},
        )

    def test_listed_product_requires_an_active_category_path(self):
        uncategorized = Product.objects.create(
            owner=self.owner,
            code="MP-NO-CAT",
            name="未分类待治理商品",
            base_uom=self.uom,
            price=Decimal("5.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        config = SaleProductConfig(
            owner=self.owner,
            product=uncategorized,
            is_listed=True,
            sale_price=Decimal("5.00"),
        )
        with self.assertRaises(DjangoValidationError):
            config.full_clean()

        # Legacy/direct writes are still prevented from leaking through public APIs.
        config.save()
        response = APIClient().get("/api/sale-mini/products/")
        self.assertNotIn(
            uncategorized.id,
            {row["id"] for row in response.data["results"]},
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

        cancel = self.client.post(
            f"/api/sale-mini/orders/{response.data['id']}/cancel/"
        )
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
            category=self.category,
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

    def test_checkout_clears_selected_cart_item_and_keeps_unselected_item(self):
        remaining_product = Product.objects.create(
            owner=self.owner,
            code="MP002",
            sku="MP002",
            name="购物车未选商品",
            category=self.category,
            base_uom=self.uom,
            price=Decimal("8.00"),
            expiry_control=False,
            batch_control=False,
            is_active=True,
        )
        SaleProductConfig.objects.create(
            owner=self.owner,
            product=remaining_product,
            is_listed=True,
            sale_price=Decimal("8.0000"),
            stock_display=SaleProductConfig.StockDisplay.EXACT,
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=remaining_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        first_add = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": self.product.id,
                "qty": "2.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        second_add = self.client.post(
            "/api/sale-mini/cart/add/",
            {
                "product_id": remaining_product.id,
                "qty": "3.000",
                "order_uom": "EA-MINI",
            },
            format="json",
        )
        self.assertEqual(first_add.status_code, 200)
        self.assertEqual(second_add.status_code, 200)
        cart_id = first_add.data["id"]
        self.assertEqual(SaleMiniCartItem.objects.filter(cart_id=cart_id).count(), 2)

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
        self.assertFalse(
            SaleMiniCartItem.objects.filter(
                cart_id=cart_id,
                product=self.product,
            ).exists()
        )
        remaining_item = SaleMiniCartItem.objects.get(
            cart_id=cart_id,
            product=remaining_product,
        )
        self.assertEqual(remaining_item.qty, Decimal("3.000"))

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

    def test_zero_payable_order_uses_internal_settlement_and_zero_refund(self):
        coupon = self._create_coupon(discount="19.00")
        order_response = self._create_sale_mini_order(
            payment_method="WECHAT",
            extra={"coupon_id": coupon.id},
        )
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        payment = SaleMiniPayment.objects.get(mapping=mapping)

        self.assertEqual(order_response.data["payable_amount"], "0.00")
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.PAID
        )
        self.assertEqual(payment.channel, SaleMiniPayment.Channel.INTERNAL_ZERO)
        self.assertEqual(payment.amount, Decimal("0.00"))
        self.assertEqual(payment.amount_cents, 0)
        self.assertIsNone(payment.transaction_id)

        prepay = self.client.post(
            "/api/sale-mini/payments/wechat/prepay/",
            {"order_id": order_response.data["id"]},
            format="json",
        )
        self.assertEqual(prepay.status_code, 200)
        self.assertTrue(prepay.data["paid"])
        self.assertIsNone(prepay.data["pay_params"])
        self.assertEqual(prepay.data["settlement_channel"], "INTERNAL_ZERO")

        refunded = self.client.post(
            "/api/sale-mini/payments/wechat/refund/",
            {"order_id": order_response.data["id"], "reason": "零元退款测试"},
            format="json",
        )
        self.assertEqual(refunded.status_code, 200)
        mapping.refresh_from_db()
        payment.refresh_from_db()
        coupon.refresh_from_db()
        refund = SaleMiniRefund.objects.get(payment=payment)
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.REFUNDED
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.REFUNDED)
        self.assertEqual(refund.status, SaleMiniRefund.Status.SUCCESS)
        self.assertEqual(refund.amount_cents, 0)
        self.assertEqual(coupon.status, SaleMiniCoupon.Status.AVAILABLE)
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))

    def test_unpaid_sale_mini_order_cannot_release_pick_task(self):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        order = mapping.outbound_order
        order.approval_status = "WHS_APPROVED"
        order.save(update_fields=["approval_status", "updated_at"])

        with self.assertRaises(DjangoValidationError):
            outbound_services.promote_reserved_pick(order, by_user=self.user)

        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model=order._meta.model_name,
            source_pk=str(order.pk),
        )
        self.assertEqual(task.status, WmsTask.Status.RESERVED)

        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
        mapping.save(update_fields=["payment_status", "updated_at"])
        released = outbound_services.promote_reserved_pick(order, by_user=self.user)
        self.assertEqual(released.status, WmsTask.Status.RELEASED)

    @patch("allapp.salesapp.services_salemini_payments.query_jsapi_payment")
    def test_payment_query_endpoint_confirms_success(self, mock_query):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-QUERY",
            out_trade_no="SMT-QUERY",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_query.return_value = {
            "out_trade_no": payment.out_trade_no,
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "transaction_id": "wx-query-success",
            "trade_state": "SUCCESS",
            "amount": {"total": 1900, "currency": "CNY"},
        }

        response = self.client.post(
            "/api/sale-mini/payments/wechat/query/",
            {"order_id": order_response.data["id"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["confirmed"])
        self.assertEqual(response.data["payment_status"], "PAID")
        mapping.refresh_from_db()
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.PAID
        )

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
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "transaction_id": "wx-transaction-1",
            "trade_state": "SUCCESS",
            "amount": {"total": 1900, "currency": "CNY"},
        }
        payload = {
            "id": "evt-pay-1",
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {"algorithm": "AEAD_AES_256_GCM"},
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

    @patch(
        "allapp.salesapp.services_salemini_payments.confirm_adjustments",
        side_effect=RuntimeError("adjustment confirmation failed"),
    )
    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_payment_callback_rolls_back_all_business_state_on_failure(
        self,
        mock_verify,
        mock_decrypt,
        mock_confirm,
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-CALLBACK-ROLLBACK",
            out_trade_no="SMT-CALLBACK-ROLLBACK",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "out_trade_no": payment.out_trade_no,
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "transaction_id": "wx-rollback",
            "trade_state": "SUCCESS",
            "amount": {"total": 1900, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            {
                "id": "evt-pay-rollback",
                "event_type": "TRANSACTION.SUCCESS",
                "resource": {"algorithm": "AEAD_AES_256_GCM"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        payment.refresh_from_db()
        mapping.refresh_from_db()
        event = SaleMiniPaymentEvent.objects.get(event_id="evt-pay-rollback")
        self.assertEqual(payment.status, SaleMiniPayment.Status.PREPAY)
        self.assertIsNone(payment.transaction_id)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.UNPAID
        )
        self.assertEqual(
            event.process_status, SaleMiniPaymentEvent.ProcessStatus.FAILED
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
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "transaction_id": "wx-transaction-adj",
            "trade_state": "SUCCESS",
            "amount": {"total": 1400, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            {
                "id": "evt-pay-adj",
                "event_type": "TRANSACTION.SUCCESS",
                "resource": {"algorithm": "AEAD_AES_256_GCM"},
            },
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
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "transaction_id": "wx-transaction-2",
            "trade_state": "SUCCESS",
            "amount": {"total": 1800, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/callback/",
            {
                "id": "evt-pay-bad",
                "event_type": "TRANSACTION.SUCCESS",
                "resource": {"algorithm": "AEAD_AES_256_GCM"},
            },
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

    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_late_payment_queues_one_refund_without_restoring_inventory(
        self, mock_verify, mock_decrypt
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        cancelled = self.client.post(
            f"/api/sale-mini/orders/{order_response.data['id']}/cancel/"
        )
        self.assertEqual(cancelled.status_code, 200)
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-LATE-1",
            out_trade_no="SMT-LATE-1",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "out_trade_no": payment.out_trade_no,
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "transaction_id": "wx-late-1",
            "trade_state": "SUCCESS",
            "amount": {"total": 1900, "currency": "CNY"},
        }
        anonymous = APIClient()
        for event_id in ["evt-late-1", "evt-late-2"]:
            response = anonymous.post(
                "/api/sale-mini/payments/wechat/callback/",
                {
                    "id": event_id,
                    "event_type": "TRANSACTION.SUCCESS",
                    "resource": {"algorithm": "AEAD_AES_256_GCM"},
                },
                format="json",
            )
            self.assertEqual(response.status_code, 200)

        mapping.refresh_from_db()
        payment.refresh_from_db()
        detail = InventoryDetail.objects.get(product=self.product)
        refund = SaleMiniRefund.objects.get(payment=payment)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.REFUNDING
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.REFUNDING)
        self.assertEqual(refund.source, SaleMiniRefund.Source.LATE_PAYMENT)
        self.assertEqual(SaleMiniRefund.objects.filter(payment=payment).count(), 1)
        self.assertEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))

    @patch("allapp.salesapp.services_salemini_payments.request_refund")
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
        mock_refund.side_effect = lambda refund, **_kwargs: (
            {"out_refund_no": refund.out_refund_no},
            {
                "out_refund_no": refund.out_refund_no,
                "refund_id": "wx-refund-1",
                "status": "PROCESSING",
                "amount": {"refund": 1900, "total": 1900, "currency": "CNY"},
            },
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
    @patch("allapp.salesapp.services_salemini_payments.request_refund")
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
        mock_refund.side_effect = lambda refund, **_kwargs: (
            {"out_refund_no": refund.out_refund_no},
            {
                "out_refund_no": refund.out_refund_no,
                "refund_id": "wx-refund-success",
                "status": "SUCCESS",
                "amount": {"refund": 1400, "total": 1400, "currency": "CNY"},
            },
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
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "out_trade_no": payment.out_trade_no,
            "out_refund_no": refund.out_refund_no,
            "refund_id": "wx-refund-cb",
            "refund_status": "SUCCESS",
            "amount": {"refund": 1900, "total": 1900, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/refund-callback/",
            {
                "id": "evt-refund-1",
                "event_type": "REFUND.SUCCESS",
                "resource": {"algorithm": "AEAD_AES_256_GCM"},
            },
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

    @patch("allapp.salesapp.salemini_api.decrypt_resource")
    @patch("allapp.salesapp.salemini_api.verify_callback_signature")
    def test_abnormal_refund_keeps_order_cancelled_and_requires_manual_action(
        self, mock_verify, mock_decrypt
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDING
        mapping.outbound_order.approval_status = "CANCELLED"
        mapping.outbound_order.save(update_fields=["approval_status", "updated_at"])
        mapping.save(update_fields=["payment_status"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-REFUND-ABNORMAL",
            out_trade_no="SMT-REFUND-ABNORMAL",
            status=SaleMiniPayment.Status.REFUNDING,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        refund = SaleMiniRefund.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            payment=payment,
            refund_no="SMR-ABNORMAL",
            out_refund_no="SMRF-ABNORMAL",
            status=SaleMiniRefund.Status.PROCESSING,
            amount=Decimal("19.00"),
            amount_cents=1900,
            total_amount_cents=1900,
        )
        mock_verify.return_value = True
        mock_decrypt.return_value = {
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "out_trade_no": payment.out_trade_no,
            "out_refund_no": refund.out_refund_no,
            "refund_id": "wx-refund-abnormal",
            "refund_status": "ABNORMAL",
            "amount": {"refund": 1900, "total": 1900, "currency": "CNY"},
        }

        response = APIClient().post(
            "/api/sale-mini/payments/wechat/refund-callback/",
            {
                "id": "evt-refund-abnormal",
                "event_type": "REFUND.ABNORMAL",
                "resource": {"algorithm": "AEAD_AES_256_GCM"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        mapping.refresh_from_db()
        payment.refresh_from_db()
        refund.refresh_from_db()
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.REFUNDING
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.REFUNDING)
        self.assertEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertTrue(refund.requires_manual_action)
        self.assertEqual(refund.status, SaleMiniRefund.Status.ABNORMAL)

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

    @patch("allapp.salesapp.services_salemini_payments.query_jsapi_payment")
    def test_expire_userpaying_order_keeps_inventory_reserved(self, mock_query):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.pay_deadline_at = timezone.now() - timedelta(minutes=1)
        mapping.save(update_fields=["pay_deadline_at"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-USERPAYING",
            out_trade_no="SMT-USERPAYING",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_query.return_value = {
            "out_trade_no": payment.out_trade_no,
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "trade_state": "USERPAYING",
            "amount": {"total": 1900, "currency": "CNY"},
        }

        call_command("expire_sale_mini_orders", stdout=StringIO())

        mapping.refresh_from_db()
        detail = InventoryDetail.objects.get(product=self.product)
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.UNPAID
        )
        self.assertNotEqual(mapping.outbound_order.approval_status, "CANCELLED")
        self.assertEqual(detail.allocated_qty, Decimal("2.0000"))
        self.assertEqual(detail.available_qty, Decimal("8.0000"))

    @patch("allapp.salesapp.services_salemini_payments.close_jsapi_payment")
    @patch("allapp.salesapp.services_salemini_payments.query_jsapi_payment")
    def test_expire_notpay_order_closes_wechat_before_releasing_inventory(
        self, mock_query, mock_close
    ):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.pay_deadline_at = timezone.now() - timedelta(minutes=1)
        mapping.save(update_fields=["pay_deadline_at"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-NOTPAY",
            out_trade_no="SMT-NOTPAY",
            status=SaleMiniPayment.Status.PREPAY,
            amount=Decimal("19.00"),
            amount_cents=1900,
        )
        mock_query.return_value = {
            "out_trade_no": payment.out_trade_no,
            "appid": settings.WECHAT_MINI_APPID,
            "mchid": settings.WECHAT_PAY_MCH_ID,
            "trade_state": "NOTPAY",
            "amount": {"total": 1900, "currency": "CNY"},
        }
        mock_close.return_value = {}

        call_command("expire_sale_mini_orders", stdout=StringIO())

        mapping.refresh_from_db()
        payment.refresh_from_db()
        detail = InventoryDetail.objects.get(product=self.product)
        mock_close.assert_called_once()
        self.assertEqual(
            mapping.payment_status, SaleMiniOrderMapping.PaymentStatus.CANCELLED
        )
        self.assertEqual(payment.status, SaleMiniPayment.Status.CLOSED)
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(detail.available_qty, Decimal("10.0000"))


@override_settings(
    WECHAT_MINI_APPID="wx-test-app",
    WECHAT_PAY_MCH_ID="1900000001",
    WECHAT_PAY_NOTIFY_URL="https://pay.example.test/callback/",
    WECHAT_PAY_REFUND_NOTIFY_URL="https://pay.example.test/refund-callback/",
)
class SaleMiniPaymentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    setUp = SaleMiniApiTests.setUp
    _create_sale_mini_order = SaleMiniApiTests._create_sale_mini_order

    def _run_concurrently(self, callback):
        if connection.vendor != "mysql":
            self.skipTest("Payment concurrency guarantees are verified on MySQL.")
        barrier = Barrier(2)

        def runner():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return callback()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(lambda _index: runner(), range(2)))

    def test_concurrent_prepay_creates_one_effective_payment_intent(self):
        self.buyer.openid = "wx-concurrent-prepay"
        self.buyer.save(update_fields=["openid"])
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping_id = order_response.data["mapping_id"]
        buyer_id = self.buyer.id
        user_id = self.user.id

        def prepare():
            buyer = MiniProgramUser.objects.get(pk=buyer_id)
            user = User.objects.get(pk=user_id)
            return _prepare_wechat_prepay(mapping_id, buyer, user)["payment"].pk

        payment_ids = self._run_concurrently(prepare)

        self.assertEqual(len(set(payment_ids)), 1)
        self.assertEqual(
            SaleMiniPayment.objects.filter(mapping_id=mapping_id).count(),
            1,
        )

    def test_concurrent_full_refund_creates_one_idempotent_record(self):
        order_response = self._create_sale_mini_order(payment_method="WECHAT")
        mapping = SaleMiniOrderMapping.objects.get(id=order_response.data["mapping_id"])
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
        mapping.save(update_fields=["payment_status", "updated_at"])
        payment = SaleMiniPayment.objects.create(
            owner=self.owner,
            customer=self.customer,
            buyer_user=self.buyer,
            mapping=mapping,
            payment_no="SMP-CONCURRENT-REFUND",
            out_trade_no="SMT-CONCURRENT-REFUND",
            transaction_id="wx-concurrent-refund",
            status=SaleMiniPayment.Status.PAID,
            amount=Decimal("19.00"),
            amount_cents=1900,
            paid_at=timezone.now(),
        )
        payment_id = payment.id
        user_id = self.user.id

        def create_refund():
            current_payment = SaleMiniPayment.objects.get(pk=payment_id)
            user = User.objects.get(pk=user_id)
            refund, _created = get_or_create_full_refund(
                current_payment,
                by_user=user,
            )
            return refund.pk

        refund_ids = self._run_concurrently(create_refund)

        self.assertEqual(len(set(refund_ids)), 1)
        self.assertEqual(
            SaleMiniRefund.objects.filter(payment_id=payment_id).count(),
            1,
        )
