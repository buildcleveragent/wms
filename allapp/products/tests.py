# allapp/products/tests.py
# -*- coding: utf-8 -*-
from decimal import Decimal
import unittest

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

# 业务模型
Owner = apps.get_model("baseinfo", "Owner")
ProductUom = apps.get_model("products", "ProductUom")
Product = apps.get_model("products", "Product")

# 视图
from .views import ProductViewSet

# 可选：DAL 自动补全视图（存在则测试）
try:
    from .autocomplete import ProductUomAutocomplete  # 如果你把视图放在其他模块，请相应调整
    DAL_OK = True
except Exception:
    DAL_OK = False

# 依赖是否齐全
DEPENDENCIES_OK = all([Owner, ProductUom, Product])


@unittest.skipUnless(DEPENDENCIES_OK, "缺少 baseinfo/products 依赖模型，跳过 products 测试")
class ProductViewSetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # 基础数据：两个货主、一个基础单位
        cls.owner_a = Owner.objects.create(code="OA", name="Owner-A")
        cls.owner_b = Owner.objects.create(code="OB", name="Owner-B")
        cls.uom = ProductUom.objects.create(code="PCS", name="件", is_active=True)

        # 用户：user_a 属于 owner_a；user_b 属于 owner_b
        User = get_user_model()
        cls.user_a = User.objects.create_user(username="a", password="a", owner=cls.owner_a, is_staff=True)
        cls.user_b = User.objects.create_user(username="b", password="b", owner=cls.owner_b, is_staff=True)

        # 给两位用户授予 Product 的增/改/查/删权限（DjangoModelPermissions 需要）
        ct = ContentType.objects.get_for_model(Product)
        perms = Permission.objects.filter(content_type=ct)
        cls.user_a.user_permissions.add(*list(perms))
        cls.user_b.user_permissions.add(*list(perms))

        # 现存商品：A/B 各一条
        cls.prod_a = Product.objects.create(
            owner=cls.owner_a, code="SKU-A", name="商品A", base_uom=cls.uom, is_active=True
        )
        cls.prod_b = Product.objects.create(
            owner=cls.owner_b, code="SKU-B", name="商品B", base_uom=cls.uom, is_active=True
        )

        cls.factory = APIRequestFactory()

    def test_list_scoped_by_owner(self):
        """
        非超管仅能看到自己 owner 的商品
        """
        view = ProductViewSet.as_view({"get": "list"})
        req = self.factory.get("/products/")
        force_authenticate(req, user=self.user_a)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        codes = [item["code"] for item in resp.data]
        self.assertIn("SKU-A", codes)
        self.assertNotIn("SKU-B", codes)

    def test_create_auto_bind_owner(self):
        """
        非超管创建商品时，若请求未显式传 owner，后台会自动绑定为当前用户的 owner
        """
        view = ProductViewSet.as_view({"post": "create"})
        payload = {
            "code": "SKU-NEW",
            "name": "新商品",
            "base_uom": self.uom.id,
            "is_active": True,
        }
        req = self.factory.post("/products/", data=payload, format="json")
        force_authenticate(req, user=self.user_a)
        resp = view(req)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["owner"], self.owner_a.id)

        # 再次 list，应该能看到新商品
        view_list = ProductViewSet.as_view({"get": "list"})
        req2 = self.factory.get("/products/")
        force_authenticate(req2, user=self.user_a)
        resp2 = view_list(req2)
        codes = [i["code"] for i in resp2.data]
        self.assertIn("SKU-NEW", codes)

    def test_barcode_action_returns_zpl(self):
        """
        /products/{id}/barcode/ 应返回 ZPL 文本，且包含 base_uom.code
        """
        view = ProductViewSet.as_view({"get": "barcode"})
        req = self.factory.get(f"/products/{self.prod_a.id}/barcode/")
        force_authenticate(req, user=self.user_a)
        resp = view(req, pk=self.prod_a.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("type"), "zpl")
        self.assertIn("PCS", resp.data.get("content", ""))  # base_uom.code

    def test_template_download_csv_headers(self):
        """
        /products/template/ 返回 CSV，且包含表头 owner_code
        """
        view = ProductViewSet.as_view({"get": "template"})
        req = self.factory.get("/products/template/")
        force_authenticate(req, user=self.user_a)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp._headers.get("content-type", ("", ""))[1])
        self.assertIn("owner_code", resp.content.decode("utf-8"))


@unittest.skipUnless(DEPENDENCIES_OK and DAL_OK, "缺少依赖（DAL 或模型），跳过 UOM 自动补全测试")
class ProductUomAutocompleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.uom1 = ProductUom.objects.create(code="PCS", name="件", is_active=True, kind="COUNT")
        cls.uom2 = ProductUom.objects.create(code="KG", name="千克", is_active=True, kind="WEIGHT")

    def test_autocomplete_filters_and_forwards(self):
        """
        基本搜索 + forwarded 参数 only_count=1 时仅返回 COUNT 类
        DAL 会把 forwarded 参数解析到 view.self.forwarded
        """
        from django.test import RequestFactory
        rf = RequestFactory()
        view = ProductUomAutocomplete.as_view()

        # 不带 forward：按关键字
        req1 = rf.get("/autocomplete/uom/?q=K")
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, 200)
        content1 = resp1.content.decode("utf-8")
        self.assertIn("KG", content1)

        # 带 forward：only_count=1
        req2 = rf.get("/autocomplete/uom/?q=&forward=%7B%22only_count%22%3A%20%221%22%7D")
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, 200)
        content2 = resp2.content.decode("utf-8")
        self.assertIn("PCS", content2)     # COUNT 类
        self.assertNotIn("KG", content2)   # 非 COUNT 类不应出现
