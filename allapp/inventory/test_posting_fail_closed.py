import threading
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inventory import services as inventory_services
from allapp.inventory.models import (
    InventoryDetail,
    InventorySummary,
    InventoryTransaction,
    PostingJournal,
)
from allapp.inventory.services_quick_adjust import (
    QuickAdjustInput,
    quick_adjust_via_post_task,
)
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.plugins.handlers import DefaultPostingHandler


class InventoryPostingFailClosedTests(TestCase):
    """The scan-driven posting boundary must reject ambiguous input atomically."""

    def setUp(self):
        self.owner = Owner.objects.create(code="INV-FC", name="Inventory FC")
        self.warehouse = Warehouse.objects.create(
            code="INV-FC-WH",
            name="Inventory FC warehouse",
        )
        Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="INVFC",
            name="Inventory FC subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="INVFC-01-01-01",
            name="Inventory fail-closed location",
        )
        self.uom = ProductUom.objects.create(
            code="INV-FC-EA",
            name="Inventory fail-closed EA",
            is_active=True,
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="INV-FC-SKU",
            sku="INV-FC-SKU",
            name="Inventory fail-closed product",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.location,
            batch_no="INV-FC-LOT",
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        self.sequence = 0

    def _next_token(self):
        self.sequence += 1
        return self.sequence

    def _task(self, task_type=WmsTask.TaskType.RECEIVE):
        token = self._next_token()
        return WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no=f"INV-FC-TASK-{token}",
            task_type=task_type,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )

    def _scan(
        self,
        task,
        *,
        status=TaskScanLog.ScanStatus.OK,
        review_status=TaskScanLog.ReviewStatus.NONE,
        qty_base=None,
        qty_base_delta=Decimal("1.000000"),
        posted_at=None,
        posting_batch=None,
        posting_journal=None,
    ):
        token = self._next_token()
        line = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.location,
            to_location=self.location,
            qty_plan=Decimal("1.000"),
            qty_done=Decimal("1.000"),
            status=WmsTaskLine.Status.COMPLETED,
        )
        return TaskScanLog.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task=task,
            task_line=line,
            product=self.product,
            location=self.location,
            status=status,
            review_status=review_status,
            qty_base=qty_base,
            qty_base_delta=qty_base_delta,
            lot_no="INV-FC-LOT",
            posted_at=posted_at,
            posting_batch=posting_batch or ("PREVIOUS" if posted_at else None),
            posting_journal=posting_journal,
            fp=f"inventory-fail-closed-{token}",
            scan_snapshot_rev=0,
        )

    def _assert_rejected_without_effects(self, task, scans, *, tracked_scans=()):
        self.detail.refresh_from_db()
        before_detail = (
            self.detail.onhand_qty,
            self.detail.allocated_qty,
            self.detail.available_qty,
        )
        tracked_ids = [scan.pk for scan in tracked_scans]
        before_scans = list(
            TaskScanLog.objects.filter(pk__in=tracked_ids)
            .order_by("pk")
            .values("pk", "posted_at", "posting_batch", "posting_journal_id")
        )

        with self.assertRaises(ValidationError):
            inventory_services.post_task(
                task=task,
                scans=scans,
                batch_no=f"INV-FC-BATCH-{task.pk}",
            )

        task.refresh_from_db()
        self.detail.refresh_from_db()
        after_scans = list(
            TaskScanLog.objects.filter(pk__in=tracked_ids)
            .order_by("pk")
            .values("pk", "posted_at", "posting_batch", "posting_journal_id")
        )
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.PENDING)
        self.assertEqual(
            (
                self.detail.onhand_qty,
                self.detail.allocated_qty,
                self.detail.available_qty,
            ),
            before_detail,
        )
        self.assertEqual(after_scans, before_scans)
        self.assertFalse(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.pk,
            ).exists()
        )
        self.assertFalse(
            PostingJournal.objects.filter(
                src_model="WmsTask",
                src_id=task.pk,
                tx_type="POST",
            ).exists()
        )
        self.assertFalse(InventorySummary.objects.filter(owner=self.owner).exists())

    def test_none_and_empty_scan_inputs_are_rejected(self):
        for label, scans in (("none", None), ("empty", [])):
            with self.subTest(scans=label):
                task = self._task()
                self._assert_rejected_without_effects(task, scans)

    def test_every_non_ok_scan_status_is_rejected(self):
        for status in (
            TaskScanLog.ScanStatus.FAIL,
            TaskScanLog.ScanStatus.DUP,
            TaskScanLog.ScanStatus.IGNORED,
        ):
            with self.subTest(status=status):
                task = self._task()
                scan = self._scan(
                    task,
                    status=status,
                    qty_base_delta=None,
                )
                self._assert_rejected_without_effects(
                    task,
                    [scan],
                    tracked_scans=[scan],
                )

    def test_review_rejected_scan_is_rejected(self):
        task = self._task()
        scan = self._scan(
            task,
            review_status=TaskScanLog.ReviewStatus.REJECTED,
        )

        self._assert_rejected_without_effects(
            task,
            [scan],
            tracked_scans=[scan],
        )

    def test_already_posted_scan_is_rejected(self):
        task = self._task()
        scan = self._scan(task, posted_at=timezone.now())

        self._assert_rejected_without_effects(
            task,
            [scan],
            tracked_scans=[scan],
        )

    def test_dangling_scan_posting_markers_are_rejected(self):
        batch_task = self._task()
        batch_scan = self._scan(batch_task, posting_batch="DANGLING-BATCH")
        self._assert_rejected_without_effects(
            batch_task,
            [batch_scan],
            tracked_scans=[batch_scan],
        )

        marker = PostingJournal.objects.create(
            src_model="TestFixture",
            src_id=self._next_token(),
            tx_type="POST",
            status="PENDING",
        )
        journal_task = self._task()
        journal_scan = self._scan(journal_task, posting_journal=marker)
        self._assert_rejected_without_effects(
            journal_task,
            [journal_scan],
            tracked_scans=[journal_scan],
        )

    def test_scan_from_another_task_is_rejected(self):
        task = self._task()
        foreign_task = self._task()
        foreign_scan = self._scan(foreign_task)

        self._assert_rejected_without_effects(
            task,
            [foreign_scan],
            tracked_scans=[foreign_scan],
        )

    def test_duplicate_scan_reference_is_rejected(self):
        task = self._task()
        scan = self._scan(task)

        self._assert_rejected_without_effects(
            task,
            [scan, scan],
            tracked_scans=[scan],
        )

    def test_task_types_without_posting_policy_are_rejected(self):
        unsupported_types = (
            WmsTask.TaskType.REVIEW,
            WmsTask.TaskType.PACK,
            WmsTask.TaskType.REPLEN,
            WmsTask.TaskType.OTHER,
        )
        for task_type in unsupported_types:
            with self.subTest(task_type=task_type):
                task = self._task(task_type)
                scan = self._scan(task)
                self._assert_rejected_without_effects(
                    task,
                    [scan],
                    tracked_scans=[scan],
                )

    def test_default_handler_preserves_failed_audit_for_rejected_posting(self):
        task = self._task(WmsTask.TaskType.OTHER)
        scan = self._scan(task)

        with self.assertRaises(ValidationError):
            DefaultPostingHandler().handle(
                task=task,
                scans=[scan],
                batch_no="INV-FC-HANDLER-FAIL",
                note="unsupported posting",
            )

        task.refresh_from_db()
        scan.refresh_from_db()
        journal = PostingJournal.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
            tx_type="POST",
        )
        self.detail.refresh_from_db()
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.FAILED)
        self.assertEqual(journal.status, "FAILED")
        self.assertIsNone(scan.posted_at)
        self.assertIsNone(scan.posting_batch)
        self.assertIsNone(scan.posting_journal_id)
        self.assertEqual(self.detail.onhand_qty, Decimal("10.0000"))
        self.assertFalse(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.pk,
            ).exists()
        )

    def test_legacy_ship_alias_is_not_treated_as_dispatch(self):
        task = self._task(WmsTask.TaskType.OTHER)
        scan = self._scan(task)
        task.task_type = "SHIP"

        # SHIP cannot be persisted because of the model constraint. Return an
        # in-memory legacy row from the task lock to exercise the service
        # boundary without disabling database protections.
        with (
            mock.patch.object(inventory_services, "_lock_task", return_value=task),
            mock.patch.object(task, "save"),
        ):
            self._assert_rejected_without_effects(
                task,
                [scan],
                tracked_scans=[scan],
            )

    def test_task_and_journal_state_mismatch_is_rejected(self):
        task = self._task()
        scan = self._scan(task)
        journal = PostingJournal.objects.create(
            src_model="WmsTask",
            src_id=task.pk,
            tx_type="POST",
            status="POSTED",
            message="LEGACY-BATCH",
        )
        before_onhand = self.detail.onhand_qty

        with self.assertRaises(ValidationError):
            inventory_services.post_task(
                task=task,
                scans=[scan],
                batch_no="INV-FC-MISMATCH",
            )

        task.refresh_from_db()
        scan.refresh_from_db()
        journal.refresh_from_db()
        self.detail.refresh_from_db()
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.PENDING)
        self.assertEqual(journal.status, "POSTED")
        self.assertIsNone(scan.posted_at)
        self.assertIsNone(scan.posting_batch)
        self.assertIsNone(scan.posting_journal_id)
        self.assertEqual(self.detail.onhand_qty, before_onhand)
        self.assertFalse(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.pk,
            ).exists()
        )

    def test_positive_adjustment_posts_an_adjustment_gain(self):
        task = self._task(WmsTask.TaskType.ADJUST)
        scan = self._scan(task, qty_base_delta=Decimal("2.000000"))

        result = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-ADJ-GAIN",
        )

        task.refresh_from_db()
        scan.refresh_from_db()
        self.detail.refresh_from_db()
        transaction = InventoryTransaction.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["affected_tx_count"], 1)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertIsNotNone(scan.posted_at)
        self.assertEqual(self.detail.onhand_qty, Decimal("12.0000"))
        self.assertEqual(transaction.tx_type, InvTxType.ADJ_GAIN)
        self.assertEqual(transaction.qty_delta, Decimal("2.0000"))

    def test_negative_adjustment_posts_an_adjustment_loss(self):
        task = self._task(WmsTask.TaskType.ADJUST)
        scan = self._scan(task, qty_base_delta=Decimal("-3.000000"))

        result = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-ADJ-LOSS",
        )

        task.refresh_from_db()
        scan.refresh_from_db()
        self.detail.refresh_from_db()
        transaction = InventoryTransaction.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["affected_tx_count"], 1)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertIsNotNone(scan.posted_at)
        self.assertEqual(self.detail.onhand_qty, Decimal("7.0000"))
        self.assertEqual(transaction.tx_type, InvTxType.ADJ_LOSS)
        self.assertEqual(transaction.qty_delta, Decimal("-3.0000"))

    def test_negative_quick_adjustment_keeps_task_line_quantity_nonnegative(self):
        no_batch_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )

        result = quick_adjust_via_post_task(
            QuickAdjustInput(
                user=None,
                owner=self.owner,
                warehouse=self.warehouse,
                location=self.location,
                product=self.product,
                qty_base_delta=Decimal("-2.0000"),
                reason="FAIL_CLOSED_TEST",
            )
        )

        no_batch_detail.refresh_from_db()
        task = WmsTask.objects.get(task_type=WmsTask.TaskType.ADJUST)
        transaction = InventoryTransaction.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(task.lines.get().qty_plan, Decimal("2.000"))
        self.assertEqual(no_batch_detail.onhand_qty, Decimal("8.0000"))
        self.assertEqual(transaction.tx_type, InvTxType.ADJ_LOSS)
        self.assertEqual(transaction.qty_delta, Decimal("-2.0000"))

    def test_zero_adjustment_is_rejected_using_only_delta_quantity(self):
        task = self._task(WmsTask.TaskType.ADJUST)
        # qty_base is non-zero only to satisfy the legacy scan-row check constraint.
        # ADJUST must read qty_base_delta and reject its zero value.
        scan = self._scan(
            task,
            qty_base=Decimal("1.000000"),
            qty_base_delta=Decimal("0.000000"),
        )

        self._assert_rejected_without_effects(
            task,
            [scan],
            tracked_scans=[scan],
        )

    def test_non_count_zero_transaction_result_is_rejected_atomically(self):
        task = self._task(WmsTask.TaskType.RECEIVE)
        scan = self._scan(task, qty_base_delta=Decimal("1.000000"))

        with mock.patch.object(
            inventory_services,
            "_apply_receive_like",
            return_value=0,
        ):
            self._assert_rejected_without_effects(
                task,
                [scan],
                tracked_scans=[scan],
            )

    def test_zero_difference_count_posts_without_inventory_transaction(self):
        task = self._task(WmsTask.TaskType.COUNT)
        # qty_base_delta is non-zero only to satisfy the legacy scan-row check
        # constraint. COUNT must use qty_base as the authoritative difference.
        scan = self._scan(
            task,
            qty_base=Decimal("0.000000"),
            qty_base_delta=Decimal("1.000000"),
        )

        result = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-COUNT-ZERO",
        )

        task.refresh_from_db()
        scan.refresh_from_db()
        self.detail.refresh_from_db()
        journal = PostingJournal.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
            tx_type="POST",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["affected_tx_count"], 0)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertEqual(journal.status, "POSTED")
        self.assertIsNotNone(scan.posted_at)
        self.assertEqual(self.detail.onhand_qty, Decimal("10.0000"))
        self.assertFalse(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.pk,
            ).exists()
        )

    def test_successful_posting_retry_is_idempotent(self):
        task = self._task(WmsTask.TaskType.RECEIVE)
        scan = self._scan(task, qty_base_delta=Decimal("2.000000"))

        first = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-IDEMPOTENT",
        )
        second = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-IDEMPOTENT-RETRY",
        )

        task.refresh_from_db()
        scan.refresh_from_db()
        self.detail.refresh_from_db()
        journal = PostingJournal.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
            tx_type="POST",
        )
        transactions = InventoryTransaction.objects.filter(
            src_model="WmsTask",
            src_id=task.pk,
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["affected_tx_count"], 1)
        self.assertTrue(second["ok"])
        self.assertEqual(second["affected_tx_count"], 0)
        self.assertEqual(transactions.count(), 1)
        self.assertEqual(self.detail.onhand_qty, Decimal("12.0000"))
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertEqual(journal.status, "POSTED")
        self.assertEqual(journal.attempt_count, 1)
        self.assertEqual(scan.posting_batch, "INV-FC-IDEMPOTENT")

    def test_pick_decreases_inventory_and_writes_issue(self):
        task = self._task(WmsTask.TaskType.PICK)
        scan = self._scan(task, qty_base_delta=Decimal("2.000000"))

        result = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-PICK",
        )

        self.detail.refresh_from_db()
        transaction = InventoryTransaction.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
        )
        self.assertEqual(result["affected_tx_count"], 1)
        self.assertEqual(self.detail.onhand_qty, Decimal("8.0000"))
        self.assertEqual(transaction.tx_type, InvTxType.ISSUE)
        self.assertEqual(transaction.qty_delta, Decimal("-2.0000"))

    def test_putaway_moves_inventory_with_a_paired_transaction(self):
        destination = Location.objects.create(
            warehouse=self.warehouse,
            code="INVFC-01-01-02",
            name="Inventory fail-closed destination",
        )
        task = self._task(WmsTask.TaskType.PUTAWAY)
        scan = self._scan(task, qty_base_delta=Decimal("3.000000"))
        line = scan.task_line
        line.to_location = destination
        line.save(update_fields=["to_location"])

        result = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-PUTAWAY",
        )

        self.detail.refresh_from_db()
        destination_detail = InventoryDetail.objects.get(
            owner=self.owner,
            product=self.product,
            location=destination,
            batch_no="INV-FC-LOT",
        )
        transactions = list(
            InventoryTransaction.objects.filter(
                src_model="WmsTask",
                src_id=task.pk,
            ).order_by("id")
        )
        self.assertEqual(result["affected_tx_count"], 2)
        self.assertEqual(self.detail.onhand_qty, Decimal("7.0000"))
        self.assertEqual(destination_detail.onhand_qty, Decimal("3.0000"))
        self.assertCountEqual(
            [(tx.tx_type, tx.qty_delta) for tx in transactions],
            [
                (InvTxType.ISSUE, Decimal("-3.0000")),
                (InvTxType.RECEIVE, Decimal("3.0000")),
            ],
        )
        self.assertEqual(len({tx.pair_id for tx in transactions}), 1)
        self.assertIsNotNone(transactions[0].pair_id)

    def test_nonzero_count_gain_writes_adjustment_transaction(self):
        task = self._task(WmsTask.TaskType.COUNT)
        scan = self._scan(
            task,
            qty_base=Decimal("2.000000"),
            qty_base_delta=None,
        )

        result = inventory_services.post_task(
            task=task,
            scans=[scan],
            batch_no="INV-FC-COUNT-GAIN",
        )

        self.detail.refresh_from_db()
        transaction = InventoryTransaction.objects.get(
            src_model="WmsTask",
            src_id=task.pk,
        )
        self.assertEqual(result["affected_tx_count"], 1)
        self.assertEqual(self.detail.onhand_qty, Decimal("12.0000"))
        self.assertEqual(transaction.tx_type, InvTxType.ADJ_GAIN)
        self.assertEqual(transaction.qty_delta, Decimal("2.0000"))


