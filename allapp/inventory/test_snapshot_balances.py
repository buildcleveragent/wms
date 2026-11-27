from datetime import datetime, time, timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.models import F
from django.test import TestCase, override_settings
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.inventory.models import (
    InventoryDetail,
    InventoryHoldBalance,
    InventorySnapshotDaily,
    InventoryTransaction,
)
from allapp.inventory.services import post_task
from allapp.inventory.snapshot_services import (
    _load_hold_balances,
    generate_inventory_snapshot_for_date,
)
from allapp.locations.models import Container, Warehouse
from allapp.outbound.testing import stock_for_owner_order
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskScanLog, WmsTask


class InventorySnapshotBalanceTests(TestCase):
    def setUp(self):
        self.first_day = timezone.now().date()
        self.owner = Owner.objects.create(code="HOLDS", name="快照货主")
        self.warehouse = Warehouse.objects.create(code="HOLDS", name="快照仓")
        uom = ProductUom.objects.create(code="HPC", name="件")
        self.product = Product.objects.create(
            owner=self.owner, code="HOLDS", sku="HOLDS", name="快照商品", base_uom=uom
        )
        with self.clock(0):
            self.detail = stock_for_owner_order(self.product, self.warehouse, quantity="10")

    def clock(self, offset):
        return patch(
            "django.utils.timezone.now",
            return_value=datetime.combine(self.first_day + timedelta(days=offset), time(12)),
        )

    def generate(self, offset, *, bootstrap=False):
        day = self.first_day + timedelta(days=offset)
        generate_inventory_snapshot_for_date(
            day, owner_id=self.owner.pk, warehouse_id=self.warehouse.pk, bootstrap=bootstrap
        )
        return InventorySnapshotDaily.objects.get(snapshot_date=day, product=self.product)

    def test_bootstrap_sums_containers_and_all_quantity_buckets(self):
        with self.clock(0):
            for number, onhand, allocated, locked, damaged in (
                ("BOX-A", 7, 1, 2, 1),
                ("BOX-B", 11, 2, 3, 2),
            ):
                box = Container.objects.create(
                    warehouse=self.warehouse,
                    owner=self.owner,
                    location=self.detail.location,
                    container_no=number,
                )
                if number == "BOX-A":
                    row = self.detail
                else:
                    row = InventoryDetail(
                        owner=self.owner, product=self.product, location=self.detail.location
                    )
                row.container = box
                row.onhand_qty, row.allocated_qty, row.locked_qty, row.damaged_qty = (
                    Decimal(value) for value in (onhand, allocated, locked, damaged)
                )
                row.save()
        row = self.generate(0, bootstrap=True)
        self.assertEqual(
            (row.onhand_qty, row.available_qty, row.allocated_qty, row.locked_qty, row.damaged_qty),
            (18, 7, 3, 5, 3),
        )
        with self.clock(1):
            self.detail.refresh_from_db()
            self.detail.allocated_qty = 0
            self.detail.save()
        row = self.generate(1)
        self.assertEqual(
            (row.onhand_qty, row.available_qty, row.allocated_qty, row.locked_qty), (18, 8, 2, 5)
        )

    def test_replay_reserve_and_release_without_any_physical_movement(self):
        self.generate(0, bootstrap=True)
        detail = InventoryDetail.objects.filter(pk=self.detail.pk)
        with self.clock(1):
            detail.update(allocated_qty=1, available_qty=9)
            self.assertEqual(
                detail.filter(available_qty__gte=2).update(
                    allocated_qty=F("allocated_qty") + 2, available_qty=F("available_qty") - 2
                ),
                1,
            )
        with self.clock(2):
            detail.update(allocated_qty=0, available_qty=10)
        with self.clock(3), self.assertRaises(RuntimeError), transaction.atomic():
            detail.update(allocated_qty=2, available_qty=8)
            raise RuntimeError("abort reservation")
        for offset, available, allocated in [(1, 7, 3), (2, 10, 0), (1, 7, 3), (3, 10, 0)]:
            row = self.generate(offset)
            self.assertEqual(
                (row.onhand_qty, row.available_qty, row.allocated_qty), (10, available, allocated)
            )
        self.assertEqual(InventoryHoldBalance.objects.filter(detail_id=self.detail.pk).count(), 3)

    def test_rollforward_rejects_hold_without_opening_stock(self):
        with self.clock(1):
            self.detail.allocated_qty = 3
            self.detail.save()
        with self.assertRaisesRegex(ValueError, "在库基线不足"):
            self.generate(1)
        self.assertFalse(InventorySnapshotDaily.objects.filter(product=self.product).exists())

    @override_settings(INVENTORY_FIFO_ENABLED=False)
    def test_midnight_dispatch_uses_posting_day_for_stock_and_reservation_release(self):
        with self.clock(0):
            self.detail.allocated_qty = 3
            self.detail.save()
        self.generate(0, bootstrap=True)
        task = WmsTask.objects.create(
            task_type="DISPATCH",
            task_no="HOLD-MIDNIGHT",
            owner=self.owner,
            warehouse=self.warehouse,
            status="COMPLETED",
            review_status="APPROVED",
            posting_status="PENDING",
        )
        line = task.lines.create(
            product=self.product,
            from_location=self.detail.location,
            qty_plan=3,
            qty_done=3,
            status="COMPLETED",
        )
        scan = TaskScanLog.objects.create(
            task=task,
            task_line=line,
            product=self.product,
            location=self.detail.location,
            owner=self.owner,
            warehouse=self.warehouse,
            qty_base_delta=3,
            status="OK",
            review_status="APPROVED",
            scan_snapshot_rev=0,
            fp="hold-midnight",
        )
        posting_time = datetime.combine(self.first_day + timedelta(days=1), time(23, 59, 59))
        with self.clock(2):
            post_task(task=task, scans=[scan], now=posting_time)
        self.assertEqual(
            InventoryTransaction.objects.get(src_model="WmsTask", src_id=task.pk).posted_at,
            posting_time,
        )
        row = self.generate(1)
        self.assertEqual((row.onhand_qty, row.available_qty, row.allocated_qty), (7, 7, 0))
        self.assertFalse(
            InventoryHoldBalance.objects.filter(
                detail_id=self.detail.pk, balance_date=self.first_day + timedelta(days=2)
            ).exists()
        )

    def test_non_return_damaged_stock_leaves_before_a_reservation(self):
        with self.clock(0):
            self.detail.damaged_qty = 5
            self.detail.save()
        self.generate(0, bootstrap=True)
        with self.clock(1):
            self.detail.damaged_qty = 0
            self.detail.onhand_qty = 5
            self.detail.allocated_qty = 3
            self.detail.save()
            InventoryTransaction.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                location=self.detail.location,
                product=self.product,
                tx_type="ISSUE",
                src_model="WmsTask",
                src_id=1,
                qty_delta=-5,
                posted_at=datetime.combine(self.first_day + timedelta(days=1), time(12)),
            )
        row = self.generate(1)
        self.assertEqual(
            (row.onhand_qty, row.damaged_qty, row.allocated_qty, row.available_qty), (5, 0, 3, 2)
        )

    def test_bulk_insert_and_deletion_retain_prior_held_balance(self):
        row = InventoryDetail(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            subwarehouse_id=self.detail.subwarehouse_id,
            location=self.detail.location,
            batch_no="BULK",
            base_unit=self.detail.base_unit,
            onhand_qty=4,
            allocated_qty=2,
            locked_qty=1,
            available_qty=1,
        )
        with self.clock(0):
            InventoryDetail.objects.bulk_create([row])
        with self.clock(1):
            InventoryDetail.objects.filter(product=self.product, batch_no="BULK").delete()
        self.assertEqual(list(_load_hold_balances(self.first_day).values()), [(2, 1, 0)])
        self.assertEqual(_load_hold_balances(self.first_day + timedelta(days=1)), {})

    def test_nonempty_migration_seeds_each_active_layer_on_cutover_day(self):
        loader = MigrationLoader(None)
        old_apps = loader.project_state([("inventory", "0012_owner_inventory_alert_policies")]).apps
        new_apps = loader.project_state([("inventory", "0013_inventory_hold_balance")]).apps
        old_detail = old_apps.get_model("inventory", "InventoryDetail")
        for index, (active, deleted) in enumerate(
            ((True, False), (True, False), (False, False), (True, True)), 1
        ):
            box = Container.objects.create(
                warehouse=self.warehouse,
                owner=self.owner,
                location=self.detail.location,
                container_no=f"OLD-{index}",
            )
            old_detail.objects.create(
                owner_id=self.owner.pk,
                product_id=self.product.pk,
                location_id=self.detail.location_id,
                warehouse_id=self.warehouse.pk,
                subwarehouse_id=self.detail.subwarehouse_id,
                container_id=box.pk,
                onhand_qty=10,
                allocated_qty=index,
                locked_qty=1,
                damaged_qty=1,
                available_qty=8 - index,
                is_active=active,
                is_deleted=deleted,
                base_unit=self.detail.base_unit,
            )
        day = self.first_day + timedelta(days=5)
        migration = import_module("allapp.inventory.migrations.0013_inventory_hold_balance")
        with self.clock(5):
            migration.seed_current_balances(new_apps, connection.schema_editor())
        seeded = InventoryHoldBalance.objects.filter(balance_date=day, owner_id=self.owner.pk)
        self.assertEqual(
            list(
                seeded.order_by("allocated_qty").values_list(
                    "allocated_qty", "locked_qty", "damaged_qty"
                )
            ),
            [(0, 0, 0), (1, 1, 1), (2, 1, 1)],
        )
