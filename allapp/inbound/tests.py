import datetime
import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from allapp.baseinfo.models import Owner, Supplier
from allapp.inbound.models import InboundOrder, InboundReceipt, Lot, LotWarehouse
from allapp.inbound.services import create_receive_task_draft
from allapp.locations.models import Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask


class InboundWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inbound", code="OWN-INB")
        self.warehouse = Warehouse.objects.create(code="WH-INB-1", name="Warehouse Inbound 1")
        self.supplier = Supplier.objects.create(owner=self.owner, code="SUP-INB", name="Supplier Inbound")
        self.user = get_user_model().objects.create_user(username="inbound-user", password="x")
        self.base_uom = ProductUom.objects.create(code="PCS-INB", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-INB",
            name="Inbound Product",
            sku="SKU-INB",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )

    def test_inbound_order_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            InboundOrder.objects.create(
                owner=self.owner,
                supplier=self.supplier,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_inbound_receipt_derives_warehouse_from_order(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
        )

        receipt = InboundReceipt.objects.create(
            receipt_no="RCPT-INB-1",
            order=order,
            owner=self.owner,
            supplier=self.supplier,
            biz_date=datetime.date(2026, 3, 29),
        )

        self.assertEqual(receipt.warehouse_id, self.warehouse.id)

    def test_lot_warehouse_requires_explicit_warehouse(self):
        lot = Lot.objects.create(owner=self.owner, product_code="SKU-INB", lot_no="LOT-INB-1")

        with self.assertRaises(ValidationError) as exc:
            LotWarehouse.objects.create(
                lot=lot,
                owner=self.owner,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_create_receive_task_draft_is_idempotent(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("5.000"),
            base_price=Decimal("8.0000"),
        )

        first_task = create_receive_task_draft(order, by_user=self.user)
        second_task = create_receive_task_draft(order, by_user=self.user)

        self.assertEqual(first_task.id, second_task.id)
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.RECEIVE,
                source_app="inbound",
                source_model="InboundOrder",
                source_pk=str(order.pk),
            ).count(),
            1,
        )
        self.assertEqual(first_task.lines.count(), 1)


class InboundConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inbound Concurrent", code="OWN-INB-C")
        self.warehouse = Warehouse.objects.create(code="WH-INB-C", name="Warehouse Inbound Concurrent")
        self.supplier = Supplier.objects.create(owner=self.owner, code="SUP-INB-C", name="Supplier Inbound Concurrent")
        self.user = get_user_model().objects.create_user(username="inbound-concurrent-user", password="x")
        self.base_uom = ProductUom.objects.create(code="PCS-INB-C", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-INB-C",
            name="Inbound Concurrent Product",
            sku="SKU-INB-C",
            base_uom=self.base_uom,
            volume="0.100000",
            price="10.00",
            batch_control=False,
            expiry_control=False,
        )

    def test_create_receive_task_draft_is_single_under_concurrency(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("5.000"),
            base_price=Decimal("8.0000"),
        )

        sequence_entered = threading.Event()
        release_sequence = threading.Event()
        sequence_calls = 0
        sequence_lock = threading.Lock()
        results = [None, None]
        errors = []

        def fake_next_code(*args, **kwargs):
            nonlocal sequence_calls
            with sequence_lock:
                sequence_calls += 1
                current_call = sequence_calls
            if current_call == 1:
                sequence_entered.set()
                if not release_sequence.wait(timeout=5):
                    raise AssertionError("timed out waiting to release inbound concurrent test")
            return f"SH-CONC-{current_call}"

        def invoke(index):
            close_old_connections()
            try:
                results[index] = create_receive_task_draft(order, by_user=self.user)
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch("allapp.inbound.services.DocSequence.next_code", side_effect=fake_next_code):
            thread1 = threading.Thread(target=invoke, args=(0,))
            thread1.start()
            self.assertTrue(sequence_entered.wait(timeout=5))

            thread2 = threading.Thread(target=invoke, args=(1,))
            thread2.start()

            release_sequence.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent inbound task creation threads did not finish")
        if errors:
            raise errors[0]

        self.assertEqual(sequence_calls, 1)
        self.assertEqual(results[0].id, results[1].id)
        self.assertEqual(
            WmsTask.objects.filter(
                task_type=WmsTask.TaskType.RECEIVE,
                source_app="inbound",
                source_model="InboundOrder",
                source_pk=str(order.pk),
            ).count(),
            1,
        )
        self.assertEqual(results[0].lines.count(), 1)
