"""Business statistics acceptance: calendar totals, review facts, cents and isolation."""

from datetime import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from openpyxl import load_workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.inbound.arrival_models import ArrivalPhoto
from allapp.inbound.models import InboundOrderLine, InboundReceipt, InboundReceiptLine
from allapp.outbound import test_physical_review as physical_fixtures
from allapp.outbound.models import OutboundOrderLine
from allapp.pos.models import PosSale, PosSaleLine
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import TaskScanLog, WmsTask, WmsTaskLine

from . import test_owner_history as history_fixtures
from .models import InventoryDetail, InventoryTransaction
from .owner_statistics_views import OwnerStatisticsView


@override_settings(USE_TZ=True)
class OwnerStatisticsTests(TestCase):
    setUpTestData = classmethod(history_fixtures.OwnerHistoryTests.setUpTestData.__func__)
    tx = history_fixtures.OwnerHistoryTests.tx

    def call(self, section="summary", params=None, user=None, event_id=None):
        request = APIRequestFactory().get("/api/inventory/owner-history/statistics/", params or {})
        force_authenticate(request, user=user or self.user)
        return OwnerStatisticsView.as_view(section=section)(request, event_id=event_id)

    def data(self, section="summary", **params):
        response = self.call(section, params)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def receipt(self, qty, day="2026-11-02T10:00:00+08:00", price="2.50"):
        line = InboundOrderLine.objects.create(
            order=self.inbound,
            product=self.product,
            base_qty=qty,
            base_uom=self.unit.code,
            base_price=Decimal(price),
            line_no=self.inbound.lines.count() + 1,
        )
        return self.tx(qty, day, src_line_id=line.pk)

    def review(
        self,
        qty,
        day="2026-11-02T11:00:00+08:00",
        price="5",
        line=None,
        posting="FAILED",
        batch="B",
    ):
        if line is None:
            line = OutboundOrderLine.objects.create(
                order=self.outbound,
                product=self.product,
                base_qty=qty,
                base_uom=self.unit,
                base_price=Decimal(price),
                line_no=self.outbound.lines.count() + 1,
            )
        pick = WmsTask.objects.create(
            task_no="SP-" + uuid4().hex[:16],
            task_type="PICK",
            status="COMPLETED",
            review_status="APPROVED",
            posting_status=posting,
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="outbound",
            source_model="OutboundOrder",
            source_pk=str(self.outbound.pk),
        )
        pl = WmsTaskLine.objects.create(
            task=pick,
            product=self.product,
            qty_plan=qty,
            qty_done=qty,
            status="COMPLETED",
            from_location=self.location,
            src_model="outbound.OutboundOrderLine",
            src_id=line.pk,
        )
        review = WmsTask.objects.create(
            task_no="SR-" + uuid4().hex[:16],
            task_type="REVIEW",
            status="COMPLETED",
            review_status="APPROVED",
            posting_status="PENDING",
            approved_at=datetime.fromisoformat(day) if day else None,
            owner=self.owner,
            warehouse=self.warehouse,
            source_app="tasking",
            source_model="WmsTask",
            source_pk=str(pick.pk),
        )
        WmsTaskLine.objects.create(
            task=review,
            product=self.product,
            qty_plan=qty,
            qty_done=qty,
            status="COMPLETED",
            src_model="WmsTaskLine",
            src_id=pl.pk,
        )
        TaskScanLog.objects.create(
            task=pick,
            task_line=pl,
            product=self.product,
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location,
            qty_base_delta=qty,
            fp=uuid4().hex,
            scan_snapshot_rev=pl.scan_snapshot_rev,
            lot_no=batch,
            status="OK",
            method="SCAN",
        )
        return pick, review, pl, line

    @staticmethod
    def total(payload, category):
        return next(t for t in payload["totals"] if t["category"] == category)

    def test_same_day_160_and_50_with_amounts_details_exports_and_no_dispatch(self):
        self.receipt(100)
        self.receipt(60)
        self.review(30)
        self.review(20)
        self.assertFalse(WmsTask.objects.filter(task_type="DISPATCH").exists())
        summary = self.data()
        self.assertEqual(Decimal(self.total(summary, "inbound")["qty"]), 160)
        self.assertEqual(Decimal(self.total(summary, "inbound")["amount"]), 400)
        outbound = self.total(summary, "outbound")
        self.assertEqual(Decimal(outbound["qty"]), 50)
        self.assertEqual(Decimal(outbound["amount"]), 250)
        self.assertEqual(outbound["base_unit_name"], "瓶")
        self.assertEqual(outbound["posting_pending_count"], 2)
        for grain in ("day", "week", "month", "year"):
            response = self.data("periods", grain=grain)
            self.assertEqual(response["count"], 1)
            self.assertEqual(Decimal(self.total(response["results"][0], "outbound")["qty"]), 50)
        products = self.data("products")["results"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["current_stock"][0]["current_onhand"], "0")
        self.assertEqual(self.data("documents")["count"], 2)
        events = self.data("events", category="outbound")["results"]
        detail = self.call("detail", event_id=events[0]["event_id"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["results"][0]["base_unit_name"], "瓶")
        for view in ("periods", "products", "documents", "events"):
            response = self.call("export", {"view": view, "page": 99, "page_size": 1})
            self.assertEqual(response.status_code, 200)
            book = load_workbook(BytesIO(b"".join(response.streaming_content)))
            rows = list(book.worksheets[1].values)
            header = rows[0]
            inbound = [r for r in rows[1:] if r[header.index("业务类型")] == "正常入库"]
            outbound = [r for r in rows[1:] if r[header.index("业务类型")] == "正常出库"]
            self.assertEqual(sum(r[header.index("数量")] for r in inbound), 160)
            self.assertEqual(sum(r[header.index("已知金额")] for r in outbound), 250)
            self.assertEqual({r[header.index("单位")] for r in inbound + outbound}, {"瓶"})

    def test_charts_reconcile_with_events_and_paginate_complete_periods(self):
        self.receipt(100)
        self.review(30)
        self.receipt(60, "2026-11-03T10:00:00+08:00")
        self.review(20, "2026-11-03T11:00:00+08:00")
        for grain in ("day", "week", "month", "year"):
            payload = self.data("chart", grain=grain)
            self.assertEqual(payload["dimension"]["label"], "瓶")
            for direction, expected in (("inbound", 160), ("outbound", 50)):
                points = [r for r in payload["results"] if r["category"] == direction]
                self.assertEqual(sum(Decimal(r["value"]) for r in points), expected)
        ranked = self.data("chart", chart="ranking", metric="amount")
        self.assertEqual(Decimal(ranked["results"][0]["value"]), 250)
        records = self.data(
            "events",
            product_id=self.product.pk,
            category="outbound",
            currency="CNY",
            snapshot=ranked["snapshot"],
        )
        self.assertEqual(sum(Decimal(r["amounts"][0]["amount"]) for r in records["results"]), 250)
        for day in range(4, 16):
            self.receipt(1, f"2026-11-{day:02}T10:00:00+08:00")
        first = self.data("chart")
        self.assertEqual(len({r["period_start"] for r in first["results"]}), 12)
        self.assertEqual(first["next_page"], 2)
        second = self.data("chart", page=2, snapshot=first["snapshot"])
        self.assertEqual(len(second["results"]), 4)
        self.assertIsNone(second["next_page"])

    def test_chart_ranks_entire_range_separates_units_and_handles_missing_prices(self):
        self.receipt(1, price="0.001")
        self.tx(2, "2026-11-02T10:00:00+08:00")
        for i in range(12):
            product = Product.objects.create(
                owner=self.owner, code=f"CH{i}", name=f"排行{i}", sku=f"CH-{i}", base_uom=self.unit
            )
            self.tx(i + 10, f"2026-10-{20-i:02}T10:00:00+08:00", product=product)
        unit = ProductUom.objects.create(code="CH-BOX", name="箱")
        extra = self.tx(900, "2026-11-02T10:00:00+08:00")
        InventoryTransaction.objects.filter(pk=extra.pk).update(base_unit=unit.code)
        ranked = self.data("chart", chart="ranking", direction="inbound")
        self.assertEqual(len(ranked["results"]), 10)
        self.assertEqual(ranked["results"][0]["product_id"], product.pk)
        self.assertEqual(Decimal(ranked["results"][0]["value"]), 21)
        self.assertEqual(ranked["dimension"]["code"], self.unit.code)
        box = self.data("chart", dimension="base_unit:CH-BOX")
        self.assertEqual(Decimal(box["results"][0]["value"]), 900)
        self.assertEqual(self.data("events", base_unit=unit.code)["count"], 1)
        money = self.data("chart", chart="ranking", direction="inbound", metric="amount")
        self.assertEqual(money["excluded_count"], 12)
        self.assertEqual(money["results"][0]["amount_status"], "PARTIAL")
        self.assertEqual(Decimal(money["results"][0]["value"]), 0)
        self.assertEqual(self.data("chart", metric="amount")["results"][0]["value"], None)

    def test_review_date_survives_late_posting_retry_and_snapshot(self):
        pick, review, pl, _ = self.review(30)
        first = self.data("periods")
        self.tx(
            -10, "2026-11-03T10:00:00+08:00", src_model="WmsTask", src_id=pick.pk, src_line_id=pl.pk
        )
        self.tx(
            -20,
            "2026-11-03T10:00:00+08:00",
            src_model="WmsTask",
            src_id=pick.pk,
            batch_no="SECOND",
        )
        WmsTask.objects.filter(pk=pick.pk).update(posting_status="POSTED")
        self.review(7)
        same = self.data("periods", snapshot=first["snapshot"])
        self.assertEqual(Decimal(self.total(same["results"][0], "outbound")["qty"]), 30)
        self.assertEqual(same["results"][0]["period_start"], "2026-11-02")
        self.assertEqual(self.total(same["results"][0], "outbound")["posting_pending_count"], 0)
        self.assertEqual(Decimal(self.total(self.data(), "outbound")["qty"]), 37)

    def test_pending_and_rejected_review_do_not_count_even_with_stray_ledger(self):
        for status in ("PENDING", "REJECTED"):
            pick, review, pl, _ = self.review(3)
            WmsTask.objects.filter(pk=review.pk).update(
                review_status=status, posting_status="NOT_READY", approved_at=None
            )
            self.tx(-3, "2026-11-02T10:00:00+08:00", src_model="WmsTask", src_id=pick.pk)
        self.assertEqual(self.data()["totals"], [])

    def test_dispatch_ledger_and_unapproved_legacy_pick_cannot_inflate_review_totals(self):
        self.review(3)
        for task_type in ("DISPATCH", "LOAD", "PICK"):
            task = WmsTask.objects.create(
                task_no=f"ST-UNCONFIRMED-{task_type}",
                task_type=task_type,
                owner=self.owner,
                warehouse=self.warehouse,
                source_app="outbound",
                source_model="OutboundOrder",
                source_pk=str(self.outbound.pk),
            )
            self.tx(-9, "2026-11-03T10:00:00+08:00", src_model="WmsTask", src_id=task.pk)
        totals = self.data()
        self.assertEqual(Decimal(self.total(totals, "outbound")["qty"]), 3)
        self.assertEqual(Decimal(self.total(totals, "unknown")["qty"]), 27)

    def test_mixed_units_paginate_calendar_days_not_units_and_keep_historical_names(self):
        self.receipt(100)
        box = ProductUom.objects.create(code="ST-BOX", name="箱")
        old_unit = self.tx(8, "2026-11-02T09:00:00+08:00")
        InventoryTransaction.objects.filter(pk=old_unit.pk).update(base_unit=box.code)
        self.receipt(60, "2026-11-03T10:00:00+08:00")
        Product.objects.filter(pk=self.product.pk).update(base_uom=box)
        first = self.data("periods", page_size=1, order="asc")
        self.assertEqual(first["count"], 2)
        self.assertEqual({t["base_unit_name"] for t in first["results"][0]["totals"]}, {"瓶", "箱"})
        second = self.data("periods", page_size=1, page=2, order="asc", snapshot=first["snapshot"])
        self.assertEqual(second["results"][0]["period_start"], "2026-11-03")

    def test_custom_range_crosses_year_and_preserves_cumulative_half_even_cents(self):
        _, _, _, line = self.review(1, "2025-12-31T23:59:59+08:00", price="0.005")
        self.review(1, "2026-01-01T00:00:00+08:00", line=line)
        self.review(1, "2026-01-02T00:00:00+08:00", line=line)
        week = self.data(
            "chart", grain="week", metric="amount", date_from="2025-12-31", date_to="2026-01-01"
        )["results"]
        self.assertEqual(len(week), 1)
        self.assertEqual(
            (week[0]["range_start"], week[0]["range_end"]), ("2025-12-31", "2026-01-01")
        )
        self.assertEqual(Decimal(week[0]["value"]), Decimal("0.01"))
        days = self.data("periods", order="asc")["results"]
        self.assertEqual(
            [Decimal(self.total(r, "outbound")["amount"]) for r in days],
            [Decimal("0"), Decimal("0.01"), Decimal("0.01")],
        )
        for grain in ("day", "week", "month", "year"):
            result = self.data("periods", grain=grain, date_from="2026-01-01", date_to="2026-01-01")
            self.assertEqual(
                sum(Decimal(self.total(r, "outbound")["amount"]) for r in result["results"]),
                Decimal("0.01"),
            )
            self.assertEqual(result["results"][0]["range_start"], "2026-01-01")
            self.assertEqual(result["results"][0]["range_end"], "2026-01-01")

    def test_unpriced_and_explicit_free_lines_do_not_become_false_zero_totals(self):
        self.receipt(2, price="0")
        self.receipt(3)
        total = self.total(self.data(), "inbound")
        self.assertEqual(total["amount_status"], "PARTIAL")
        self.assertEqual(Decimal(total["unpriced_qty"]), 2)
        self.outbound.pricing_status = "CONFIRMED"
        self.outbound.save(update_fields=["pricing_status"])
        self.review(2, price="0")
        self.assertEqual(self.total(self.data(), "outbound")["amount_status"], "PRICED")
        free = self.data("chart", chart="ranking", metric="amount")
        self.assertEqual(free["excluded_count"], 0)
        self.assertEqual(free["results"][0]["amount_status"], "PRICED")
        self.assertEqual(Decimal(free["results"][0]["value"]), 0)
        _, _, _, line = self.review(3)
        OutboundOrderLine.objects.filter(pk=line.pk).update(is_priced=False)
        self.assertEqual(self.total(self.data(), "outbound")["amount_status"], "PARTIAL")

    def test_missing_dates_unknown_units_and_foreign_snapshot_fail_closed(self):
        old_unit = self.tx(-4, "2026-11-02T10:00:00+08:00")
        InventoryTransaction.objects.filter(pk=old_unit.pk).update(base_unit="UNKNOWN")
        self.review(2, day=None)
        result = self.data("periods")
        self.assertIsNone(result["results"][0]["period_start"])
        self.assertIn(
            "单位待核对", {t["base_unit_name"] for t in result["results"][0]["totals"]}, result
        )
        self.assertEqual(self.data("periods", date_from="2026-11-01")["count"], 0)
        self.assertEqual(self.call("summary", {"snapshot": "forged"}).status_code, 400)
        self.assertEqual(self.call("detail", event_id="review-9999999").status_code, 404)
        self.assertEqual(
            self.call("summary", {"date_from": "2026-11-03", "date_to": "2026-11-02"}).status_code,
            400,
        )

    def test_current_stock_includes_units_absent_from_selected_history(self):
        self.receipt(100)
        box = ProductUom.objects.create(code="ST-CURRENT-BOX", name="箱")
        Product.objects.filter(pk=self.product.pk).update(base_uom=box)
        self.product.refresh_from_db()
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            subwarehouse=self.sub,
            location=self.location,
            onhand_qty=8,
        )
        row = self.data("products", date_to="2026-11-02")["results"][0]
        self.assertEqual({t["base_unit_name"] for t in row["totals"]}, {"瓶"})
        self.assertEqual(
            {s["base_unit_name"]: Decimal(s["current_onhand"]) for s in row["current_stock"]},
            {"瓶": 0, "箱": 8},
        )

    def test_batch_adjustments_use_review_actual_qty_and_do_not_multiply_original_line(self):
        pick, review, pl, line = self.review(5)
        TaskScanLog.objects.filter(task=pick).update(qty_base_delta=8)
        TaskScanLog.objects.create(
            task=pick,
            task_line=pl,
            product=self.product,
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location,
            qty_base_delta=-3,
            fp=uuid4().hex,
            scan_snapshot_rev=pl.scan_snapshot_rev,
            lot_no="B",
            status="OK",
            method="MANUAL",
        )
        self.review(2, price="9", batch="SECOND")
        result = self.data("products", batch="B")["results"][0]
        self.assertEqual(Decimal(self.total(result, "outbound")["qty"]), 5)
        self.assertEqual(Decimal(self.total(result, "outbound")["amount"]), 25)
        self.assertEqual(Decimal(self.total(self.data(), "outbound")["amount"]), 43)

    def test_query_count_is_bounded_and_report_does_not_write_inventory(self):
        self.receipt(1)
        self.review(2)
        with CaptureQueriesContext(connection) as captured:
            self.data("products")
        before = len(captured)
        self.review(3)
        with CaptureQueriesContext(connection) as captured:
            self.data("products")
        self.assertLessEqual(len(captured), before + 1)
        writes = [
            q["sql"]
            for q in captured
            if q["sql"].lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT"))
        ]
        self.assertFalse(any("inventory_" in sql.lower() for sql in writes))

    def test_receipt_snapshot_price_quality_and_photos_ignore_rejected_quantity(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="RECEIVE",
            task_no="STAT-RCPT",
            source_app="inbound",
            source_model="InboundOrder",
            source_pk=str(self.inbound.pk),
        )
        receipt = InboundReceipt.objects.create(
            order=self.inbound,
            owner=self.owner,
            warehouse=self.warehouse,
            supplier=self.supplier,
            task=task,
            receipt_no="STAT-REC",
            biz_date="2026-11-02",
            stage="POSTED",
        )
        tx = self.tx(100, "2026-11-02T10:00:00+08:00", src_model="WmsTask", src_id=task.pk)
        line = InboundReceiptLine.objects.create(
            receipt=receipt,
            line_no=1,
            product=self.product,
            product_name=self.product.name,
            product_code=self.product.code,
            base_uom=self.unit.code,
            base_qty=100,
            qty_ok=98,
            qty_damage=2,
            qty_reject=3,
            unit_price=Decimal("1.2345"),
            inventory_transaction=tx,
        )
        ArrivalPhoto.objects.create(
            line=line, file="synthetic-statistics.jpg", actor=self.user, sealed=True
        )
        total = self.total(self.data(), "inbound")
        self.assertEqual(Decimal(total["qty"]), 100)
        self.assertEqual(Decimal(total["amount"]), Decimal("123.45"))
        detail = self.call("detail", event_id=f"ledger-{tx.pk}")
        self.assertEqual(detail.status_code, 200)
        evidence = detail.data["receipts"][0]["lines"][0]
        self.assertEqual(evidence["base_unit_name"], "瓶")
        self.assertEqual(Decimal(evidence["qty_reject"]), 3)
        self.assertEqual(len(evidence["photos"]), 1)

    def test_pos_no_order_receiving_restoration_and_internal_movement(self):
        sale = PosSale.objects.create(sale_no="ST-POS", warehouse=self.warehouse)
        line = PosSaleLine.objects.create(
            sale=sale,
            owner=self.owner,
            product=self.product,
            line_no=1,
            qty=2,
            price=Decimal("1.25"),
            amount=Decimal("2.50"),
        )
        self.tx(-2, "2026-11-02T10:00:00+08:00", src_model="PosSaleLine", src_id=line.pk)
        self.tx(2, "2026-11-03T10:00:00+08:00", src_model="PosSaleLine", src_id=line.pk)
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="RECEIVE",
            task_no="ST-NOORDER",
            source_app="wmspda",
            source_model="ReceiveWithoutOrder",
        )
        self.tx(8, "2026-11-02T10:00:00+08:00", src_model="WmsTask", src_id=task.pk)
        pair = uuid4()
        self.tx(100, "2026-11-02T10:00:00+08:00", pair_id=pair)
        self.tx(-100, "2026-11-02T10:00:00+08:00", pair_id=pair)
        result = self.data()
        self.assertEqual(
            {t["category"] for t in result["totals"]}, {"inbound", "outbound", "reversal"}
        )
        self.assertEqual(Decimal(self.total(result, "outbound")["amount"]), Decimal("2.5"))
        self.assertEqual(self.total(result, "inbound")["amount_status"], "UNPRICED")
        self.assertEqual(Decimal(self.total(result, "inbound")["qty"]), 8)
        self.assertEqual(
            self.data("periods", category="outbound")["results"][0]["period_start"], "2026-11-02"
        )

    def test_scope_is_checked_on_all_surfaces_and_signed_snapshot(self):
        tx = self.receipt(1)
        pick, _, _, _ = self.review(2)
        snapshot = self.data()["snapshot"]
        other = get_user_model().objects.create_user(username="statistics-other", owner=self.other)
        UserRoleScope.objects.create(user=other, role="owner_manager", owner=self.other)
        other.user_permissions.set(self.user.user_permissions.all())
        for section in ("summary", "products", "documents", "events", "periods"):
            response = self.call(section, user=other)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.data.get("totals", response.data.get("results")))
        for key in (f"ledger-{tx.pk}", f"review-{pick.pk}"):
            self.assertEqual(self.call("detail", user=other, event_id=key).status_code, 404)
        self.assertEqual(self.call("export", {"view": "products"}, user=other).status_code, 400)
        self.assertEqual(self.call("summary", {"snapshot": snapshot}, user=other).status_code, 400)
        self.assertEqual(self.call("summary", {"owner_id": self.other.pk}).status_code, 400)
        UserRoleScope.objects.filter(user=other).update(role="owner_salesperson")
        other = get_user_model().objects.get(pk=other.pk)
        for section in ("summary", "products", "documents", "events", "export"):
            self.assertEqual(self.call(section, user=other).status_code, 403)

    def test_salesperson_statistics_filter_original_documents_on_every_surface(self):
        sales = get_user_model().objects.create_user(username="statistics-sales", owner=self.owner)
        UserRoleScope.objects.create(user=sales, role="owner_salesperson", owner=self.owner)
        sales.user_permissions.set(self.user.user_permissions.all())
        sales.user_permissions.add(Permission.objects.get(codename="access_owner_sales"))
        type(self.inbound).objects.filter(pk=self.inbound.pk).update(created_by=sales)
        type(self.outbound).objects.filter(pk=self.outbound.pk).update(created_by=sales)
        own = self.receipt(100)
        self.review(30)
        other_in = type(self.inbound).objects.create(
            owner=self.owner, warehouse=self.warehouse, supplier=self.supplier, created_by=self.user
        )
        foreign = self.tx(900, "2026-11-02T10:00:00+08:00", src_id=other_in.pk)
        original = self.outbound
        self.outbound = type(original).objects.create(
            owner=self.owner, warehouse=self.warehouse, customer=self.customer, created_by=self.user
        )
        foreign_pick, _, _, _ = self.review(20)
        self.outbound = original
        for section in ("summary", "periods", "products", "documents", "events"):
            response = self.call(section, user=sales)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["access_scope"], "personal")
            totals = response.data.get("totals") or [
                t for r in response.data["results"] for t in r["totals"]
            ]
            self.assertEqual(
                sum(Decimal(t["qty"]) for t in totals if t["category"] == "inbound"), 100
            )
            self.assertEqual(
                sum(Decimal(t["qty"]) for t in totals if t["category"] == "outbound"), 30
            )
        chart = self.call("chart", user=sales).data
        self.assertEqual(sorted(Decimal(r["value"]) for r in chart["results"]), [30, 100])
        rank = self.call("chart", {"chart": "ranking"}, user=sales).data
        self.assertEqual(Decimal(rank["results"][0]["value"]), 30)
        for key in (f"ledger-{foreign.pk}", f"review-{foreign_pick.pk}"):
            self.assertEqual(self.call("detail", user=sales, event_id=key).status_code, 404)
        self.assertEqual(
            self.call("detail", user=sales, event_id=f"ledger-{own.pk}").status_code, 200
        )
        self.assertEqual(
            self.call("products", {"document_key": f"inbound:{other_in.pk}"}, user=sales).data[
                "count"
            ],
            0,
        )
        self.assertEqual(
            self.call("summary", {"created_by": self.user.pk}, user=sales).status_code, 400
        )
        for view in ("periods", "products", "documents", "events"):
            exported = self.call("export", {"view": view}, user=sales)
            self.assertEqual(exported.status_code, 200)
            book = load_workbook(BytesIO(b"".join(exported.streaming_content)))
            info = dict(book.worksheets[0].values)
            self.assertEqual(info["授权范围"], "本人经手的出入库")
            rows = list(book.worksheets[1].values)
            self.assertEqual(sum(Decimal(str(r[rows[0].index("数量")])) for r in rows[1:]), 130)
        self.assertEqual(Decimal(self.total(self.data(), "outbound")["qty"]), 50)

    def test_salesperson_needs_role_permission_and_export_permission(self):
        sales = get_user_model().objects.create_user(
            username="statistics-sales-denied", owner=self.owner
        )
        UserRoleScope.objects.create(user=sales, role="owner_salesperson", owner=self.owner)
        sales.user_permissions.add(Permission.objects.get(codename="view_owner_operations"))
        self.assertEqual(self.call(user=sales).status_code, 403)
        sales.user_permissions.add(Permission.objects.get(codename="access_owner_sales"))
        sales = get_user_model().objects.get(pk=sales.pk)
        self.assertEqual(self.call(user=sales).status_code, 200)
        self.assertEqual(self.call("export", user=sales).status_code, 403)

    def test_salesperson_returns_follow_original_order_and_pos_follows_cashier(self):
        sales = get_user_model().objects.create_user(username="statistics-return", owner=self.owner)
        UserRoleScope.objects.create(user=sales, role="owner_salesperson", owner=self.owner)
        sales.user_permissions.set(self.user.user_permissions.all())
        sales.user_permissions.add(Permission.objects.get(codename="access_owner_sales"))
        type(self.outbound).objects.filter(pk=self.outbound.pk).update(created_by=sales)
        original_line = self.outbound.lines.create(product=self.product, base_qty=10, base_price=1)
        order = history_fixtures.CustomerReturn.objects.create(
            number="STAT-PERSONAL-RETURN",
            original_order=self.outbound,
            owner=self.owner,
            warehouse=self.warehouse,
            created_by=self.user,
        )
        line = history_fixtures.CustomerReturnLine.objects.create(
            order=order,
            original_line=original_line,
            product=self.product,
            product_name=self.product.name,
            product_code=self.product.code,
            unit=self.unit.code,
            qty=5,
            reason_code="OTHER",
            reason="验收退回",
        )
        receipt = history_fixtures.CustomerReturnReceipt.objects.create(
            order=order, actor=self.user
        )
        row = history_fixtures.CustomerReturnReceiptLine.objects.create(
            receipt=receipt, line=line, location=self.location, qty_ok=4, qty_damage=0, qty_reject=0
        )
        returned = self.tx(
            4,
            "2026-11-02T10:00:00+08:00",
            src_model="inbound.CustomerReturnReceiptLine",
            src_id=row.pk,
            src_line_id=line.pk,
        )
        for cashier, qty in ((sales, 2), (self.user, 90)):
            sale = PosSale.objects.create(
                sale_no=f"STAT-CASHIER-{cashier.pk}", warehouse=self.warehouse, cashier=cashier
            )
            sale_line = PosSaleLine.objects.create(
                sale=sale,
                owner=self.owner,
                product=self.product,
                line_no=1,
                qty=qty,
                price=Decimal("1.25"),
                amount=Decimal("1.25") * qty,
            )
            self.tx(-qty, "2026-11-02T10:00:00+08:00", src_model="PosSaleLine", src_id=sale_line.pk)
        self.tx(700, "2026-11-02T10:00:00+08:00", src_model="Unknown", src_id=0)
        response = self.call(user=sales)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {r["category"] for r in response.data["totals"]}, {"outbound", "customer_return"}
        )
        self.assertEqual(Decimal(self.total(response.data, "outbound")["qty"]), 2)
        self.assertEqual(Decimal(self.total(response.data, "customer_return")["qty"]), 4)
        self.assertEqual(
            self.call("detail", user=sales, event_id=f"ledger-{returned.pk}").status_code, 200
        )
        # Creating a return for a colleague's order does not grant its history.
        type(self.outbound).objects.filter(pk=self.outbound.pk).update(created_by=self.user)
        type(order).objects.filter(pk=order.pk).update(created_by=sales)
        self.assertEqual(
            self.call("detail", user=sales, event_id=f"ledger-{returned.pk}").status_code, 404
        )

    def test_foreign_order_association_never_supplies_price_or_document(self):
        pick, _, pl, line = self.review(2)
        customer = history_fixtures.Customer.objects.create(
            owner=self.other, salesperson=self.user, code="ST-OTHER-C", name="另一货主客户"
        )
        foreign = history_fixtures.OutboundOrder.objects.create(
            owner=self.other,
            warehouse=self.warehouse,
            customer=customer,
            created_by=self.user,
        )
        # Simulate corrupt legacy associations without changing operational services.
        OutboundOrderLine.objects.filter(pk=line.pk).update(order=foreign)
        result = self.data()
        self.assertEqual(result["totals"][0]["category"], "unknown")
        detail = self.call("detail", event_id=f"review-{pick.pk}").data["results"][0]
        self.assertIsNone(detail["outbound_id"])
        self.assertIsNone(detail["amount"])
        self.assertNotEqual(detail["document_no"], foreign.order_no)
        self.assertIsNone(detail["price_key"])

    def test_closed_order_keeps_archived_review_and_separate_restoration(self):
        pick, review, _, line = self.review(5, posting="POSTED")
        self.tx(-5, "2026-11-02T10:00:00+08:00", src_model="WmsTask", src_id=pick.pk)
        sale = PosSale.objects.create(sale_no="ST-CANCELLED", warehouse=self.warehouse)
        sale_line = PosSaleLine.objects.create(
            sale=sale,
            owner=self.owner,
            product=self.product,
            outbound_order_line=line,
            line_no=1,
            qty=5,
            price=5,
            amount=25,
        )
        self.tx(5, "2026-11-03T10:00:00+08:00", src_model="PosSaleLine", src_id=sale_line.pk)
        history_fixtures.OutboundOrder.objects.filter(pk=self.outbound.pk).update(
            is_closed=True, close_reason="测试关闭"
        )
        WmsTask.objects.filter(pk__in=[pick.pk, review.pk]).update(is_deleted=True)
        result = self.data()
        self.assertEqual(Decimal(self.total(result, "outbound")["qty"]), 5)
        self.assertEqual(Decimal(self.total(result, "reversal")["qty"]), 5)

    def test_other_change_categories_remain_separate(self):
        returned = history_fixtures.ReturnOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            return_no="ST-RETURN",
            recipient_type="SUPPLIER",
            supplier=self.supplier,
            recipient_name="供应商",
            operator=self.user,
            reason="退供",
            request_id=uuid4(),
            fingerprint="statistics-return",
        )
        self.tx(-4, "2026-11-01T10:00:00+08:00", src_model="ReturnOrder", src_id=returned.pk)
        self.tx(4, "2026-11-02T10:00:00+08:00", src_model="ReturnOrder", src_id=returned.pk)
        type(self.inbound).objects.filter(pk=self.inbound.pk).update(inbound_type="CUST_RETURN")
        self.tx(2, "2026-11-03T10:00:00+08:00")
        totals = {r["category"]: r for r in self.data()["totals"]}
        self.assertEqual(set(totals), {"supplier_return", "reversal", "customer_return"})
        self.assertEqual(Decimal(totals["reversal"]["qty"]), 4)


