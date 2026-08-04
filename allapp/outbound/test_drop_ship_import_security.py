import io
import zipfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
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
)
from allapp.outbound.models import OutboundOrder
from allapp.products.models import Product, ProductUom


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

    def row(self, order_no="EXCEL-1", qty=1, sku=None):
        return [
            "张三",
            "13800000000",
            "测试地址",
            qty,
            order_no,
            self.product.sku if sku is None else sku,
            "Excel Product",
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
