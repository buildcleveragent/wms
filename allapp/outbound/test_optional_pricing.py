from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import TestCase
from openpyxl import load_workbook

from allapp.inventory.models import InventoryDetail
from allapp.outbound.models import OutboundOrder
from allapp.outbound.test_reorder import ReorderFixture
from allapp.products.models import Product


class OptionalPricingTests(ReorderFixture, TestCase):
    def unprice(self, value=None):
        Product.objects.filter(pk=self.product.pk).update(price=value)

    def approve(self, order_id):
        self.manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound", codename="approve_outbound_as_owner_manager"
            )
        )
        self.client.force_authenticate(self.manager)
        return self.client.post(f"/api/outbound/orders/{order_id}/owner-approve/")

    def test_unpriced_create_approve_and_replay_preserve_snapshot(self):
        self.unprice()
        payload = self.payload()
        payload["items"][0]["price"] = None
        result = self.create(payload)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data["amount_status"], "UNPRICED")
        self.assertFalse(result.data["lines"][0]["is_priced"])
        self.assertEqual(result.data["lines"][0]["amount"], Decimal("0.00"))
        self.assertEqual(result.data["unpriced_line_count"], 1)
        self.unprice(20)
        replay = self.create(payload)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["id"], result.data["id"])
        self.assertEqual(replay.data["amount_status"], "UNPRICED")
        approved = self.approve(result.data["id"])
        self.assertEqual(approved.status_code, 200, approved.data)
        order = OutboundOrder.objects.get(pk=result.data["id"])
        self.assertEqual(order.approval_status, "OWNER_APPROVED")
        self.assertEqual(order.final_order_amount, Decimal("0.00"))
        self.assertFalse(order.lines.get().is_priced)
        self.stock.refresh_from_db()
        self.assertGreater(self.stock.allocated_qty, Decimal("1"))

    def test_zero_reference_ignores_price_and_guards_but_not_stock(self):
        self.unprice(0)
        Product.objects.filter(pk=self.product.pk).update(max_discount=101)
        preview = self.preview()
        self.assertTrue(preview.data["can_submit"], preview.data)
        self.assertFalse(preview.data["items"][0]["is_priced"])
        payload = self.payload()
        payload["items"][0]["price"] = "99"
        result = self.create(payload)
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(Decimal(result.data["lines"][0]["base_price"]), 0)
        shortage = self.create(self.payload(qty="99"), key="unpriced-stock")
        self.assertEqual(shortage.status_code, 409, shortage.data)

    def test_forged_unpriced_flag_cannot_skip_minimum(self):
        payload = self.payload()
        payload["items"][0].update(is_priced=False, price=None)
        self.assertEqual(self.create(payload).status_code, 400)
        payload["items"][0]["price"] = "1"
        self.assertEqual(self.create(payload).status_code, 400)

    def test_unpriced_products_do_not_bypass_customer_or_submit_permissions(self):
        self.unprice()
        before = OutboundOrder.objects.count()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.create().status_code, 400)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(OutboundOrder.objects.count(), before)

    def test_valid_saved_price_survives_reference_removal_and_draft_save(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.data)
        order = OutboundOrder.objects.get(pk=created.data["id"])
        line = order.lines.get()
        order.submit_status = "DRAFT"
        order.save(update_fields=["submit_status", "updated_at"])
        self.unprice()
        payload = self.payload()
        payload["items"][0]["line_key"] = f"edit-{line.pk}"
        payload["expected_updated_at"] = order.updated_at.isoformat()
        saved = self.client.put(f"/api/outbound/orders/{order.pk}/", payload, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertTrue(saved.data["lines"][0]["is_priced"])
        self.assertEqual(Decimal(saved.data["lines"][0]["base_price"]), Decimal("12.5000"))
        submitted = self.client.post(f"/api/outbound/orders/{order.pk}/submit/")
        self.assertEqual(submitted.status_code, 200, submitted.data)
        approved = self.approve(order.pk)
        self.assertEqual(approved.status_code, 200, approved.data)
        order.refresh_from_db()
        self.assertEqual(order.final_order_amount, Decimal("26.56"))

    def test_mixed_create_and_audit_failure_are_atomic(self):
        self.unprice()
        priced = Product.objects.create(
            owner=self.owner,
            code="PRICED",
            sku="PRICED",
            name="有价商品",
            base_uom=self.uom,
            price=10,
            min_price=8,
            batch_control=False,
            expiry_control=False,
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.wh,
            location=self.loc,
            product=priced,
            onhand_qty=10,
        )
        payload = self.payload(qty="1")
        payload["items"][0]["price"] = None
        payload["items"].append(
            {
                "product_id": priced.pk,
                "qty": "1.25",
                "price": "10",
                "base_uom_id": self.uom.pk,
                "line_key": "second",
            }
        )
        before = OutboundOrder.objects.count()
        with patch("allapp.outbound.views.record_audit_event", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError):
                self.create(payload)
        self.assertEqual(OutboundOrder.objects.count(), before)
        response = self.create(payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["amount_status"], "PARTIAL")
        self.assertEqual(response.data["priced_amount"], Decimal("12.50"))
        approved = self.approve(response.data["id"])
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(
            OutboundOrder.objects.get(pk=response.data["id"]).final_order_amount, Decimal("12.50")
        )

    def test_mixed_summary_and_export_leave_unpriced_cells_empty(self):
        priced = self.create(key="priced-before")
        self.assertEqual(priced.status_code, 201, priced.data)
        order = OutboundOrder.objects.get(pk=priced.data["id"])
        # Server-owned saved unpriced line alongside a normally priced line.
        order.lines.create(product=self.product, base_qty=1, base_price=0, is_priced=False)
        result = self.client.get(f"/api/outbound/orders/{order.pk}/")
        self.assertEqual(result.data["amount_status"], "PARTIAL")
        self.assertEqual(result.data["priced_amount"], Decimal("26.56"))
        self.assertEqual(result.data["unpriced_line_count"], 1)
        response = self.client.get("/api/outbound/orders/owner-export/", {"search": order.order_no})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(b"".join(response.streaming_content)))
        rows = list(book["订单商品明细"].values)
        unpriced = [row for row in rows if "未计价" in row]
        self.assertEqual(len(unpriced), 1)
        self.assertIsNone(unpriced[0][15])
        self.assertIsNone(unpriced[0][16])

    def test_legacy_zero_draft_becomes_unpriced_and_edited_snapshot_survives(self):
        self.unprice()
        order = self.make_order(submit_status="DRAFT", approval_status="OWNER_PENDING")
        line = order.lines.create(product=self.product, base_qty=1, base_price=0)
        submitted = self.client.post(f"/api/outbound/orders/{order.pk}/submit/")
        self.assertEqual(submitted.status_code, 200, submitted.data)
        line.refresh_from_db()
        self.assertFalse(line.is_priced)
        # Simulate an editable returned order, then configure a reference price.
        order.submit_status = "DRAFT"
        order.save(update_fields=["submit_status", "updated_at"])
        self.unprice(20)
        payload = self.payload(qty="1")
        payload.pop("reorder_source_id")
        payload["items"][0].update(line_key=f"edit-{line.pk}", price=None)
        payload["editing_order_id"] = order.pk
        checked = self.client.post("/api/outbound/orders/validate/", payload, format="json")
        self.assertTrue(checked.data["can_submit"], checked.data)
        payload["expected_updated_at"] = order.updated_at.isoformat()
        updated = self.client.put(f"/api/outbound/orders/{order.pk}/", payload, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertFalse(updated.data["lines"][0]["is_priced"])

    def test_legacy_zero_with_new_reference_requires_confirmation(self):
        order = self.make_order(submit_status="DRAFT", approval_status="OWNER_PENDING")
        order.lines.create(product=self.product, base_qty=1, base_price=0)
        response = self.client.post(f"/api/outbound/orders/{order.pk}/submit/")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn("当前参考价", str(response.data))
        order.refresh_from_db()
        self.assertEqual(order.submit_status, "DRAFT")

    def test_reorder_uses_new_reference_and_cannot_borrow_another_orders_snapshot(self):
        self.unprice()
        result = self.create()
        self.assertEqual(result.status_code, 201, result.data)
        order = OutboundOrder.objects.get(pk=result.data["id"])
        self.unprice(20)
        preview = self.client.get(f"/api/outbound/orders/{order.pk}/reorder-context/")
        self.assertTrue(preview.data["items"][0]["is_priced"])
        payload = self.payload()
        payload["editing_order_id"] = order.pk
        payload["items"][0].update(price=None, line_key=f"edit-{order.lines.get().pk}")
        response = self.client.post("/api/outbound/orders/validate/", payload, format="json")
        self.assertEqual(response.status_code, 403)
