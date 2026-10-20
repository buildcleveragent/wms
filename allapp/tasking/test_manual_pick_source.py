from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError as InputError

from allapp.accounts.models import AuditEvent
from allapp.inventory import test_posting_fail_closed as fixtures
from allapp.inventory.models import InventoryTransaction
from allapp.inventory.services import post_task
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine
from allapp.tasking.services import adjust_pick_line_qty


class ManualPickSourceTests(TestCase):
    def setUp(self):
        fixtures.InventoryPostingFailClosedTests.setUp(self)
        self.detail.production_date = date(2026, 1, 1)
        self.detail.expiry_date = date(2027, 1, 1)
        self.detail.allocated_qty = 8
        self.detail.save()
        self.task = WmsTask.objects.create(
            task_no="MANUAL-SOURCE",
            task_type="PICK",
            status="RELEASED",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.line = WmsTaskLine.objects.create(
            task=self.task,
            product=self.product,
            from_location=self.location,
            qty_plan=8,
            plan_meta={"inventory_detail_id": self.detail.pk},
        )

    def adjust(self, qty, key):
        return adjust_pick_line_qty(self.task.pk, self.line.pk, qty, client_seq=key)

    def post(self):
        WmsTask.objects.filter(pk=self.task.pk).update(
            status="COMPLETED",
            review_status="APPROVED",
            posting_status="PENDING",
        )
        self.task.refresh_from_db()
        return post_task(task=self.task, scans=list(TaskScanLog.objects.filter(task=self.task)))

    def test_signed_adjustments_preserve_dimensions_and_retry_does_not_rewind(self):
        self.adjust(6, "first")
        self.adjust(4, "second")
        replay = self.adjust(6, "first")
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["qty_done"], Decimal("4"))
        self.post()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, Decimal("6"))
        tx = InventoryTransaction.objects.get(src_id=self.task.pk, src_model="WmsTask")
        self.assertEqual(tx.qty_delta, Decimal("-4"))
        self.assertEqual(tx.batch_no, self.detail.batch_no)
        self.assertEqual(tx.expiry_date, self.detail.expiry_date)
        self.assertEqual(tx.production_date, self.detail.production_date)
        post_task(task=self.task, scans=list(TaskScanLog.objects.filter(task=self.task)))
        self.assertEqual(InventoryTransaction.objects.filter(src_id=self.task.pk).count(), 1)

    def test_serial_and_unit_sources_fail_closed(self):
        from allapp.inventory.models import InventoryDetail

        InventoryDetail.objects.filter(pk=self.detail.pk).update(base_unit="WRONG")
        with self.assertRaisesMessage(InputError, "库存来源待核对"):
            self.adjust(1, "wrong-unit")
        InventoryDetail.objects.filter(pk=self.detail.pk).update(
            base_unit=self.uom.code,
            onhand_qty=1,
            allocated_qty=1,
            available_qty=0,
            serial_no="SN-0001",
            serial_no_norm="SN-0001",
        )
        self.adjust(1, "serial")
        self.assertEqual(TaskScanLog.objects.get(task=self.task).serial_no, "SN-0001")
        self.post()
        self.assertTrue(self.adjust(1, "serial")["idempotent"])
        self.assertEqual(InventoryTransaction.objects.get(src_id=self.task.pk).serial_no, "SN-0001")

    def test_legacy_blank_dimensions_are_resolved_without_rewriting_evidence(self):
        self.adjust(3, "legacy")
        scan = TaskScanLog.objects.get(task=self.task)
        TaskScanLog.objects.filter(pk=scan.pk).update(lot_no=None, mfg_date=None, exp_date=None)
        self.post()
        scan.refresh_from_db()
        self.assertIsNone(scan.lot_no)
        self.assertIsNone(scan.exp_date)
        self.assertTrue(
            AuditEvent.objects.filter(action="inventory.pick.resolve_manual_source").exists()
        )

    @override_settings(INVENTORY_FIFO_ENABLED=True)
    def test_manual_pick_consumes_the_matching_fifo_layer(self):
        from allapp.inventory.fifo import create_receipt_layer
        from allapp.inventory.models import InventoryLayerMovement, InventoryLayerPosition

        layer, _ = create_receipt_layer(
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
            product_id=self.product.pk,
            location_id=self.location.pk,
            quantity=Decimal("10"),
            source_type="TEST_OPENING",
            source_id=self.detail.pk,
            batch_no=self.detail.batch_no,
            expiry_date=self.detail.expiry_date,
            unit_cost=Decimal("2.50"),
            cost_currency="CNY",
        )
        self.adjust(2, "fifo")
        self.post()

        self.assertEqual(InventoryLayerPosition.objects.get(layer=layer).remaining_qty, 8)
        movement = InventoryLayerMovement.objects.get(layer=layer, movement_type="ISSUE")
        self.assertEqual(movement.quantity, 2)
        self.assertEqual(movement.inventory_transaction.qty_delta, -2)

    def test_invalid_quantity_and_source_rejected_without_scan_or_quantity_change(self):
        for qty in (-1, 9, "NaN", "bad"):
            with self.subTest(qty=qty), self.assertRaises(InputError):
                self.adjust(qty, str(qty))
        self.line.plan_meta = {}
        self.line.save(update_fields=["plan_meta"])
        with self.assertRaisesMessage(InputError, "库存来源待核对"):
            self.adjust(1, "missing")
        self.assertFalse(TaskScanLog.objects.filter(task=self.task).exists())
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_done, 0)

    def test_conflicting_dimensions_and_failed_audit_roll_back_posting(self):
        self.adjust(2, "conflict")
        TaskScanLog.objects.filter(task=self.task).update(lot_no="WRONG")
        with self.assertRaisesMessage(ValidationError, "库存来源待核对"):
            self.post()
        self.assertFalse(InventoryTransaction.objects.filter(src_id=self.task.pk).exists())
        TaskScanLog.objects.filter(task=self.task).update(lot_no=None)
        with patch(
            "allapp.accounts.audit.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.post()
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.onhand_qty, 10)
