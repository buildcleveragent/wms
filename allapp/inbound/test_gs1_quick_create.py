from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.products.models import (
    Gs1LookupCache,
    Product,
    ProductCategory,
    ProductUom,
)


@override_settings(APIZERO_GS1_ENABLED=True, APIZERO_GS1_API_KEY="test-key")
class Gs1QuickCreateApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="gq", name="GS1 快建货主")
        cls.warehouse = Warehouse.objects.create(code="GQ-WH", name="GS1 快建仓")
        OwnerWarehouseBinding.objects.create(owner=cls.owner, warehouse=cls.warehouse)
        cls.category = ProductCategory.objects.create(code="GQ-CAT", name="饮品")
        cls.uom = ProductUom.objects.create(code="GQ-EA", name="瓶")
        cls.user = get_user_model().objects.create_user(
            username="gs1-operator", password="x"
        )
        UserRoleScope.objects.create(
            user=cls.user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=cls.warehouse,
        )
        cls.user.user_permissions.add(
            Permission.objects.get(codename="receive_without_order")
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def provider_result(*, registered=False):
        return {
            "code": 0,
            "msg": "成功",
            "request_id": "gs1-request-1",
            "data": {
                "barcode": "6921168509256",
                "gtin14": "06921168509256",
                "found": True,
                "registered": registered,
                "registration_message": "测试注册状态",
                "name": "测试饮用水",
                "brand": "测试品牌",
                "specification": "550ml",
                "manufacturer": "测试饮品制造有限公司",
                "category": "包装饮用水",
                "images": [
                    "https://www.gds.org.cn/image/test.jpg",
                    "https://example.invalid/not-allowed.jpg",
                ],
            },
        }

    @mock.patch("allapp.products.gs1._provider_request")
    def test_lookup_then_create_unregistered_product_and_cart_item(self, provider):
        provider.return_value = self.provider_result(registered=False)
        lookup = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": self.owner.pk, "barcode": "6921168509256"},
            format="json",
        )
        self.assertEqual(lookup.status_code, 200, lookup.data)
        self.assertEqual(lookup.data["source"], "gs1")
        self.assertFalse(lookup.data["candidate"]["registered"])
        self.assertEqual(len(lookup.data["candidate"]["images"]), 1)

        created = self.client.post(
            "/api/inbound/gs1-products/quick-create/",
            {
                "owner_id": self.owner.pk,
                "lookup_id": lookup.data["candidate"]["lookup_id"],
                "category_id": self.category.pk,
                "base_uom_id": self.uom.pk,
                "quantity": "12",
                "batch_control": True,
                "lot_no": "lot-a",
                "expiry_control": False,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        product = Product.objects.get(pk=created.data["product"]["id"])
        self.assertEqual(product.code, "GQ1")
        self.assertEqual(product.sku, "GQ1")
        self.assertEqual(product.gtin, "6921168509256")
        self.assertEqual(product.vender, "测试饮品制造有限公司")
        self.assertFalse(product.extra["gs1"]["registered"])
        self.assertEqual(created.data["cart_item"]["quantity"], "12.0000")
        self.assertEqual(created.data["cart_item"]["lot_no"], "LOT-A")

    @mock.patch("allapp.products.gs1._provider_request")
    def test_second_lookup_uses_cache_and_then_local_product(self, provider):
        provider.return_value = self.provider_result(registered=True)
        first = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": self.owner.pk, "barcode": "6921168509256"},
            format="json",
        )
        second = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": self.owner.pk, "barcode": "06921168509256"},
            format="json",
        )
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(second.data["cache_hit"])
        self.assertEqual(provider.call_count, 1)

        create = self.client.post(
            "/api/inbound/gs1-products/quick-create/",
            {
                "owner_id": self.owner.pk,
                "lookup_id": first.data["candidate"]["lookup_id"],
                "category_id": self.category.pk,
                "base_uom_id": self.uom.pk,
                "quantity": "1",
                "batch_control": False,
                "expiry_control": False,
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201, create.data)
        local = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": self.owner.pk, "barcode": "06921168509256"},
            format="json",
        )
        self.assertEqual(local.data["source"], "local")
        self.assertEqual(local.data["product"]["id"], create.data["product"]["id"])

    def test_quick_create_validates_tracking_fields_and_scope(self):
        cache = Gs1LookupCache.objects.create(
            canonical_gtin="06921168509256",
            query_code="6921168509256",
            status=Gs1LookupCache.Status.SUCCESS,
            found=True,
            registered=True,
            payload=self.provider_result()["data"],
            expires_at=timezone.now() + timedelta(hours=1),
        )
        invalid = self.client.post(
            "/api/inbound/gs1-products/quick-create/",
            {
                "owner_id": self.owner.pk,
                "lookup_id": str(cache.pk),
                "category_id": self.category.pk,
                "base_uom_id": self.uom.pk,
                "quantity": "1",
                "batch_control": True,
                "expiry_control": True,
                "expiry_basis": "MFG",
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertIn("lot_no", invalid.data)

        other = Owner.objects.create(code="OTHER", name="其他 GS1 货主")
        denied = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": other.pk, "barcode": "6921168509256"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)
