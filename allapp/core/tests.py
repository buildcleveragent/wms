import datetime
import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.billing.enums import AccrualStatus, CalcMethod, ChargeType
from allapp.billing.models import (
    Bill,
    BillLine,
    BillingAccrual,
    BillingPeriod,
    BillingRule,
)
from allapp.core.models import DocSequence
from allapp.inventory.models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
)
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom


class CoreWarehouseScopeTests(TestCase):
    def test_doc_sequence_without_warehouse_stays_null(self):
        owner = Owner.objects.create(name="Owner Core", code="OWN-CORE")
        biz_date = datetime.date(2026, 3, 29)

        next_no = DocSequence.next_number(
            doc_type="CORE",
            warehouse=None,
            owner=owner,
            biz_date=biz_date,
        )

        seq = DocSequence.objects.get(
            doc_type="CORE",
            biz_date=biz_date,
            owner=owner,
            warehouse__isnull=True,
        )
        self.assertEqual(next_no, 1)
        self.assertIsNone(seq.warehouse_id)


class DataAccuracyCommandTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Accuracy Owner", code="ACC-OWN")
        self.warehouse = Warehouse.objects.create(
            code="ACC-WH", name="Accuracy Warehouse"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="ACC01",
            name="Accuracy Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="ACC01-01-01-01",
            name="Accuracy Location",
        )
        self.base_uom = ProductUom.objects.create(
            code="PCS-ACC", name="Piece", decimal_places=0
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="ACC-SKU",
            name="Accuracy SKU",
            sku="ACC-SKU",
            base_uom=self.base_uom,
            volume=Decimal("0.250000"),
            price=Decimal("10.00"),
            batch_control=False,
            expiry_control=False,
        )
        self.period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-04",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 4, 30),
            currency="CNY",
        )
        self.rule = BillingRule.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            charge_type=ChargeType.STORAGE,
            calc_method=CalcMethod.PER_CBM_DAY,
            unit_price=Decimal("2.0000"),
            currency="CNY",
        )

    def create_posted_transaction(
        self,
        *,
        product=None,
        qty_delta="1.0000",
        batch_no="",
        production_date=None,
        expiry_date=None,
        serial_no="",
        location=None,
    ):
        next_src_id = InventoryTransaction.objects.count() + 1
        qty_delta = Decimal(qty_delta)
        return InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE if qty_delta > 0 else InvTxType.ISSUE,
            owner=self.owner,
            product=product or self.product,
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            location=location or self.location,
            qty_delta=qty_delta,
            batch_no=batch_no,
            production_date=production_date,
            expiry_date=expiry_date,
            serial_no=serial_no,
            src_model="core.data_accuracy.tests",
            src_id=next_src_id,
            src_line_id=next_src_id,
            src_no=f"ACC-TX-{next_src_id}",
            posted_at=datetime.datetime(2026, 4, 2, 10, 0),
        )

    def create_consistent_inventory(self):
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("1.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        self.create_posted_transaction(qty_delta="5.0000")
        InventorySummary.objects.create(
            owner=self.owner,
            product=self.product,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("1.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )

    def create_consistent_billing(self):
        accrual = BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period,
            charge_type=ChargeType.STORAGE,
            rule=self.rule,
            service_date=datetime.date(2026, 4, 2),
            currency="CNY",
            quantity=Decimal("1.2500"),
            unit_price=Decimal("2.0000"),
            amount=Decimal("2.50"),
            tax_amount=Decimal("0.00"),
            status=AccrualStatus.INVOICED,
            acc_fingerprint="acc-accuracy-1",
        )
        bill = Bill.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period,
            invoice_no="INV-ACC-001",
            issue_date=datetime.date(2026, 4, 3),
            due_date=datetime.date(2026, 4, 10),
            currency="CNY",
            subtotal=Decimal("2.50"),
            tax_total=Decimal("0.00"),
            total=Decimal("2.50"),
        )
        BillLine.objects.create(
            bill=bill,
            accrual=accrual,
            charge_type=ChargeType.STORAGE,
            service_date=accrual.service_date,
            quantity=accrual.quantity,
            unit_price=accrual.unit_price,
            amount=accrual.amount,
            tax_amount=accrual.tax_amount,
            description="accuracy line",
        )
        return bill

    def test_reconcile_data_accuracy_command_reports_pass_for_consistent_data(self):
        self.create_consistent_inventory()
        self.create_consistent_billing()

        out = StringIO()
        call_command(
            "reconcile_data_accuracy",
            "--owner",
            str(self.owner.id),
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issue_count"], 0)

    def test_reconcile_data_accuracy_command_detects_inventory_summary_mismatch(self):
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        InventorySummary.objects.create(
            owner=self.owner,
            product=self.product,
            onhand_qty=Decimal("4.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )

        out = StringIO()
        call_command(
            "reconcile_data_accuracy",
            "--owner",
            str(self.owner.id),
            "--inventory-only",
            "--json",
            stdout=out,
        )
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertGreater(payload["issue_count"], 0)
        self.assertEqual(
            payload["inventory"]["checks"][2]["name"], "inventory_summary_vs_detail"
        )
        self.assertFalse(payload["inventory"]["checks"][2]["ok"])

        with self.assertRaises(CommandError):
            call_command(
                "reconcile_data_accuracy",
                "--owner",
                str(self.owner.id),
                "--inventory-only",
                "--fail-on-issues",
            )

    def test_reconcile_data_accuracy_command_detects_bill_header_mismatch(self):
        self.create_consistent_billing()
        Bill.objects.filter(invoice_no="INV-ACC-001").update(total=Decimal("9.99"))

        out = StringIO()
        call_command(
            "reconcile_data_accuracy",
            "--owner",
            str(self.owner.id),
            "--billing-only",
            "--period",
            str(self.period.id),
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        check_names = [
            check["name"] for check in payload["billing"]["checks"] if not check["ok"]
        ]
        self.assertIn("bill_header_totals", check_names)

    def test_reconcile_data_accuracy_command_detects_inventory_transaction_replay_mismatch(
        self,
    ):
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        InventorySummary.objects.create(
            owner=self.owner,
            product=self.product,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        self.create_posted_transaction(qty_delta="4.0000")

        out = StringIO()
        call_command(
            "reconcile_data_accuracy",
            "--owner",
            str(self.owner.id),
            "--inventory-only",
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        checks = {check["name"]: check for check in payload["inventory"]["checks"]}
        self.assertFalse(payload["ok"])
        self.assertIn("inventory_transaction_replay_onhand", checks)
        self.assertFalse(checks["inventory_transaction_replay_onhand"]["ok"])
        self.assertEqual(
            checks["inventory_transaction_replay_onhand"]["samples"][0]["issue"],
            "detail_onhand_replay_mismatch",
        )
        self.assertEqual(
            checks["inventory_transaction_replay_onhand"]["samples"][0][
                "replayed_onhand_qty"
            ],
            "4.0000",
        )

    def test_reconcile_data_accuracy_command_detects_batch_serial_expiry_issues(self):
        batch_product = Product.objects.create(
            owner=self.owner,
            code="ACC-BATCH",
            name="Accuracy Batch SKU",
            sku="ACC-BATCH",
            base_uom=self.base_uom,
            volume=Decimal("0.100000"),
            price=Decimal("12.00"),
            batch_control=True,
            expiry_control=False,
        )
        expiry_product = Product.objects.create(
            owner=self.owner,
            code="ACC-EXP",
            name="Accuracy Expiry SKU",
            sku="ACC-EXP",
            base_uom=self.base_uom,
            volume=Decimal("0.200000"),
            price=Decimal("15.00"),
            batch_control=False,
            expiry_control=True,
            expiry_basis="MFG",
            shelf_life_days=30,
            expiry_warning_days=5,
        )
        serial_product = Product.objects.create(
            owner=self.owner,
            code="ACC-SERIAL",
            name="Accuracy Serial SKU",
            sku="ACC-SERIAL",
            base_uom=self.base_uom,
            volume=Decimal("0.050000"),
            price=Decimal("20.00"),
            serial_control=True,
            batch_control=False,
            expiry_control=False,
        )

        batch_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=batch_product,
            warehouse=self.warehouse,
            location=self.location,
            batch_no="LOT-001",
            onhand_qty=Decimal("1.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        batch_tx = self.create_posted_transaction(
            product=batch_product,
            qty_delta="1.0000",
            batch_no="LOT-001",
        )
        InventoryDetail.objects.filter(pk=batch_detail.pk).update(batch_no="")
        InventoryTransaction.objects.filter(pk=batch_tx.pk).update(batch_no="")

        expiry_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=expiry_product,
            warehouse=self.warehouse,
            location=self.location,
            production_date=datetime.date(2026, 4, 1),
            expiry_date=datetime.date(2026, 5, 1),
            onhand_qty=Decimal("2.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        expiry_tx = self.create_posted_transaction(
            product=expiry_product,
            qty_delta="2.0000",
            production_date=datetime.date(2026, 4, 1),
            expiry_date=datetime.date(2026, 5, 1),
        )
        InventoryDetail.objects.filter(pk=expiry_detail.pk).update(
            production_date=None,
            expiry_date=None,
        )
        InventoryTransaction.objects.filter(pk=expiry_tx.pk).update(
            production_date=datetime.date(2026, 4, 10),
            expiry_date=datetime.date(2026, 4, 9),
        )

        serial_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=serial_product,
            warehouse=self.warehouse,
            location=self.location,
            serial_no="SN-001",
            onhand_qty=Decimal("1.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )
        serial_tx = self.create_posted_transaction(
            product=serial_product,
            qty_delta="1.0000",
            serial_no="SN-001",
        )
        InventoryDetail.objects.filter(pk=serial_detail.pk).update(
            serial_no="",
            serial_no_norm=None,
        )
        InventoryTransaction.objects.filter(pk=serial_tx.pk).update(
            serial_no="",
            qty_delta=Decimal("2.0000"),
        )

        out = StringIO()
        call_command(
            "reconcile_data_accuracy",
            "--owner",
            str(self.owner.id),
            "--warehouse",
            str(self.warehouse.id),
            "--inventory-only",
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        checks = {check["name"]: check for check in payload["inventory"]["checks"]}
        self.assertFalse(payload["ok"])
        self.assertFalse(checks["inventory_batch_tracking_integrity"]["ok"])
        self.assertFalse(checks["inventory_expiry_tracking_integrity"]["ok"])
        self.assertFalse(checks["inventory_serial_tracking_integrity"]["ok"])
        self.assertIn(
            "missing_batch_no",
            checks["inventory_batch_tracking_integrity"]["samples"][0]["problems"],
        )
        self.assertIn(
            "missing_expiry_date",
            checks["inventory_expiry_tracking_integrity"]["samples"][0]["problems"],
        )
        serial_problem_text = ",".join(
            sample["problems"]
            for sample in checks["inventory_serial_tracking_integrity"]["samples"]
        )
        self.assertIn("missing_serial_no", serial_problem_text)
        self.assertIn("tx_abs_qty_not_one", serial_problem_text)

    def test_reconcile_data_accuracy_cleanup_command_applies_safe_fixes(self):
        self.create_consistent_inventory()
        self.create_consistent_billing()
        InventorySummary.objects.filter(
            owner=self.owner,
            product=self.product,
        ).update(
            onhand_qty=Decimal("4.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            available_qty=Decimal("4.0000"),
        )
        Bill.objects.filter(invoice_no="INV-ACC-001").update(total=Decimal("9.99"))

        out = StringIO()
        call_command(
            "reconcile_data_accuracy_cleanup",
            "--owner",
            str(self.owner.id),
            "--apply-safe-fixes",
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        self.assertFalse(payload["before"]["ok"])
        self.assertEqual(payload["before"]["issue_count"], 2)
        self.assertTrue(payload["after"]["ok"])
        self.assertEqual(payload["after"]["issue_count"], 0)
        self.assertEqual(
            payload["fixes"]["inventory_summaries"]["updated"],
            1,
        )
        self.assertEqual(payload["fixes"]["bill_headers"]["updated"], 1)

        summary = InventorySummary.objects.get(owner=self.owner, product=self.product)
        bill = Bill.objects.get(invoice_no="INV-ACC-001")
        self.assertEqual(summary.onhand_qty, Decimal("5.0000"))
        self.assertEqual(summary.available_qty, Decimal("4.0000"))
        self.assertEqual(bill.total, Decimal("2.50"))

    def test_reconcile_data_accuracy_cleanup_command_skips_summary_rebuild_for_warehouse_scope(self):
        self.create_consistent_inventory()

        out = StringIO()
        call_command(
            "reconcile_data_accuracy_cleanup",
            "--owner",
            str(self.owner.id),
            "--warehouse",
            str(self.warehouse.id),
            "--apply-safe-fixes",
            "--json",
            stdout=out,
        )

        payload = json.loads(out.getvalue())
        self.assertTrue(payload["after"]["ok"])
        self.assertTrue(payload["fixes"]["inventory_summaries"]["skipped"])

    def test_generate_data_accuracy_workpack_command_creates_prefilled_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "accuracy-workpack"
            out = StringIO()

            call_command(
                "generate_data_accuracy_workpack",
                "--owner",
                str(self.owner.id),
                "--warehouse",
                str(self.warehouse.id),
                "--period",
                str(self.period.id),
                "--days",
                "5",
                "--output-dir",
                str(output_dir),
                stdout=out,
            )

            self.assertIn(str(output_dir), out.getvalue())
            scope_payload = json.loads((output_dir / "scope.json").read_text(encoding="utf-8"))
            self.assertEqual(scope_payload["owner"]["id"], self.owner.id)
            self.assertEqual(scope_payload["warehouse"]["id"], self.warehouse.id)
            self.assertEqual(scope_payload["period"]["id"], self.period.id)
            self.assertEqual(scope_payload["service_date"], self.period.end_date.isoformat())
            self.assertEqual(scope_payload["shadow_run_days"], 5)

            commands = (output_dir / "commands.sh").read_text(encoding="utf-8")
            self.assertIn(
                (
                    f"python manage.py inventory_generate_snapshot --date {self.period.end_date.isoformat()} "
                    f"--owner {self.owner.id} --warehouse {self.warehouse.id}"
                ),
                commands,
            )
            self.assertIn(
                (
                    f"python manage.py reconcile_data_accuracy --owner {self.owner.id} "
                    f"--warehouse {self.warehouse.id} --period {self.period.id} --json"
                ),
                commands,
            )

            runbook = (output_dir / "RUNBOOK.md").read_text(encoding="utf-8")
            self.assertIn(self.owner.code, runbook)
            self.assertIn(self.warehouse.code, runbook)
            self.assertIn(self.period.label, runbook)

            daily_record_rows = (
                (output_dir / "daily-record.csv")
                .read_text(encoding="utf-8")
                .strip()
                .splitlines()
            )
            self.assertEqual(len(daily_record_rows), 10)
            self.assertIn(f",{self.owner.id},{self.warehouse.id},{self.period.id},", daily_record_rows[1])
            self.assertTrue(daily_record_rows[1].endswith("Day 0 baseline"))
            self.assertTrue(daily_record_rows[-1].endswith("Day 8 shadow run 5/5"))

    def test_generate_data_accuracy_workpack_command_rejects_period_scope_mismatch(self):
        other_warehouse = Warehouse.objects.create(code="ACC-WH-02", name="Other Warehouse")

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(CommandError):
                call_command(
                    "generate_data_accuracy_workpack",
                    "--owner",
                    str(self.owner.id),
                    "--warehouse",
                    str(other_warehouse.id),
                    "--period",
                    str(self.period.id),
                    "--output-dir",
                    str(Path(tmpdir) / "invalid-workpack"),
                )
