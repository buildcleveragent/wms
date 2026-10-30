import io
import threading
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import TransactionTestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse

from .excel_import import IMPORT_SHEET_NAME, OWNER_NAME_PRODUCT_HEADERS, PRODUCT_HEADERS
from .models import Product, ProductCategory, ProductUom


class ProductImportConcurrencyTests(TransactionTestCase):
    def test_concurrent_unidentified_receipts_share_one_archive(self):
        self._assert_concurrent_receipts_share_one_archive(use_owner_names=False)

    def test_concurrent_owner_name_receipts_share_one_archive(self):
        self._assert_concurrent_receipts_share_one_archive(use_owner_names=True)

    def _assert_concurrent_receipts_share_one_archive(self, *, use_owner_names):
        owner = Owner.objects.create(code="IMP-CON", name="并发导入货主")
        uom = ProductUom.objects.create(code="IMP-EA", name="件")
        category = ProductCategory.objects.create(code="IMP-CAT", name="并发分类")
        user = get_user_model().objects.create_superuser(username="import-concurrency")
        warehouse = Warehouse.objects.create(code="IMP-WH", name="并发仓库")
        subwarehouse = Subwarehouse.objects.create(warehouse=warehouse, code="IMPSW", name="子仓")
        location = Location.objects.create(
            warehouse=warehouse, subwarehouse=subwarehouse, code="IMPSW-01-01-01", name="收货位"
        )
        warehouse.default_receive_location = location
        warehouse.save(update_fields=["default_receive_location"])
        OwnerWarehouseBinding.objects.create(owner=owner, warehouse=warehouse)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = IMPORT_SHEET_NAME
        headers = OWNER_NAME_PRODUCT_HEADERS if use_owner_names else PRODUCT_HEADERS
        sheet.append(list(headers))
        row = {
            "货主编码": owner.code,
            "货主名称": owner.name,
            "商品名称": "并发无码商品",
            "基本单位": uom.name,
            "分类": category.name,
            "基本单位数量": 2,
        }
        sheet.append([row.get(header) for header in headers])
        output = io.BytesIO()
        workbook.save(output)
        content = output.getvalue()
        barrier = threading.Barrier(2)

        def upload():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=user.pk))
                barrier.wait(timeout=10)
                response = client.post(
                    "/api/products/import-excel/",
                    {
                        "file": SimpleUploadedFile("products.xlsx", content),
                        "warehouse_id": warehouse.pk,
                    },
                    format="multipart",
                )
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(upload) for _ in range(2)]
            responses = [future.result(timeout=30) for future in futures]

        self.assertEqual([status for status, _ in responses], [200, 200], responses)
        self.assertEqual(sum(data["created_count"] for _, data in responses), 1)
        self.assertEqual(sum(data["reused_count"] for _, data in responses), 1)
        product = Product.objects.get(owner=owner)
        self.assertEqual(InventoryDetail.objects.get(product=product).onhand_qty, 4)
        owner.refresh_from_db()
        self.assertEqual(owner.next_sku_sequence, 2)
