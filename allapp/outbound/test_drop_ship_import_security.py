import io
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIRequestFactory

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding
from allapp.locations.models import Warehouse
from allapp.outbound.drop_ship_import import (
    GENERIC_ROW_ERROR,
    MAX_IMPORT_FILE_SIZE,
    MAX_IMPORT_ROWS,
    MAX_XLSX_ENTRIES,
    MAX_XLSX_UNCOMPRESSED_SIZE,
    DropShipImportFileError,
    DropShipImportService,
    ParsedRow,
)
from allapp.outbound.models import OutboundOrder
from allapp.products.identifier_services import (
    add_external_identifier,
    add_product_barcode,
    set_identifier_active,
)
from allapp.products.models import (
    Product,
    ProductBarcode,
    ProductExternalIdentifier,
    ProductUom,
)


HEADERS = [
    "收件人姓名",
    "收件人手机/电话",
    "收件人详细地址",
    "数量",
    "订单编号",
    "商家编码",
    "商品名称",
]


class DropShipImportSecurityTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="EXCEL-OWN", name="Excel Owner")
        self.warehouse = Warehouse.objects.create(code="EXCEL-WH", name="Excel WH")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        self.user = get_user_model().objects.create_user(username="excel-user")
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.OWNER_SALESPERSON,
            owner=self.owner,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CASH",
            name="Cash Customer",
        )
        uom = ProductUom.objects.create(code="EXCEL-PC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="EXCEL-P",
            sku="EXCEL-SKU",
            name="Excel Product",
            base_uom=uom,
            price=Decimal("10"),
            min_price=Decimal("1"),
        )
        self.request = APIRequestFactory().post("/api/outbound/orders/import/")
        self.request.user = self.user

    def service(self):
        return DropShipImportService(
            request=self.request,
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            cash_customer=self.customer,
        )

    def row(
        self,
        order_no="EXCEL-1",
        qty=1,
        merchant_code=None,
        product_name="Excel Product",
    ):
        return [
            "张三",
            "13800000000",
            "测试地址",
            qty,
            order_no,
            self.product.code if merchant_code is None else merchant_code,
            product_name,
        ]

    def workbook_file(self, rows, headers=HEADERS, name="orders.xlsx"):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        buffer = io.BytesIO()
        workbook.save(buffer)
        workbook.close()
        return SimpleUploadedFile(
            name,
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @staticmethod
    def zip_file(entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
        return SimpleUploadedFile("orders.xlsx", buffer.getvalue())

    def assert_file_rejected_without_orders(self, uploaded_file, message=None):
        before = OutboundOrder.all_objects.count()
        with self.assertRaises(DropShipImportFileError) as caught:
            self.service().import_file(uploaded_file)
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertEqual(OutboundOrder.all_objects.count(), before)

    def test_limits_are_fixed_to_the_approved_values(self):
        self.assertEqual(MAX_IMPORT_FILE_SIZE, 5 * 1024 * 1024)
        self.assertEqual(MAX_IMPORT_ROWS, 1000)
        self.assertEqual(MAX_XLSX_UNCOMPRESSED_SIZE, 50 * 1024 * 1024)
        self.assertEqual(MAX_XLSX_ENTRIES, 300)

    def test_empty_non_zip_corrupt_and_oversized_files_are_rejected(self):
        valid_zip = self.zip_file([("entry.txt", b"content")])
        corrupt_zip = SimpleUploadedFile("corrupt.xlsx", valid_zip.read()[:-8])
        cases = (
            (SimpleUploadedFile("empty.xlsx", b""), "为空"),
            (SimpleUploadedFile("plain.xlsx", b"not a zip"), "无法解析"),
            (corrupt_zip, "无法解析"),
            (
                SimpleUploadedFile("large.xlsx", b"x" * (MAX_IMPORT_FILE_SIZE + 1)),
                "5 MB",
            ),
        )
        for uploaded, message in cases:
            with self.subTest(message=message):
                self.assert_file_rejected_without_orders(uploaded, message)

    def test_zip_entry_and_uncompressed_limits_are_checked_before_openpyxl(self):
        too_many = self.zip_file(
            [(f"entry-{index}.txt", b"x") for index in range(MAX_XLSX_ENTRIES + 1)]
        )
        self.assert_file_rejected_without_orders(too_many, "300")

        with mock.patch(
            "allapp.outbound.drop_ship_import.MAX_XLSX_UNCOMPRESSED_SIZE",
            50,
        ):
            expanded = self.zip_file([("huge.txt", b"x" * 51)])
            self.assert_file_rejected_without_orders(expanded, "50 MB")

    def test_missing_headers_and_1001_rows_are_file_level_atomic_rejections(self):
        missing_headers = [header for header in HEADERS if header != "数量"]
        source_row = self.row()
        missing_row = source_row[:3] + source_row[4:]
        missing = self.workbook_file([missing_row], headers=missing_headers)
        self.assert_file_rejected_without_orders(missing, "数量")

        rows = [self.row(order_no=f"ROW-{index}") for index in range(1001)]
        self.assert_file_rejected_without_orders(self.workbook_file(rows), "1000")

    def test_formula_business_cell_is_rejected_without_formula_evaluation(self):
        result = self.service().import_file(
            self.workbook_file([self.row(order_no="FORMULA", qty="=1+1")])
        )
        self.assertEqual(result["fail_count"], 1)
        self.assertIn("不允许使用公式", result["errors"][0]["reason"])
        self.assertFalse(OutboundOrder.objects.filter(src_bill_no="FORMULA").exists())

    def test_valid_rows_keep_success_skip_and_failure_result_shape(self):
        OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=self.customer,
            src_bill_no="DUPLICATE",
            outbound_type="SALES",
            created_by=self.user,
        )
        uploaded = self.workbook_file(
            [
                self.row(order_no="SUCCESS"),
                self.row(order_no="DUPLICATE"),
                self.row(order_no="BAD-QTY", qty=0),
            ]
        )
        result = self.service().import_file(uploaded)
        self.assertEqual(
            (
                result["total_rows"],
                result["success_count"],
                result["skip_count"],
                result["fail_count"],
            ),
            (3, 1, 1, 1),
            result,
        )
        self.assertTrue(OutboundOrder.objects.filter(src_bill_no="SUCCESS").exists())

    def test_merchant_code_matches_owner_code_and_effective_external_identifier(self):
        external = add_external_identifier(
            product=self.product,
            source_system="ERP",
            external_code="ERP-EXCEL-P",
        )
        add_external_identifier(
            product=self.product,
            source_system="OMS",
            external_code=external.external_code,
        )
        legacy = add_external_identifier(
            product=self.product,
            source_system="LEGACY",
            external_code="LEGACY-EXCEL-P",
            is_primary=True,
        )
        for order_no, merchant_code in (
            ("BY-CODE", "  excel-p  "),
            ("BY-EXTERNAL", external.external_code.lower()),
            ("BY-LEGACY", legacy.external_code),
        ):
            with self.subTest(merchant_code=merchant_code):
                result = self.service().import_file(
                    self.workbook_file(
                        [self.row(order_no=order_no, merchant_code=merchant_code)]
                    )
                )
                self.assertEqual(result["success_count"], 1, result)

    def test_wms_sku_and_barcode_are_rejected_without_name_fallback(self):
        barcode = add_product_barcode(
            product=self.product,
            barcode="EXCEL-BARCODE",
            barcode_type=ProductBarcode.BarcodeType.OTHER,
        )
        cases = (
            ("SKU-ONLY", self.product.sku, "不接受仓库SKU编码"),
            ("BARCODE-ONLY", barcode.barcode, "匹配不到商品"),
        )
        for order_no, merchant_code, message in cases:
            with self.subTest(merchant_code=merchant_code):
                result = self.service().import_file(
                    self.workbook_file(
                        [
                            self.row(
                                order_no=order_no,
                                merchant_code=merchant_code,
                                product_name=self.product.name,
                            )
                        ]
                    )
                )
                self.assertEqual(result["fail_count"], 1, result)
                self.assertIn(message, result["errors"][0]["reason"])
                self.assertFalse(
                    OutboundOrder.objects.filter(src_bill_no=order_no).exists()
                )

    def test_external_identifier_must_be_currently_effective(self):
        now = timezone.now()
        retired = add_external_identifier(
            product=self.product,
            source_system="ERP",
            external_code="ERP-RETIRED",
        )
        set_identifier_active(retired, False)
        add_external_identifier(
            product=self.product,
            source_system="OMS",
            external_code="OMS-FUTURE",
            valid_from=now + timedelta(days=1),
        )
        add_external_identifier(
            product=self.product,
            source_system="PIM",
            external_code="PIM-EXPIRED",
            valid_to=now - timedelta(days=1),
        )
        deleted = add_external_identifier(
            product=self.product,
            source_system="ECOM",
            external_code="ECOM-DELETED",
        )
        ProductExternalIdentifier.all_objects.filter(pk=deleted.pk).update(
            is_deleted=True
        )

        rows = [
            self.row(order_no=f"INACTIVE-{index}", merchant_code=value)
            for index, value in enumerate(
                ("ERP-RETIRED", "OMS-FUTURE", "PIM-EXPIRED", "ECOM-DELETED"),
                start=1,
            )
        ]
        result = self.service().import_file(self.workbook_file(rows))
        self.assertEqual(result["fail_count"], 4, result)
        self.assertEqual(result["success_count"], 0, result)

        set_identifier_active(retired, True)
        reactivated = self.service().import_file(
            self.workbook_file(
                [self.row(order_no="REACTIVATED", merchant_code="ERP-RETIRED")]
            )
        )
        self.assertEqual(reactivated["success_count"], 1, reactivated)

    def test_external_identifier_is_owner_scoped_and_conflicts_fail_closed(self):
        other_owner = Owner.objects.create(code="EXT-OTHER", name="Other")
        other_product = Product.objects.create(
            owner=other_owner,
            code="OTHER-P",
            name="Other Product",
            base_uom=self.product.base_uom,
        )
        add_external_identifier(
            product=other_product,
            source_system="ERP",
            external_code="OTHER-OWNER-CODE",
        )
        isolated = self.service().import_file(
            self.workbook_file(
                [
                    self.row(
                        order_no="CROSS-OWNER",
                        merchant_code="OTHER-OWNER-CODE",
                    )
                ]
            )
        )
        self.assertEqual(isolated["fail_count"], 1, isolated)

        conflicting_product = Product.objects.create(
            owner=self.owner,
            code="CONFLICTING-P",
            name="Conflicting Product",
            base_uom=self.product.base_uom,
        )
        conflict = ProductExternalIdentifier(
            owner=self.owner,
            product=conflicting_product,
            source_system="BROKEN",
            external_code=self.product.code,
            normalized_value=self.product.code,
        )
        conflict._identifier_service_write = True
        conflict.save()
        conflicted = self.service().import_file(
            self.workbook_file(
                [self.row(order_no="CONFLICT", merchant_code=self.product.code)]
            )
        )
        self.assertEqual(conflicted["fail_count"], 1, conflicted)
        self.assertIn("编码冲突", conflicted["errors"][0]["reason"])

    def test_empty_merchant_code_keeps_unique_name_fallback(self):
        result = self.service().import_file(
            self.workbook_file(
                [
                    self.row(
                        order_no="NAME-FALLBACK",
                        merchant_code="",
                        product_name=self.product.name,
                    )
                ]
            )
        )
        self.assertEqual(result["success_count"], 1, result)

    def test_product_mapping_query_count_is_constant(self):
        def parsed_rows(count):
            return [
                ParsedRow(
                    row_number=index + 2,
                    values={
                        "商家编码": self.product.code,
                        "商品名称": self.product.name,
                    },
                    has_formula=False,
                )
                for index in range(count)
            ]

        for count in (1, 100):
            with (
                self.subTest(count=count),
                CaptureQueriesContext(connection) as queries,
            ):
                self.service()._product_maps(parsed_rows(count))
            self.assertEqual(len(queries), 2, [query["sql"] for query in queries])

    def test_unknown_row_exception_is_logged_with_context_and_returns_safe_message(
        self,
    ):
        service = self.service()
        with (
            mock.patch.object(
                service,
                "_find_product",
                side_effect=RuntimeError("secret database detail"),
            ),
            self.assertLogs("allapp.outbound.drop_ship_import", level="ERROR") as logs,
        ):
            result = service.import_file(
                self.workbook_file([self.row(order_no="UNKNOWN")])
            )

        self.assertEqual(result["errors"][0]["reason"], GENERIC_ROW_ERROR)
        self.assertNotIn("secret database detail", result["errors"][0]["reason"])
        record = logs.records[0]
        self.assertIn(f"owner_id={self.owner.id}", record.getMessage())
        self.assertIn(f"user_id={self.user.id}", record.getMessage())
        self.assertIn("row=2", record.getMessage())
        self.assertIsNotNone(record.exc_info)