class OwnerStatisticsReviewWorkflowTests(physical_fixtures.PhysicalReviewFixture, TestCase):
    def report(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/inventory/owner-history/statistics/summary/")
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_real_pick_review_failure_and_retry_count_once_before_dispatch(self):
        task = self.ready_pick()
        self.manager.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["access_owner_management", "view_owner_operations"]
            )
        )
        self.assertFalse(self.report()["totals"])
        with patch(
            "allapp.tasking.plugins.handlers.DefaultPostingHandler._handle_atomic",
            side_effect=ValueError("statistics-post-failure"),
        ):
            with self.assertRaisesRegex(ValueError, "statistics-post-failure"):
                self.post_review(task)
        failed = self.report()
        total = OwnerStatisticsTests.total(failed, "outbound")
        self.assertEqual(Decimal(total["qty"]), Decimal("2.125"))
        self.assertEqual(total["posting_pending_count"], 1)
        self.assertEqual(total["amount_status"], "PRICED")
        self.assertNotEqual(total["base_unit_name"], "单位待核对")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.onhand_qty, 10)
        self.assertEqual(self.post_review(task).status_code, 200)
        self.assertEqual(self.post_review(task).status_code, 200)
        posted = OwnerStatisticsTests.total(self.report(), "outbound")
        self.assertEqual(posted["qty"], total["qty"])
        self.assertEqual(posted["amount"], total["amount"])
        self.assertEqual(posted["posting_pending_count"], 0)
        self.assertFalse(WmsTask.objects.filter(task_type="DISPATCH", status="COMPLETED").exists())
