from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inventory.models import InventoryDetail, InventoryTransaction, ReviewDifference
from allapp.inventory.services_quick_adjust import QuickAdjustInput, quick_adjust_via_post_task
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom


class InventoryWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Inventory", code="OWN-INV")
        self.warehouse = Warehouse.objects.create(code="WH-INV-1", name="Warehouse Inventory 1")
        self.other_warehouse = Warehouse.objects.create(code="WH-INV-2", name="Warehouse Inventory 2")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWINV1",
            name="Subwarehouse Inventory 1",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="SWINV2",
            name="Subwarehouse Inventory 2",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWINV1-01-01-01",
            name="Inventory Location 1",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWINV2-01-01-01",
            name="Inventory Location 2",
        )
        self.uom = ProductUom.objects.create(code="PCS-INV", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-INV",
            name="Inventory Product",
            sku="SKU-INV",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="inventory-user",
            password="x",
            warehouse=self.warehouse,
        )

    def test_inventory_detail_derives_warehouse_from_location(self):
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )

        self.assertEqual(detail.warehouse_id, self.warehouse.id)

    def test_inventory_transaction_derives_warehouse_from_location(self):
        tx = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product,
            location=self.location,
            qty_delta=Decimal("2.0000"),
            src_model="inventory.tests",
            src_id=1,
            src_line_id=1,
            src_no="INV-TX-1",
        )

        self.assertEqual(tx.warehouse_id, self.warehouse.id)

    def test_review_difference_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            ReviewDifference.objects.create(order_no="RD-INV-1")

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_quick_adjust_rejects_mismatched_location_and_warehouse(self):
        with self.assertRaisesMessage(ValueError, "warehouse 必须与 location.warehouse 一致"):
            quick_adjust_via_post_task(
                QuickAdjustInput(
                    user=self.user,
                    owner=self.owner,
                    product=self.product,
                    qty_base_delta=Decimal("1.0000"),
                    warehouse=self.other_warehouse,
                    location=self.location,
                )
            )
