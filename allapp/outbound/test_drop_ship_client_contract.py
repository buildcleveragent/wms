from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]


class DropShipClientContractTests(SimpleTestCase):
    def test_upload_sends_selected_warehouse_as_multipart_form_data(self):
        request_source = (
            REPO_ROOT / "wmsownersale" / "utils" / "request.js"
        ).read_text(encoding="utf-8")

        self.assertIn("importDropShipExcel(filePath, warehouseId)", request_source)
        self.assertIn("formData:", request_source)
        self.assertIn("warehouse_id: String(warehouseId || '')", request_source)
        self.assertIn("message: getFriendlyMessage(data, '上传失败')", request_source)

    def test_import_page_only_accepts_xlsx_and_passes_warehouse(self):
        page_source = (
            REPO_ROOT / "wmsownersale" / "pages" / "orders" / "import_drop_ship.vue"
        ).read_text(encoding="utf-8")
        picker_source = (
            REPO_ROOT / "wmsownersale" / "utils" / "filePicker.js"
        ).read_text(encoding="utf-8")

        self.assertIn("chooseExcelFile", page_source)
        self.assertIn("extension: ['.xlsx']", picker_source)
        self.assertNotIn("extension: ['.xlsx', '.xls']", picker_source)
        self.assertIn("name.toLowerCase().endsWith('.xlsx')", picker_source)
        self.assertIn(
            "api.importDropShipExcel(filePath.value, warehouseId.value)",
            page_source,
        )
        self.assertIn("货主商品编码或当前有效外部系统商品编码", page_source)
        self.assertIn("不接受仓库SKU编码或条码", page_source)

    def test_import_page_handles_zero_one_and_multiple_warehouses(self):
        page_source = (
            REPO_ROOT / "wmsownersale" / "pages" / "orders" / "import_drop_ship.vue"
        ).read_text(encoding="utf-8")

        self.assertIn("const response = await api.warehouses()", page_source)
        self.assertIn("warehouses.value.length === 1", page_source)
        self.assertIn("warehouses.length > 1", page_source)
        self.assertIn("当前货主未配置可用出库仓库，请联系管理员", page_source)
        self.assertIn(':disabled="!filePath || !warehouseId || uploading"', page_source)
