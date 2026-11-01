import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook

from allapp.baseinfo.models import Owner
from allapp.inbound.models import InboundReceiptLine
from allapp.locations.models import Warehouse
from allapp.pos.exports import build_sales_export_workbook
from allapp.pos.models import PosReturn, PosReturnLine, PosSale, PosSaleLine, PosShift
from allapp.pos.serializers import PosSaleLineReadSerializer
from allapp.pos.services import build_receipt
from allapp.products.excel_export import build_product_export_workbook
from allapp.products.identifier_lookup import filter_by_product_search
from allapp.products.models import Brand, Product, ProductCategory, ProductUom
from allapp.products.serializers import ProductSerializer


class ProductIdentityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="IDENTITY", name="Identity owner")
        cls.brand = Brand.objects.create(code="DY", name="大窑")
        cls.uom = ProductUom.objects.create(code="IDENTITY-EA", name="瓶")
        cls.category = ProductCategory.objects.create(code="IDENTITY-DRINK", name="饮品")
        cls.product = Product.objects.create(
            owner=cls.owner,
            code="IDENTITY-01",
            name="大窑橙诺汽水",
            model="橙诺橙味",
            brand=cls.brand,
            spec="480ml",
            base_uom=cls.uom,
            category=cls.category,
            batch_control=False,
            expiry_control=False,
            expiry_basis=None,
        )
        cls.warehouse = Warehouse.objects.create(code="ID-WH", name="Identity warehouse")

    def sale_line(self, product=None):
        sale = PosSale.objects.create(
            sale_no=f"IDENTITY-{PosSale.objects.count()}", warehouse=self.warehouse
        )
        return PosSaleLine.objects.create(
            sale=sale,
            product=product or self.product,
            owner=self.owner,
            line_no=1,
            qty=2,
            price=3,
            amount=6,
        )

    def test_catalog_exposes_model_and_composed_name(self):
        data = ProductSerializer(self.product).data
        self.assertEqual(data["brand_model"], "大窑·橙诺橙味")
        self.assertEqual(data["name"], "大窑橙诺汽水")
        self.assertIn("橙诺橙味", data["display_name"])

    def test_legacy_receipt_text_is_visible_before_backfill_without_a_product(self):
        line = InboundReceiptLine(product_name="手填旧品名", spec="手填旧规格")
        self.assertEqual(line.frozen_product_name, "手填旧品名")
        self.assertEqual(line.frozen_product_spec, "手填旧规格")

    def test_archive_export_keeps_generic_name_and_model_in_separate_columns(self):
        self.product.name = "汽水"
        self.product.save(update_fields=["name"])
        content, count, _ = build_product_export_workbook(self.owner)
        self.assertEqual(count, 1)
        sheet = load_workbook(io.BytesIO(content))["商品导入"]
        rows = list(sheet.values)
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["商品名称"], "汽水")
        self.assertEqual(row["型号"], "橙诺橙味")
        self.assertEqual(row["品牌型号"], "大窑·橙诺橙味")
        self.assertEqual(row["规格"], "480ml")

    def test_history_remains_frozen_after_product_and_brand_rename(self):
        line = self.sale_line()
        self.product.name, self.product.model = "汽水", "橙味新版"
        self.product.save(update_fields=["name", "model"])
        self.brand.name = "新品牌"
        self.brand.save(update_fields=["name"])
        line.refresh_from_db()
        data = PosSaleLineReadSerializer(line).data
        self.assertEqual(data["product_name"], "大窑橙诺汽水")
        self.assertEqual(data["brand_model"], "大窑·橙诺橙味")
        receipt_line = build_receipt(line.sale)["lines"][0]
        self.assertEqual(receipt_line["name"], "大窑橙诺汽水")
        self.assertEqual(receipt_line["brand_model"], "大窑·橙诺橙味")
        self.assertEqual(receipt_line["spec"], "480ml")
        line.qty = 3
        line.save(update_fields=["qty"])
        self.assertEqual(line.frozen_product_name, "大窑橙诺汽水")
        fresh = self.sale_line()
        self.assertEqual(fresh.frozen_product_name, "汽水")
        self.assertEqual(fresh.frozen_brand_model, "新品牌·橙味新版")

    def test_legacy_backfill_required_and_idempotent(self):
        line = self.sale_line()
        PosSaleLine.objects.filter(pk=line.pk).update(product_snapshot={})
        self.product.name = "汽水"
        with self.assertRaisesMessage(ValidationError, "历史单据"):
            self.product.save(update_fields=["name"])
        call_command("backfill_product_snapshots", stdout=io.StringIO())
        line.refresh_from_db()
        self.assertEqual(line.product_snapshot, {})
        call_command("backfill_product_snapshots", apply=True, batch_size=1, stdout=io.StringIO())
        self.product.save(update_fields=["name"])
        call_command("backfill_product_snapshots", apply=True, stdout=io.StringIO())
        line.refresh_from_db()
        self.assertEqual(line.frozen_product_name, "大窑橙诺汽水")

    def test_return_and_export_inherit_original_sale_after_rename(self):
        line = self.sale_line()
        self.product.name, self.product.model = "汽水", "新型号"
        self.product.save(update_fields=["name", "model"])
        user = get_user_model().objects.create_user(username="identity-cashier")
        shift = PosShift.objects.create(
            shift_no="IDENTITY-SHIFT",
            warehouse=self.warehouse,
            cashier=user,
            opened_at=timezone.now(),
        )
        returned = PosReturn.objects.create(
            return_no="IDENTITY-RETURN",
            sale=line.sale,
            warehouse=self.warehouse,
            shift=shift,
        )
        return_line = PosReturnLine.objects.create(
            return_order=returned,
            sale_line=line,
            product=self.product,
            owner=self.owner,
            line_no=1,
            qty=1,
            price=3,
            amount=3,
        )
        self.assertEqual(return_line.product_snapshot, line.product_snapshot)
        workbook = build_sales_export_workbook(PosSale.objects.filter(pk=line.sale_id))
        for title in ("销售明细", "退货明细"):
            rows = list(workbook[title].values)
            values = dict(zip(rows[0], rows[1]))
            self.assertEqual(values["商品名称"], "大窑橙诺汽水")
            self.assertEqual(values["品牌型号"], "大窑·橙诺橙味")
            self.assertEqual(values["规格"], "480ml")

    def test_same_names_and_specs_are_separate_products_in_search(self):
        other = Product.objects.create(
            owner=self.owner,
            code="IDENTITY-02",
            name=self.product.name,
            model="嘉宾原味",
            brand=self.brand,
            spec="480ml",
            base_uom=self.uom,
        )
        matches = filter_by_product_search(
            Product.objects.all(), "大窑·嘉宾原味", product_field="pk"
        )
        self.assertEqual(list(matches.values_list("pk", flat=True)), [other.pk])
        self.assertNotEqual(other.sku, self.product.sku)
        self.assertEqual(
            filter_by_product_search(
                Product.objects.all(), "大窑 橙诺橙味", product_field="pk"
            ).get(),
            self.product,
        )

    def test_model_is_literal_text_in_detail_and_combined_excel_sheets(self):
        self.product.brand = None
        self.product.model = "=1+1"
        self.product.save(update_fields=["brand", "model"])
        line = self.sale_line()
        workbook = build_sales_export_workbook(
            PosSale.objects.filter(pk=line.sale_id), include_all_sheet=True
        )
        output = io.BytesIO()
        workbook.save(output)
        restored = load_workbook(io.BytesIO(output.getvalue()))
        for title in ("销售明细", "全部信息"):
            cells = [cell for row in restored[title] for cell in row if cell.value == "=1+1"]
            self.assertEqual(len(cells), 1)
            self.assertEqual(cells[0].data_type, "s")

    def test_bulk_rename_cannot_bypass_history_guard(self):
        with self.assertRaises(ValueError):
            Product.objects.filter(pk=self.product.pk).update(name="汽水")
        with self.assertRaises(ValidationError):
            Brand.objects.filter(pk=self.brand.pk).update(name="新品牌")

    def test_replacing_product_refreshes_snapshot_but_partial_save_does_not(self):
        line = self.sale_line()
        other = Product.objects.create(
            owner=self.owner, code="IDENTITY-03", name="抽纸", model="超韧", base_uom=self.uom
        )
        line.product = other
        line.qty = 4
        line.save(update_fields=["qty"])
        line.refresh_from_db()
        self.assertEqual(line.frozen_product_name, self.product.name)
        line.product = other
        line.save(update_fields=["product"])
        line.refresh_from_db()
        self.assertEqual(line.frozen_product_name, "抽纸")

    def test_mapping_preview_apply_and_stale_file_rejection(self):
        line = self.sale_line()
        identifiers = (self.product.pk, self.product.code, self.product.sku)
        with TemporaryDirectory() as directory:
            mapping = Path(directory) / "mapping.csv"
            call_command(
                "product_identity_mapping",
                owner=self.owner.code,
                export=str(mapping),
                stdout=io.StringIO(),
            )
            with mapping.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            rows[1][5] = "汽水"
            with mapping.open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)
            call_command(
                "product_identity_mapping",
                owner=self.owner.code,
                input=str(mapping),
                report=str(Path(directory) / "preview.json"),
                stdout=io.StringIO(),
            )
            self.product.refresh_from_db()
            self.assertEqual(self.product.name, "大窑橙诺汽水")
            call_command(
                "product_identity_mapping",
                owner=self.owner.code,
                input=str(mapping),
                report=str(Path(directory) / "applied.json"),
                apply=True,
                stdout=io.StringIO(),
            )
            self.product.refresh_from_db()
            self.assertEqual(self.product.name, "汽水")
            self.assertEqual((self.product.pk, self.product.code, self.product.sku), identifiers)
            self.assertTrue(json.loads((Path(directory) / "applied.json").read_text())["applied"])
            with self.assertRaises(CommandError):
                call_command(
                    "product_identity_mapping",
                    owner=self.owner.code,
                    input=str(mapping),
                    report=str(Path(directory) / "stale.json"),
                    apply=True,
                    stdout=io.StringIO(),
                )
        line.refresh_from_db()
        self.assertEqual(line.frozen_product_name, "大窑橙诺汽水")

    def test_mapping_rolls_back_valid_rows_when_later_row_is_invalid(self):
        Product.objects.create(
            owner=self.owner,
            code="IDENTITY-99",
            name="纸巾",
            base_uom=self.uom,
            category=self.category,
        )
        with TemporaryDirectory() as directory:
            mapping = Path(directory) / "mapping.csv"
            report = Path(directory) / "result.json"
            call_command(
                "product_identity_mapping",
                owner=self.owner.code,
                export=str(mapping),
                stdout=io.StringIO(),
            )
            with mapping.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            rows[1][5] = "汽水"
            rows[2][6] = "MISSING-BRAND"
            with mapping.open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)
            with self.assertRaises(CommandError):
                call_command(
                    "product_identity_mapping",
                    owner=self.owner.code,
                    input=str(mapping),
                    report=str(report),
                    apply=True,
                    stdout=io.StringIO(),
                )
            self.product.refresh_from_db()
            self.assertEqual(self.product.name, "大窑橙诺汽水")
            self.assertFalse(json.loads(report.read_text())["applied"])
