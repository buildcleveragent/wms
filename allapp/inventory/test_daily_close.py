from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.inventory.daily_close import DAY, opening_candidate, run_daily_close
from allapp.inventory.daily_close_schedule import process_due_closes
from allapp.inventory.models import (
    InventoryClosePilot,
    InventoryCloseScope,
    InventoryDailyClose,
    InventoryDetail,
    InventoryOpeningVersion,
    InventorySnapshotDaily,
    InventoryTransaction,
)
from allapp.inventory.snapshot_services import generate_inventory_snapshot_for_date
from allapp.locations.models import Warehouse
from allapp.outbound.testing import stock_for_owner_order
from allapp.products.models import Product, ProductUom


class InventoryDailyCloseTests(TestCase):
    def setUp(self):
        # Exercise real MySQL history coverage from the applied 0013 cutover onwards.
        self.day = timezone.now().date() + DAY
        self.owner = Owner.objects.create(code="DCO", name="日结货主")
        self.warehouse = Warehouse.objects.create(code="DCW", name="日结试点仓")
        self.binding = OwnerWarehouseBinding.objects.create(
            owner=self.owner, warehouse=self.warehouse
        )
        self.pilot = InventoryClosePilot.objects.create(
            warehouse=self.warehouse, enabled=True, start_date=self.day
        )
        self.tenant = dict(owner_id=self.owner.pk, warehouse_id=self.warehouse.pk)
        self.user = get_user_model().objects.create_user(username="dc-fixture")
        uom = ProductUom.objects.create(code="DCEA", name="件")
        self.product = Product.objects.create(
            owner=self.owner, code="DCP", sku="DCP", name="日结商品", base_uom=uom
        )
        self.detail = stock_for_owner_order(self.product, self.warehouse, quantity="10")

    def clock(self, days, hour=1, minutes=0):
        return patch(
            "django.utils.timezone.now",
            return_value=datetime.combine(self.day + days * DAY, time(hour, minutes)),
        )

    def run_day(self, offset=0):
        return run_daily_close(**self.tenant, business_date=self.day + offset * DAY)

    def seed_opening(self, *, rows=None):
        header = InventoryDailyClose.objects.create(**self.tenant, business_date=self.day - DAY)
        if rows is None:
            rows = opening_candidate(header)
        return InventoryOpeningVersion.objects.create(
            close=header,
            submitted_by=self.user,
            approved_by=get_user_model().objects.create_user(username="dc-opening-signer"),
            approved_at=timezone.now(),
            status="APPROVED",
            version=1,
            rows=rows,
        )

    def movement(self, delta, *, when):
        return InventoryTransaction.objects.create(
            **self.tenant,
            product=self.product,
            location=self.detail.location,
            tx_type="RECEIVE" if delta > 0 else "ISSUE",
            qty_delta=delta,
            src_model="DailyCloseTest",
            src_id=abs(delta),
            posted_at=when,
        )

    def test_missing_without_snapshot_is_unknown_and_deduplicated(self):
        with self.clock(2):
            first = self.run_day()
            repeated = self.run_day()
        self.assertEqual(first.quality, "MISSING")
        self.assertIsNone(first.sealed_at)
        self.assertIsNone(first.totals["onhand_qty"])
        self.assertEqual(first.review_state, "WAREHOUSE")
        self.assertEqual(first.case_id, repeated.case_id)
        self.assertEqual(first.revision, repeated.revision)

    def test_approved_opening_rolls_all_buckets_and_seals_across_writers(self):
        self.seed_opening()
        with self.clock(0, 23, 59):
            InventoryDetail.objects.filter(pk=self.detail.pk).update(
                allocated_qty=Decimal(2), available_qty=Decimal(8)
            )
        with self.clock(1):
            close = self.run_day()
            self.assertEqual(close.quality, "TRUSTED")
            self.assertEqual(close.issues, [])
            self.assertEqual(
                (close.details[0]["opening_qty"], close.totals["available_qty"]),
                ("10.0000", "8.0000"),
            )
            pk = InventorySnapshotDaily.objects.get(snapshot_date=self.day).pk
            for bootstrap in (True, False):
                for tenant in (self.tenant, {}):
                    generate_inventory_snapshot_for_date(self.day, **tenant, bootstrap=bootstrap)
        self.assertTrue(InventorySnapshotDaily.objects.filter(pk=pk, onhand_qty=10).exists())

    def test_approved_empty_is_distinct_from_missing_and_can_receive_next_day(self):
        InventoryDetail.objects.filter(pk=self.detail.pk).update(onhand_qty=0, available_qty=0)
        self.seed_opening(rows=[])
        with self.clock(1):
            empty = self.run_day()
            self.movement(4, when=datetime.combine(self.day + DAY, time.min))
            InventoryDetail.objects.filter(pk=self.detail.pk).update(onhand_qty=4, available_qty=4)
        with self.clock(2):
            next_day = self.run_day(1)
        self.assertEqual(
            (empty.quality, empty.row_count, empty.totals["onhand_qty"]), ("TRUSTED", 0, "0.0000")
        )
        self.assertEqual((next_day.issues, next_day.totals["movement_qty"]), ([], "4.0000"))

    def test_offsetting_late_postings_reopen_without_touching_snapshot_evidence(self):
        self.seed_opening()
        with self.clock(1):
            initial = self.run_day()
        with self.clock(2):
            when = datetime.combine(self.day, time(23, 59))
            self.movement(2, when=when)
            self.movement(-2, when=when)
            changed = self.run_day()
            repeat = self.run_day()
        self.assertCountEqual(
            [i["issue"] for i in changed.issues],
            ["late_posting", "late_posting", "posting_evidence_changed"],
        )
        self.assertEqual(
            (changed.sealed_at, changed.totals, changed.details),
            (initial.sealed_at, initial.totals, initial.details),
        )
        self.assertEqual((changed.case_id, changed.revision), (repeat.case_id, repeat.revision))

    def test_approximate_and_undated_evidence_are_not_trusted(self):
        generate_inventory_snapshot_for_date(self.day, **self.tenant, bootstrap=True)
        self.movement(1, when=None)
        with self.clock(2):
            close = self.run_day()
        self.assertEqual(close.quality, "APPROXIMATE")
        self.assertEqual(
            {i["issue"] for i in close.issues}, {"approximate_source", "undated_movements"}
        )
        with self.assertRaisesMessage(ValueError, "缺少过账日期"):
            opening_candidate(close)

    def test_scheduler_respects_deadline_binding_and_resumes_missing_days(self):
        self.seed_opening()
        with self.clock(1, 0, 29):
            self.assertEqual(process_due_closes(), (0, 0))
        with self.clock(1, 0, 30):
            self.assertEqual(process_due_closes(), (1, 0))
        with self.clock(4):
            self.assertEqual(process_due_closes(), (4, 0))
            self.assertEqual(process_due_closes(), (0, 0))
        self.assertEqual(InventoryCloseScope.objects.get().through_date, self.day + 3 * DAY)
        self.binding.is_active = False
        self.binding.save(update_fields=["is_active"])
        with self.clock(6):
            self.assertEqual(process_due_closes(), (0, 0))
            with self.assertRaisesMessage(ValueError, "绑定"):
                self.run_day(5)

    def test_unverified_legacy_baseline_cannot_seed_empty_or_new_rows(self):
        with self.clock(0):
            generate_inventory_snapshot_for_date(self.day - DAY, **self.tenant, bootstrap=True)
        InventorySnapshotDaily.objects.filter(snapshot_date=self.day - DAY).update(
            snapshot_source="TX_ROLLFORWARD", onhand_qty=20, available_qty=20
        )
        with self.clock(1):
            missing = self.run_day()
        self.assertEqual(missing.quality, "MISSING")
        self.assertFalse(InventorySnapshotDaily.objects.filter(snapshot_date=self.day).exists())

    def test_billing_preserves_missing_date_and_sealed_empty_history(self):
        from allapp.billing.enums import SourceQuality
        from allapp.billing.services._metrics import (
            _inventory_metric_rows,
            _inventory_rows_source_quality,
        )

        with self.clock(1):
            missing = self.run_day()
            estimate = _inventory_metric_rows(self.owner.pk, self.warehouse.pk, self.day)
            self.assertEqual(_inventory_rows_source_quality(estimate), SourceQuality.APPROXIMATE)
            self.assertEqual(estimate[0].onhand_qty, 10)
            self.assertFalse(InventorySnapshotDaily.objects.filter(snapshot_date=self.day).exists())
            self.assertEqual(self.run_day().quality, missing.quality)
            InventoryDetail.objects.filter(pk=self.detail.pk).update(onhand_qty=0, available_qty=0)
            self.seed_opening(rows=[])
            empty = self.run_day()
        with self.clock(2):
            InventoryDetail.objects.filter(pk=self.detail.pk).update(onhand_qty=3, available_qty=3)
            billed = _inventory_metric_rows(self.owner.pk, self.warehouse.pk, self.day)
            self.assertEqual(billed, [])
            self.assertEqual(_inventory_rows_source_quality(billed), SourceQuality.VERIFIED)
            self.assertEqual(empty.quality, "TRUSTED")

    def test_existing_rollforward_with_bad_onhand_stays_untrusted_at_first_seal(self):
        generate_inventory_snapshot_for_date(self.day, **self.tenant, bootstrap=True)
        InventorySnapshotDaily.objects.filter(snapshot_date=self.day).update(
            snapshot_source="TX_ROLLFORWARD", onhand_qty=12, available_qty=12
        )
        with self.clock(2):
            close = self.run_day()
            next_day = self.run_day(1)
        self.assertEqual(close.quality, "APPROXIMATE")
        self.assertIn("onhand_difference", {issue["issue"] for issue in close.issues})
        self.assertEqual(next_day.quality, "MISSING")

    def test_backdated_net_zero_invalidates_a_signed_opening_until_resigned(self):
        approved = self.seed_opening()
        with self.clock(1):
            when = datetime.combine(self.day - DAY, time(22))
            self.movement(1, when=when)
            self.movement(-1, when=when)
            original = self.run_day(-1)
            following = self.run_day()
        self.assertIn("opening_changed", {item["issue"] for item in original.issues})
        self.assertEqual(following.quality, "MISSING")
        approved.refresh_from_db()
        self.assertEqual(approved.status, "APPROVED")


