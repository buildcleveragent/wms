"""Owner stock facts, pagination and document visibility on real MySQL."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding, Supplier
from allapp.inbound.models import InboundOrder
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.outbound.transfer_models import AssistedOwnershipTransfer
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask

from .models import InventoryDetail, InventorySummary, InventoryTransaction
from .views import OwnerInventorySummaryViewSet


class OwnerInventoryDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Owner.objects.create(code="OID", name="库存明细货主")
        cls.other = Owner.objects.create(code="OID-X", name="其他货主")
        cls.unit = ProductUom.objects.create(code="OID-EA", name="瓶")
        cls.product = Product.objects.create(
            owner=cls.owner, code="OID", sku="OID", name="库存商品", base_uom=cls.unit
        )
        cls.summary = InventorySummary.objects.create(owner=cls.owner, product=cls.product)
        cls.user = get_user_model().objects.create_user(username="oid-sales", owner=cls.owner)
        UserRoleScope.objects.create(user=cls.user, role="owner_salesperson", owner=cls.owner)
        cls.user.user_permissions.add(
            Permission.objects.get(codename="submit_outbound_as_owner_buyers"),
            Permission.objects.get(codename="access_owner_sales"),
        )
        cls.warehouse = Warehouse.objects.create(code="OID-W", name="一号仓库")
        cls.warehouse2 = Warehouse.objects.create(code="OID-W2", name="二号仓库")
        OwnerWarehouseBinding.objects.create(owner=cls.owner, warehouse=cls.warehouse)
        cls.sub = Subwarehouse.objects.create(warehouse=cls.warehouse, code="OIDS", name="子仓")
        cls.location = Location.objects.create(
            warehouse=cls.warehouse, subwarehouse=cls.sub, code="OIDS-01-01-01", name="库位"
        )
        cls.location2 = Location.objects.create(
            warehouse=cls.warehouse, subwarehouse=cls.sub, code="OIDS-01-01-02", name="第二库位"
        )
        cls.sub2 = Subwarehouse.objects.create(
            warehouse=cls.warehouse2, code="OIDS2", name="子仓二"
        )
        cls.remote = Location.objects.create(
            warehouse=cls.warehouse2, subwarehouse=cls.sub2, code="OIDS2-01-01-01", name="异仓库位"
        )
        cls.customer = Customer.objects.create(
            owner=cls.owner, salesperson=cls.user, code="OID-C", name="保密客户"
        )

    def stock(self, qty="10.25", location=None, **kwargs):
        loc = location or self.location
        return InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=loc.warehouse,
            subwarehouse=loc.subwarehouse,
            location=loc,
            onhand_qty=Decimal(qty),
            **kwargs,
        )

    def tx(self, qty="2.50", kind="RECEIVE", location=None, **kwargs):
        loc = location or self.location
        defaults = dict(src_model="unknown", src_id=1, posted_at=datetime(2026, 10, 19, 12))
        if kind.startswith("OWNER_XFER_"):
            defaults["pair_id"] = uuid4()
        defaults.update(kwargs)
        return InventoryTransaction.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=loc.warehouse,
            subwarehouse=loc.subwarehouse,
            location=loc,
            qty_delta=Decimal(qty),
            tx_type=kind,
            **defaults,
        )

    def task(self, kind="ADJUST", parent=None, **kwargs):
        values = dict(
            owner=self.owner,
            warehouse=self.warehouse,
            task_type=kind,
            task_no=f"OID-T-{WmsTask.all_objects.count() + 1}",
        )
        if parent:
            values.update(source_model="tasking.WmsTask", source_pk=str(parent.pk))
        values.update(kwargs)
        return WmsTask.objects.create(**values)

    def call(self, action="inventory_detail", params=None, user=None, pk=None):
        request = APIRequestFactory().get("/", params or {})
        force_authenticate(request, user=user or self.user)
        return OwnerInventorySummaryViewSet.as_view({"get": action})(
            request, pk=pk or self.summary.pk
        )

    def ledger(self, params=None):
        response = self.call("transactions", params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_stock_groups_locations_units_and_decimal_totals(self):
        self.stock(batch_no="B", allocated_qty=1, locked_qty=2, damaged_qty=1)
        self.stock("2.75", self.location2, batch_no="B")
        self.stock("0", self.remote)
        result = self.call().data
        self.assertEqual(result["count"], 1)
        row = result["results"][0]
        self.assertEqual(Decimal(row["onhand_qty"]), 13)
        self.assertEqual(Decimal(row["available_qty"]), 9)
        self.assertIsInstance(row["onhand_qty"], str)
        self.assertEqual(len(result["warehouses"]), 2)
        InventoryDetail.objects.filter(location=self.location2).update(base_unit="OLD-BOX")
        result = self.call().data
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["unit_mismatch"])
        self.assertEqual(len(result["totals"]), 2)

    def test_expiry_boundary_batch_warehouse_and_page_filters(self):
        from unittest.mock import patch

        self.stock(batch_no="OLD", expiry_date=date(2026, 10, 18))
        self.stock(location=self.location2, batch_no="TODAY", expiry_date=date(2026, 10, 19))
        self.stock(location=self.remote)
        with patch(
            "allapp.inventory.owner_detail.timezone.now", return_value=datetime(2026, 10, 19, 0)
        ):
            for expiry, batch in (("expired", "OLD"), ("valid", "TODAY"), ("unknown", "")):
                result = self.call(params={"expiry": expiry}).data
                self.assertEqual(result["count"], 1)
                self.assertEqual(result["results"][0]["batch_no"], batch)
        result = self.call(params={"warehouse_id": self.warehouse.pk, "page_size": 1}).data
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["next_page"], 2)
        self.assertEqual(self.call(params={"batch": "today"}).data["count"], 1)

    def test_ledger_cursor_ignores_new_rows_and_rejects_changed_filters(self):
        first = self.tx()
        second = self.tx()
        result = self.ledger({"page_size": 1})
        self.assertEqual(result["results"][0]["id"], second.pk)
        self.tx()
        params = {"page_size": 1, "cursor": result["next_cursor"]}
        page = self.ledger(params)
        self.assertEqual([r["id"] for r in page["results"]], [first.pk])
        self.assertIsNone(page["next_cursor"])
        self.assertEqual(page, {**self.ledger(params), "as_of": page["as_of"]})
        self.assertEqual(self.call("transactions", {**params, "batch": "changed"}).status_code, 400)
        self.assertEqual(self.call("transactions", {"cursor": "forged"}).status_code, 400)

    def test_all_transaction_kinds_missing_time_and_old_units(self):
        for kind in (
            "RECEIVE",
            "ISSUE",
            "ADJ_GAIN",
            "ADJ_LOSS",
            "MOVE_IN",
            "MOVE_OUT",
            "OWNER_XFER_IN",
            "OWNER_XFER_OUT",
        ):
            self.tx(
                (
                    "-1.125"
                    if kind in {"ISSUE", "ADJ_LOSS", "MOVE_OUT", "OWNER_XFER_OUT"}
                    else "1.125"
                ),
                kind,
            )
        missing = self.tx(posted_at=None)
        InventoryTransaction.objects.filter(pk=missing.pk).update(base_unit="OLD")
        rows = self.ledger()["results"]
        self.assertEqual(len(rows), 9)
        self.assertIsNone(rows[0]["posted_at"])
        self.assertTrue(rows[0]["unit_mismatch"])
        self.assertTrue(rows[1]["posted_at"].endswith("+08:00"))
        self.assertEqual(
            len(self.ledger({"date_from": "2026-10-19", "date_to": "2026-10-19"})["results"]), 8
        )
        self.assertEqual(
            self.call(
                "transactions", {"date_from": "2026-10-20", "date_to": "2026-10-19"}
            ).status_code,
            400,
        )

    def test_move_pair_and_legacy_move_filters(self):
        pair = uuid4()
        self.tx("-2", "ISSUE", pair_id=pair)
        incoming = self.tx("2", "RECEIVE", pair_id=pair, location=self.location2)
        rows = self.ledger()["results"]
        self.assertTrue(all("总量不变" in r["movement_note"] for r in rows))
        self.assertEqual(len(self.ledger({"tx_type": "MOVE_IN"})["results"]), 1)
        self.assertEqual(len(self.ledger({"tx_type": "RECEIVE"})["results"]), 0)
        InventoryTransaction.objects.filter(pk=incoming.pk).update(base_unit="OLD")
        self.assertTrue(all(not r["movement_note"] for r in self.ledger()["results"]))

    def test_zero_stock_history_and_deleted_records(self):
        self.tx(location=self.remote)
        self.tx(is_deleted=True)
        self.tx(is_active=False)
        self.stock(is_deleted=True)
        detail = self.call().data
        self.assertEqual(detail["count"], 0)
        self.assertEqual(
            detail["warehouses"], [{"id": self.warehouse2.pk, "name": self.warehouse2.name}]
        )
        self.assertEqual(len(self.ledger()["results"]), 1)

    def test_scope_denies_other_owner_and_warehouse_account(self):
        foreign = get_user_model().objects.create_user(username="oid-foreign", owner=self.other)
        UserRoleScope.objects.create(user=foreign, role="owner_salesperson", owner=self.other)
        warehouse = get_user_model().objects.create_user(username="oid-warehouse")
        UserRoleScope.objects.create(
            user=warehouse, role="warehouse_operator", warehouse=self.warehouse
        )
        foreign.user_permissions.add(Permission.objects.get(codename="access_owner_sales"))
        for user in (foreign, warehouse):
            for action in ("inventory_detail", "transactions"):
                self.assertEqual(self.call(action, user=user).status_code, 404 if user == foreign else 403)

    def test_order_links_are_strict_and_notes_do_not_leak(self):
        order = OutboundOrder.objects.create(
            owner=self.owner, warehouse=self.warehouse, customer=self.customer, created_by=self.user
        )
        task = self.task(
            "PICK", source_app="outbound", source_model="OutboundOrder", source_pk=str(order.pk)
        )
        child = self.task("PICK", parent=task)
        self.tx(
            "-2",
            "ISSUE",
            src_model="WmsTask",
            src_id=child.pk,
            src_no=order.order_no,
            memo="保密客户",
        )
        row = self.ledger()["results"][0]
        self.assertEqual(row["document"]["order_id"], order.pk)
        OutboundOrder.objects.filter(pk=order.pk).update(created_by=None)
        row = self.ledger()["results"][0]
        self.assertEqual(row["document"]["status"], "restricted")
        self.assertNotIn(order.order_no, str(row))
        self.assertNotIn("保密客户", str(row))

    def test_adjustment_and_inbound_documents_and_technical_reason(self):
        task = self.task(remark="盘点发现破损，核实后调整")
        self.tx("-1", "ADJ_LOSS", src_model="WmsTask", src_id=task.pk)
        self.assertIn("破损", self.ledger()["results"][0]["reason"])
        WmsTask.objects.filter(pk=task.pk).update(remark="Traceback SQL password=hidden")
        self.assertEqual(self.ledger()["results"][0]["reason"], "原因未记录")
        supplier = Supplier.objects.create(code="OID-SUP", name="供应商", owner=self.owner)
        order = InboundOrder.objects.create(
            owner=self.owner, warehouse=self.warehouse, supplier=supplier
        )
        self.tx(src_model="inbound.InboundOrder", src_id=order.pk)
        self.assertEqual(self.ledger()["results"][0]["document"]["number"], order.order_no)

    def test_wrong_source_app_cross_warehouse_and_cycles(self):
        task = self.task()
        child = self.task(parent=task)
        self.tx(src_model="tasking.WmsTask", src_id=child.pk)
        WmsTask.objects.filter(pk=task.pk).update(source_model="WmsTask", source_pk=str(child.pk))
        self.assertEqual(self.ledger()["results"][0]["document"]["status"], "unknown")
        WmsTask.objects.filter(pk=task.pk).update(
            source_model="", source_pk="", warehouse=self.warehouse2
        )
        self.assertEqual(self.ledger()["results"][0]["document"]["status"], "unknown")
        WmsTask.objects.filter(pk=child.pk).update(source_app="inbound")
        # Qualified model's namespace must agree with source_app too.
        WmsTask.objects.filter(pk=child.pk).update(source_model="WmsTask")
        self.assertEqual(self.ledger()["results"][0]["document"]["status"], "unknown")

    def test_queries_are_read_only_and_not_per_row(self):
        task = self.task()
        self.tx(src_model="WmsTask", src_id=task.pk)
        with CaptureQueriesContext(connection) as small:
            self.ledger()
        for _ in range(15):
            self.tx(src_model="WmsTask", src_id=task.pk)
        with CaptureQueriesContext(connection) as large:
            self.ledger()
            self.call()
        with CaptureQueriesContext(connection) as repeat:
            self.ledger()
        self.assertLessEqual(len(repeat), len(small))
        self.assertFalse(
            any(q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for q in large)
        )

    def test_ownership_transfer_only_shows_current_owners_transfer_number(self):
        OwnerWarehouseBinding.objects.create(owner=self.other, warehouse=self.warehouse)
        customer = Customer.objects.create(
            owner=self.other, code="OID-SECRET", name="另一货主客户", salesperson=self.user
        )
        order = OutboundOrder.objects.create(
            owner=self.other, warehouse=self.warehouse, customer=customer
        )
        transfer = AssistedOwnershipTransfer.objects.create(
            transfer_no="OID-XFER",
            sales_order=order,
            source_owner=self.owner,
            target_owner=self.other,
            warehouse=self.warehouse,
        )
        task = self.task(
            "PICK",
            owner=self.other,
            source_model="OutboundOrder",
            source_pk=str(order.pk),
            source_app="outbound",
        )
        self.tx(
            "-3", "OWNER_XFER_OUT", src_model="WmsTask", src_id=task.pk, src_no=transfer.transfer_no
        )
        row = self.ledger()["results"][0]
        self.assertEqual(row["document"]["number"], transfer.transfer_no)
        self.assertNotIn("order_id", row["document"])
        self.assertNotIn(order.order_no, str(row))
        self.assertNotIn(customer.name, str(row))

    def test_public_routes_and_validation(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.user)
        for route in ("detail", "transactions"):
            url = f"/api/inventory/summary/{self.summary.pk}/{route}/"
            self.assertEqual(client.get(url).status_code, 200)
            self.assertEqual(client.get(url, {"page_size": 101}).status_code, 400)
            self.assertEqual(client.get(url, {"warehouse_id": "bad"}).status_code, 400)
