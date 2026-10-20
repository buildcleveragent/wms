from decimal import Decimal

from django.test import TestCase

from allapp.locations.models import Location
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine

from .models import OutboundOrder, OutboundOrderLine
from .test_physical_review import PhysicalReviewFixture


class ReviewDetailTests(PhysicalReviewFixture, TestCase):
    def test_unpriced_order_still_has_reviewable_evidence(self):
        Product.objects.filter(pk=self.product.pk).update(price=None)
        task = self.ready_pick()
        order = OutboundOrder.objects.get(pk=task.source_pk)
        self.assertFalse(order.lines.get().is_priced)
        body = self.context(task).data
        self.assertTrue(body["task"]["can_review"])
        self.assertTrue(body["lines"][0]["picking_details"])
        self.assertEqual(self.post_review(task).status_code, 200)

    def test_order_product_and_pick_evidence_survive_posting(self):
        task = self.ready_pick()
        order = OutboundOrder.objects.get(pk=task.source_pk)
        order.memo = "轻放"
        order.ship_to = "客户配送地址"
        order.delivery_method = "OWN_TRUCK"
        order.save(update_fields=["memo", "ship_to", "delivery_method"])
        self.product.spec = "每件十二个"
        self.product.save(update_fields=["spec"])
        body = self.context(task).data
        self.assertEqual(body["order"]["customer_name"], self.customer.name)
        self.assertEqual(body["order"]["order_no"], order.order_no)
        self.assertEqual(body["order"]["ship_to"], "客户配送地址")
        self.assertEqual(body["order"]["memo"], "轻放")
        row = body["lines"][0]
        self.assertEqual(row["product_code"], self.product.code)
        self.assertEqual(row["product_spec"], "每件十二个")
        self.assertEqual(row["base_uom_name"], self.uom.name)
        self.assertEqual(Decimal(row["order_qty"]), Decimal("2.125"))
        self.assertEqual(row["qty_difference"], "0.000")
        record = row["picking_details"][0]
        self.assertEqual(Decimal(record["qty"]), Decimal("2.125"))
        self.assertEqual(record["operators"], [self.picker.username])
        self.assertIsNotNone(record["last_at"])
        self.assertIsNotNone(body["task"]["finished_at"])
        self.assertEqual(self.post_review(task).status_code, 200)
        self.assertEqual(
            self.context(task).data["lines"][0]["picking_details"], row["picking_details"]
        )

    def test_split_locations_batches_and_units_do_not_duplicate_order_totals(self):
        task = self.ready_pick()
        line = task.lines.get()
        line.qty_plan = line.qty_done = Decimal("1.125")
        line.save(update_fields=["qty_plan", "qty_done"])
        TaskScanLog.objects.filter(task=task).update(
            qty_base_delta=Decimal("1.125"), lot_no="BATCH-A"
        )
        location = Location.objects.create(
            warehouse=self.wh, subwarehouse=self.sub, code="REORDERS-01-01-02", name="库位二"
        )
        split = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=location,
            qty_plan=1,
            qty_done=1,
            status="COMPLETED",
            src_model="OutboundOrderLine",
            src_id=line.src_id,
        )
        TaskScanLog.objects.create(
            task=task,
            task_line=split,
            product=self.product,
            owner=self.owner,
            warehouse=self.wh,
            location=location,
            qty_base_delta=1,
            by_user=self.picker,
            lot_no="BATCH-B",
            container_no="BOX-1",
            scan_snapshot_rev=0,
            fp="split-test",
        )
        kg = ProductUom.objects.create(code="REVIEW-KG", name="千克")
        product = Product.objects.create(
            owner=self.owner,
            code="REVIEW-P2",
            sku="REVIEW-SKU2",
            name="散装商品",
            base_uom=kg,
            batch_control=False,
            expiry_control=False,
        )
        origin = OutboundOrderLine.objects.create(
            order_id=task.source_pk,
            product=product,
            base_qty=Decimal("3.5"),
            base_uom=kg,
            is_priced=False,
            line_no=2,
        )
        WmsTaskLine.objects.create(
            task=task,
            product=product,
            from_location=self.loc,
            qty_plan=Decimal("3.5"),
            qty_done=Decimal("3.5"),
            status="COMPLETED",
            src_model="OutboundOrderLine",
            src_id=origin.pk,
        )
        body = self.context(task).data
        self.assertTrue(body["task"]["can_review"])
        totals = {row["base_uom_id"]: row for row in body["quantity_totals"]}
        self.assertEqual(Decimal(totals[self.uom.pk]["order_qty"]), Decimal("2.125"))
        self.assertEqual(Decimal(totals[self.uom.pk]["qty_done"]), Decimal("2.125"))
        self.assertEqual(Decimal(totals[kg.pk]["qty_done"]), Decimal("3.5"))
        self.assertEqual(body["lines"][0]["picking_details"][0]["lot_no"], "BATCH-A")
        self.assertEqual(body["lines"][1]["picking_details"][0]["lot_no"], "BATCH-B")
        self.assertEqual(
            body["lines"][1]["picking_details"][0]["location_code"], "REORDERS-01-01-02"
        )
        self.assertEqual(body["lines"][2]["picking_details"], [])

    def test_effective_scan_history_preserves_signed_manual_corrections(self):
        task = self.ready_pick()
        line = task.lines.get()
        base = dict(
            task=task,
            task_line=line,
            product=self.product,
            owner=self.owner,
            warehouse=self.wh,
            location=self.loc,
            by_user=self.picker,
            scan_snapshot_rev=0,
        )
        # Earlier successful scan revisions remain part of PICK's signed history.
        TaskScanLog.objects.filter(task=task).update(scan_snapshot_rev=0)
        line.scan_snapshot_rev = 3
        line.save(update_fields=["scan_snapshot_rev"])
        for index, (method, qty, status, review) in enumerate(
            [
                ("MANUAL", "-1", "OK", "NONE"),
                ("MANUAL", "1", "OK", "NONE"),
                ("SCAN", "9", "FAIL", "NONE"),
                ("SCAN", "9", "DUP", "NONE"),
                ("SCAN", "9", "OK", "REJECTED"),
            ]
        ):
            TaskScanLog.objects.create(
                **base,
                method=method,
                qty_base_delta=qty,
                status=status,
                review_status=review,
                fp=f"correction-{index}",
            )
        record = self.context(task).data["lines"][0]["picking_details"][0]
        self.assertEqual(Decimal(record["qty"]), Decimal("2.125"))
        self.assertEqual(record["record_count"], 3)

    def test_bad_source_order_and_line_links_fail_closed_without_repair(self):
        task = self.ready_pick()
        line = task.lines.get()
        original_id = line.src_id
        for source_id in (None, self.line.pk):
            with self.subTest(source_id=source_id):
                WmsTaskLine.objects.filter(pk=line.pk).update(src_id=source_id)
                body = self.context(task).data
                self.assertFalse(body["task"]["can_review"])
                self.assertEqual(body["lines"], [])
                self.assertTrue(body["content_error"])
                self.assertEqual(self.post_review(task).status_code, 400)
        WmsTaskLine.objects.filter(pk=line.pk).update(src_id=original_id)
        WmsTask.objects.filter(pk=task.pk).update(source_pk="99999999")
        count = WmsTask.objects.count()
        body = self.context(task).data
        self.assertIsNone(body["order"])
        self.assertFalse(body["task"]["can_review"])
        self.assertEqual(self.post_review(task).status_code, 400)
        self.assertEqual(WmsTask.objects.count(), count)

    def test_no_effective_lines_or_product_blocks_read_and_post(self):
        task = self.ready_pick()
        line = task.lines.get()
        for changes in (
            {"product_id": None},
            {"product_id": self.product.pk, "status": "CANCELLED"},
        ):
            with self.subTest(changes=changes):
                WmsTaskLine.objects.filter(pk=line.pk).update(**changes)
                body = self.context(task).data
                self.assertFalse(body["task"]["can_review"])
                self.assertEqual(body["lines"], [])
                self.assertEqual(body["quantity_totals"], [])
                self.assertEqual(self.post_review(task).status_code, 400)
