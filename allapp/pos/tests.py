from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from allapp.baseinfo.models import Customer, Owner
from allapp.core.choices import InvTxType
from allapp.inventory.models import InventoryDetail, InventorySummary, InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.pos.models import PosPayment, PosSale, PosSaleLine, PosSaleOrder
from allapp.products.models import Product, ProductPackage, ProductUom


class PosApiTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="POS Owner", code="POSOWN")
        self.other_owner = Owner.objects.create(name="POS Other Owner", code="POSOTH")
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
        self.other_customer = Customer.objects.create(
            owner=self.other_owner,
            salesperson=self.user,
            code="POS-OTH-CUS",
            name="POS Other Customer",
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
        self.other_product = Product.objects.create(
            owner=self.other_owner,
            code="POS-OTH-SKU",
            name="POS Other Product",
            sku="POS-OTH-SKU",
            gtin="6901234567893",
            unit_barcode="POS-OTH-UNIT-BAR",
            base_uom=self.uom,
            price=Decimal("20.00"),
            min_price=Decimal("15.00"),
            max_discount=Decimal("10.00"),
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
        InventoryDetail.objects.create(
            owner=self.other_owner,
            product=self.other_product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("8.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit=self.uom.code,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def payment(self, amount):
        return {"method": "CASH", "amount_received": str(amount)}

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

    def test_product_lookup_returns_other_owner_available_qty(self):
        response = self.client.get("/api/pos/products/", {"barcode": "POS-OTH-UNIT-BAR"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], self.other_product.id)
        self.assertEqual(Decimal(str(row["available_qty"])), Decimal("8.0000"))

    def test_checkout_creates_submitted_sales_outbound_order(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.customer.id,
                "src_bill_no": "POS-RECEIPT-001",
                "remark": "cashier sale",
                "idempotency_key": "idem-pos-001",
                "payment": self.payment("20.00"),
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

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["sale"]["src_bill_no"], "POS-RECEIPT-001")
        self.assertEqual(response.data["payment"]["method"], "CASH")
        self.assertEqual(Decimal(str(response.data["payment"]["amount_due"])), Decimal("18.00"))
        self.assertEqual(Decimal(str(response.data["payment"]["change_amount"])), Decimal("2.00"))
        order = OutboundOrder.objects.get(src_bill_no="POS-RECEIPT-001")
        self.assertEqual(order.owner_id, self.owner.id)
        self.assertEqual(order.warehouse_id, self.warehouse.id)
        self.assertEqual(order.customer_id, self.customer.id)
        self.assertEqual(order.outbound_type, "SALES")
        self.assertEqual(order.delivery_method, "PICKUP")
        self.assertEqual(order.submit_status, "SUBMITTED")
        self.assertEqual(order.approval_status, "WHS_APPROVED")
        self.assertTrue(order.is_closed)
        self.assertEqual(order.final_order_amount, Decimal("18.00"))
        line = OutboundOrderLine.objects.get(order=order)
        self.assertEqual(line.product_id, self.product.id)
        self.assertEqual(line.base_qty, Decimal("2.000"))
        self.assertEqual(line.base_price, Decimal("9.0000"))
        sale = PosSale.objects.get(src_bill_no="POS-RECEIPT-001")
        self.assertEqual(sale.total_amount, Decimal("18.00"))
        self.assertEqual(sale.payment.amount_received, Decimal("20.00"))
        self.assertEqual(PosSaleOrder.objects.get(sale=sale).outbound_order_id, order.id)
        self.assertEqual(PosSaleLine.objects.get(sale=sale).outbound_order_line_id, line.id)
        self.assertEqual(
            InventoryTransaction.objects.get(src_model="PosSaleLine").tx_type,
            InvTxType.ISSUE,
        )
        self.assertEqual(
            InventoryDetail.objects.get(owner=self.owner, product=self.product).available_qty,
            Decimal("8.0000"),
        )
        self.assertEqual(
            InventorySummary.objects.get(owner=self.owner, product=self.product).available_qty,
            Decimal("8.0000"),
        )

    def test_checkout_without_customer_uses_cash_customer(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "src_bill_no": "POS-RECEIPT-CASH",
                "payment": self.payment("9.00"),
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "price": "9.0000",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        cash_customer = Customer.objects.get(owner=self.owner, code="CASH")
        self.assertEqual(cash_customer.name, "散客")
        order = OutboundOrder.objects.get(src_bill_no="POS-RECEIPT-CASH")
        self.assertEqual(order.customer_id, cash_customer.id)

    def test_checkout_does_not_require_user_owner(self):
        no_owner_user = get_user_model().objects.create_user(
            username="pos-checkout-no-owner",
            password="x",
            warehouse=self.warehouse,
        )
        self.client.force_authenticate(no_owner_user)

        response = self.client.post(
            "/api/pos/checkout/",
            {
                "src_bill_no": "POS-RECEIPT-NO-OWNER",
                "payment": self.payment("9.00"),
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "price": "9.0000",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["order_count"], 1)
        order = OutboundOrder.objects.get(src_bill_no="POS-RECEIPT-NO-OWNER")
        self.assertEqual(order.owner_id, self.owner.id)

    def test_checkout_splits_products_by_owner(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.customer.id,
                "src_bill_no": "POS-RECEIPT-MULTI",
                "payment": self.payment("50.00"),
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "price": "9.0000",
                    },
                    {
                        "product_id": self.other_product.id,
                        "qty": "2.000",
                        "price": "18.0000",
                    },
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["order_count"], 2)
        self.assertEqual(Decimal(str(response.data["payment"]["amount_due"])), Decimal("45.00"))
        self.assertEqual(Decimal(str(response.data["payment"]["change_amount"])), Decimal("5.00"))
        sale = PosSale.objects.get(src_bill_no="POS-RECEIPT-MULTI")
        self.assertEqual(sale.total_amount, Decimal("45.00"))
        self.assertEqual(PosPayment.objects.get(sale=sale).amount_received, Decimal("50.00"))
        orders = OutboundOrder.objects.filter(src_bill_no="POS-RECEIPT-MULTI").order_by(
            "owner_id"
        )
        self.assertEqual(orders.count(), 2)
        order_by_owner = {order.owner_id: order for order in orders}
        self.assertEqual(order_by_owner[self.owner.id].customer_id, self.customer.id)
        cash_customer = Customer.objects.get(owner=self.other_owner, code="CASH")
        self.assertEqual(order_by_owner[self.other_owner.id].customer_id, cash_customer.id)
        self.assertEqual(
            OutboundOrderLine.objects.get(order=order_by_owner[self.owner.id]).product_id,
            self.product.id,
        )
        self.assertEqual(
            OutboundOrderLine.objects.get(order=order_by_owner[self.other_owner.id]).product_id,
            self.other_product.id,
        )
        self.assertEqual(PosSaleOrder.objects.filter(sale=sale).count(), 2)
        self.assertEqual(PosSaleLine.objects.filter(sale=sale).count(), 2)
        self.assertEqual(
            InventoryDetail.objects.get(owner=self.owner, product=self.product).available_qty,
            Decimal("9.0000"),
        )
        self.assertEqual(
            InventoryDetail.objects.get(owner=self.other_owner, product=self.other_product).available_qty,
            Decimal("6.0000"),
        )
        self.assertEqual(
            InventoryTransaction.objects.filter(src_model="PosSaleLine", tx_type=InvTxType.ISSUE).count(),
            2,
        )

    def test_checkout_idempotency_key_does_not_double_post_stock(self):
        payload = {
            "src_bill_no": "POS-RECEIPT-IDEM",
            "idempotency_key": "idem-repeat-sale",
            "payment": self.payment("9.00"),
            "items": [
                {
                    "product_id": self.product.id,
                    "qty": "1.000",
                    "price": "9.0000",
                }
            ],
        }

        first = self.client.post("/api/pos/checkout/", payload, format="json")
        second = self.client.post("/api/pos/checkout/", payload, format="json")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["sale"]["id"], second.data["sale"]["id"])
        self.assertEqual(PosSale.objects.filter(src_bill_no="POS-RECEIPT-IDEM").count(), 1)
        self.assertEqual(
            InventoryTransaction.objects.filter(src_model="PosSaleLine", tx_type=InvTxType.ISSUE).count(),
            1,
        )
        self.assertEqual(
            InventoryDetail.objects.get(owner=self.owner, product=self.product).available_qty,
            Decimal("9.0000"),
        )

    def test_void_sale_restores_stock_and_cancels_outbound_orders(self):
        checkout = self.client.post(
            "/api/pos/checkout/",
            {
                "src_bill_no": "POS-RECEIPT-VOID",
                "payment": self.payment("9.00"),
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "price": "9.0000",
                    }
                ],
            },
            format="json",
        )
        sale_id = checkout.data["sale"]["id"]

        response = self.client.post(
            f"/api/pos/sales/{sale_id}/void/",
            {"reason": "cashier mistake"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        sale = PosSale.objects.get(pk=sale_id)
        self.assertEqual(sale.status, PosSale.Status.VOIDED)
        self.assertEqual(sale.payment.status, PosPayment.Status.VOIDED)
        order = OutboundOrder.objects.get(src_bill_no="POS-RECEIPT-VOID")
        self.assertEqual(order.approval_status, "CANCELLED")
        self.assertEqual(
            InventoryDetail.objects.get(owner=self.owner, product=self.product).available_qty,
            Decimal("10.0000"),
        )
        self.assertEqual(
            InventoryTransaction.objects.filter(src_model="PosSaleLine", tx_type=InvTxType.ISSUE).count(),
            1,
        )
        self.assertEqual(
            InventoryTransaction.objects.filter(src_model="PosSaleLine", tx_type=InvTxType.RECEIVE).count(),
            1,
        )

    def test_checkout_rejects_price_below_min_price(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.customer.id,
                "src_bill_no": "POS-RECEIPT-LOW",
                "payment": self.payment("8.00"),
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
                "payment": self.payment("99.00"),
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
