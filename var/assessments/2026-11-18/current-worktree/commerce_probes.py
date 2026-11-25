"""Audit only. Run named cases under the repository's validated MySQL test lease."""
from decimal import Decimal

from django.test import TestCase

from allapp.inventory.models import InventoryDetail
from allapp.outbound.models import OutboundOrder
from allapp.salesapp import tests as mall
from allapp.salesapp.models import SaleMiniOrderMapping


class BuyerOrderProbes(TestCase):
    setUp = mall.SaleMiniApiTests.setUp
    _create_other_owner_sale_binding = mall.SaleMiniApiTests._create_other_owner_sale_binding

    def payload(self, products):
        carts = []
        lines = []
        for product, qty in products:
            line = {"product_id": product.id, "qty": str(qty), "order_uom": self.uom.code}
            added = self.client.post("/api/sale-mini/cart/add/", line, format="json")
            self.assertEqual(added.status_code, 200)
            carts.append(added.data["cart_id"])
            lines.append(line)
        return dict(cart_ids=carts, lines=lines, contact="Fixture", contact_phone="13800000000",
                    ship_to="Synthetic warehouse", delivery_method="OWN_TRUCK", payment_method="OFFLINE")

    def test_same_cart_after_lost_receipt(self):
        payload = self.payload([(self.product, 2)])
        first = self.client.post("/api/sale-mini/orders/", payload, format="json")
        self.assertEqual(first.status_code, 201)
        new_preview = self.client.post("/api/sale-mini/orders/preview/", payload, format="json")
        self.assertEqual(new_preview.status_code, 200)
        self.assertTrue(new_preview.data["ok"])
        replay = self.client.post("/api/sale-mini/orders/", payload, format="json")
        detail = InventoryDetail.objects.get(product=self.product)
        observed = (replay.status_code, SaleMiniOrderMapping.objects.filter(buyer_user=self.buyer).count(),
                    str(detail.allocated_qty), str(detail.available_qty), first.data["payable_amount"],
                    replay.data.get("payable_amount"), first.data["id"] == replay.data.get("id"))
        self.assertEqual(observed[1:4], (1, "2.0000", "8.0000"), f"Observed replay status, mapping_count, allocated, available, first_amount, second_amount, same_order: {observed}")

    def test_package_filter_preserves_order_amount(self):
        other_product = self._create_other_owner_sale_binding()[-1]
        payload = self.payload([(self.product, 2), (other_product, 1)])
        response = self.client.post("/api/sale-mini/orders/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["order_count"], 2)
        self.assertEqual(Decimal(response.data["payable_amount"]), Decimal("24"))
        first_id = response.data["orders"][0]["id"]
        order = OutboundOrder.objects.get(pk=first_id)
        order.is_closed = True
        order.close_reason = "Synthetic completed package"
        order.save(update_fields=["is_closed", "close_reason", "updated_at"])
        groups = self.client.get("/api/sale-mini/orders/", {"status": "COMPLETED"})
        self.assertEqual(groups.status_code, 200)
        rows = groups.data["results"]
        group = self.client.get(f"/api/sale-mini/orders/{first_id}/")
        self.assertEqual(group.status_code, 200)
        matched = [(row["status"], row["payable_amount"], row.get("order_count",1)) for row in rows]
        overview = (group.data["status"], group.data["payable_amount"], group.data.get("order_count",1))
        self.assertTrue(all(item == overview for item in matched),
                        f"Completed tab rows {matched}, detail {overview}")
