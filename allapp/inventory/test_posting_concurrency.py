import threading
from decimal import Decimal
from unittest import mock

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inventory import services
from allapp.inventory.models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
)
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask


class InventoryPostingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="INV-POST-C", name="Inventory posting C")
        self.warehouse = Warehouse.objects.create(code="INV-POST-C-WH", name="Inventory posting WH")
        Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="IPC",
            name="Inventory posting subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="IPC-01-01-01",
            name="Inventory posting L1",
        )
        uom = ProductUom.objects.create(code="INV-POST-C-EA", name="EA", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="INV-POST-C-SKU",
            sku="INV-POST-C-SKU",
            name="Inventory posting SKU",
            base_uom=uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("2.0000"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        self.tasks = [
            WmsTask.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                task_no=f"INV-POST-C-{index}",
                task_type=WmsTask.TaskType.PICK,
            )
            for index in (1, 2)
        ]

    def _group(self, task, batch):
        key = services._AggKey(
            posting_batch=batch,
            task_id=task.id,
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            location_id=self.location.id,
            batch_no=None,
            production_date=None,
            expiry_date=None,
            serial_no=None,
            tx_type=InvTxType.ISSUE,
        )
        return {key: Decimal("-1.0000")}

    def test_posting_refreshes_summary_from_the_locked_detail_instances(self):
        with transaction.atomic():
            services._apply_receive_like(
                self.tasks[0],
                self._group(self.tasks[0], "INV-SINGLE"),
                now=timezone.now(),
                batch_no="INV-SINGLE",
            )

        detail = InventoryDetail.objects.get(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
        )
        summary = InventorySummary.objects.get(owner=self.owner, product=self.product)
        self.assertEqual(detail.onhand_qty, Decimal("9.0000"))
        self.assertEqual(detail.allocated_qty, Decimal("1.0000"))
        self.assertEqual(summary.onhand_qty, Decimal("9.0000"))
        self.assertEqual(summary.allocated_qty, Decimal("1.0000"))

    def test_one_pick_writes_multiple_issue_transactions_with_null_source_line(self):
        second_location = Location.objects.create(
            warehouse=self.warehouse,
            code="IPC-01-01-02",
            name="Inventory posting L2",
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            location=second_location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("1.0000"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        task = self.tasks[0]
        first_key = next(iter(self._group(task, "INV-MULTI")))
        second_key = services._AggKey(
            posting_batch="INV-MULTI",
            task_id=task.id,
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            product_id=self.product.id,
            location_id=second_location.id,
            batch_no=None,
            production_date=None,
            expiry_date=None,
            serial_no=None,
            tx_type=InvTxType.ISSUE,
        )

        with transaction.atomic():
            created = services._apply_receive_like(
                task,
                {
                    first_key: Decimal("-1.0000"),
                    second_key: Decimal("-1.0000"),
                },
                now=timezone.now(),
                batch_no="INV-MULTI",
            )

        transactions = InventoryTransaction.objects.filter(
            src_model="WmsTask",
            src_id=task.id,
            tx_type=InvTxType.ISSUE,
        ).order_by("location_id")
        self.assertEqual(created, 2)
        self.assertEqual(transactions.count(), 2)
        self.assertEqual(
            set(transactions.values_list("location_id", flat=True)),
            {self.location.id, second_location.id},
        )
        self.assertTrue(all(tx.src_line_id is None for tx in transactions))

    @skipUnlessDBFeature("has_select_for_update")
    def test_different_tasks_serialize_inventory_read_modify_write_and_summary(self):
        first_inside_update = threading.Event()
        release_first = threading.Event()
        calls = 0
        calls_lock = threading.Lock()
        errors = []
        real_upsert = services._upsert_detail

        def delayed_upsert(*args, **kwargs):
            nonlocal calls
            with calls_lock:
                calls += 1
                current = calls
            if current == 1:
                first_inside_update.set()
                if not release_first.wait(timeout=5):
                    raise AssertionError("timed out releasing first inventory update")
            return real_upsert(*args, **kwargs)

        def invoke(task, batch):
            close_old_connections()
            try:
                with transaction.atomic():
                    services._apply_receive_like(
                        task,
                        self._group(task, batch),
                        now=timezone.now(),
                        batch_no=batch,
                    )
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch("allapp.inventory.services._upsert_detail", side_effect=delayed_upsert):
            first = threading.Thread(target=invoke, args=(self.tasks[0], "INV-C-1"))
            first.start()
            self.assertTrue(first_inside_update.wait(timeout=5))

            second = threading.Thread(target=invoke, args=(self.tasks[1], "INV-C-2"))
            second.start()
            self.assertTrue(second.is_alive())
            self.assertEqual(calls, 1)

            release_first.set()
            first.join(timeout=5)
            second.join(timeout=5)

        if first.is_alive() or second.is_alive():
            self.fail("inventory posting threads did not finish")
        if errors:
            raise errors[0]

        detail = InventoryDetail.objects.get(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
        )
        summary = InventorySummary.objects.get(owner=self.owner, product=self.product)
        self.assertEqual(detail.onhand_qty, Decimal("8.0000"))
        self.assertEqual(detail.allocated_qty, Decimal("0.0000"))
        self.assertEqual(summary.onhand_qty, Decimal("8.0000"))
        self.assertEqual(summary.allocated_qty, Decimal("0.0000"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id__in=[task.id for task in self.tasks],
                tx_type=InvTxType.ISSUE,
            ).count(),
            2,
        )
