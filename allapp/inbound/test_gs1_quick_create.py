from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import ProgrammingError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.core.models import SystemSetting
from allapp.locations.models import Warehouse
from allapp.products.gs1 import Gs1LookupError
from allapp.products.models import (
    Gs1LookupCache,
    Product,
    ProductCategory,
    ProductUom,
)


TEST_SYSTEM_SETTING_KEY = Fernet.generate_key().decode("ascii")


@override_settings(SYSTEM_SETTING_ENCRYPTION_KEY=TEST_SYSTEM_SETTING_KEY)
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
        api_key, _ = SystemSetting.objects.update_or_create(
            namespace=SystemSetting.INTEGRATION_NAMESPACE,
            key=SystemSetting.APIZERO_GS1_API_KEY,
            defaults={
                "name": "GS1 API key",
                "value_type": SystemSetting.ValueType.STRING,
                "client_visible": False,
                "is_secret": True,
                "is_active": True,
            },
        )
        api_key.set_secret_value("test-key")
        api_key.save(update_fields=["value", "updated_at"])

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

    @mock.patch("allapp.products.gs1._provider_request")
    def test_provider_not_found_is_a_successful_empty_candidate(self, provider):
        result = self.provider_result()
        result["data"] = {
            "barcode": "6901234567892",
            "gtin14": "06901234567892",
            "found": False,
            "registered": False,
        }
        provider.return_value = result

        response = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": self.owner.pk, "barcode": "6901234567892"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["source"], "gs1")
        self.assertFalse(response.data["candidate"]["found"])

    def test_provider_configuration_is_read_from_system_settings(self):
        api_key = SystemSetting.objects.get(
            namespace=SystemSetting.INTEGRATION_NAMESPACE,
            key=SystemSetting.APIZERO_GS1_API_KEY,
        )
        api_key.value = ""
        api_key.save(update_fields=["value", "updated_at"])
        missing = self.client.post(
            "/api/inbound/gs1-products/lookup/",
            {"owner_id": self.owner.pk, "barcode": "6912345678901"},
            format="json",
        )
        self.assertEqual(missing.status_code, 503, missing.data)
        self.assertEqual(missing.data["code"], "GS1_CONFIG_MISSING")
        self.assertIn("ApiZero API Key", missing.data["detail"])

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
        self.assertEqual(denied.data["code"], "GS1_OWNER_FORBIDDEN")

    def test_lookup_reports_missing_database_schema_with_request_id(self):
        with mock.patch(
            "allapp.inbound.gs1_views.get_or_fetch_lookup",
            side_effect=ProgrammingError(
                1146,
                "Table 'wms.products_gs1lookupcache' doesn't exist",
            ),
        ):
            response = self.client.post(
                "/api/inbound/gs1-products/lookup/",
                {"owner_id": self.owner.pk, "barcode": "6901234567892"},
                format="json",
                HTTP_X_REQUEST_ID="gs1-schema-test-1",
            )

        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data["code"], "GS1_SCHEMA_NOT_READY")
        self.assertIn("products.0012", response.data["detail"])
        self.assertEqual(response.data["request_id"], "gs1-schema-test-1")
        self.assertEqual(response["X-Request-ID"], "gs1-schema-test-1")

    def test_lookup_returns_specific_provider_errors(self):
        scenarios = (
            ("provider_not_configured", "GS1_CONFIG_MISSING", 503),
            ("provider_rate_limited", "GS1_RATE_LIMITED", 429),
            ("lookup_in_progress", "GS1_LOOKUP_IN_PROGRESS", 429),
            ("provider_invalid_response", "GS1_INVALID_RESPONSE", 502),
            ("provider_quota_exhausted", "GS1_QUOTA_EXHAUSTED", 503),
            ("provider_timeout", "GS1_TIMEOUT", 503),
            ("provider_network_error", "GS1_NETWORK_ERROR", 503),
        )
        for index, (provider_code, api_code, http_status) in enumerate(scenarios):
            with self.subTest(provider_code=provider_code), mock.patch(
                "allapp.inbound.gs1_views.get_or_fetch_lookup",
                side_effect=Gs1LookupError(
                    f"specific {provider_code}",
                    code=provider_code,
                    retry_after=1 if http_status == 429 else None,
                ),
            ):
                response = self.client.post(
                    "/api/inbound/gs1-products/lookup/",
                    {
                        "owner_id": self.owner.pk,
                        "barcode": f"6901234567{index:03d}",
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, http_status, response.data)
                self.assertEqual(response.data["code"], api_code)
                self.assertEqual(response.data["detail"], f"specific {provider_code}")
                self.assertTrue(response.data["request_id"])

    def test_lookup_unexpected_error_is_logged_but_not_leaked(self):
        with mock.patch(
            "allapp.inbound.gs1_views.get_or_fetch_lookup",
            side_effect=RuntimeError("database-secret-detail"),
        ), self.assertLogs("allapp.inbound.gs1_views", level="ERROR") as logs:
            response = self.client.post(
                "/api/inbound/gs1-products/lookup/",
                {"owner_id": self.owner.pk, "barcode": "6901234567892"},
                format="json",
            )

        self.assertEqual(response.status_code, 500, response.data)
        self.assertEqual(response.data["code"], "GS1_LOOKUP_INTERNAL_ERROR")
        self.assertNotIn("database-secret-detail", response.data["detail"])
        self.assertIn(response.data["request_id"], response.data["detail"])
        self.assertIn("database-secret-detail", "\n".join(logs.output))
