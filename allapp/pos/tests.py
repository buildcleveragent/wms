from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from allapp.baseinfo.models import Customer, Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product, ProductPackage, ProductUom


class PosApiTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="POS Owner", code="POSOWN")
        self.warehouse = Warehouse.objects.create(code="WHPOS", name="POS Warehouse")
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWPOS",
            name="POS Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWPOS-01-01-01",
            name="POS Pick Location",
        )
        self.user = get_user_model().objects.create_user(
            username="pos-admin",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="POS-CUS",
            name="POS Customer",
        )
        self.uom = ProductUom.objects.create(code="PCS-POS", name="件", is_active=True)
        self.carton_uom = ProductUom.objects.create(code="CTN-POS", name="箱", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="POS-SKU",
            name="POS Product",
            sku="POS-SKU",
            gtin="6901234567892",
            unit_barcode="POS-UNIT-BAR",
            base_uom=self.uom,
            price=Decimal("10.00"),
            min_price=Decimal("8.00"),
            max_discount=Decimal("20.00"),
            batch_control=False,
            expiry_control=False,
        )
        ProductPackage.objects.create(
            product=self.product,
            uom=self.carton_uom,
            qty_in_base=12,
            barcode="POS-CTN-BAR",
            is_sales_default=True,
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_product_lookup_by_package_barcode_returns_available_qty(self):
        response = self.client.get("/api/pos/products/", {"barcode": "POS-CTN-BAR"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], self.product.id)
        self.assertEqual(row["code"], self.product.code)
        self.assertEqual(Decimal(str(row["available_qty"])), Decimal("10.0000"))
        self.assertEqual(row["unit_options"][0]["kind"], "base")
        self.assertEqual(row["unit_options"][1]["kind"], "package")

    def test_product_lookup_does_not_require_user_owner(self):
        no_owner_user = get_user_model().objects.create_user(
            username="pos-no-owner",
            password="x",
            warehouse=self.warehouse,
        )
        self.client.force_authenticate(no_owner_user)

        response = self.client.get("/api/pos/products/", {"barcode": "POS-CTN-BAR"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], self.product.id)
        self.assertEqual(Decimal(str(row["available_qty"])), Decimal("10.0000"))

    def test_checkout_creates_submitted_sales_outbound_order(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.customer.id,
                "src_bill_no": "POS-RECEIPT-001",
                "remark": "cashier sale",
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "2.000",
                        "price": "9.0000",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        order = OutboundOrder.objects.get(src_bill_no="POS-RECEIPT-001")
        self.assertEqual(order.owner_id, self.owner.id)
        self.assertEqual(order.warehouse_id, self.warehouse.id)
        self.assertEqual(order.customer_id, self.customer.id)
        self.assertEqual(order.outbound_type, "SALES")
        self.assertEqual(order.delivery_method, "PICKUP")
        self.assertEqual(order.submit_status, "SUBMITTED")
        self.assertEqual(order.approval_status, "OWNER_PENDING")
        line = OutboundOrderLine.objects.get(order=order)
        self.assertEqual(line.product_id, self.product.id)
        self.assertEqual(line.base_qty, Decimal("2.000"))
        self.assertEqual(line.base_price, Decimal("9.0000"))

    def test_checkout_rejects_price_below_min_price(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.customer.id,
                "src_bill_no": "POS-RECEIPT-LOW",
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "price": "7.9900",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            OutboundOrder.objects.filter(src_bill_no="POS-RECEIPT-LOW").count(),
            0,
        )

    def test_checkout_rejects_qty_above_available_stock(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.customer.id,
                "src_bill_no": "POS-RECEIPT-STOCK",
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "11.000",
                        "price": "9.0000",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            OutboundOrder.objects.filter(src_bill_no="POS-RECEIPT-STOCK").count(),
            0,
        )
