import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner, Supplier
from allapp.inbound.models import InboundOrder, InboundReceipt, Lot, LotWarehouse
from allapp.locations.models import Warehouse


class InboundWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inbound", code="OWN-INB")
        self.warehouse = Warehouse.objects.create(code="WH-INB-1", name="Warehouse Inbound 1")
        self.supplier = Supplier.objects.create(owner=self.owner, code="SUP-INB", name="Supplier Inbound")

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
