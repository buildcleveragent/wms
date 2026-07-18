from __future__ import annotations

import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from allapp.baseinfo.models import Customer, Owner, Supplier
from allapp.core.choices import InvTxType
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product, ProductUom
from allapp.reports.models import (
    AggThroughputDaily,
    EtlJobRun,
    EtlWatermark,
    FactInboundLine,
    FactInventoryTxn,
    FactOutboundLine,
    OwnerDim,
    ProductDim,
    WarehouseDim,
)
from allapp.tasking.models import ReceiveLineExtra, WmsTask


pytestmark = pytest.mark.integration


class OperationsEtlTests(TestCase):
    day = datetime.date(2026, 7, 14)

    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="ETLOWN", name="ETL Owner")
        cls.warehouse = Warehouse.objects.create(code="ETLWH", name="ETL Warehouse")
        subwarehouse = Subwarehouse.objects.create(
            warehouse=cls.warehouse, code="ETLSW", name="ETL Subwarehouse"
        )
        cls.location = Location.objects.create(
            warehouse=cls.warehouse,
            subwarehouse=subwarehouse,
            code="ETLSW-01-01-01",
            name="ETL Location",
        )
        uom = ProductUom.objects.create(code="ETLEA", name="ETL Each")
        cls.product = Product.objects.create(
            owner=cls.owner,
            code="ETL-P",
            sku="ETL-SKU",
            name="ETL Product",
            base_uom=uom,
            weight=Decimal("2.500"),
            volume=Decimal("0.012500"),
            shelf_life_days=365,
        )
        cls.actor = get_user_model().objects.create_user(username="etl-actor")
        cls.supplier = Supplier.objects.create(
            owner=cls.owner, code="ETL-SUP", name="ETL Supplier"
        )
        cls.customer = Customer.objects.create(
            owner=cls.owner,
            salesperson=cls.actor,
            code="ETL-CUST",
            name="ETL Customer",
        )

        cls.inbound = InboundOrder.objects.create(
            owner=cls.owner,
            supplier=cls.supplier,
            warehouse=cls.warehouse,
            created_by=cls.actor,
            order_no="ETL-IN-1",
            biz_date=cls.day,
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        cls.inbound_line = InboundOrderLine.objects.create(
            order=cls.inbound,
            product=cls.product,
            base_uom="ETLEA",
            base_qty=Decimal("10"),
            base_price=Decimal("1"),
            line_no=10,
            lot_no="ETL-LOT",
        )
        order_created = datetime.datetime(2026, 7, 14, 8, 0)
        InboundOrder.objects.filter(pk=cls.inbound.pk).update(created_at=order_created)
        cls.inbound.created_at = order_created

        receive_at = datetime.datetime(2026, 7, 14, 9, 30)
        cls.receive_task = cls._completed_task(
            task_no="ETL-RCV-1",
            task_type=WmsTask.TaskType.RECEIVE,
            source_model="InboundOrder",
            source_pk=str(cls.inbound.pk),
            ref_no=cls.inbound.order_no,
            started_at=datetime.datetime(2026, 7, 14, 9, 0),
            finished_at=receive_at,
        )
        receive_line = cls.receive_task.lines.create(
            product=cls.product,
            qty_plan=Decimal("10"),
            qty_done=Decimal("6"),
            status=WmsTask.Status.COMPLETED,
            finished_at=receive_at,
            finished_by=cls.actor,
            src_model="InboundOrderLine",
            src_id=cls.inbound_line.pk,
            plan_meta={"lot_no": "ETL-LOT"},
        )
        ReceiveLineExtra.objects.create(
            line=receive_line,
            lot_no="ETL-LOT",
            mfg_date=cls.day,
            exp_date=cls.day + datetime.timedelta(days=365),
            qty_ok=Decimal("6"),
            qty_damage=Decimal("1"),
            qty_reject=Decimal("2"),
            damage_reason_code="DAMAGED",
            reject_reason_code="REJECTED",
        )
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=cls.owner,
            product=cls.product,
            warehouse=cls.warehouse,
            location=cls.location,
            qty_delta=Decimal("6"),
            batch_no="ETL-LOT",
            src_model="WmsTask",
            src_id=cls.receive_task.pk,
            src_line_id=receive_line.pk,
            src_no=cls.inbound.order_no,
            posted_at=receive_at,
            posting_batch="ETL-RCV-1",
        )
        putaway_at = datetime.datetime(2026, 7, 14, 10, 30)
        cls.putaway_task = cls._completed_task(
            task_no="ETL-PA-1",
            task_type=WmsTask.TaskType.PUTAWAY,
            source_model="WmsTask",
            source_pk=str(cls.receive_task.pk),
            ref_no=cls.inbound.order_no,
            started_at=receive_at,
            finished_at=putaway_at,
        )
        cls.putaway_task.lines.create(
            product=cls.product,
            to_location=cls.location,
            qty_plan=Decimal("6"),
            qty_done=Decimal("6"),
            status=WmsTask.Status.COMPLETED,
            finished_at=putaway_at,
            finished_by=cls.actor,
            src_model="InboundOrderLine",
            src_id=cls.inbound_line.pk,
        )

        cls.outbound = OutboundOrder.objects.create(
            owner=cls.owner,
            customer=cls.customer,
            warehouse=cls.warehouse,
            created_by=cls.actor,
            order_no="ETL-OUT-1",
            biz_date=cls.day,
            etd=datetime.datetime(2026, 7, 14, 16, 0),
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        cls.outbound_line = OutboundOrderLine.objects.create(
            order=cls.outbound,
            product=cls.product,
            base_uom=cls.product.base_uom,
            base_qty=Decimal("10"),
            base_price=Decimal("1"),
            line_no=10,
            lot_no="ETL-LOT",
        )
        OutboundOrder.objects.filter(pk=cls.outbound.pk).update(created_at=order_created)
        cls.outbound.created_at = order_created

        cls.pick_task = cls._completed_task(
            task_no="ETL-PICK-1",
            task_type=WmsTask.TaskType.PICK,
            source_model="OutboundOrder",
            source_pk=str(cls.outbound.pk),
            ref_no=cls.outbound.order_no,
            released_at=datetime.datetime(2026, 7, 14, 10, 0),
            started_at=datetime.datetime(2026, 7, 14, 10, 15),
            finished_at=datetime.datetime(2026, 7, 14, 11, 0),
        )
        cls.pick_line = cls.pick_task.lines.create(
            product=cls.product,
            from_location=cls.location,
            qty_plan=Decimal("10"),
            qty_done=Decimal("9"),
            status=WmsTask.Status.COMPLETED,
            finished_at=cls.pick_task.finished_at,
            finished_by=cls.actor,
            src_model="OutboundOrderLine",
            src_id=cls.outbound_line.pk,
            plan_meta={"lot_no": "ETL-LOT"},
        )
        InventoryTransaction.objects.create(
            tx_type=InvTxType.ISSUE,
            owner=cls.owner,
            product=cls.product,
            warehouse=cls.warehouse,
            location=cls.location,
            qty_delta=Decimal("-9"),
            batch_no="ETL-LOT",
            src_model="WmsTask",
            src_id=cls.pick_task.pk,
            src_line_id=cls.pick_line.pk,
            src_no=cls.outbound.order_no,
            posted_at=cls.pick_task.finished_at,
            posting_batch="ETL-PICK-1",
        )
        cls.pack_task = cls._completed_task(
            task_no="ETL-PACK-1",
            task_type=WmsTask.TaskType.PACK,
            source_model="WmsTask",
            source_pk=str(cls.pick_task.pk),
            ref_no=cls.outbound.order_no,
            started_at=cls.pick_task.finished_at,
            finished_at=datetime.datetime(2026, 7, 14, 12, 0),
        )
        cls.pack_line = cls.pack_task.lines.create(
            product=cls.product,
            qty_plan=Decimal("10"),
            qty_done=Decimal("9"),
            status=WmsTask.Status.COMPLETED,
            finished_at=cls.pack_task.finished_at,
            finished_by=cls.actor,
            src_model="OutboundOrderLine",
            src_id=cls.outbound_line.pk,
        )
        cls.dispatch_task = cls._completed_task(
            task_no="ETL-DSP-1",
            task_type=WmsTask.TaskType.DISPATCH,
            source_model="WmsTask",
            source_pk=str(cls.pack_task.pk),
            ref_no=cls.outbound.order_no,
            started_at=cls.pack_task.finished_at,
            finished_at=datetime.datetime(2026, 7, 14, 15, 0),
        )
        cls.dispatch_line = cls.dispatch_task.lines.create(
            product=cls.product,
            qty_plan=Decimal("10"),
            qty_done=Decimal("9"),
            status=WmsTask.Status.COMPLETED,
            finished_at=cls.dispatch_task.finished_at,
            finished_by=cls.actor,
            src_model="OutboundOrderLine",
            src_id=cls.outbound_line.pk,
        )

    @classmethod
    def _completed_task(
        cls,
        *,
        task_no,
        task_type,
        source_model,
        source_pk,
        ref_no,
        started_at,
        finished_at,
        released_at=None,
    ):
        return WmsTask.objects.create(
            owner=cls.owner,
            warehouse=cls.warehouse,
            task_no=task_no,
            task_type=task_type,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
            approved_by=cls.actor,
            approved_at=finished_at,
            posted_by=cls.actor,
            posted_at=finished_at,
            released_at=released_at,
            started_at=started_at,
            finished_at=finished_at,
            ref_no=ref_no,
            source_app="inbound" if task_type in {"RECEIVE", "PUTAWAY"} else "outbound",
            source_model=source_model,
            source_pk=source_pk,
        )

    def _full(self):
        call_command(
            "etl_full_reports",
            "--snapdate",
            self.day.isoformat(),
            stdout=StringIO(),
        )

    def _full_range(self):
        call_command(
            "etl_full_reports",
            "--snapdate",
            self.day.isoformat(),
            "--from",
            self.day.isoformat(),
            "--to",
            self.day.isoformat(),
            stdout=StringIO(),
        )

    def _incremental(self, **options):
        call_command("etl_incremental_reports", stdout=StringIO(), **options)

    def test_full_etl_is_idempotent_and_populates_business_milestones(self):
        self._full()

        self.assertEqual(OwnerDim.objects.count(), 1)
        self.assertEqual(WarehouseDim.objects.count(), 1)
        self.assertEqual(ProductDim.objects.count(), 1)
        self.assertEqual(FactInboundLine.objects.count(), 1)
        self.assertEqual(FactOutboundLine.objects.count(), 1)
        self.assertEqual(FactInventoryTxn.objects.count(), 2)

        inbound = FactInboundLine.objects.get(line_id=self.inbound_line.pk)
        self.assertEqual(inbound.qty_plan, Decimal("10"))
        self.assertEqual(inbound.qty_received, Decimal("6"))
        self.assertEqual(inbound.qty_damage, Decimal("1"))
        self.assertEqual(inbound.qty_reject, Decimal("2"))
        self.assertEqual(inbound.receive_date.date, self.day)
        self.assertEqual(inbound.putaway_date.date, self.day)
        self.assertEqual(inbound.sec_to_receive, 90 * 60)
        self.assertEqual(inbound.sec_to_putaway, 60 * 60)

        outbound = FactOutboundLine.objects.get(line_id=self.outbound_line.pk)
        self.assertEqual(outbound.qty_plan, Decimal("10"))
        self.assertEqual(outbound.qty_alloc, Decimal("10"))
        self.assertEqual(outbound.qty_picked, Decimal("9"))
        self.assertEqual(outbound.qty_packed, Decimal("9"))
        self.assertEqual(outbound.qty_shipped, Decimal("9"))
        self.assertEqual(outbound.sec_alloc, 2 * 60 * 60)
        self.assertEqual(outbound.sec_pick, 60 * 60)
        self.assertEqual(outbound.sec_pack, 60 * 60)
        self.assertEqual(outbound.sec_ship, 3 * 60 * 60)
        self.assertFalse(outbound.in_full)
        self.assertFalse(outbound.on_time)

        run = EtlJobRun.objects.get(job_name="etl_full_reports")
        self.assertTrue(run.ok)
        self.assertTrue(run.reconciliation["ok"])
        self.assertFalse(run.reconciliation["differences"])
        self.assertGreaterEqual(run.reconciliation["aggregates_refreshed"], 1)
        aggregate = AggThroughputDaily.objects.get(
            date__date=self.day,
            owner__owner_id=self.owner.id,
            warehouse__warehouse_id=self.warehouse.id,
        )
        self.assertEqual(aggregate.inbound_qty, Decimal("6"))
        self.assertEqual(aggregate.outbound_qty, Decimal("9"))

        self._full()
        self.assertEqual(OwnerDim.objects.count(), 1)
        self.assertEqual(WarehouseDim.objects.count(), 1)
        self.assertEqual(ProductDim.objects.count(), 1)
        self.assertEqual(FactInboundLine.objects.count(), 1)
        self.assertEqual(FactOutboundLine.objects.count(), 1)
        self.assertEqual(FactInventoryTxn.objects.count(), 2)
        self.assertEqual(EtlJobRun.objects.filter(job_name="etl_full_reports", ok=True).count(), 2)

    def test_full_etl_keeps_posted_transactions_for_soft_deleted_products(self):
        archived_product = Product.objects.create(
            owner=self.owner,
            code="ETL-ARCHIVED",
            sku="ETL-ARCHIVED-SKU",
            name="Archived ETL Product",
            base_uom=self.product.base_uom,
            shelf_life_days=365,
        )
        transaction_row = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=archived_product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("3"),
            batch_no="ETL-ARCHIVED-LOT",
            src_model="LegacyReceipt",
            src_id=90001,
            src_line_id=90001,
            src_no="ETL-ARCHIVED-1",
            posted_at=datetime.datetime(2026, 7, 14, 13, 0),
            posting_batch="ETL-ARCHIVED-1",
        )
        Product.objects.filter(pk=archived_product.pk).update(is_deleted=True)

        self._full()

        self.assertTrue(
            ProductDim.objects.filter(product_id=archived_product.pk, is_current=True).exists()
        )
        fact = FactInventoryTxn.objects.get(txn_id=transaction_row.pk)
        self.assertEqual(fact.product.product_id, archived_product.pk)
        self.assertEqual(fact.qty_delta, Decimal("3"))
        run = EtlJobRun.objects.get(job_name="etl_full_reports")
        self.assertTrue(run.reconciliation["ok"])
        self.assertFalse(run.reconciliation["differences"])

    def test_etl_excludes_putaway_move_receive_and_cancelled_dispatch(self):
        """Internal moves and cancelled work must not inflate actual receipts/shipments."""

        putaway_line = self.putaway_task.lines.get()
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("6"),
            batch_no="ETL-LOT",
            src_model="WmsTask",
            src_id=self.putaway_task.pk,
            src_line_id=putaway_line.pk,
            src_no=self.inbound.order_no,
            posted_at=datetime.datetime(2026, 7, 14, 10, 30),
            posting_batch="ETL-PA-1",
        )
        WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="ETL-DSP-CANCEL",
            task_type=WmsTask.TaskType.DISPATCH,
            status=WmsTask.Status.CANCELLED,
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
            finished_at=datetime.datetime(2026, 7, 15, 9, 0),
            ref_no=self.outbound.order_no,
            source_app="outbound",
            source_model="WmsTask",
            source_pk=str(self.pack_task.pk),
        )

        self._full()

        inbound = FactInboundLine.objects.get(line_id=self.inbound_line.pk)
        outbound = FactOutboundLine.objects.get(line_id=self.outbound_line.pk)
        self.assertEqual(inbound.qty_received, Decimal("6"))
        self.assertEqual(inbound.receive_date.date, self.day)
        self.assertEqual(outbound.ship_date.date, self.day)
        self.assertEqual(outbound.sec_ship, 3 * 60 * 60)
        self.assertEqual(
            FactInventoryTxn.objects.get(txn_id=InventoryTransaction.objects.get(
                posting_batch="ETL-PA-1"
            ).id).order_type,
            "TRANSFER",
        )

    def test_etl_keeps_same_sku_quantities_on_their_source_order_lines(self):
        """A line-level source reference must beat product-level allocation."""

        inbound_second = InboundOrderLine.objects.create(
            order=self.inbound,
            product=self.product,
            base_uom="ETLEA",
            base_qty=Decimal("10"),
            base_price=Decimal("1"),
            line_no=20,
            lot_no="ETL-LOT-2",
        )
        receive_second = self.receive_task.lines.create(
            product=self.product,
            qty_plan=Decimal("10"),
            qty_done=Decimal("4"),
            status=WmsTask.Status.COMPLETED,
            finished_at=self.receive_task.finished_at,
            finished_by=self.actor,
            src_model="InboundOrderLine",
            src_id=inbound_second.pk,
        )
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("4"),
            batch_no="ETL-LOT-2",
            src_model="WmsTask",
            src_id=self.receive_task.pk,
            src_line_id=receive_second.pk,
            src_no=self.inbound.order_no,
            posted_at=self.receive_task.finished_at,
            posting_batch="ETL-RCV-2",
        )

        outbound_second = OutboundOrderLine.objects.create(
            order=self.outbound,
            product=self.product,
            base_uom=self.product.base_uom,
            base_qty=Decimal("10"),
            base_price=Decimal("1"),
            line_no=20,
            lot_no="ETL-LOT-2",
        )
        for task, quantity in (
            (self.pick_task, Decimal("2")),
            (self.pack_task, Decimal("2")),
            (self.dispatch_task, Decimal("2")),
        ):
            task.lines.create(
                product=self.product,
                qty_plan=Decimal("2"),
                qty_done=quantity,
                status=WmsTask.Status.COMPLETED,
                finished_at=task.finished_at,
                finished_by=self.actor,
                src_model="OutboundOrderLine",
                src_id=outbound_second.pk,
                plan_meta={"lot_no": "ETL-LOT-2"},
            )

        self._full()

        self.assertEqual(
            FactInboundLine.objects.get(line_id=self.inbound_line.pk).qty_received,
            Decimal("6"),
        )
        self.assertEqual(
            FactInboundLine.objects.get(line_id=inbound_second.pk).qty_received,
            Decimal("4"),
        )
        self.assertEqual(
            FactOutboundLine.objects.get(line_id=self.outbound_line.pk).qty_shipped,
            Decimal("9"),
        )
        self.assertEqual(
            FactOutboundLine.objects.get(line_id=outbound_second.pk).qty_shipped,
            Decimal("2"),
        )

    def test_ranged_full_etl_reconciles_the_same_window_on_an_empty_mart(self):
        self._full_range()

        self.assertEqual(FactInboundLine.objects.count(), 1)
        self.assertEqual(FactOutboundLine.objects.count(), 1)
        self.assertEqual(FactInventoryTxn.objects.count(), 2)
        run = EtlJobRun.objects.get(job_name="etl_full_reports")
        self.assertTrue(run.ok)
        self.assertTrue(run.reconciliation["ok"])

    def test_failed_full_reconciliation_rolls_back_all_mart_mutations(self):
        with patch(
            "allapp.reports.management.commands.etl_full_reports.require_reconciliation",
            side_effect=RuntimeError("forced full reconciliation failure"),
        ):
            with self.assertRaises(CommandError):
                self._full()

        self.assertEqual(OwnerDim.objects.count(), 0)
        self.assertEqual(WarehouseDim.objects.count(), 0)
        self.assertEqual(ProductDim.objects.count(), 0)
        self.assertEqual(FactInboundLine.objects.count(), 0)
        self.assertEqual(FactOutboundLine.objects.count(), 0)
        self.assertEqual(FactInventoryTxn.objects.count(), 0)
        run = EtlJobRun.objects.get(job_name="etl_full_reports")
        self.assertFalse(run.ok)
        self.assertIn("forced full reconciliation failure", run.error)

    def test_incremental_etl_updates_affected_order_and_is_idempotent(self):
        self._full()
        self._incremental(since="1970-01-01T00:00:00")
        first_watermark = EtlWatermark.objects.get(domain="operations").watermark_value

        changed_at = timezone.now()
        for line in (self.pick_line, self.pack_line, self.dispatch_line):
            type(line).objects.filter(pk=line.pk).update(
                qty_done=Decimal("10"), updated_at=changed_at
            )

        self._incremental()
        second_watermark = EtlWatermark.objects.get(domain="operations").watermark_value
        self.assertGreater(second_watermark, first_watermark)
        fact = FactOutboundLine.objects.get(line_id=self.outbound_line.pk)
        self.assertEqual(fact.qty_picked, Decimal("10"))
        self.assertEqual(fact.qty_packed, Decimal("10"))
        self.assertEqual(fact.qty_shipped, Decimal("10"))
        self.assertTrue(fact.in_full)
        self.assertTrue(fact.on_time)

        self._incremental(since=first_watermark)
        self.assertEqual(FactOutboundLine.objects.count(), 1)
        self.assertEqual(FactInventoryTxn.objects.count(), 2)
        self.assertTrue(EtlJobRun.objects.filter(job_name="etl_incremental_reports").last().ok)

    def test_failed_incremental_reconciliation_does_not_advance_watermark(self):
        self._full()
        before = FactOutboundLine.objects.get(line_id=self.outbound_line.pk)
        self.assertEqual(before.qty_shipped, Decimal("9"))
        self._incremental(since="1970-01-01T00:00:00")
        watermark = EtlWatermark.objects.get(domain="operations").watermark_value
        type(self.dispatch_line).objects.filter(pk=self.dispatch_line.pk).update(
            qty_done=Decimal("10"), updated_at=timezone.now()
        )

        with patch(
            "allapp.reports.management.commands.etl_incremental_reports.require_reconciliation",
            side_effect=RuntimeError("forced reconciliation failure"),
        ):
            with self.assertRaises(CommandError):
                self._incremental()

        current = EtlWatermark.objects.get(domain="operations").watermark_value
        self.assertEqual(current, watermark)
        # The candidate fact update was in the same transaction as the failed
        # reconciliation and must not leak into the published mart.
        fact = FactOutboundLine.objects.get(line_id=self.outbound_line.pk)
        self.assertEqual(fact.qty_shipped, Decimal("9"))
        run = EtlJobRun.objects.filter(job_name="etl_incremental_reports").latest("id")
        self.assertFalse(run.ok)
        self.assertIn("forced reconciliation failure", run.error)
        self.assertIsNotNone(run.finished_at)
