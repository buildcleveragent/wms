from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from allapp.accounts.audit import record_audit_event
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.reports import test_boss_activity as fixtures
from allapp.reports.boss_activity import local_today
from allapp.tasking.models import WmsTask, WmsTaskLine


class BossTodayTests(TestCase):
    setUp = fixtures.BossActivityTests.setUp
    open_shift = fixtures.BossActivityTests.open_shift
    payment = fixtures.BossActivityTests.payment
    checkout = fixtures.BossActivityTests.checkout
    get_report = fixtures.BossActivityTests.get_report

    def order(self, direction="outbound", suffix="1"):
        inbound = direction == "inbound"
        if inbound:
            from allapp.baseinfo.models import Supplier

            supplier = Supplier.objects.create(
                owner=self.owner, code=f"TODAY-S-{suffix}", name="supplier"
            )
            order = InboundOrder.objects.create(
                order_no=f"TODAY-IN-{suffix}",
                owner=self.owner,
                warehouse=self.warehouse,
                supplier=supplier,
            )
            line = InboundOrderLine.objects.create(
                order=order,
                line_no=1,
                product=self.product,
                base_qty=10,
                base_price=Decimal("12.34"),
                base_uom=self.uom.code,
            )
        else:
            order = OutboundOrder.objects.create(
                order_no=f"TODAY-OUT-{suffix}",
                owner=self.owner,
                warehouse=self.warehouse,
                customer=self.customer,
                submit_status="SUBMITTED",
                approval_status="WHS_APPROVED",
            )
            line = OutboundOrderLine.objects.create(
                order=order,
                line_no=1,
                product=self.product,
                base_qty=10,
                base_price=Decimal("12.34"),
                base_uom=self.uom,
            )
        return order, line

    def execute(self, order, line, suffix="1", qty=2, at=None):
        inbound = isinstance(order, InboundOrder)
        task = WmsTask.objects.create(
            task_no=f"TODAY-{order.pk}-{suffix}",
            owner=self.owner,
            warehouse=self.warehouse,
            source_model=order.__class__.__name__,
            source_pk=str(order.pk),
            task_type="RECEIVE" if inbound else "DISPATCH",
            status="COMPLETED",
            review_status="APPROVED" if inbound else "PENDING",
            posting_status="POSTED" if inbound else "NONE",
            finished_at=at or timezone.now(),
        )
        work = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            qty_plan=10,
            qty_done=qty,
            src_model=f"{line._meta.app_label}.{line.__class__.__name__}",
            src_id=line.pk,
        )
        if inbound:
            InventoryTransaction.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                product=self.product,
                location=self.location,
                tx_type="RECEIVE",
                qty_delta=qty,
                src_model="WmsTask",
                src_id=task.pk,
                src_line_id=work.pk,
                posted_at=at or timezone.now(),
            )
        return task, work

    def test_partial_dispatch_distinct_orders_and_exact_detail_value(self):
        order, line = self.order()
        self.execute(order, line)
        self.execute(order, line, "2", qty=3)
        summary = self.get_report("today")
        self.assertEqual(summary["orders"]["outbound"]["count"], 1)
        self.assertEqual(summary["orders"]["outbound"]["execution_records"], 2)
        self.assertEqual(summary["orders"]["outbound"]["money"], [])
        self.assertEqual(summary["backlog"]["outbound"], 1)
        rows = self.get_report("today/details", metric="outbound")
        self.assertEqual(rows["count"], 1)
        self.assertEqual(rows["results"][0]["drill"]["document_id"], order.pk)
        details = self.get_report("today/details", metric="outbound", document_id=order.pk)
        self.assertEqual(details["count"], 2)
        values = [{f["label"]: f["value"] for f in r["fields"]} for r in details["results"]]
        self.assertEqual(sum(v["参考金额"] for v in values), Decimal("61.70"))
        self.assertNotIn("123.40", str(details))

    def test_inbound_posted_only_and_submission_rollback(self):
        order, line = self.order("inbound")
        self.execute(order, line)
        self.execute(order, line, "2", at=timezone.now() - timedelta(days=1))
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                order.submit_by_owner_buyers(self.cashier)
                raise RuntimeError("rollback")
        self.assertEqual(self.get_report("today")["orders"]["inbound"]["submitted"], 0)
        order.refresh_from_db()
        order.submit_by_owner_buyers(self.cashier)
        record_audit_event(
            action="inbound.order.submit", module="inbound", obj=order, user=self.cashier
        )
        data = self.get_report("today")
        self.assertEqual(data["orders"]["inbound"]["count"], 1)
        self.assertEqual(data["orders"]["inbound"]["execution_records"], 1)
        self.assertEqual(data["orders"]["inbound"]["submitted"], 1)
        rows = self.get_report("today/details", metric="inbound_submitted")
        self.assertEqual(rows["count"], 1)
        InboundOrder.objects.filter(pk=order.pk).update(receiving_closed_at=timezone.now())
        self.assertEqual(self.get_report("today")["backlog"]["inbound"], 0)
        InboundOrderLine.objects.filter(pk=line.pk).update(base_uom="DIFFERENT")
        detail = self.get_report("today/details", metric="inbound", document_id=order.pk)
        self.assertIn("缺少可靠价格", str(detail))

    def test_missing_identity_never_counts_task_as_order(self):
        order, line = self.order()
        task, work = self.execute(order, line)
        WmsTask.objects.filter(pk=task.pk).update(source_model="Unrelated", source_pk=str(order.pk))
        data = self.get_report("today")["orders"]["outbound"]
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["unlinked_records"], 1)
        details = self.get_report("today/details", metric="outbound_unlinked")
        self.assertEqual(details["count"], 1)
        self.assertIn("缺少可靠价格", str(details))

    def test_zero_and_unpriced_values_are_not_synthetic_money(self):
        order, line = self.order()
        self.execute(order, line)
        OutboundOrderLine.objects.filter(pk=line.pk).update(is_priced=False)
        details = self.get_report("today/details", metric="outbound", document_id=order.pk)
        self.assertIn("缺少可靠价格", str(details))
        self.assertEqual(
            self.get_report("today")["orders"]["outbound"]["value_status"], "UNAVAILABLE"
        )
        OutboundOrderLine.objects.filter(pk=line.pk).update(is_priced=True, base_price=0)
        zero = self.get_report("today/details", metric="outbound", document_id=order.pk)
        self.assertIn("缺少可靠价格", str(zero))

    def test_pos_scoped_amount_and_no_duplicate_outbound(self):
        sale = self.checkout(multi=True)
        pos_line = sale.lines.get(owner=self.owner).outbound_order_line
        self.execute(pos_line.order, pos_line)
        data = self.get_report("today", owner=self.owner.pk)
        row = next(r for r in data["financial"] if r["key"] == "pos_sales")
        self.assertEqual(row["count"], 1)
        self.assertTrue(row["unknown_currency"])
        self.assertEqual(row["money"], [])
        self.assertEqual(data["orders"]["outbound"]["count"], 0)
        self.assertEqual(data["orders"]["outbound"]["unlinked_records"], 0)
        details = self.get_report("today/details", metric="pos_sales", owner=self.owner.pk)
        self.assertEqual(details["count"], 1)
        self.assertNotIn(self.other_product.name, str(details))
        self.assertEqual(sale.lines.count(), 2)

    def test_scope_readonly_validation_and_pagination(self):
        for i in range(2):
            order, line = self.order(suffix=str(i))
            self.execute(order, line)
        first = self.get_report("today/details", metric="outbound", page_size=1)
        second = self.get_report("today/details", metric="outbound", page_size=1, page=2)
        self.assertEqual(first["count"], 2)
        self.assertNotEqual(first["results"][0]["id"], second["results"][0]["id"])
        for path in ["today", "today/details"]:
            self.assertEqual(
                self.client.get(
                    f"/api/reports/boss/{path}/", {"warehouse": self.other_warehouse.pk}
                ).status_code,
                403,
            )
            self.assertEqual(self.client.post(f"/api/reports/boss/{path}/", {}).status_code, 405)
        self.assertEqual(
            self.client.get("/api/reports/boss/today/details/", {"metric": "bad"}).status_code, 400
        )

    def test_home_date_is_server_today_not_saved_historical_range(self):
        data = self.get_report("today", date_from="2020-01-01", date_to="2020-12-31")
        self.assertEqual(data["business_today"], local_today())
        self.assertEqual(data["scope"]["date_from"], local_today())
