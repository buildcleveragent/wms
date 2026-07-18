# allapp/products/tests.py
# -*- coding: utf-8 -*-
import json
import io
import os
import tempfile
import unittest

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from allapp.accounts.models import AuditEvent, UserRoleScope

from .excel_import import HEADERS, IMPORT_SHEET_NAME, MAX_IMPORT_FILE_SIZE
from .views import ProductViewSet

# 业务模型
Owner = apps.get_model("baseinfo", "Owner")
ProductUom = apps.get_model("products", "ProductUom")
Product = apps.get_model("products", "Product")

# 可选：DAL 自动补全视图（存在则测试）
try:
    # 如果你把视图放在其他模块，请相应调整
    from .autocomplete import (
        ProductUomAutocomplete,
    )

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
        cls.user_a = User.objects.create_user(
            username="a", password="a", owner=cls.owner_a, is_staff=True
        )
        cls.user_b = User.objects.create_user(
            username="b", password="b", owner=cls.owner_b, is_staff=True
        )

        # 给两位普通用户授予 Product 的内置增/改/查/删权限（DjangoModelPermissions 需要）。
        ct = ContentType.objects.get_for_model(Product)
        builtin_perms = Permission.objects.filter(
            content_type=ct,
            codename__in=[
                "add_product",
                "change_product",
                "delete_product",
                "view_product",
            ],
        )
        cls.user_a.user_permissions.add(*list(builtin_perms))
        cls.user_b.user_permissions.add(*list(builtin_perms))

        cls.view_all_user = User.objects.create_user(
            username="view-all",
            password="x",
            owner=cls.owner_a,
            is_staff=True,
        )
        cls.manage_all_user = User.objects.create_user(
            username="manage-all",
            password="x",
            owner=cls.owner_a,
            is_staff=True,
        )
        cls.view_all_user.user_permissions.add(*list(builtin_perms))
        cls.view_all_user.user_permissions.add(
            Permission.objects.get(content_type=ct, codename="view_all_owner_products")
        )
        cls.manage_all_user.user_permissions.add(*list(builtin_perms))
        cls.manage_all_user.user_permissions.add(
            Permission.objects.get(content_type=ct, codename="manage_all_owner_products")
        )

        for user, owner in (
            (cls.user_a, cls.owner_a),
            (cls.user_b, cls.owner_b),
            (cls.view_all_user, cls.owner_a),
            (cls.manage_all_user, cls.owner_a),
        ):
            UserRoleScope.objects.create(
                user=user,
                role=UserRoleScope.Role.OWNER_MANAGER,
                owner=owner,
            )

        # 现存商品：A/B 各一条
        cls.prod_a = Product.objects.create(
            owner=cls.owner_a,
            code="SKU-A",
            name="商品A",
            base_uom=cls.uom,
            is_active=True,
        )
        cls.prod_b = Product.objects.create(
            owner=cls.owner_b,
            code="SKU-B",
            name="商品B",
            base_uom=cls.uom,
            is_active=True,
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

    def test_regular_user_cannot_create_for_other_owner(self):
        view = ProductViewSet.as_view({"post": "create"})
        payload = {
            "owner": self.owner_b.id,
            "code": "SKU-LOCKED",
            "name": "锁回当前货主",
            "base_uom": self.uom.id,
            "is_active": True,
        }
        req = self.factory.post("/products/", data=payload, format="json")
        force_authenticate(req, user=self.user_a)
        resp = view(req)

        self.assertEqual(resp.status_code, 201, resp.data)
        product = Product.objects.get(code="SKU-LOCKED")
        self.assertEqual(product.owner_id, self.owner_a.id)

    def test_view_all_owner_permission_can_list_other_owner_products(self):
        view = ProductViewSet.as_view({"get": "list"})
        req = self.factory.get("/products/")
        force_authenticate(req, user=self.view_all_user)
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        codes = [item["code"] for item in resp.data]
        self.assertIn("SKU-A", codes)
        self.assertIn("SKU-B", codes)

    def test_view_all_owner_permission_cannot_modify_other_owner_products(self):
        view = ProductViewSet.as_view({"patch": "partial_update"})
        req = self.factory.patch(
            f"/products/{self.prod_b.id}/",
            data={"name": "Should Not Update"},
            format="json",
        )
        force_authenticate(req, user=self.view_all_user)
        resp = view(req, pk=self.prod_b.id)

        self.assertEqual(resp.status_code, 404)

    def test_manage_all_owner_permission_can_modify_other_owner_products(self):
        view = ProductViewSet.as_view({"patch": "partial_update"})
        req = self.factory.patch(
            f"/products/{self.prod_b.id}/",
            data={"name": "Managed Update"},
            format="json",
        )
        force_authenticate(req, user=self.manage_all_user)
        resp = view(req, pk=self.prod_b.id)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.prod_b.refresh_from_db()
        self.assertEqual(self.prod_b.name, "Managed Update")
        self.assertEqual(self.prod_b.owner_id, self.owner_b.id)

    def test_regular_user_cannot_reassign_product_owner_on_update(self):
        view = ProductViewSet.as_view({"patch": "partial_update"})
        req = self.factory.patch(
            f"/products/{self.prod_a.id}/",
            data={"owner": self.owner_b.id, "name": "Owner Guarded"},
            format="json",
        )
        force_authenticate(req, user=self.user_a)
        resp = view(req, pk=self.prod_a.id)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.prod_a.refresh_from_db()
        self.assertEqual(self.prod_a.owner_id, self.owner_a.id)
        self.assertEqual(self.prod_a.name, "Owner Guarded")

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

    def test_template_download_xlsx_headers(self):
        """
        /products/template/ 默认返回 Excel 商品模板。
        """
        view = ProductViewSet.as_view({"get": "template"})
        req = self.factory.get("/products/template/")
        force_authenticate(req, user=self.user_a)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            resp["Content-Type"],
        )
        workbook = load_workbook(io.BytesIO(resp.content))
        self.assertIn(IMPORT_SHEET_NAME, workbook.sheetnames)
        headers = [cell.value for cell in workbook[IMPORT_SHEET_NAME][1]]
        self.assertIn("商品编号", headers)

    def test_template_download_keeps_csv_compatibility(self):
        view = ProductViewSet.as_view({"get": "template"})
        req = self.factory.get("/products/template/?format=csv")
        force_authenticate(req, user=self.user_a)
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        self.assertIn("owner_code", resp.content.decode("utf-8"))

    def test_bulk_activate_and_deactivate_are_owner_scoped(self):
        self.prod_a.is_active = False
        self.prod_a.save(update_fields=["is_active"])
        self.prod_b.is_active = False
        self.prod_b.save(update_fields=["is_active"])

        activate_view = ProductViewSet.as_view({"post": "bulk_activate"})
        req = self.factory.post(
            "/products/bulk-activate/",
            data={"ids": [self.prod_a.id, self.prod_b.id]},
            format="json",
        )
        force_authenticate(req, user=self.user_a)
        resp = activate_view(req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["updated"], 1)
        self.prod_a.refresh_from_db()
        self.prod_b.refresh_from_db()
        self.assertTrue(self.prod_a.is_active)
        self.assertFalse(self.prod_b.is_active)

        deactivate_view = ProductViewSet.as_view({"post": "bulk_deactivate"})
        req = self.factory.post(
            "/products/bulk-deactivate/",
            data={"ids": [self.prod_a.id, self.prod_b.id]},
            format="json",
        )
        force_authenticate(req, user=self.user_a)
        resp = deactivate_view(req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["updated"], 1)
        self.prod_a.refresh_from_db()
        self.prod_b.refresh_from_db()
        self.assertFalse(self.prod_a.is_active)
        self.assertFalse(self.prod_b.is_active)

    def test_import_requires_file_and_export_still_reports_missing_resource(self):
        import_view = ProductViewSet.as_view({"post": "import_file"})
        req = self.factory.post("/products/import/", data={}, format="multipart")
        force_authenticate(req, user=self.user_a)
        import_resp = import_view(req)

        export_view = ProductViewSet.as_view({"get": "export_file"})
        req = self.factory.get("/products/export/")
        force_authenticate(req, user=self.user_a)
        export_resp = export_view(req)

        self.assertEqual(import_resp.status_code, 400)
        self.assertIn("file", import_resp.data["detail"])
        self.assertEqual(export_resp.status_code, 501)
        self.assertIn("ProductResource", export_resp.data["detail"])

    def test_get_product_details_returns_base_and_package_uoms(self):
        pack_uom = ProductUom.objects.create(code="CTN", name="箱", is_active=True)
        self.prod_a.packages.create(uom=pack_uom, qty_in_base=12)

        request = RequestFactory().get(f"/products/get_product_details/{self.prod_a.id}/")
        from .views import get_product_details

        response = get_product_details(request, self.prod_a.id)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(payload["base_uom"], self.uom.name)
        self.assertEqual(payload["pack_uoms"][0]["uom"], "箱")
        self.assertEqual(payload["pack_uoms"][0]["pack_qty"], 12)


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
        self.assertIn("PCS", content2)  # COUNT 类
        self.assertNotIn("KG", content2)  # 非 COUNT 类不应出现


