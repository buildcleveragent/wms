from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask, WmsTaskLine


class ProductOwnerInvariantTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Invariant Owner A", code="INV-A")
        self.other_owner = Owner.objects.create(name="Invariant Owner B", code="INV-B")
        self.warehouse = Warehouse.objects.create(code="INV-WH", name="Invariant WH")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse, code="INVSW", name="Invariant SW"
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            subwarehouse=self.subwarehouse,
            code="INVSW-01-01-01",
            name="Invariant Location",
        )
        self.uom = ProductUom.objects.create(code="INV-PCS", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="INV-SKU",
            sku="INV-SKU",
            name="Invariant Product",
            base_uom=self.uom,
            volume=Decimal("0.1"),
        )

    def test_task_line_rejects_product_from_another_owner(self):
        task = WmsTask.objects.create(
            owner=self.other_owner,
            warehouse=self.warehouse,
            task_no="INV-TASK",
            task_type=WmsTask.TaskType.RECEIVE,
        )

        with self.assertRaises(ValidationError):
            WmsTaskLine.objects.create(task=task, product=self.product, qty_plan=1)

    def test_inventory_detail_rejects_product_from_another_owner(self):
        with self.assertRaises(ValidationError):
            InventoryDetail.objects.create(
                owner=self.other_owner,
                product=self.product,
                warehouse=self.warehouse,
                subwarehouse=self.subwarehouse,
                location=self.location,
                onhand_qty=Decimal("1"),
            )

    def test_inventory_transaction_rejects_product_from_another_owner(self):
        with self.assertRaises(ValidationError):
            InventoryTransaction.objects.create(
                tx_type=InvTxType.RECEIVE,
                owner=self.other_owner,
                product=self.product,
                warehouse=self.warehouse,
                subwarehouse=self.subwarehouse,
                location=self.location,
                qty_delta=Decimal("1"),
                src_model="InvariantTest",
                src_id=1,
            )
