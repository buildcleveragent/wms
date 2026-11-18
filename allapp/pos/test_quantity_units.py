"""Unit-aware POS contracts against real MySQL aggregates and rendered exports."""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse
from allapp.products.models import Product, ProductUom

from .exports import build_pos_stats_export_workbook, build_sales_export_workbook
from .models import PosReturn, PosReturnLine, PosSale, PosSaleLine, PosShift
from .quantities import document_quantities, pos_quantities_by_unit, quantity_text
from .services import build_receipt
from .shift_services import serialize_shift


class PosQuantityUnitTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="UNIT-OWNER", name="单位货主")
        self.other_owner = Owner.objects.create(code="UNIT-OTHER", name="另一货主")
        self.warehouse = Warehouse.objects.create(code="UNIT-WH", name="单位仓库")
        self.user = get_user_model().objects.create_user(
            username="unit-cashier", warehouse=self.warehouse
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_possale", "view_posreturn"])
        )
        self.shift = PosShift.objects.create(
            shift_no="UNIT-SHIFT",
            warehouse=self.warehouse,
            cashier=self.user,
            opened_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.bag = ProductUom.objects.create(code="UNIT-BAG", name="袋")
        self.kg = ProductUom.objects.create(code="UNIT-KG", name="千克")
        self.same_name = ProductUom.objects.create(code="UNIT-BAG2", name="袋")
        self.products = [
            Product.objects.create(
                owner=owner,
                code=f"UNIT-P{i}",
                sku=f"UNIT-P{i}",
                name=f"单位商品{i}",
                base_uom=unit,
            )
            for i, (unit, owner) in enumerate(
                [
                    (self.bag, self.owner),
                    (self.bag, self.owner),
                    (self.kg, self.owner),
                    (self.same_name, self.other_owner),
                ]
            )
        ]
        self.sale = self.make_sale("UNIT-SALE")
        self.lines = [
            self.make_line(self.sale, product, i + 1, qty)
            for i, (product, qty) in enumerate(zip(self.products, ["2", "3", "1.250", "4"]))
        ]
        self.void = self.make_sale("UNIT-VOID", status=PosSale.Status.VOIDED)
        self.make_line(self.void, self.products[0], 1, "8")
        self.return_order = PosReturn.objects.create(
            return_no="UNIT-RETURN",
            sale=self.sale,
            warehouse=self.warehouse,
            shift=self.shift,
            cashier=self.user,
            total_amount=Decimal("0.500"),
        )
        PosReturnLine.objects.create(
            return_order=self.return_order,
            sale_line=self.lines[2],
            owner=self.owner,
            product=self.products[2],
            line_no=1,
            qty=Decimal("0.500"),
            price=1,
            amount=Decimal("0.50"),
        )

    def make_sale(self, number, **kwargs):
        return PosSale.objects.create(
            sale_no=number,
            warehouse=self.warehouse,
            cashier=self.user,
            shift=self.shift,
            **kwargs,
        )

    def make_line(self, sale, product, number, qty):
        return PosSaleLine.objects.create(
            sale=sale,
            owner=product.owner,
            product=product,
            line_no=number,
            qty=Decimal(qty),
            price=1,
            amount=Decimal(qty).quantize(Decimal("0.01")),
        )

    def stats(self, **params):
        now = timezone.now()
        today = (timezone.localtime(now) if timezone.is_aware(now) else now).date().isoformat()
        response = self.client.get(
            "/api/pos/stats/",
            {
                "start_date": today,
                "end_date": today,
                **params,
            },
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_stats_group_by_identity_preserve_scope_and_ignore_top_limit(self):
        data = self.stats(top_n=1)
        units = {row["base_unit_id"]: row for row in data["summary"]["quantities_by_unit"]}
        self.assertEqual(len(data["products"]), 1)
        self.assertEqual(len(units), 3)
        self.assertEqual(units[self.bag.id]["total_qty"], "13.000")
        self.assertEqual(units[self.bag.id]["completed_qty"], "5.000")
        self.assertEqual(units[self.bag.id]["voided_qty"], "8.000")
        self.assertEqual(units[self.kg.id]["net_qty"], "0.750")
        self.assertEqual(units[self.same_name.id]["net_qty"], "4.000")
        owner = next(row for row in data["owners"] if row["owner_id"] == self.owner.id)
        self.assertEqual(
            {row["base_unit_id"] for row in owner["quantities_by_unit"]}, {self.bag.id, self.kg.id}
        )
        filtered = self.stats(owner_id=self.other_owner.id)
        self.assertEqual(len(filtered["summary"]["quantities_by_unit"]), 1)
        self.assertEqual(filtered["products"][0]["base_unit_code"], self.same_name.code)
        self.assertEqual(
            self.stats(cashier_id=self.user.id + 1000)["summary"]["quantities_by_unit"], []
        )
        other_wh = Warehouse.objects.create(code="UNIT-WH2", name="其他仓库")
        PosSale.objects.filter(pk=self.sale.pk).update(warehouse=other_wh)
        scoped = self.stats()
        self.assertEqual(scoped["summary"]["completed_qty"], "0.000")
        self.assertEqual(scoped["summary"]["quantities_by_unit"][1]["net_qty"], "-0.500")

    def test_return_only_period_and_voided_returns(self):
        yesterday = timezone.now() - datetime.timedelta(days=2)
        PosSale.objects.filter(pk__in=[self.sale.pk, self.void.pk]).update(created_at=yesterday)
        data = self.stats()
        self.assertEqual(data["products"][0]["base_unit"], "千克")
        self.assertEqual(data["products"][0]["net_qty"], "-0.500")
        self.assertEqual(data["owners"][0]["quantities_by_unit"][0]["net_qty"], "-0.500")
        self.assertEqual(data["summary"]["quantities_by_unit"][0]["net_qty"], "-0.500")
        self.return_order.status = PosReturn.Status.VOIDED
        self.return_order.save(update_fields=["status"])
        self.assertEqual(self.stats()["summary"]["quantities_by_unit"], [])

    def test_receipt_detail_export_and_closed_shift_use_same_units(self):
        receipt = build_receipt(self.sale)
        self.assertEqual(receipt["lines"][2]["unit"], "千克")
        self.assertEqual(receipt["quantity_text"], "5 袋、1.25 千克、4 袋")
        detail = self.client.get(f"/api/pos/sales/{self.sale.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["quantities_by_unit"], receipt["quantities_by_unit"])
        returned = self.client.get(f"/api/pos/returns/{self.return_order.id}/")
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.data["return"]["lines"][0]["base_unit"], "千克")
        self.assertEqual(returned.data["return"]["quantities_by_unit"][0]["qty"], "0.500")
        for _ in range(2):
            printed = self.client.get(f"/api/pos/sales/{self.sale.id}/print/")
            self.assertContains(printed, "1.250 千克")
            self.assertContains(printed, "5 袋、1.25 千克、4 袋")
        workbook = build_sales_export_workbook(
            PosSale.objects.filter(pk=self.sale.pk), include_all_sheet=True
        )
        self.assertEqual(workbook["销售明细"]["Q1"].value, "基本单位")
        self.assertEqual(workbook["销售明细"]["Q4"].value, "千克")
        self.assertEqual(workbook["销售明细"]["I4"].value, Decimal("1.250"))
        self.assertEqual(workbook["退货明细"]["P2"].value, "千克")
        self.assertEqual(workbook["销售单"]["T2"].value, receipt["quantity_text"])
        self.assertEqual(workbook["退货单"]["J2"].value, "0.5 千克")
        stats_book = build_pos_stats_export_workbook(self.stats(), include_all_sheet=True)
        summary = dict(stats_book["统计汇总"].values)
        self.assertEqual(summary["净销售数量"], "5 袋、0.75 千克、4 袋")
        self.assertEqual(stats_book["商品汇总"]["O1"].value, "基本单位")
        self.assertIsInstance(stats_book["商品汇总"]["I2"].value, Decimal)
        self.assertIn("5 袋、0.75 千克", [r[8] for r in stats_book["货主汇总"].values])
        self.assertIn(
            receipt["quantity_text"], [c.value for row in workbook["全部信息"] for c in row]
        )
        self.shift.status = PosShift.Status.CLOSED
        self.shift.save(update_fields=["status"])
        summary = serialize_shift(self.shift)["summary"]
        self.assertEqual(
            quantity_text(summary["quantities_by_unit"], "completed_qty"), receipt["quantity_text"]
        )

    def test_aggregation_has_fixed_query_count_and_document_totals_do_not_query(self):
        with self.assertNumQueries(2):
            rows = pos_quantities_by_unit(PosSaleLine.objects.all(), PosReturnLine.objects.all())
        self.assertEqual(len(rows), 3)
        lines = list(self.sale.lines.select_related("product__base_uom"))
        with self.assertNumQueries(0):
            self.assertEqual(quantity_text(document_quantities(lines)), "5 袋、1.25 千克、4 袋")
        self.assertEqual(quantity_text(None), "单位明细不可用")
        self.assertEqual(quantity_text([]), "0")
        self.assertEqual(quantity_text([{"qty": "0.000"}]), "0 单位未设置")