class InventoryPostingRealConcurrencyTests(TransactionTestCase):
    """Exercise the real handler and inventory transaction lock sequence."""

    def setUp(self):
        self.owner = Owner.objects.create(
            code="IFC-CONC",
            name="Inventory FC concurrency",
        )
        self.warehouse = Warehouse.objects.create(
            code="IFCC-WH",
            name="Inventory FC concurrent WH",
        )
        Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="INVFCC",
            name="Inventory FC concurrent SW",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="INVFCC-01-01-01",
            name="Inventory fail-closed concurrency location",
        )
        uom = ProductUom.objects.create(
            code="INV-FC-CONC-EA",
            name="Inventory fail-closed concurrency EA",
            is_active=True,
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="INV-FC-CONC-SKU",
            sku="INV-FC-CONC-SKU",
            name="Inventory fail-closed concurrency product",
            base_uom=uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.location,
            batch_no="INV-FC-CONC-LOT",
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        self.task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="INV-FC-CONC-TASK",
            task_type=WmsTask.TaskType.ADJUST,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )
        line = WmsTaskLine.objects.create(
            task=self.task,
            product=self.product,
            from_location=self.location,
            qty_plan=Decimal("2.000"),
            qty_done=Decimal("2.000"),
            status=WmsTaskLine.Status.COMPLETED,
        )
        self.scan = TaskScanLog.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task=self.task,
            task_line=line,
            product=self.product,
            location=self.location,
            lot_no="INV-FC-CONC-LOT",
            qty_base_delta=Decimal("2.000000"),
            fp="inventory-fail-closed-concurrency",
            scan_snapshot_rev=0,
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_real_handler_posts_adjustment_once(self):
        rendezvous = threading.Barrier(2)
        winner_inside_inventory = threading.Event()
        release_winner = threading.Event()
        post_call_lock = threading.Lock()
        journal_status_lock = threading.Lock()
        post_call_count = 0
        lock_outside_journal_statuses = []
        results = [None, None]
        errors = []
        batches = ["INV-FC-CONC-1", "INV-FC-CONC-2"]
        real_handle_atomic = DefaultPostingHandler._handle_atomic
        real_post_task = inventory_services.post_task

        def synchronized_handle_atomic(handler, **kwargs):
            with journal_status_lock:
                lock_outside_journal_statuses.append(kwargs["pj"].status)
            try:
                rendezvous.wait(timeout=10)
            except threading.BrokenBarrierError as exc:
                raise AssertionError(
                    "posting requests did not reach the barrier"
                ) from exc
            return real_handle_atomic(handler, **kwargs)

        def held_post_task(*args, **kwargs):
            nonlocal post_call_count
            with post_call_lock:
                post_call_count += 1
                call_number = post_call_count
            if call_number == 1:
                winner_inside_inventory.set()
                if not release_winner.wait(timeout=10):
                    raise AssertionError("timed out releasing winning posting request")
            return real_post_task(*args, **kwargs)

        def invoke(index):
            close_old_connections()
            try:
                task = WmsTask.objects.get(pk=self.task.pk)
                scan = TaskScanLog.objects.get(pk=self.scan.pk)
                results[index] = DefaultPostingHandler().handle(
                    task=task,
                    scans=[scan],
                    batch_no=batches[index],
                    note=f"concurrent attempt {index}",
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=invoke, args=(index,)) for index in range(2)]
        with (
            mock.patch.object(
                DefaultPostingHandler,
                "_handle_atomic",
                new=synchronized_handle_atomic,
            ),
            mock.patch.object(
                inventory_services,
                "post_task",
                side_effect=held_post_task,
            ),
        ):
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(
                    winner_inside_inventory.wait(timeout=10),
                    "no posting request reached the inventory service",
                )
                self.assertTrue(all(thread.is_alive() for thread in threads))
            finally:
                release_winner.set()
                rendezvous.abort()
                for thread in threads:
                    thread.join(timeout=10)

        if any(thread.is_alive() for thread in threads):
            self.fail("concurrent posting threads did not finish")
        if errors:
            raise errors[0]

        self.task.refresh_from_db()
        self.scan.refresh_from_db()
        self.detail.refresh_from_db()
        journals = PostingJournal.objects.filter(
            src_model="WmsTask",
            src_id=self.task.pk,
            tx_type="POST",
        )
        self.assertEqual(journals.count(), 1)
        journal = journals.get()
        transactions = InventoryTransaction.objects.filter(
            src_model="WmsTask",
            src_id=self.task.pk,
        )

        self.assertEqual(sorted(results), [0, 1])
        self.assertEqual(lock_outside_journal_statuses, ["PENDING", "PENDING"])
        self.assertEqual(post_call_count, 1)
        self.assertEqual(transactions.count(), 1)
        transaction = transactions.get()
        self.assertEqual(transaction.tx_type, InvTxType.ADJ_GAIN)
        self.assertEqual(transaction.qty_delta, Decimal("2.0000"))
        self.assertEqual(self.detail.onhand_qty, Decimal("12.0000"))
        self.assertEqual(TaskScanLog.objects.filter(pk=self.scan.pk).count(), 1)
        self.assertIsNotNone(self.scan.posted_at)
        self.assertIn(self.scan.posting_batch, batches)
        self.assertEqual(self.scan.posting_journal_id, journal.pk)
        self.assertEqual(self.task.posting_status, WmsTask.PostingStatus.POSTED)
        self.assertEqual(journal.status, "POSTED")
        self.assertEqual(journal.attempt_count, 1)
