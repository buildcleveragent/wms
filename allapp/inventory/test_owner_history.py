"""History acceptance on MySQL: no dispatch workflow or inventory snapshot required."""

from datetime import datetime
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from openpyxl import load_workbook
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, Supplier
from allapp.inbound.arrival_models import ArrivalPhoto
from allapp.inbound.customer_return_models import (
    CustomerReturn,
    CustomerReturnLine,
    CustomerReturnReceipt,
    CustomerReturnReceiptLine,
)
from allapp.inbound.models import InboundOrder, InboundReceipt, InboundReceiptLine
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound.return_models import ReturnOrder
from allapp.outbound.transfer_models import AssistedOwnershipTransfer
from allapp.pos.models import PosSale, PosSaleLine
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask

from .models import InventoryTransaction
from .owner_history_views import OwnerHistoryView


class OwnerHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="HIST", name="历史货主")
        cls.other = Owner.objects.create(code="HIST-X", name="另一货主")
        cls.unit = ProductUom.objects.create(code="HIST-EA", name="瓶")
        cls.product = Product.objects.create(
            owner=cls.owner, code="HIST-P", name="历史商品", sku="HIST-SKU", base_uom=cls.unit
        )
        cls.warehouse = Warehouse.objects.create(code="HIST-W", name="历史仓库")
        cls.sub = Subwarehouse.objects.create(warehouse=cls.warehouse, code="HISTS", name="子仓")
        cls.location = Location.objects.create(
            warehouse=cls.warehouse, subwarehouse=cls.sub, code="HISTS-01-01-01", name="库位"
        )
        cls.user = get_user_model().objects.create_user(username="history-manager", owner=cls.owner)
        UserRoleScope.objects.create(user=cls.user, role="owner_manager", owner=cls.owner)
        cls.user.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    "access_owner_management",
                    "view_owner_operations",
                    "export_operations",
                ]
            )
        )
        cls.customer = Customer.objects.create(
            owner=cls.owner, salesperson=cls.user, code="HIST-C", name="历史客户"
        )
        cls.supplier = Supplier.objects.create(owner=cls.owner, code="HIST-SUP", name="历史供应商")
        cls.inbound = InboundOrder.objects.create(
            owner=cls.owner, warehouse=cls.warehouse, supplier=cls.supplier, created_by=cls.user
        )
        cls.outbound = OutboundOrder.objects.create(
            owner=cls.owner, warehouse=cls.warehouse, customer=cls.customer, created_by=cls.user
        )

    def tx(self, qty, day, *, kind=None, **extra):
        kind = kind or ("RECEIVE" if Decimal(qty) > 0 else "ISSUE")
        inbound = kind == "RECEIVE"
        values = dict(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            subwarehouse=self.sub,
            tx_type=kind,
            qty_delta=Decimal(qty),
            posted_at=datetime.fromisoformat(day) if day else None,
            src_model="InboundOrder" if inbound else "OutboundOrder",
            src_id=self.inbound.pk if inbound else self.outbound.pk,
            posting_batch=day or "UNDATED",
            batch_no="B",
        )
        values.update(extra)
        return InventoryTransaction.objects.create(**values)

    def call(self, section="summary", params=None, user=None, event_id=None):
        request = APIRequestFactory().get("/api/inventory/owner-history/", params or {})
        force_authenticate(request, user=user or self.user)
        return OwnerHistoryView.as_view(section=section)(request, event_id=event_id)

    def data(self, section="summary", **params):
        r = self.call(section, params)
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        return r.data

    def seed(self):
        return [
            self.tx(q, d)
            for q, d in [
                (100, "2026-10-01T10:00:00"),
                (60, "2026-11-02T10:00:00"),
                (-30, "2026-11-03T10:00:00"),
                (-20, "2026-11-04T10:00:00"),
            ]
        ]

    def test_all_history_totals_drilldown_and_export_without_dispatch(self):
        self.seed()
        self.assertFalse(WmsTask.objects.filter(task_type="DISPATCH").exists())
        summary = self.data()
        self.assertEqual(summary["scope_label"], "历史累计")
        totals = {r["category"]: r for r in summary["totals"]}
        self.assertEqual(Decimal(totals["inbound"]["qty"]), 160)
        self.assertEqual(Decimal(totals["outbound"]["qty"]), 50)
        self.assertEqual(totals["inbound"]["event_count"], 2)
        product = self.data("products")["results"][0]
        self.assertEqual(Decimal(product["outbound_qty"]), 50)
        self.assertEqual(product["current_onhand"], "0")
        documents = self.data("documents")["results"]
        self.assertEqual(len(documents), 2)
        self.assertEqual({r["counterparty"] for r in documents}, {"历史客户", "历史供应商"})
        rows = self.data("events", category="inbound", product_id=self.product.pk, order="asc")[
            "results"
        ]
        self.assertEqual([r["product_sequence"] for r in rows], [1, 2])
        detail = self.call("detail", event_id=rows[1]["event_id"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(Decimal(detail.data["results"][0]["qty_delta"]), 60)
        response = self.call("export", {"view": "products", "page_size": 1, "page": 99})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(b"".join(response.streaming_content)))
        values = list(book["商品收发汇总"].values)
        self.assertEqual(values[1][values[0].index("正常入库数量")], 160)
        self.assertEqual(values[1][values[0].index("正常出库数量")], 50)

    def test_numbers_survive_date_batch_filter_and_snapshot_pagination(self):
        self.seed()
        data = self.data(
            "events",
            category="inbound",
            product_id=self.product.pk,
            date_from="2026-11-01",
            batch="B",
            page_size=1,
        )
        self.assertEqual(data["results"][0]["product_sequence"], 2)
        first = self.data("events", category="inbound", page_size=1)
        self.tx(7, "2026-11-10T12:00:00")
        second = self.data(
            "events", category="inbound", page_size=1, page=2, snapshot=first["snapshot"]
        )
        self.assertEqual(Decimal(second["results"][0]["qty"]), 100)
        self.assertEqual(second["count"], 2)

    def test_month_week_day_and_custom_dates_share_totals(self):
        self.seed()
        for grain in ("month", "week", "day"):
            rows = self.data("periods", grain=grain)["results"]
            self.assertEqual(sum(Decimal(r["inbound_qty"]) for r in rows), 160)
            self.assertEqual(sum(Decimal(r["outbound_qty"]) for r in rows), 50)
        weeks = self.data("periods", grain="week")["results"]
        self.assertEqual(weeks[0]["period_start"], "2026-11-02")
        self.assertEqual(weeks[0]["period_end"], "2026-11-08")
        filtered = self.data(
            "periods", grain="month", date_from="2026-11-03", date_to="2026-11-03"
        )["results"]
        self.assertEqual(Decimal(filtered[0]["outbound_qty"]), 30)
        self.assertEqual(
            self.call("summary", {"date_from": "2026-11-04", "date_to": "2026-11-03"}).status_code,
            400,
        )

    def test_multiple_stock_layers_are_one_event_and_units_never_merge(self):
        a = self.tx(40, "2026-11-01T10:00:00")
        self.tx(60, "2026-11-01T10:00:00", batch_no="SECOND")
        self.assertEqual(self.data()["totals"][0]["event_count"], 1)
        self.assertEqual(self.data("events")["count"], 1)
        InventoryTransaction.objects.filter(pk=a.pk).update(base_unit="OLD-BOX")
        self.assertEqual(len(self.data()["totals"]), 2)
        self.assertEqual(self.data("products")["count"], 2)

    def test_movements_adjustments_unknown_and_missing_time(self):
        self.tx(10, "2026-11-01T10:00:00")
        self.tx(3, "2026-11-01T11:00:00", kind="ADJ_GAIN")
        self.tx(-2, "2026-11-01T12:00:00", kind="ADJ_LOSS")
        pair = uuid4()
        self.tx(99, "2026-11-01T13:00:00", pair_id=pair)
        self.tx(-99, "2026-11-01T13:00:00", pair_id=pair)
        unknown = self.tx(4, None, src_model="unknown")
        totals = {r["category"]: r for r in self.data()["totals"]}
        self.assertEqual(set(totals), {"inbound", "adjust_gain", "adjust_loss", "unknown"})
        self.assertEqual(totals["unknown"]["event_count"], 0)
        row = self.data("events", undated="true")["results"][0]
        self.assertEqual(row["event_id"], unknown.pk)
        self.assertIsNone(row["document_sequence"])
        self.assertEqual(self.data("events", date_from="2026-11-01")["count"], 3)

    def test_pos_direct_issue_and_void_remain_separate(self):
        sale = PosSale.objects.create(sale_no="HIST-POS", warehouse=self.warehouse)
        line = PosSaleLine.objects.create(
            sale=sale, owner=self.owner, product=self.product, line_no=1, qty=2, price=1, amount=2
        )
        self.tx(
            -2, "2026-11-01T10:00:00", src_model="PosSaleLine", src_id=line.pk, posting_batch="SALE"
        )
        self.tx(
            2, "2026-11-02T10:00:00", src_model="PosSaleLine", src_id=line.pk, posting_batch="VOID"
        )
        totals = {r["category"]: r for r in self.data()["totals"]}
        self.assertEqual(set(totals), {"outbound", "reversal"})
        self.assertEqual(Decimal(totals["outbound"]["qty"]), 2)

    def test_tasks_no_order_receipt_and_posted_pick_without_dispatch(self):
        receive = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="RECEIVE",
            task_no="HIST-R",
            source_app="wmspda",
            source_model="ReceiveWithoutOrder",
        )
        pick = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="PICK",
            task_no="HIST-P",
            source_app="outbound",
            source_model="OutboundOrder",
            source_pk=str(self.outbound.pk),
        )
        self.tx(8, "2026-11-01T10:00:00", src_model="WmsTask", src_id=receive.pk)
        self.tx(-3, "2026-11-02T10:00:00", src_model="tasking.WmsTask", src_id=pick.pk)
        totals = {r["category"]: r for r in self.data()["totals"]}
        self.assertEqual(Decimal(totals["inbound"]["qty"]), 8)
        self.assertEqual(Decimal(totals["outbound"]["qty"]), 3)

    def test_legacy_no_order_receive_and_internal_replenishment(self):
        receive = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="RECEIVE",
            task_no="HIST-LEGACY",
            posting_note="PDA无ASN收货",
        )
        replenish = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="REPLEN",
            task_no="HIST-REPLEN",
            source_app="outbound",
            source_model="OutboundOrder",
            source_pk=str(self.outbound.pk),
        )
        self.tx(9, "2026-11-01T10:00:00", src_model="WmsTask", src_id=receive.pk)
        self.tx(-9, "2026-11-02T10:00:00", src_model="WmsTask", src_id=replenish.pk)
        totals = self.data()["totals"]
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["category"], "inbound")
        self.assertEqual(Decimal(totals[0]["qty"]), 9)

    def test_search_filters_and_scope_on_every_surface(self):
        first = self.seed()[0]
        self.product.refresh_from_db()
        self.assertTrue(self.product.sku)
        self.assertEqual(self.data("products", search=self.product.sku)["count"], 1)
        self.assertEqual(self.data("products", search="missing")["count"], 0)
        self.assertEqual(self.data("documents", document_no=self.inbound.order_no)["count"], 1)
        other = get_user_model().objects.create_user(username="history-other", owner=self.other)
        UserRoleScope.objects.create(user=other, role="owner_manager", owner=self.other)
        other.user_permissions.set(self.user.user_permissions.all())
        for section in ("summary", "products", "documents", "events", "periods"):
            r = self.call(section, {"grain": "month"}, user=other)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.data.get("results", r.data.get("totals")))
        self.assertEqual(self.call("detail", user=other, event_id=first.pk).status_code, 404)
        self.assertEqual(self.call("export", {"view": "products"}, user=other).status_code, 400)
        self.assertEqual(self.call(params={"owner_id": self.other.pk}).status_code, 400)
        scope = UserRoleScope.objects.get(user=other)
        scope.role = "owner_salesperson"
        scope.save()
        other.user_permissions.add(Permission.objects.get(codename="access_owner_sales"))
        other = get_user_model().objects.get(pk=other.pk)
        for section in ("summary", "products", "documents", "events", "export"):
            self.assertEqual(self.call(section, user=other).status_code, 403)

    def test_receipt_evidence_and_multiple_products_share_one_receive_event(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="RECEIVE",
            task_no="HIST-RCPT",
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
            receipt_no="HIST-REC-1",
            biz_date="2026-11-01",
            stage="POSTED",
        )
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
            damage_reason="箱体破损",
            reject_reason="拒收",
            location=self.location,
        )
        ArrivalPhoto.objects.create(
            line=line, file="synthetic-evidence.jpg", actor=self.user, sealed=True
        )
        first = self.tx(100, "2026-11-01T10:00:00", src_model="WmsTask", src_id=task.pk)
        product2 = Product.objects.create(
            owner=self.owner, code="HIST-2", name="另一商品", base_uom=self.unit
        )
        self.tx(10, "2026-11-01T10:00:00", src_model="WmsTask", src_id=task.pk, product=product2)
        summary = self.data()["totals"][0]
        self.assertEqual(Decimal(summary["qty"]), 110)
        self.assertEqual(summary["event_count"], 1)
        detail = self.call("detail", event_id=first.pk)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["count"], 2)
        evidence = detail.data["receipts"][0]["lines"][0]
        self.assertEqual(Decimal(evidence["qty_damage"]), 2)
        self.assertEqual(Decimal(evidence["qty_reject"]), 3)
        self.assertIn(f"/batches/{receipt.pk}/photos/", evidence["photos"][0]["url"])
        InboundReceipt.objects.filter(pk=receipt.pk).update(stage="LEGACY")
        legacy = self.call("detail", event_id=first.pk).data["receipts"][0]["lines"][0]
        self.assertFalse(legacy["quality_recorded"])
        self.assertIsNone(legacy["qty_ok"])

    def test_customer_return_receipt_line_provenance_and_counts(self):
        original_line = self.outbound.lines.create(product=self.product, base_qty=10, base_price=1)
        order = CustomerReturn.objects.create(
            number="HIST-CR",
            original_order=self.outbound,
            owner=self.owner,
            warehouse=self.warehouse,
            created_by=self.user,
        )
        line = CustomerReturnLine.objects.create(
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
        receipt = CustomerReturnReceipt.objects.create(order=order, actor=self.user)
        row = CustomerReturnReceiptLine.objects.create(
            receipt=receipt, line=line, location=self.location, qty_ok=3, qty_damage=1, qty_reject=1
        )
        tx = self.tx(
            4,
            "2026-11-01T10:00:00",
            src_model="inbound.CustomerReturnReceiptLine",
            src_id=row.pk,
            src_line_id=line.pk,
            posting_batch=f"CRR-{receipt.pk}",
        )
        total = self.data()["totals"][0]
        self.assertEqual(total["category"], "customer_return")
        self.assertEqual(total["event_count"], 1)
        self.assertEqual(Decimal(total["qty"]), 4)
        detail = self.call("detail", event_id=tx.pk).data["results"][0]
        self.assertEqual(detail["customer_return_id"], order.pk)
        self.assertEqual(detail["document_no"], order.number)

    def test_return_out_and_reversal_are_not_normal_inbound_outbound(self):
        order = ReturnOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            return_no="HIST-RET",
            recipient_type="SUPPLIER",
            supplier=self.supplier,
            recipient_name="历史供应商",
            operator=self.user,
            reason="退供",
            request_id=uuid4(),
            fingerprint="history-return",
        )
        self.tx(-4, "2026-11-01T10:00:00", src_model="ReturnOrder", src_id=order.pk)
        self.tx(4, "2026-11-02T10:00:00", src_model="ReturnOrder", src_id=order.pk)
        self.inbound.inbound_type = "CUST_RETURN"
        self.inbound.save()
        self.tx(2, "2026-11-03T10:00:00")
        totals = {r["category"]: r for r in self.data()["totals"]}
        self.assertEqual(set(totals), {"supplier_return", "reversal", "customer_return"})
        self.assertEqual(Decimal(totals["reversal"]["qty"]), 4)

    def test_transfer_original_owner_cannot_see_new_owner_sale(self):
        new_order = OutboundOrder.objects.create(
            owner=self.other, warehouse=self.warehouse, customer=self.customer, created_by=self.user
        )
        transfer = AssistedOwnershipTransfer.objects.create(
            source_owner=self.owner,
            target_owner=self.other,
            warehouse=self.warehouse,
            transfer_no="HIST-XFER",
            sales_order=new_order,
        )
        row = self.tx(
            -5,
            "2026-11-01T10:00:00",
            kind="OWNER_XFER_OUT",
            pair_id=uuid4(),
            src_no=transfer.transfer_no,
            src_model="WmsTask",
            src_id=9999999,
        )
        self.assertEqual(self.data()["totals"][0]["category"], "transfer_out")
        self.assertEqual(self.data()["totals"][0]["event_count"], 1)
        detail = self.call("detail", event_id=row.pk).data["results"][0]
        self.assertEqual(detail["document_no"], transfer.transfer_no)
        self.assertIsNone(detail["outbound_id"])
        self.assertNotIn(new_order.order_no, str(detail))

    def test_bad_source_scope_and_task_cycles_fail_closed_without_losing_quantity(self):
        other_order = OutboundOrder.objects.create(
            owner=self.other, warehouse=self.warehouse, customer=self.customer, created_by=self.user
        )
        self.tx(-3, "2026-11-01T10:00:00", src_id=other_order.pk)
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="PICK",
            task_no="HIST-CYCLE",
            source_model="WmsTask",
            source_app="tasking",
        )
        WmsTask.objects.filter(pk=task.pk).update(source_pk=str(task.pk))
        self.tx(-7, "2026-11-02T10:00:00", src_model="WmsTask", src_id=task.pk)
        result = self.data()
        self.assertEqual(result["totals"][0]["category"], "unknown")
        self.assertEqual(Decimal(result["totals"][0]["qty"]), 10)
        self.assertEqual(result["totals"][0]["event_count"], 0)
        self.assertNotIn(other_order.order_no, str(self.data("documents")))

    def test_cancelled_business_order_does_not_erase_posted_history(self):
        self.seed()
        OutboundOrder.objects.filter(pk=self.outbound.pk).update(is_closed=True)
        self.assertEqual(Decimal(self.data("products")["results"][0]["outbound_qty"]), 50)

    def test_export_detail_uses_product_batch_units_and_exact_precision(self):
        self.tx("9007199254740.1250", "2026-11-01T10:00:00")
        self.tx("1.0001", "2026-11-02T10:00:00", batch_no="OTHER")
        response = self.call("export", {"view": "events", "batch": "B", "date_to": "2026-11-01"})
        self.assertEqual(response.status_code, 200)
        rows = list(
            load_workbook(BytesIO(b"".join(response.streaming_content)))["逐次收发明细"].values
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][rows[0].index("库存增减数量")], "9007199254740.1250")
        self.assertEqual(rows[1][rows[0].index("商品名称")], self.product.name)
        self.assertEqual(rows[1][rows[0].index("批次")], "B")

    def test_server_aggregates_before_pagination_without_per_row_queries(self):
        self.seed()
        with CaptureQueriesContext(connection) as small:
            self.data("events", page_size=1)
        for n in range(8):
            self.tx(1, f"2026-11-{n + 10:02d}T10:00:00")
        with CaptureQueriesContext(connection) as larger:
            result = self.data("events", page_size=1)
        self.assertEqual(result["count"], 12)
        self.assertEqual(len(result["results"]), 1)
        self.assertLessEqual(len(larger), len(small))

    @override_settings(USE_TZ=True)
    def test_periods_use_business_timezone_when_database_stores_utc(self):
        from datetime import timezone as utc

        self.tx(1, "2026-11-01T23:59:00", posted_at=datetime(2026, 11, 1, 16, 0, tzinfo=utc.utc))
        rows = self.data("periods", grain="day", date_from="2026-11-02", date_to="2026-11-02")[
            "results"
        ]
        self.assertEqual(rows[0]["period_start"], "2026-11-02")
        self.assertEqual(Decimal(rows[0]["inbound_qty"]), 1)
