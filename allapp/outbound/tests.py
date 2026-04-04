import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from allapp.baseinfo.models import Customer, Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound import services as ob_services
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask


class OutboundWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Outbound", code="OWN-OUT")
        self.user = get_user_model().objects.create_user(username="outbound-sales", password="x")
        self.warehouse = Warehouse.objects.create(code="WH-OUT-1", name="Warehouse Outbound 1")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWOUT1",
            name="Subwarehouse Outbound 1",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWOUT1-01-01-01",
            name="Outbound Location 1",
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUS-OUT",
            name="Customer Outbound",
        )
        self.base_uom = ProductUom.objects.create(code="PCS-OUT", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-OUT",
            name="Outbound Product",
            sku="SKU-OUT",
            base_uom=self.base_uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
            batch_control=False,
            expiry_control=False,
        )

    def test_outbound_order_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            OutboundOrder.objects.create(
                owner=self.owner,
                customer=self.customer,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_owner_approve_is_idempotent_for_allocation(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            created_by=self.user,
            submit_status="SUBMITTED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("3.000"),
            base_price=Decimal("10.0000"),
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )

        order.owner_approve(by_user=self.user, allow_backorder=False)
        detail.refresh_from_db()
        self.assertEqual(detail.allocated_qty, Decimal("3.0000"))
        self.assertEqual(detail.available_qty, Decimal("7.0000"))

        order.owner_approve(by_user=self.user, allow_backorder=False)

        detail.refresh_from_db()
        self.assertEqual(detail.allocated_qty, Decimal("3.0000"))
        self.assertEqual(detail.available_qty, Decimal("7.0000"))

        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model=order._meta.model_name,
            source_pk=str(order.pk),
        )
        self.assertEqual(WmsTask.objects.filter(pk=task.pk).count(), 1)
        self.assertEqual(task.lines.count(), 1)
        self.assertEqual(task.lines.first().qty_plan, Decimal("3.000"))


class OutboundConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Outbound Concurrent", code="OWN-OUT-C")
        self.user = get_user_model().objects.create_user(username="outbound-concurrent-user", password="x")
        self.warehouse = Warehouse.objects.create(code="WH-OUT-C", name="Warehouse Outbound Concurrent")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWOUTC",
            name="SW Outbound C",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWOUTC-01-01-01",
            name="Outbound Concurrent Location 1",
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUS-OUT-C",
            name="Customer Outbound Concurrent",
        )
        self.base_uom = ProductUom.objects.create(code="PCS-OUT-C", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-OUT-C",
            name="Outbound Concurrent Product",
            sku="SKU-OUT-C",
            base_uom=self.base_uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
            batch_control=False,
            expiry_control=False,
        )

    def test_owner_approve_allocates_once_under_concurrency(self):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            created_by=self.user,
            submit_status="SUBMITTED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("3.000"),
            base_price=Decimal("10.0000"),
        )
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.base_uom.code,
        )

        reserved_task_entered = threading.Event()
        release_reserved_task = threading.Event()
        errors = []
        real_get_or_create_reserved_task = ob_services._get_or_create_reserved_task

        def fake_get_or_create_reserved_task(current_order, by_user=None):
            if not reserved_task_entered.is_set():
                reserved_task_entered.set()
                if not release_reserved_task.wait(timeout=5):
                    raise AssertionError("timed out waiting to release outbound concurrent test")
            return real_get_or_create_reserved_task(current_order, by_user=by_user)

        def invoke():
            close_old_connections()
            try:
                thread_order = OutboundOrder.objects.get(pk=order.pk)
                thread_order.owner_approve(by_user=self.user, allow_backorder=False)
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch("allapp.outbound.services._get_or_create_reserved_task", side_effect=fake_get_or_create_reserved_task):
            thread1 = threading.Thread(target=invoke)
            thread1.start()
            self.assertTrue(reserved_task_entered.wait(timeout=5))

            thread2 = threading.Thread(target=invoke)
            thread2.start()

            release_reserved_task.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent outbound approval threads did not finish")
        if errors:
            raise errors[0]

        detail.refresh_from_db()
        self.assertEqual(detail.allocated_qty, Decimal("3.0000"))
        self.assertEqual(detail.available_qty, Decimal("7.0000"))

        task = WmsTask.objects.get(
            task_type=WmsTask.TaskType.PICK,
            source_model=order._meta.model_name,
            source_pk=str(order.pk),
        )
        self.assertEqual(WmsTask.objects.filter(pk=task.pk).count(), 1)
        self.assertEqual(task.lines.count(), 1)
        self.assertEqual(task.lines.first().qty_plan, Decimal("3.000"))
