import csv
import datetime
import threading
import tempfile
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inventory import snapshot_services as inventory_snapshot_services
from allapp.inventory.models import InventoryDetail, InventorySnapshotDaily, InventoryTransaction, ReviewDifference
from allapp.inventory.snapshot_services import generate_inventory_snapshot_for_date
from allapp.inventory.services_quick_adjust import QuickAdjustInput, quick_adjust_via_post_task
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom


class InventoryWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inventory", code="OWN-INV")
        self.warehouse = Warehouse.objects.create(code="WH-INV-1", name="Warehouse Inventory 1")
        self.other_warehouse = Warehouse.objects.create(
            code="WH-INV-2",
            name="Warehouse Inventory 2",
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWINV1",
            name="Subwarehouse Inventory 1",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="SWINV2",
            name="Subwarehouse Inventory 2",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWINV1-01-01-01",
            name="Inventory Location 1",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWINV2-01-01-01",
            name="Inventory Location 2",
        )
        self.uom = ProductUom.objects.create(code="PCS-INV", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-INV",
            name="Inventory Product",
            sku="SKU-INV",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="inventory-user",
            password="x",
            warehouse=self.warehouse,
        )

    def test_inventory_detail_derives_warehouse_from_location(self):
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )

        self.assertEqual(detail.warehouse_id, self.warehouse.id)

    def test_inventory_transaction_derives_warehouse_from_location(self):
        tx = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product,
            location=self.location,
            qty_delta=Decimal("2.0000"),
            src_model="inventory.tests",
            src_id=1,
            src_line_id=1,
            src_no="INV-TX-1",
        )

        self.assertEqual(tx.warehouse_id, self.warehouse.id)

    def test_review_difference_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            ReviewDifference.objects.create(order_no="RD-INV-1")

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_quick_adjust_rejects_mismatched_location_and_warehouse(self):
        with self.assertRaisesMessage(ValueError, "warehouse 必须与 location.warehouse 一致"):
            quick_adjust_via_post_task(
                QuickAdjustInput(
                    user=self.user,
                    owner=self.owner,
                    product=self.product,
                    qty_base_delta=Decimal("1.0000"),
                    warehouse=self.other_warehouse,
                    location=self.location,
                )
            )

    def test_tracking_repair_commands_export_and_apply_csv(self):
        tracked_product = Product.objects.create(
            owner=self.owner,
            code="SKU-TRACK",
            name="Tracked Product",
            sku="SKU-TRACK",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("12.00"),
            batch_control=True,
            expiry_control=True,
            expiry_basis="MFG",
            shelf_life_days=30,
            expiry_warning_days=5,
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("2.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        tx = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("2.0000"),
            src_model="inventory.tests",
            src_id=2,
            src_line_id=2,
            src_no="INV-TX-2",
            posted_at="2026-04-02 10:00:00",
        )

        with tempfile.NamedTemporaryFile(
            "w+",
            newline="",
            suffix=".csv",
            encoding="utf-8-sig",
        ) as export_file:
            out = StringIO()
            call_command(
                "export_inventory_tracking_repair_template",
                export_file.name,
                "--owner",
                str(self.owner.id),
                stdout=out,
            )
            export_file.seek(0)
            rows = list(csv.DictReader(export_file))

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source"] for row in rows}, {"detail", "transaction"})

        with tempfile.NamedTemporaryFile("w+", newline="", suffix=".csv") as repair_file:
            writer = csv.DictWriter(repair_file, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                row["new_batch_no"] = "LOT-202604"
                row["new_production_date"] = "2026-04-01"
                row["new_expiry_date"] = "2026-05-01"
                writer.writerow(row)
            repair_file.flush()

            out = StringIO()
            call_command(
                "apply_inventory_tracking_repairs",
                repair_file.name,
                stdout=out,
            )

        detail.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(detail.batch_no, "LOT-202604")
        self.assertEqual(detail.production_date.isoformat(), "2026-04-01")
        self.assertEqual(detail.expiry_date.isoformat(), "2026-05-01")
        self.assertEqual(tx.batch_no, "LOT-202604")
        self.assertEqual(tx.production_date.isoformat(), "2026-04-01")
        self.assertEqual(tx.expiry_date.isoformat(), "2026-05-01")

    def test_tracking_repair_command_rejects_stale_template(self):
        tracked_product = Product.objects.create(
            owner=self.owner,
            code="SKU-TRACK-STALE",
            name="Tracked Product Stale",
            sku="SKU-TRACK-STALE",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("12.00"),
            batch_control=True,
            expiry_control=False,
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("1.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        tx = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("1.0000"),
            src_model="inventory.tests",
            src_id=3,
            src_line_id=3,
            src_no="INV-TX-3",
            posted_at="2026-04-02 11:00:00",
        )

        with tempfile.NamedTemporaryFile(
            "w+",
            newline="",
            suffix=".csv",
            encoding="utf-8-sig",
        ) as export_file:
            call_command(
                "export_inventory_tracking_repair_template",
                export_file.name,
                "--owner",
                str(self.owner.id),
            )
            export_file.seek(0)
            rows = list(csv.DictReader(export_file))

        detail.batch_no = "MANUAL-FIXED"
        detail.save()

        with tempfile.NamedTemporaryFile(
            "w+",
            newline="",
            suffix=".csv",
            encoding="utf-8-sig",
        ) as repair_file:
            writer = csv.DictWriter(repair_file, fieldnames=rows[0].keys())
            writer.writeheader()
            for row in rows:
                row["new_batch_no"] = "LOT-STALE"
                writer.writerow(row)
            repair_file.flush()

            with self.assertRaisesMessage(CommandError, "current value does not match database"):
                call_command("apply_inventory_tracking_repairs", repair_file.name)

        detail.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(detail.batch_no, "MANUAL-FIXED")
        self.assertEqual(tx.batch_no, "")

    def test_business_reply_sheet_can_merge_back_into_repair_template(self):
        tracked_product = Product.objects.create(
            owner=self.owner,
            code="SKU-TRACK-REPLY",
            name="Tracked Product Reply",
            sku="SKU-TRACK-REPLY",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("12.00"),
            batch_control=True,
            expiry_control=True,
            expiry_basis="MFG",
            shelf_life_days=30,
            expiry_warning_days=5,
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("3.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        tx = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=tracked_product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("3.0000"),
            src_model="inventory.tests",
            src_id=4,
            src_line_id=4,
            src_no="INV-TX-4",
            posted_at="2026-04-02 12:00:00",
        )

        with tempfile.NamedTemporaryFile(
            "w+",
            newline="",
            suffix=".csv",
            encoding="utf-8-sig",
        ) as template_file, tempfile.NamedTemporaryFile(
            "w+",
            newline="",
            suffix=".csv",
            encoding="utf-8-sig",
        ) as reply_file, tempfile.NamedTemporaryFile(
            "w+",
            newline="",
            suffix=".csv",
            encoding="utf-8-sig",
        ) as merged_file:
            call_command(
                "export_inventory_tracking_repair_template",
                template_file.name,
                "--owner",
                str(self.owner.id),
            )
            call_command(
                "export_inventory_tracking_business_reply_sheet",
                template_file.name,
                reply_file.name,
            )

            reply_file.seek(0)
            reply_rows = list(csv.DictReader(reply_file))
            self.assertEqual(len(reply_rows), 1)
            reply_rows[0]["business_confirmed_batch_no"] = "LOT-REPLY-1"
            reply_rows[0]["business_confirmed_production_date"] = "2026-04-01"
            reply_rows[0]["business_confirmed_expiry_date"] = "2026-05-01"
            reply_rows[0]["evidence_source"] = "ERP"
            reply_rows[0]["confirmed_by"] = "ops_user"
            reply_rows[0]["confirmed_at"] = "2026-04-03"
            reply_rows[0]["remarks"] = "checked"

            reply_file.seek(0)
            reply_file.truncate()
            writer = csv.DictWriter(reply_file, fieldnames=reply_rows[0].keys())
            writer.writeheader()
            writer.writerows(reply_rows)
            reply_file.flush()

            call_command(
                "merge_inventory_tracking_business_reply",
                template_file.name,
                reply_file.name,
                "--output",
                merged_file.name,
            )

            merged_file.seek(0)
            merged_rows = list(csv.DictReader(merged_file))

            self.assertEqual(len(merged_rows), 2)
            self.assertEqual({row["new_batch_no"] for row in merged_rows}, {"LOT-REPLY-1"})
            self.assertEqual(
                {row["new_production_date"] for row in merged_rows},
                {"2026-04-01"},
            )
            self.assertEqual(
                {row["new_expiry_date"] for row in merged_rows},
                {"2026-05-01"},
            )
            self.assertTrue(all("confirmed_by=ops_user" in row["note"] for row in merged_rows))

            call_command("apply_inventory_tracking_repairs", merged_file.name)

        detail.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(detail.batch_no, "LOT-REPLY-1")
        self.assertEqual(detail.production_date.isoformat(), "2026-04-01")
        self.assertEqual(detail.expiry_date.isoformat(), "2026-05-01")
        self.assertEqual(tx.batch_no, "LOT-REPLY-1")
        self.assertEqual(tx.production_date.isoformat(), "2026-04-01")
        self.assertEqual(tx.expiry_date.isoformat(), "2026-05-01")


class InventorySnapshotConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Snapshot C", code="OWSNPC")
        self.warehouse = Warehouse.objects.create(code="WHSNPC", name="Warehouse Snapshot C")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWSNPC",
            name="SW Snap C",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWSNPC-01-01-01",
            name="Snapshot C Loc",
        )
        self.uom = ProductUom.objects.create(code="PCS-SNPC", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-SNPC",
            name="Snapshot Product C",
            sku="SKU-SNPC",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )

    def test_generate_inventory_snapshot_for_date_is_serialized_under_concurrency(self):
        service_date = datetime.date(2026, 4, 3)
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )

        payload_entered = threading.Event()
        release_payload = threading.Event()
        payload_calls = 0
        payload_lock = threading.Lock()
        results = [None, None]
        errors = []
        real_build_bootstrap_payloads = inventory_snapshot_services._build_bootstrap_payloads

        def fake_build_bootstrap_payloads(*args, **kwargs):
            nonlocal payload_calls
            with payload_lock:
                payload_calls += 1
                current_call = payload_calls
            if current_call == 1:
                payload_entered.set()
                if not release_payload.wait(timeout=5):
                    raise AssertionError("timed out waiting to release snapshot concurrent test")
            return real_build_bootstrap_payloads(*args, **kwargs)

        def invoke(index):
            close_old_connections()
            try:
                results[index] = generate_inventory_snapshot_for_date(
                    service_date,
                    owner_id=self.owner.id,
                    warehouse_id=self.warehouse.id,
                    bootstrap=True,
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch("allapp.inventory.snapshot_services._build_bootstrap_payloads", side_effect=fake_build_bootstrap_payloads):
            thread1 = threading.Thread(target=invoke, args=(0,))
            thread1.start()
            self.assertTrue(payload_entered.wait(timeout=5))

            thread2 = threading.Thread(target=invoke, args=(1,))
            thread2.start()

            self.assertEqual(payload_calls, 1)
            self.assertTrue(thread2.is_alive())

            release_payload.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent inventory snapshot threads did not finish")
        if errors:
            raise errors[0]

        self.assertEqual(payload_calls, 2)
        self.assertEqual(
            InventorySnapshotDaily.objects.filter(
                snapshot_date=service_date,
                owner=self.owner,
                warehouse=self.warehouse,
            ).count(),
            1,
        )
        snapshot = InventorySnapshotDaily.objects.get(
            snapshot_date=service_date,
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location,
            product=self.product,
        )
        self.assertEqual(snapshot.onhand_qty, Decimal("5.0000"))
        self.assertEqual(results[0]["rows_created"], 1)
        self.assertEqual(results[1]["rows_created"], 1)
