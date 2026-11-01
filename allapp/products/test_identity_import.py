from django.contrib.auth.models import Permission
from django.test import TestCase

from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail
from allapp.products import tests as fixtures
from allapp.products.excel_import import V12_PRODUCT_HEADERS
from allapp.products.models import Product


class IdentityImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.ProductExcelImportApiTests.setUpTestData.__func__(cls)

    setUp = fixtures.ProductExcelImportApiTests.setUp
    _workbook_file = fixtures.ProductExcelImportApiTests._workbook_file
    _post_rows = fixtures.ProductExcelImportApiTests._post_rows
    _valid_row = fixtures.ProductExcelImportApiTests._valid_row

    def allow_receipts(self):
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.user.user_permissions.add(Permission.objects.get(codename="receive_without_order"))

    def test_v12_archive_template_remains_accepted(self):
        result = self._post_rows([self._valid_row("LEGACY-V12")], headers=V12_PRODUCT_HEADERS)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(Product.objects.get(owner=self.owner, code="LEGACY-V12").model, "")

    def test_import_stores_model_and_ignores_composed_display_columns(self):
        result = self._post_rows(
            [
                self._valid_row(
                    "IDENTITY-IMPORT",
                    **{
                        "商品名称": "汽水",
                        "型号": "橙诺橙味",
                        "规格": "480ml",
                        "品牌": "不作为写入来源",
                        "品牌型号": "不作为写入来源",
                    },
                )
            ]
        )
        self.assertEqual(result.status_code, 200, result.data)
        product = Product.objects.get(owner=self.owner, code="IDENTITY-IMPORT")
        self.assertEqual(product.name, "汽水")
        self.assertEqual(product.model, "橙诺橙味")
        self.assertEqual(product.brand_id, self.brand.pk)
        self.assertEqual(product.brand_model, "测试品牌·橙诺橙味")

    def test_receipt_requires_code_when_model_is_missing(self):
        self.allow_receipts()
        first = self._post_rows(
            [
                self._valid_row(
                    "IDENTITY-OLD",
                    **{
                        "商品名称": "汽水",
                        "型号": "橙诺橙味",
                        "规格": "480ml",
                    },
                )
            ]
        )
        self.assertEqual(first.status_code, 200, first.data)
        before = Product.objects.filter(owner=self.owner).count()
        result = self._post_rows(
            [
                self._valid_row(
                    "",
                    **{
                        "商品名称": "汽水",
                        "规格": "480ml",
                        "基本单位数量": 2,
                    },
                )
            ],
            warehouse_id=self.warehouse.pk,
        )
        self.assertEqual(result.status_code, 400, result.data)
        self.assertEqual(Product.objects.filter(owner=self.owner).count(), before)
        self.assertFalse(InventoryDetail.objects.filter(product__code="IDENTITY-OLD").exists())

    def test_receipt_matches_model_without_combining_same_names(self):
        self.allow_receipts()
        rows = [
            self._valid_row(
                f"IDENTITY-{number}",
                **{
                    "商品名称": "汽水",
                    "型号": model,
                    "规格": "480ml",
                },
            )
            for number, model in enumerate(("橙诺橙味", "嘉宾原味"))
        ]
        self.assertEqual(self._post_rows(rows).status_code, 200)
        result = self._post_rows(
            [
                self._valid_row(
                    "",
                    **{
                        "商品名称": "汽水",
                        "型号": "嘉宾原味",
                        "规格": "480ml",
                        "基本单位数量": 2,
                    },
                )
            ],
            warehouse_id=self.warehouse.pk,
        )
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data["reused_count"], 1)
        self.assertEqual(InventoryDetail.objects.get(product__code="IDENTITY-1").onhand_qty, 2)
        self.assertFalse(InventoryDetail.objects.filter(product__code="IDENTITY-0").exists())
