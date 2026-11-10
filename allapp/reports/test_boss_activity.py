"""Live Boss projections exercised against real POS posting and MySQL scopes."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.accounts.roles import ROLE_GROUP_TEMPLATES
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.pos import tests as pos_fixtures
from allapp.pos.models import PosAuditLog, PosSale
from allapp.tasking.models import WmsTask


class BossActivityTests(TestCase):
    open_shift = pos_fixtures.PosApiTests.open_shift
    payment = pos_fixtures.PosApiTests.payment
    create_legacy_issue_for_sale_line = pos_fixtures.PosApiTests.create_legacy_issue_for_sale_line

    def setUp(self):
        pos_fixtures.PosApiTests.setUp(self)
        self.cashier = self.user
        self.cashier_client = self.client
        call_command("sync_wms_role_groups", stdout=StringIO())
        self.boss = get_user_model().objects.create_user(username="activity-boss")
        UserRoleScope.objects.create(
            user=self.boss, role="warehouse_boss", warehouse=self.warehouse
        )
        self.boss.groups.add(
            Group.objects.get(
                name=ROLE_GROUP_TEMPLATES[UserRoleScope.Role.WAREHOUSE_BOSS].group_name
            )
        )
        self.client = APIClient()
        self.client.force_authenticate(self.boss)
        self.other_warehouse = Warehouse.objects.create(code="ACT-OTHER", name="Other")

    def checkout(self, *, multi=False, key="act-sale"):
        items = [{"product_id": self.product.pk, "qty": "2", "price": "10"}]
        if multi:
            items.append({"product_id": self.other_product.pk, "qty": "1", "price": "20"})
        response = self.cashier_client.post(
            "/api/pos/checkout/",
            {
                "idempotency_key": key,
                "items": items,
                "payment": self.payment("40" if multi else "20"),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return PosSale.objects.get(idempotency_key=key)

    def get_report(self, name="activities", **params):
        response = self.client.get(f"/api/reports/boss/{name}/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_pos_checkout_is_one_important_event_with_stock_and_scoped_details(self):
        sale = self.checkout(multi=True)
        data = self.get_report(priority="important")
        self.assertEqual(len(data["results"]), 1)
        event = data["results"][0]
        self.assertEqual(event["title"], "POS 销售完成")
        self.assertIn("-2", event["summary"])
        detail = self.get_report(f'activities/pos/{event["target"]["id"]}', owner=self.owner.pk)
        self.assertEqual(detail["sections"]["lines"]["count"], 1)
        self.assertEqual(detail["sections"]["inventory"]["count"], 1)
        self.assertNotIn("payments", detail["sections"])
        self.assertNotIn(self.other_product.name, str(detail))
        amounts = {f["label"]: f["value"] for f in detail["header"]["fields"]}
        self.assertEqual(amounts["当前筛选商品金额"], 20)
        complete = self.get_report(f'activities/pos/{event["target"]["id"]}')
        self.assertEqual(complete["sections"]["payments"]["count"], 1)
        self.assertEqual(complete["sections"]["orders"]["count"], 2)
        self.assertEqual(sale.total_amount, 40)

    def test_void_and_legacy_trace_show_real_restock(self):
        sale = self.checkout()
        self.create_legacy_issue_for_sale_line(sale.lines.get(), remove_task_issue=True)
        response = self.cashier_client.post(
            f"/api/pos/sales/{sale.pk}/void/", {"reason": "test cancellation"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        rows = self.get_report(priority="important")["results"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "POS 销售作废")
        self.assertIn("+2", rows[0]["summary"])
        detail = self.get_report(f'activities/pos/{rows[0]["target"]["id"]}')
        self.assertEqual(detail["sections"]["inventory"]["count"], 1)

    def test_scope_read_only_and_query_audit_exclusion(self):
        sale = self.checkout()
        event = PosAuditLog.objects.get(sale=sale, action="CHECKOUT")
        record_audit_event(
            action="QUERY", module="reports", user=self.boss, warehouse_id=self.warehouse.pk
        )
        count = AuditEvent.objects.count()
        self.get_report()
        self.get_report()
        self.assertEqual(AuditEvent.objects.count(), count)
        self.assertEqual(
            self.client.get(
                "/api/reports/boss/activities/", {"warehouse": self.other_warehouse.pk}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                f"/api/reports/boss/activities/pos/{event.pk}/", {"owner": 999999}
            ).status_code,
            404,
        )
        self.assertEqual(self.client.post("/api/reports/boss/activities/", {}).status_code, 405)
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get("/api/reports/boss/activities/").status_code, 403)

    def order_event(self, number):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            order_no=number,
            customer=self.customer,
            created_by=self.cashier,
        )
        record_audit_event(
            action="outbound.order.create",
            module="outbound",
            user=self.cashier,
            obj=order,
            after={"submit_status": "DRAFT"},
        )
        self.assertNotIn(order.order_no, str(self.get_report(priority="important")))
        return order

    def test_submission_cursor_ties_and_rollback(self):
        now = timezone.now()
        expected = []
        for i in range(3):
            order = self.order_event(f"ACT-ORDER-{i}")
            with patch("django.utils.timezone.now", return_value=now):
                event = record_audit_event(
                    action="outbound.order.submit", module="outbound", user=self.cashier, obj=order
                )
                expected.append(f"order:{event.pk}")
        first = self.get_report(limit=1, priority="important")
        second = self.get_report(limit=1, priority="important", cursor=first["next_cursor"])
        third = self.get_report(limit=1, priority="important", cursor=second["next_cursor"])
        self.assertEqual(
            [page["results"][0]["id"] for page in [first, second, third]], list(reversed(expected))
        )
        self.assertIsNone(third["next_cursor"])
        self.assertEqual(
            self.client.get(
                "/api/reports/boss/activities/",
                {"cursor": first["next_cursor"], "owner": self.owner.pk},
            ).status_code,
            400,
        )
        with self.assertRaises(RuntimeError), transaction.atomic():
            record_audit_event(
                action="outbound.order.submit", module="outbound", user=self.cashier, obj=order
            )
            raise RuntimeError("rollback")
        self.assertEqual(len(self.get_report(priority="important")["results"]), 3)
        detail = self.get_report(f"activities/order/{event.pk}")
        self.assertIn("history", detail["sections"])

    def test_completed_old_task_and_all_task_types(self):
        from datetime import timedelta

        from allapp.reports.services_boss import _task_progress_rows

        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="ACT-PUTAWAY",
            task_type="PUTAWAY",
            status="COMPLETED",
            review_status="PENDING",
            finished_at=timezone.now(),
        )
        WmsTask.objects.filter(pk=task.pk).update(created_at=timezone.now() - timedelta(days=3))
        from allapp.reports.boss_activity import local_today

        rows = _task_progress_rows(WmsTask.objects.filter(pk=task.pk), local_today())
        row = next(r for r in rows if r["task_type"] == "PUTAWAY")
        self.assertEqual(row["today_completed"], 1)
        self.assertEqual(row["today_total"], 0)
        self.assertEqual(self.get_report("tasks", mode="completed")["count"], 1)
        self.assertIn("scans", self.get_report(f"tasks/{task.pk}")["sections"])

    def test_invalid_inputs(self):
        for params in [
            {"category": "login"},
            {"actor": "bad"},
            {"limit": -1},
            {"date_to": "bad"},
            {"priority": "bad"},
        ]:
            with self.subTest(params=params):
                self.assertEqual(
                    self.client.get("/api/reports/boss/activities/", params).status_code, 400
                )

    def test_partial_return_is_one_event_and_does_not_leak_other_owner(self):
        sale = self.checkout(multi=True)
        request = {
            "sale_id": sale.pk,
            "reason": "退回一件",
            "idempotency_key": "activity-return",
            "lines": [{"sale_line_id": sale.lines.get(owner=self.owner).pk, "qty": "1"}],
            "refunds": [{"method": "CASH", "amount": "10"}],
        }
        for _ in range(2):
            response = self.cashier_client.post("/api/pos/returns/", request, format="json")
            self.assertIn(response.status_code, [200, 201], response.data)
        rows = self.get_report(priority="important")["results"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "POS 退货")
        self.assertIn("+1", rows[0]["summary"])
        detail = self.get_report(f'activities/pos/{rows[0]["target"]["id"]}')
        self.assertEqual(detail["sections"]["payments"]["count"], 1)
        other = self.get_report(priority="important", owner=self.other_owner.pk)["results"]
        self.assertEqual(len(other), 1)
        self.assertNotIn(self.product.name, other[0]["summary"])

    def test_failed_posting_produces_no_success_activity(self):
        from django.core.exceptions import ValidationError

        with patch(
            "allapp.pos.services.execute_posting_handler",
            side_effect=ValidationError("forced failure"),
        ):
            result = self.cashier_client.post(
                "/api/pos/checkout/",
                {
                    "items": [{"product_id": self.product.pk, "qty": "2", "price": "10"}],
                    "payment": self.payment("20"),
                },
                format="json",
            )
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.get_report(priority="important")["results"], [])

    def test_assurance_and_contract_blockers_paginate_beyond_ten(self):
        from allapp.billing.models import BillingEvent, BillingPeriod
        from allapp.billing.services.integrity import build_close_readiness
        from allapp.reports.boss_activity import local_today

        today = local_today()
        period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            start_date=today.replace(day=1),
            end_date=today,
            currency="CNY",
            label="activity-period",
        )
        events = [
            BillingEvent.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                charge_type="PICK",
                calc_method="PER_QTY_ABSDEL",
                service_date=today,
                quantity=1,
                event_fp=f"activity-unpriced-{i}",
            )
            for i in range(25)
        ]
        data = self.get_report("revenue-assurance/sections/unpriced_events", details=1, page=2)
        self.assertEqual(data["count"], 25)
        self.assertEqual([r["id"] for r in data["results"]], [e.pk for e in events[20:]])
        data = self.get_report(
            "revenue-assurance/sections/close_blockers",
            details=1,
            period=period.pk,
            blocker="BILLING_EVENT_PRICING_INCOMPLETE",
            page=2,
        )
        self.assertEqual(data["count"], 25)
        self.assertEqual(len(data["results"]), 5)
        readiness = build_close_readiness(
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
            start_date=period.start_date,
            end_date=period.end_date,
        )
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["blocker_count"], 25)
        self.assertEqual(len(readiness["blockers"][0]["samples"]), 10)
        for section in [
            "missing_accruals",
            "approximate_sources",
            "billing_job_failures",
            "periods_pending_close",
            "late_arriving_charges",
            "accrual_invoice_variance",
        ]:
            self.get_report(f"revenue-assurance/sections/{section}", details=1)

    def test_task_actor_filter_and_timestamp_cursor_across_sources(self):
        from allapp.tasking.models import TaskAssignment, TaskStatusLog

        task = WmsTask.objects.create(
            owner=self.owner, warehouse=self.warehouse, task_no="ACT-RECEIVE", task_type="RECEIVE"
        )
        assignment = TaskAssignment.objects.create(
            task=task, assignee=self.cashier, accepted_at=timezone.now()
        )
        log = TaskStatusLog.objects.create(
            task=task, old_status="DRAFT", new_status="RELEASED", changed_by=self.cashier
        )
        with patch("django.utils.timezone.now", return_value=log.changed_at):
            from allapp.billing.models import BillingEvent

            BillingEvent.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                charge_type="PICK",
                service_date=log.changed_at.date(),
                event_fp="activity-tied-billing",
            )
        first = self.get_report(limit=1)
        second = self.get_report(limit=1, cursor=first["next_cursor"])
        third = self.get_report(limit=1, cursor=second["next_cursor"])
        ids = [r["results"][0]["id"] for r in [first, second, third]]
        self.assertEqual(len(set(ids)), 3)
        self.assertIn(f"assignment:{assignment.pk}", ids)
        self.assertIsNone(third["next_cursor"])
        filtered = self.get_report(category="task", actor=self.cashier.pk)
        self.assertEqual(len(filtered["results"]), 2)

    def test_stock_move_is_not_reported_as_warehouse_loss(self):
        from decimal import Decimal
        from uuid import uuid4

        from allapp.inventory.models import InventoryTransaction
        from allapp.locations.models import Location

        destination = Location.objects.create(
            warehouse=self.warehouse, code="SWPOS-01-01-02", name="Destination"
        )
        pair = uuid4()
        entries = []
        for tx_type, qty, location in [("ISSUE", -3, self.location), ("RECEIVE", 3, destination)]:
            entries.append(
                InventoryTransaction.objects.create(
                    owner=self.owner,
                    warehouse=self.warehouse,
                    product=self.product,
                    location=location,
                    tx_type=tx_type,
                    qty_delta=qty,
                    pair_id=pair,
                    src_model="Move",
                    src_id=77,
                    posted_at=timezone.now(),
                    created_by=self.cashier,
                )
            )
        rows = self.get_report(priority="important", category="inventory")["results"]
        self.assertEqual({r["title"] for r in rows}, {"库存移位 · 移出", "库存移位 · 移入"})
        data = self.get_report(f"activities/inventory/{entries[0].pk}")
        self.assertEqual(data["sections"]["inventory"]["count"], 2)
        deltas = [
            next(f["value"] for f in row["fields"] if f["label"] == "本次增减")
            for row in data["sections"]["inventory"]["results"]
        ]
        self.assertEqual(sum(deltas), Decimal(0))
        self.assertIn("同仓同货主移动", str(data["notices"]))

    def test_receipt_reversal_and_paginated_collection_match_posted_records(self):
        from allapp.billing.models import BillingPeriod, PaymentAllocation, PaymentReceipt
        from allapp.billing.services.receivables import post_receipt, reverse_receipt
        from allapp.billing.test_p1_receivables import PaymentReceiptServiceTests
        from allapp.reports.boss_activity import local_today

        today = local_today()
        self.period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="ACT-RECEIPTS",
            start_date=today.replace(day=1),
            end_date=today,
            currency="CNY",
            status="INVOICED",
        )
        bill = PaymentReceiptServiceTests.bill(self, "ACT-BILL", 100)
        receipt = PaymentReceipt.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            currency="CNY",
            receipt_no="ACT-RECEIPT",
            receipt_date=today,
            amount=30,
            created_by=self.cashier,
        )
        PaymentAllocation.objects.create(receipt=receipt, bill=bill, amount=30)
        self.assertEqual(self.get_report(category="receipt")["results"], [])
        post_receipt(receipt.pk, by_user=self.cashier)
        reversal = reverse_receipt(
            receipt.pk, receipt_no="ACT-REVERSAL", reversal_date=today, by_user=self.cashier
        )
        rows = self.get_report(priority="important", category="receipt")["results"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "收款冲销")
        detail = self.get_report(f"activities/receipt/{reversal.pk}")
        self.assertEqual(detail["sections"]["allocations"]["count"], 1)
        fields = {
            f["label"]: f["value"]
            for f in detail["sections"]["allocations"]["results"][0]["fields"]
        }
        self.assertTrue(fields["反向核销"])
        # Receivables retain the original positive amount; the reversal flag determines direction.
        self.assertEqual(fields["金额"], 30)
        history = self.get_report(
            f"receivables/bills/{bill.pk}/collection-history", page=2, page_size=1
        )
        self.assertEqual(history["allocations_count"], 2)
        self.assertEqual(len(history["allocations"]), 1)
        self.assertTrue(history["allocations"][0]["is_reversal"])

    def test_important_alert_history_paginates_and_respects_selected_owner(self):
        from allapp.reports.models import AlertCase, AlertCaseHistory

        case = AlertCase.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            dedup_key="ACT-ALERT",
            alert_type="STOCK",
            source_type="inventory",
            source_id="77",
            severity="HIGH",
            title="库存异常",
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
            assignee=self.cashier,
        )
        logs = [
            AlertCaseHistory.objects.create(
                case=case, action="NOTE", note=f"检查 {i}", created_by=self.cashier
            )
            for i in range(23)
        ]
        feed = self.get_report(priority="important", category="alert", limit=100)
        self.assertEqual(len(feed["results"]), 23)
        detail = self.get_report(f"activities/alert/{logs[-1].pk}", history_page=2)
        self.assertEqual(detail["sections"]["history"]["count"], 23)
        self.assertEqual(len(detail["sections"]["history"]["results"]), 3)
        paged = self.get_report(f"alert-cases/{case.pk}", page=2)
        self.assertEqual(paged["history_count"], 23)
        self.assertEqual(len(paged["case"]["history"]), 3)
        self.assertEqual(
            self.client.get(
                f"/api/reports/boss/alert-cases/{case.pk}/", {"owner": self.other_owner.pk}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/reports/boss/activities/alert/{logs[-1].pk}/", {"owner": self.other_owner.pk}
            ).status_code,
            404,
        )

    def test_new_records_do_not_duplicate_older_cursor_pages(self):
        orders = [self.order_event(f"ACT-CONCURRENT-{i}") for i in range(3)]
        for order in orders:
            record_audit_event(
                action="outbound.order.submit", module="outbound", user=self.cashier, obj=order
            )
        first = self.get_report(category="order", limit=2)
        new_order = self.order_event("ACT-CONCURRENT-NEW")
        record_audit_event(
            action="outbound.order.submit", module="outbound", user=self.cashier, obj=new_order
        )
        older = self.get_report(category="order", limit=2, cursor=first["next_cursor"])
        self.assertEqual(len(older["results"]), 1)
        self.assertTrue(
            {r["id"] for r in first["results"]}.isdisjoint(r["id"] for r in older["results"])
        )
        self.assertEqual(
            self.get_report(category="order")["results"][0]["record_no"], new_order.order_no
        )

    def test_contract_blockers_page_across_contracts_without_changing_gate_counts(self):
        from datetime import timedelta

        from allapp.billing.models import BillingServiceContract
        from allapp.billing.services.integrity import build_close_readiness
        from allapp.reports.boss_activity import local_today

        today = local_today()
        for charge, method in [("STORAGE", "PER_PALLET_DAY"), ("STORAGE", "PER_CBM_DAY")]:
            BillingServiceContract.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                charge_type=charge,
                calc_method=method,
                source_type="DAILY_METRIC",
                effective_from=today - timedelta(days=14),
                currency="CNY",
            )
        filters = dict(
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
            start_date=today - timedelta(days=14),
            end_date=today,
        )
        all_rows = build_close_readiness(**filters, sample_limit=100)
        page = build_close_readiness(**filters, sample_offset=20, sample_limit=20)
        code = "BILLING_CONTRACT_METRIC_EVENT_MISSING"
        all_block = next(b for b in all_rows["blockers"] if b["code"] == code)
        page_block = next(b for b in page["blockers"] if b["code"] == code)
        self.assertEqual(all_block["count"], 30)
        self.assertEqual(page_block["count"], 30)
        self.assertEqual(page_block["samples"], all_block["samples"][20:])
        self.assertEqual(page["ready"], all_rows["ready"])

    def test_pos_detail_keeps_order_unit_after_catalog_unit_changes(self):
        from allapp.products.models import Product, ProductUom

        sale = self.checkout()
        original_unit = sale.lines.get().outbound_order_line.base_uom.name
        new_unit = ProductUom.objects.create(code="ACT-NEW-UOM", name="箱")
        Product.objects.filter(pk=self.product.pk).update(base_uom=new_unit)
        log = PosAuditLog.objects.get(sale=sale, action="CHECKOUT")
        data = self.get_report(f"activities/pos/{log.pk}")
        fields = {f["label"]: f["value"] for f in data["sections"]["lines"]["results"][0]["fields"]}
        self.assertEqual(fields["单位"], original_unit)
        self.assertGreater(data["sections"]["tasks"]["count"], 0)

    def test_scan_detail_keeps_package_quantity_separate_from_base_quantity(self):
        from allapp.tasking.models import TaskScanLog

        task = WmsTask.objects.create(
            owner=self.owner, warehouse=self.warehouse, task_no="ACT-PACK-SCAN", task_type="RECEIVE"
        )
        TaskScanLog.objects.create(
            task=task,
            fp="activity-pack-scan",
            scan_snapshot_rev=1,
            owner=self.owner,
            warehouse=self.warehouse,
            product=self.product,
            by_user=self.cashier,
            status="OK",
            qty_aux=1,
            pack_qty=12,
            qty_base=12,
            qty_base_delta=12,
            uom_code="BOX",
            uom_name="箱",
        )
        data = self.get_report(f"tasks/{task.pk}")
        fields = {f["label"]: f["value"] for f in data["sections"]["scans"]["results"][0]["fields"]}
        self.assertEqual(fields["扫码数量"], 1)
        self.assertEqual(fields["扫码单位"], "箱")
        self.assertEqual(fields["基本数量变化"], 12)
        self.assertEqual(fields["当前档案基本单位"], self.product.base_uom.code)

    def test_unrelated_etl_completion_is_not_used_as_selected_data_freshness(self):
        from allapp.reports.models import EtlJobRun

        EtlJobRun.objects.create(job_name="etl_full_reports", ok=True, finished_at=timezone.now())
        data = self.get_report("operations")
        self.assertIsNone(data["meta"]["etl_processed_at"])
        self.assertIsNone(data["meta"]["cycles_processed_at"])


class SubmittedOrderAuditTests(TestCase):
    """All existing order entry channels contribute only committed submissions."""

    def setUp(self):
        from allapp.salesapp.tests import SaleMiniApiTests

        SaleMiniApiTests.setUp(self)

    def test_successful_online_order_has_one_submission_record(self):
        from allapp.salesapp.tests import SaleMiniApiTests

        SaleMiniApiTests.test_create_order_generates_outbound_and_allocates_inventory(self)
        audits = AuditEvent.objects.filter(action="outbound.order.create", succeeded=True)
        self.assertEqual(audits.count(), 1)
        self.assertEqual(audits.get().after["submit_status"], "SUBMITTED")

    def test_audit_failure_rolls_back_order_inventory_reservation_and_task(self):
        from allapp.inventory.models import InventoryDetail
        from allapp.salesapp.tests import SaleMiniApiTests

        before = list(InventoryDetail.objects.values_list("id", "allocated_qty", "onhand_qty"))
        with patch(
            "allapp.accounts.audit.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                SaleMiniApiTests.test_create_order_generates_outbound_and_allocates_inventory(self)
        self.assertFalse(OutboundOrder.objects.exists())
        self.assertFalse(WmsTask.objects.exists())
        self.assertEqual(
            list(InventoryDetail.objects.values_list("id", "allocated_qty", "onhand_qty")), before
        )
