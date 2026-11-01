"""Cross-surface regressions for catalogue snapshots and financial reporting."""

import datetime
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from openpyxl import load_workbook

from allapp.billing.enums import AccrualStatus
from allapp.billing.models import Bill, BillingAccrual, PaymentAllocation, PaymentReceipt
from allapp.billing.services.receivables import post_receipt, reverse_receipt
from allapp.inventory.models import InventorySnapshotDaily
from allapp.reports import tests as fixtures
from allapp.reports.services_operations import (
    OperationFilters,
    build_operations_detail_rows,
    iter_operations_details,
    paginate_operations_details,
)


class BossUpdateTests(TestCase):
    setUp = fixtures.BossDashboardApiTests.setUp

    def get_report(self, name, **params):
        response = self.client.get(f"/api/reports/boss/{name}/", {"owner": self.owner.pk, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_inventory_identifies_same_name_products_and_searches_model(self):
        from allapp.inventory.models import InventoryDetail
        from allapp.products.models import Brand, Product

        self.product_a.name = "汽水"
        self.product_a.model = "橙味"
        self.product_a.spec = "480ml"
        self.product_a.brand = Brand.objects.create(code="BOSS-BRAND", name="品牌甲")
        self.product_a.save()
        other = Product.objects.create(
            owner=self.owner,
            code="BOSS-SAME-NAME",
            name="汽水",
            model="原味",
            spec="480ml",
            brand=self.product_a.brand,
            base_uom=self.uom_a,
        )
        InventoryDetail.objects.create(
            owner=self.owner, product=other, location=self.location, onhand_qty=2
        )
        all_rows = self.get_report("inventory/details")["results"]
        self.assertEqual({row["product_id"] for row in all_rows}, {self.product_a.pk, other.pk})
        data = self.get_report("inventory/details", search="品牌甲·橙味")
        self.assertGreater(data["count"], 0)
        for row in data["results"]:
            self.assertEqual(row["product_id"], self.product_a.pk)
            self.assertEqual(row["product_display_name"], "汽水 · 品牌甲·橙味")
            self.assertEqual(row["product_spec"], "480ml")
        self.assertNotEqual(self.product_a.pk, other.pk)

    def test_plan_pagination_and_export_keep_document_identity_after_rename(self):
        filters = OperationFilters(
            start_date=self.today,
            end_date=self.today,
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
            metric_basis="plan",
        )
        from allapp.inbound.models import InboundOrderLine
        from allapp.outbound.models import OutboundOrderLine

        identity = {"name": "旧品名", "spec": "@旧规格", "brand_model": "=1+1"}
        for model in (InboundOrderLine, OutboundOrderLine):
            model.objects.filter(product=self.product_a).update(product_snapshot=identity)
        before = build_operations_detail_rows(user=self.user, filters=filters)
        self.assertTrue(before)
        self.product_a.name = "新通用品名"
        self.product_a.model = "=1+1"
        self.product_a.spec = "新规格"
        self.product_a.save()
        count, paged = paginate_operations_details(
            user=self.user, filters=filters, page=1, page_size=100
        )
        streamed = list(iter_operations_details(user=self.user, filters=filters))
        objects = build_operations_detail_rows(user=self.user, filters=filters)
        expected = {row["detail_id"]: row["product"] for row in before}
        self.assertEqual(count, len(before))
        for rows in (paged, streamed, objects):
            self.assertEqual({row["detail_id"]: row["product"] for row in rows}, expected)
        response = self.client.post(
            "/api/reports/boss/operations/export/",
            {
                "date_from": str(self.today),
                "date_to": str(self.today),
                "owner": self.owner.pk,
                "warehouse": self.warehouse.pk,
                "metric_basis": "plan",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content)
        sheet = load_workbook(BytesIO(content))["operations"]
        rows = list(sheet.values)
        brand_col = rows[0].index("brand_model") + 1
        for cells in list(sheet.rows)[1:]:
            self.assertEqual(cells[brand_col - 1].value, "=1+1")
            self.assertEqual(cells[brand_col - 1].data_type, "s")
        self.assertIn("brand_model", rows[0])
        self.assertIn("product_spec", rows[0])
        for row in rows[1:]:
            record = dict(zip(rows[0], row))
            self.assertEqual(
                record["product_name"],
                "旧品名",
            )

    def test_signed_reversal_is_used_by_performance_and_resource_yield(self):
        original = BillingAccrual.objects.get(acc_fingerprint="boss-home-acc-a")
        BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period_a,
            rule=self.rule_a,
            charge_type=original.charge_type,
            service_date=self.today,
            currency="CNY",
            quantity=1,
            unit_price=-100,
            amount=-100,
            tax_amount=-10,
            status=AccrualStatus.VOID,
            is_reversal=True,
            reversal_of=original,
            acc_fingerprint="boss-update-negative-reversal",
        )
        BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period_a,
            rule=self.rule_a,
            charge_type=original.charge_type,
            service_date=self.today,
            currency="CNY",
            quantity=1,
            unit_price=999,
            amount=999,
            tax_amount=0,
            status=AccrualStatus.VOID,
            is_reversal=True,
            reversal_of=original,
            acc_fingerprint="boss-update-positive-replacement",
        )
        self.snapshot(self.today)
        home = self.get_report("home")
        performance = self.get_report("performance", date_from=str(self.today))
        resource = self.get_report("resource-yield", date_from=str(self.today))
        self.assertEqual(Decimal(str(home["summary"]["accruals_by_currency"][0]["total"])), 0)
        self.assertEqual(Decimal(str(performance["selected_period_vs_prior"][0]["subtotal"])), 0)
        self.assertEqual(
            Decimal(str(resource["rankings_by_currency"][0]["items"][0]["revenue_subtotal"])), 0
        )

    def receipt(self, amount, day, *, date_quality="VERIFIED"):
        bill = Bill.objects.get(invoice_no="BILL-BOSS-A")
        receipt = PaymentReceipt.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            currency="CNY",
            receipt_no=f"BOSS-UPDATE-{PaymentReceipt.objects.count()}",
            receipt_date=day,
            date_quality=date_quality,
            amount=Decimal(amount),
            created_by=self.user,
        )
        PaymentAllocation.objects.create(receipt=receipt, bill=bill, amount=Decimal(amount))
        post_receipt(receipt.pk, by_user=self.user)
        return receipt

    def assert_balance(self, expected, day=None):
        params = {
            "date_from": str(self.today - datetime.timedelta(days=4)),
            "date_to": str(day or self.today),
        }
        home = self.get_report("home", **params)
        groups = home["summary"]["overdue_receivables_by_currency"]
        self.assertEqual(
            sum((Decimal(str(g["total"])) for g in groups), Decimal(0)), Decimal(expected)
        )
        cockpit = self.get_report("receivables", **params)
        self.assertEqual(
            Decimal(str(cockpit["outstanding_by_currency"][0]["total"])), Decimal(expected)
        )
        bills = self.get_report("receivables/bills", **params)
        self.assertEqual(Decimal(str(bills["results"][0]["outstanding_amount"])), Decimal(expected))
        alerts = self.get_report("alerts", **params)["sections"]["overdue_bills"]
        self.assertEqual(alerts["count"], int(Decimal(expected) > 0))
        if alerts["count"]:
            item = alerts["items"][0]
            self.assertEqual(Decimal(str(item["outstanding_amount"])), Decimal(expected))
            detail = self.get_report(f"alerts/sections/overdue_bills/bill/{item['id']}", **params)
            self.assertEqual(
                Decimal(str(detail["detail"]["outstanding_amount"])), Decimal(expected)
            )

    def test_partial_payment_and_reversal_match_all_surfaces(self):
        receipt = self.receipt("40", self.today)
        self.assert_balance("70")
        reverse_receipt(
            receipt.pk,
            receipt_no="BOSS-UPDATE-REVERSE",
            reversal_date=self.today,
            by_user=self.user,
        )
        self.assert_balance("110")

    def test_historical_balance_ignores_later_payment_even_when_current_bill_paid(self):
        self.receipt("110", self.today)
        self.assert_balance("0")
        # Move due date earlier so the previous day is also overdue.
        bill = Bill.objects.get(invoice_no="BILL-BOSS-A")
        bill.due_date = bill.issue_date
        bill.save(update_fields=["due_date"])
        self.assert_balance("110", self.today - datetime.timedelta(days=1))

    def test_unknown_receipt_date_uses_creation_day_for_historical_balance(self):
        self.receipt("40", self.today - datetime.timedelta(days=2), date_quality="UNKNOWN")
        bill = Bill.objects.get(invoice_no="BILL-BOSS-A")
        bill.due_date = bill.issue_date
        bill.save(update_fields=["due_date"])
        self.assert_balance("70")
        self.assert_balance("110", self.today - datetime.timedelta(days=1))

    def snapshot(self, day, quantity=1):
        return InventorySnapshotDaily.objects.create(
            snapshot_date=day,
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location,
            product=self.product_a,
            onhand_qty=quantity,
            available_qty=quantity,
            base_unit_code=self.uom_a.code,
            base_unit_source="VERIFIED",
            snapshot_source="TX_ROLLFORWARD",
            unit_volume_m3_snapshot=Decimal("0.5"),
        )

    def test_zero_snapshot_is_available_and_missing_snapshot_is_not(self):
        day = self.today - datetime.timedelta(days=1)
        self.snapshot(day, quantity=0)
        data = self.get_report("inventory", date_from=str(day), date_to=str(day))
        self.assertNotEqual(data["meta"]["data_status"], "UNAVAILABLE")
        self.assertEqual(data["summary"]["sku_count"], 0)
        missing = day - datetime.timedelta(days=1)
        data = self.get_report("inventory", date_from=str(missing), date_to=str(missing))
        self.assertEqual(data["meta"]["data_status"], "UNAVAILABLE")

    def test_unverified_snapshot_units_are_visible_but_not_summed_as_trusted(self):
        day = self.today - datetime.timedelta(days=1)
        self.snapshot(day, quantity=3)
        inferred = InventorySnapshotDaily.objects.create(
            snapshot_date=day,
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location_2,
            product=self.product_a,
            onhand_qty=7,
            available_qty=7,
            base_unit_code=self.uom_a.code,
            base_unit_source="LEGACY_INFERRED",
            snapshot_source="TX_ROLLFORWARD",
        )
        params = {"date_from": str(day), "date_to": str(day)}
        overview = self.get_report("inventory", **params)
        self.assertEqual(Decimal(str(overview["summary"]["quantity_by_uom"][0]["onhand_qty"])), 3)
        detail = self.get_report("inventory/details", **params)
        self.assertEqual(detail["count"], 2)
        self.assertIn(
            "HISTORICAL_INVENTORY_UNIT_INFERRED",
            {row["code"] for row in detail["meta"]["warnings"]},
        )
        self.assertEqual(
            {row["base_unit_source"] for row in detail["results"]}, {"VERIFIED", "LEGACY_INFERRED"}
        )
        for unit_code, source in ((self.uom_a.code, "UNKNOWN"), ("", "VERIFIED")):
            InventorySnapshotDaily.objects.filter(pk=inferred.pk).update(
                base_unit_code=unit_code, base_unit_source=source
            )
            overview = self.get_report("inventory", **params)
            self.assertEqual(
                Decimal(str(overview["summary"]["quantity_by_uom"][0]["onhand_qty"])), 3
            )
            self.assertIn(
                "HISTORICAL_INVENTORY_UNIT_UNKNOWN",
                {row["code"] for row in overview["meta"]["warnings"]},
            )

    def test_fifo_history_detail_does_not_claim_current_positions_are_historical(self):
        day = self.today - datetime.timedelta(days=1)
        detail = self.get_report("inventory-risk/details", date_from=str(day), date_to=str(day))
        self.assertEqual(detail["meta"]["data_status"], "UNAVAILABLE")
        self.assertEqual(detail["results"], [])