@unittest.skipUnless(DEPENDENCIES_OK, "缺少 baseinfo/products 依赖模型，跳过商品 Excel 导入测试")
class ProductExcelImportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="PXIA", name="Product Excel Owner A")
        cls.other_owner = Owner.objects.create(code="PXIB", name="Product Excel Owner B")
        cls.uom = ProductUom.objects.create(code="EA-X", name="个", is_active=True)
        cls.carton_uom = ProductUom.objects.create(code="CTN-X", name="箱", is_active=True)
        cls.category = apps.get_model("products", "ProductCategory").objects.create(
            code="FOOD-X", name="食品", is_active=True
        )
        cls.brand = apps.get_model("products", "Brand").objects.create(
            code="BRAND-X", name="测试品牌", is_active=True
        )

        User = get_user_model()
        cls.user = User.objects.create_user(username="product-excel-owner", password="x")
        cls.cross_owner_user = User.objects.create_user(
            username="product-excel-global", password="x"
        )
        cls.no_permission_user = User.objects.create_user(
            username="product-excel-denied", password="x"
        )
        for user in (cls.user, cls.cross_owner_user, cls.no_permission_user):
            UserRoleScope.objects.create(
                user=user,
                role=UserRoleScope.Role.OWNER_MANAGER,
                owner=cls.owner,
            )

        product_ct = ContentType.objects.get_for_model(Product)
        add_permission = Permission.objects.get(content_type=product_ct, codename="add_product")
        view_permission = Permission.objects.get(content_type=product_ct, codename="view_product")
        cls.user.user_permissions.add(add_permission, view_permission)
        cls.cross_owner_user.user_permissions.add(add_permission, view_permission)
        cls.cross_owner_user.user_permissions.add(
            Permission.objects.get(
                content_type=product_ct,
                codename="manage_all_owner_products",
            )
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _workbook_file(self, rows, *, headers=HEADERS, filename="products.xlsx"):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = IMPORT_SHEET_NAME
        worksheet.append(list(headers))
        for row in rows:
            worksheet.append([row.get(header) for header in headers])
        output = io.BytesIO()
        workbook.save(output)
        return SimpleUploadedFile(
            filename,
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _post_rows(self, rows, *, client=None, headers=HEADERS, url=None):
        return (client or self.client).post(
            url or "/api/products/import-excel/",
            {"file": self._workbook_file(rows, headers=headers)},
            format="multipart",
        )

    def _valid_row(self, code="PDA-XLSX-1", **overrides):
        row = {
            "商品编号": code,
            "商品名称": f"导入商品 {code}",
            "基本单位编码": self.uom.code,
            "分类编码": self.category.code,
            "品牌编码": self.brand.code,
            "批次管理": "是",
            "保质期管理": "否",
        }
        row.update(overrides)
        return row

    def test_template_contains_scoped_references_and_metadata(self):
        response = self.client.get("/api/products/import-template/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=UTF-8", response["Content-Disposition"])
        workbook = load_workbook(io.BytesIO(response.content))
        self.assertEqual(
            workbook.sheetnames,
            ["填写说明", "商品导入", "基础资料", "_meta"],
        )
        self.assertEqual(workbook["_meta"].sheet_state, "hidden")
        headers = [cell.value for cell in workbook[IMPORT_SHEET_NAME][1]]
        self.assertEqual(tuple(headers), HEADERS)
        owner_codes = {
            workbook["基础资料"].cell(row=row, column=1).value
            for row in range(2, workbook["基础资料"].max_row + 1)
        }
        self.assertIn(self.owner.code, owner_codes)
        self.assertNotIn(self.other_owner.code, owner_codes)
        self.assertIn("ProductImportUomCodes", workbook.defined_names)

    def test_happy_path_creates_product_package_and_audit_event(self):
        response = self._post_rows(
            [
                self._valid_row(
                    code=" pda-xlsx-happy ",
                    **{
                        "SKU编码": "pda-sku-happy",
                        "默认价格": "12.50",
                        "最低库存": 2,
                        "最高库存": 20,
                        "序列号管理": "否",
                        "包装单位编码": self.carton_uom.code,
                        "包装换算数量": 12,
                        "包装条码": "000123456789",
                        "采购默认": "是",
                        "销售默认": "是",
                    },
                )
            ]
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["created_count"], 1)
        product = Product.objects.get(owner=self.owner, code="PDA-XLSX-HAPPY")
        self.assertEqual(product.sku, "PDA-SKU-HAPPY")
        self.assertEqual(product.created_by_id, self.user.id)
        self.assertFalse(product.expiry_control)
        package = product.packages.get()
        self.assertEqual(package.uom_id, self.carton_uom.id)
        self.assertEqual(package.qty_in_base, 12)
        self.assertTrue(package.is_purchase_default)
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.user,
                action="products.import_excel",
                object_type="",
            ).exists()
        )

    def test_single_owner_scope_rejects_other_owner_and_writes_nothing(self):
        response = self._post_rows(
            [
                self._valid_row("PDA-OWN-A"),
                self._valid_row("PDA-OWN-B", **{"货主编码": self.other_owner.code}),
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["created_count"], 0)
        self.assertTrue(any(error["field"] == "货主编码" for error in response.data["errors"]))
        self.assertFalse(Product.objects.filter(code="PDA-OWN-A").exists())

    def test_cross_owner_permission_can_import_for_other_owner(self):
        client = APIClient()
        client.force_authenticate(self.cross_owner_user)
        response = self._post_rows(
            [self._valid_row("PDA-GLOBAL", **{"货主编码": self.other_owner.code})],
            client=client,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Product.objects.filter(owner=self.other_owner, code="PDA-GLOBAL").exists())

    def test_existing_product_is_skipped_without_update(self):
        existing = Product.objects.create(
            owner=self.owner,
            code="PDA-EXISTING",
            sku="PDA-EXISTING",
            name="原商品名称",
            base_uom=self.uom,
            expiry_control=False,
            expiry_basis=None,
        )

        response = self._post_rows([self._valid_row("PDA-EXISTING", **{"商品名称": "不应覆盖"})])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["created_count"], 0)
        self.assertEqual(response.data["skipped_count"], 1)
        existing.refresh_from_db()
        self.assertEqual(existing.name, "原商品名称")

    def test_invalid_row_makes_whole_batch_atomic(self):
        response = self._post_rows(
            [
                self._valid_row("PDA-ATOMIC-OK"),
                self._valid_row("PDA-ATOMIC-BAD", **{"基本单位编码": "NOT-FOUND"}),
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["created_count"], 0)
        self.assertFalse(Product.objects.filter(code="PDA-ATOMIC-OK").exists())

    def test_duplicate_identifiers_inside_file_are_reported(self):
        response = self._post_rows(
            [
                self._valid_row("PDA-DUP-1", **{"SKU编码": "SAME-SKU"}),
                self._valid_row("PDA-DUP-2", **{"SKU编码": "SAME-SKU"}),
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(
            any(
                error["row"] == 3 and error["field"] == "SKU编码" and "第 2 行" in error["message"]
                for error in response.data["errors"]
            )
        )
        self.assertFalse(Product.objects.filter(code__startswith="PDA-DUP").exists())

    def test_formula_cell_is_rejected(self):
        response = self._post_rows(
            [self._valid_row("PDA-FORMULA", **{"商品名称": '=CONCAT("商品","名称")'})]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["errors"][0]["field"], "商品名称")
        self.assertIn("公式", response.data["errors"][0]["message"])

    def test_permission_and_profile_capability_are_fail_closed(self):
        denied_client = APIClient()
        denied_client.force_authenticate(self.no_permission_user)
        denied = denied_client.get("/api/products/import-template/")
        allowed_profile = self.client.get("/api/auth/profile/")
        denied_profile = denied_client.get("/api/auth/profile/")

        self.assertEqual(denied.status_code, 403)
        self.assertTrue(allowed_profile.data["capabilities"]["can_import_products"])
        self.assertFalse(denied_profile.data["capabilities"]["can_import_products"])

    def test_invalid_extension_and_oversized_file_are_rejected(self):
        wrong_type = SimpleUploadedFile("products.xls", b"not-xlsx")
        wrong_response = self.client.post(
            "/api/products/import-excel/", {"file": wrong_type}, format="multipart"
        )
        oversized = SimpleUploadedFile("products.xlsx", b"x" * (MAX_IMPORT_FILE_SIZE + 1))
        oversized_response = self.client.post(
            "/api/products/import-excel/", {"file": oversized}, format="multipart"
        )

        self.assertEqual(wrong_response.status_code, 400)
        self.assertIn(".xlsx", wrong_response.data["detail"])
        self.assertEqual(oversized_response.status_code, 400)
        self.assertIn("5 MB", oversized_response.data["detail"])

    def test_legacy_import_action_uses_same_service(self):
        response = self._post_rows(
            [self._valid_row("PDA-LEGACY-ACTION")],
            url="/products/products/import/",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Product.objects.filter(code="PDA-LEGACY-ACTION").exists())


@unittest.skipUnless(DEPENDENCIES_OK, "缺少 baseinfo/products 依赖模型，跳过 products 导入命令测试")
class ProductImportCommandTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="PIC", name="Product Import Command")

    def _write_workbook(self, rows):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "sheet1"
        for row in rows:
            worksheet.append(row)
        handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        handle.close()
        workbook.save(handle.name)
        return handle.name

    def test_import_product_master_sheet_creates_product_and_uom(self):
        path = self._write_workbook(
            [
                ["owner", "code", "sku", "name", "base_uom"],
                [self.owner.code, "CMD-SKU-1", "CMD-SKU-1", "命令导入商品", "件"],
            ]
        )

        try:
            call_command("import_product_master_sheet", "--file", path)
        finally:
            os.unlink(path)

        product = Product.objects.get(owner=self.owner, code="CMD-SKU-1")
        self.assertEqual(product.name, "命令导入商品")
        self.assertEqual(product.base_uom.name, "件")

    def test_import_product_master_sheet_dry_run_does_not_persist_product(self):
        path = self._write_workbook(
            [
                ["owner", "code", "sku", "name", "base_uom"],
                [self.owner.code, "CMD-DRY-1", "CMD-DRY-1", "Dry Run 商品", "件"],
            ]
        )

        try:
            call_command("import_product_master_sheet", "--file", path, "--dry-run")
        finally:
            os.unlink(path)

        self.assertFalse(Product.objects.filter(owner=self.owner, code="CMD-DRY-1").exists())