class DailyCloseConcurrencyTests(TransactionTestCase):
    def test_competing_closers_share_one_header_and_one_case(self):
        day = timezone.now().date() - DAY
        owner = Owner.objects.create(code="CCD", name="并发日结货主")
        warehouse = Warehouse.objects.create(code="CCD", name="并发试点仓")
        OwnerWarehouseBinding.objects.create(owner=owner, warehouse=warehouse)
        InventoryClosePilot.objects.create(warehouse=warehouse, start_date=day, enabled=True)
        barrier = Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=10)
                record = run_daily_close(
                    business_date=day, owner_id=owner.pk, warehouse_id=warehouse.pk
                )
                return record.pk, record.revision, record.case_id
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            jobs = [workers.submit(worker) for _ in range(2)]
            results = [job.result(timeout=30) for job in jobs]
        self.assertEqual(results[0], results[1])
        self.assertIsNotNone(results[0][2])
        self.assertEqual(InventoryDailyClose.objects.count(), 1)

    def test_close_reads_one_view_while_stock_changes_between_queries(self):
        from allapp.inventory import daily_close

        day = timezone.now().date()
        owner = Owner.objects.create(code="MIX", name="一致读货主")
        warehouse = Warehouse.objects.create(code="MIX", name="一致读试点仓")
        tenant = dict(owner_id=owner.pk, warehouse_id=warehouse.pk)
        OwnerWarehouseBinding.objects.create(owner=owner, warehouse=warehouse)
        InventoryClosePilot.objects.create(warehouse=warehouse, start_date=day, enabled=True)
        unit = ProductUom.objects.create(code="MXEA", name="件")
        product = Product.objects.create(
            owner=owner, code="MIX", sku="MIX", name="测试", base_uom=unit
        )
        detail = stock_for_owner_order(product, warehouse, quantity="10")
        generate_inventory_snapshot_for_date(day, **tenant, bootstrap=True)
        InventorySnapshotDaily.objects.filter(snapshot_date=day).update(
            snapshot_source="TX_ROLLFORWARD"
        )
        read_rows = daily_close._collect_rows
        first_read = Event()
        stock_committed = Event()

        def interleave(*args, **kwargs):
            result = read_rows(*args, **kwargs)
            first_read.set()
            if not stock_committed.wait(timeout=15):
                raise RuntimeError("库存变动未在限定时间内完成")
            return result

        def writer():
            try:
                if not first_read.wait(timeout=15):
                    raise RuntimeError("未取得第一批日结数据")
                InventoryDetail.objects.filter(pk=detail.pk).update(onhand_qty=11, available_qty=11)
                stock_committed.set()
            finally:
                connections.close_all()

        with patch("django.utils.timezone.now", return_value=datetime.combine(day + DAY, time(1))):
            with ThreadPoolExecutor(max_workers=1) as pool:
                job = pool.submit(writer)
                with patch.object(daily_close, "_collect_rows", side_effect=interleave):
                    sealed = run_daily_close(**tenant, business_date=day)
                job.result(timeout=20)
            self.assertEqual(sealed.issues, [])
            self.assertEqual(sealed.totals["onhand_qty"], "10.0000")
            rechecked = run_daily_close(**tenant, business_date=day)
        self.assertIn("onhand_difference", {item["issue"] for item in rechecked.issues})
        self.assertEqual(rechecked.totals, sealed.totals)
