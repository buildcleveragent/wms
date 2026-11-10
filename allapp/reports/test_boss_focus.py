from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from allapp.reports import test_boss_activity as activity_fixtures
from allapp.reports.boss_attention import check_deadlines
from allapp.reports.models import AlertCase, BossCheckState, BossReadProgress
from allapp.tasking.models import WmsTask


class BossFocusTests(TestCase):
    setUp = activity_fixtures.BossActivityTests.setUp
    open_shift = activity_fixtures.BossActivityTests.open_shift
    payment = activity_fixtures.BossActivityTests.payment
    get_report = activity_fixtures.BossActivityTests.get_report
    checkout = activity_fixtures.BossActivityTests.checkout
    order_event = activity_fixtures.BossActivityTests.order_event

    def task(self, **kwargs):
        return WmsTask.objects.create(
            task_no="FOCUS-TASK",
            task_type="PICK",
            owner=self.owner,
            warehouse=self.warehouse,
            status="RELEASED",
            **kwargs,
        )

    def test_timeout_recovery_reopen_and_no_duplicate(self):
        task = self.task(planned_end=timezone.now() - timedelta(hours=1))
        check_deadlines()
        check_deadlines()
        case = AlertCase.objects.get(source_type="WmsTask", source_id=str(task.pk))
        self.assertEqual(case.history.count(), 1)
        self.assertEqual(case.first_seen_at, task.planned_end)
        response = self.get_report("attention")
        self.assertEqual(response["count"], 1)
        self.assertFalse(any(r["stale"] for r in response["checks"]))
        self.assertEqual(response["results"][0]["assignee_name"], "未分派")
        task.status = "COMPLETED"
        task.review_status = "PENDING"
        task.save(update_fields=["status", "review_status"])
        check_deadlines()
        self.assertEqual(self.get_report("attention")["count"], 0)
        task.status = "IN_PROGRESS"
        task.review_status = "NONE"
        task.save(update_fields=["status", "review_status"])
        check_deadlines()
        case.refresh_from_db()
        self.assertEqual(case.status, "OPEN")
        self.assertEqual(case.history.count(), 3)

    def test_no_deadline_no_false_timeout_and_failed_check_does_not_resolve(self):
        task = self.task()
        check_deadlines()
        self.assertEqual(self.get_report("attention")["count"], 0)
        task.planned_end = timezone.now() - timedelta(seconds=1)
        task.save(update_fields=["planned_end"])
        check_deadlines()
        with patch(
            "allapp.reports.boss_attention.overdue_sources",
            side_effect=RuntimeError("test source failure"),
        ):
            with self.assertRaises(RuntimeError):
                check_deadlines()
        self.assertEqual(self.get_report("attention")["count"], 1)
        self.assertTrue(BossCheckState.objects.filter(failed=True).exists())
        self.assertEqual(self.get_report("attention")["automation"]["state"], "disabled")

    def test_priority_scope_and_old_cases_ignore_report_dates(self):
        now = timezone.now()
        for i, severity in enumerate(["HIGH", "CRITICAL", "INFO", "WARNING"]):
            AlertCase.objects.create(
                dedup_key=f"focus:{i}",
                alert_type="TEST",
                source_type="TEST",
                source_id=str(i),
                owner=self.owner,
                warehouse=self.warehouse,
                severity=severity,
                title=severity,
                first_seen_at=now - timedelta(days=10),
                last_seen_at=now,
                due_at=now - timedelta(hours=1) if severity in {"WARNING", "INFO"} else None,
            )
        data = self.get_report("attention", date_from="2020-01-01", date_to="2020-01-01")
        self.assertEqual(
            [r["severity"] for r in data["results"]], ["CRITICAL", "HIGH", "WARNING", "INFO"]
        )
        digest = self.get_report("digest")
        response = self.client.post(
            "/api/reports/boss/digest/", {"token": digest["token"]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_report("attention")["count"], 4)
        self.assertFalse(AlertCase.objects.exclude(status="OPEN").exists())
        self.assertEqual(
            self.client.get(
                "/api/reports/boss/attention/", {"warehouse": self.other_warehouse.pk}
            ).status_code,
            403,
        )

    def test_digest_confirm_is_explicit_signed_monotonic_and_scoped(self):
        self.checkout()
        data = self.get_report("digest")
        self.assertEqual(next(r for r in data["results"] if r["key"] == "CHECKOUT")["count"], 1)
        self.assertFalse(BossReadProgress.objects.exists())
        self.assertEqual(
            self.client.post(
                "/api/reports/boss/digest/", {"token": "bad"}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/reports/boss/digest/", {"token": data["token"]}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(sum(r["count"] for r in self.get_report("digest")["results"]), 0)
        self.assertEqual(self.get_report("attention")["count"], 0)
        self.assertEqual(
            self.client.post(
                f"/api/reports/boss/digest/?owner={self.owner.pk}",
                {"token": data["token"]},
                format="json",
            ).status_code,
            400,
        )
        details = self.get_report(
            "digest/details", token=data["token"], category="pos", action="CHECKOUT"
        )
        self.assertEqual(len(details["results"]), 1)
        self.checkout(key="after-confirm")
        again = self.get_report("digest")
        self.assertEqual(next(r for r in again["results"] if r["key"] == "CHECKOUT")["count"], 1)
        self.client.post("/api/reports/boss/digest/", {"token": again["token"]}, format="json")
        self.client.post("/api/reports/boss/digest/", {"token": data["token"]}, format="json")
        self.assertEqual(sum(r["count"] for r in self.get_report("digest")["results"]), 0)

    def test_multi_year_day_pages_and_explicit_granularity(self):
        data = self.get_report(
            "periods", date_from="2020-01-01", date_to="2025-12-31", granularity="day", page_size=2
        )
        self.assertEqual(data["count"], 2192)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["results"][0]["date"], "2020-01-01")
        page = self.get_report(
            "periods",
            date_from="2020-01-01",
            date_to="2025-12-31",
            granularity="day",
            page_size=2,
            page=2,
        )
        self.assertEqual(page["results"][0]["date"], "2020-01-03")
        auto = self.get_report("periods", date_from="2020-01-01", date_to="2025-12-31")
        self.assertEqual(auto["granularity"], "year")
        self.assertEqual(auto["count"], 6)
        self.assertEqual(auto["coverage"]["completeness"], "UNKNOWN")
        sla = self.get_report("sla/details")
        self.assertIsNone(sla["meta"]["data_as_of"])
        self.assertEqual(sla["meta"]["source"], "PROCESSED_REPORTS")
        weeks = self.get_report(
            "periods", date_from="2024-12-31", date_to="2025-01-01", granularity="week"
        )
        self.assertEqual(weeks["count"], 1)
        self.assertEqual(weeks["results"][0]["date_from"], "2024-12-31")

    def test_period_counts_match_drilldown_and_pos_scope(self):
        self.checkout(multi=True)
        data = self.get_report("periods/summary", owner=self.owner.pk)
        self.assertEqual(next(r for r in data["results"] if r["key"] == "pos_sales")["count"], 1)
        self.assertEqual(
            next(r for r in data["results"] if r["key"] == "pos_sales")["money"],
            [{"currency": "UNKNOWN", "amount": 20}],
        )
        rows = self.get_report("periods/details", metric="pos_sales", owner=self.owner.pk)
        self.assertEqual(rows["count"], 1)
        self.assertNotIn(self.other_product.name, str(rows))
        trend = self.get_report("periods", owner=self.owner.pk)
        self.assertEqual(trend["results"][0]["metrics"]["pos_sales"]["count"], 1)
        self.assertEqual(self.client.post("/api/reports/boss/attention/", {}).status_code, 405)

    def test_outbound_partial_shipment_recovers_only_when_complete(self):
        from allapp.outbound.models import OutboundOrder, OutboundOrderLine
        from allapp.tasking.models import WmsTaskLine

        order = OutboundOrder.objects.create(
            order_no="FOCUS-ORDER",
            customer=self.customer,
            owner=self.owner,
            warehouse=self.warehouse,
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
            etd=timezone.now() - timedelta(hours=2),
        )
        line = OutboundOrderLine.objects.create(
            order=order, line_no=1, product=self.product, base_qty=2, base_uom=self.uom
        )
        task = WmsTask.objects.create(
            task_no="FOCUS-DISPATCH",
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="DISPATCH",
            status="COMPLETED",
            review_status="PENDING",
        )
        work = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            qty_plan=2,
            qty_done=1,
            src_model="OutboundOrderLine",
            src_id=line.pk,
        )
        check_deadlines()
        self.assertTrue(
            AlertCase.objects.filter(alert_type="DEADLINE_OUTBOUND", status="OPEN").exists()
        )
        work.qty_done = 2
        work.save(update_fields=["qty_done"])
        check_deadlines()
        self.assertFalse(
            AlertCase.objects.filter(alert_type="DEADLINE_OUTBOUND", status="OPEN").exists()
        )

    def test_digest_rejects_changed_authorization_and_other_user(self):
        from allapp.accounts.models import UserRoleScope

        data = self.get_report("digest")
        UserRoleScope.objects.create(
            user=self.boss, role="warehouse_boss", warehouse=self.other_warehouse
        )
        response = self.client.post(
            "/api/reports/boss/digest/", {"token": data["token"]}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(self.cashier)
        self.assertEqual(
            self.client.post(
                "/api/reports/boss/digest/", {"token": data["token"]}, format="json"
            ).status_code,
            403,
        )
        from django.contrib.auth import get_user_model

        other_boss = get_user_model().objects.create_user(username="focus-other-boss")
        other_boss.groups.set(self.boss.groups.all())
        UserRoleScope.objects.create(
            user=other_boss, role="warehouse_boss", warehouse=self.warehouse
        )
        self.client.force_authenticate(other_boss)
        self.assertEqual(
            self.client.post(
                "/api/reports/boss/digest/", {"token": data["token"]}, format="json"
            ).status_code,
            400,
        )
        self.assertFalse(BossReadProgress.objects.filter(user=other_boss).exists())

    def test_mysql_lock_prevents_overlapping_checks(self):
        from django.db import connections

        other = connections["default"].copy(alias="boss-lock-test")
        try:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT GET_LOCK(%s, 0)", ["boss-deadlines:" + other.settings_dict["NAME"]]
                )
                self.assertEqual(cursor.fetchone()[0], 1)
            self.assertEqual(check_deadlines(), {"busy": True})
        finally:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT RELEASE_LOCK(%s)", ["boss-deadlines:" + other.settings_dict["NAME"]]
                )
            other.close()

    def test_receipt_reversal_nets_zero_and_money_keeps_currency(self):
        from allapp.billing.models import Bill, BillingPeriod, PaymentAllocation, PaymentReceipt
        from allapp.billing.services.receivables import post_receipt, reverse_receipt

        receipt = PaymentReceipt.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            receipt_no="FOCUS-PAY",
            currency="USD",
            amount="125",
            receipt_date=timezone.now().date(),
        )
        today = timezone.now().date()
        period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="FOCUS-PERIOD",
            start_date=today.replace(day=1),
            end_date=today,
            currency="USD",
            status="INVOICED",
        )
        bill = Bill.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=period,
            invoice_no="FOCUS-INVOICE",
            issue_date=today,
            due_date=today,
            currency="USD",
            subtotal=125,
            tax_total=0,
            total=125,
            status="ISSUED",
            document_status="ISSUED",
        )
        PaymentAllocation.objects.create(receipt=receipt, bill=bill, amount=125)
        post_receipt(receipt.pk, by_user=self.cashier)
        reverse_receipt(receipt.pk, receipt_no="FOCUS-REV", by_user=self.cashier)
        row = next(
            r for r in self.get_report("periods/summary")["results"] if r["key"] == "receipts"
        )
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["money"], [{"currency": "USD", "amount": 0}])

    def test_long_range_home_operations_and_billing_do_not_reject_dates(self):
        params = {"date_from": "2000-01-01", "date_to": str(timezone.now().date())}
        self.get_report("home", **params)
        self.get_report("operations", **params)
        response = self.client.get("/api/billing/dashboard/warehouse-overview/", params)
        self.assertEqual(response.status_code, 200, response.data)

    def test_pos_stock_groups_drill_into_authorized_underlying_posting(self):
        self.checkout(multi=True)
        groups = self.get_report("periods/details", metric="inventory", owner=self.owner.pk)
        self.assertEqual(groups["count"], 1)
        row = groups["results"][0]
        values = {f["label"]: f["value"] for f in row["fields"]}
        self.assertEqual(values["本期实际净增减"], -2)
        stock = self.get_report(
            "periods/details",
            metric="inventory",
            owner=self.owner.pk,
            product=self.product.pk,
            unit=self.uom.code,
        )
        self.assertEqual(stock["count"], 1)
        detail = self.get_report(
            f"activities/inventory/{stock['results'][0]['id']}", owner=self.owner.pk
        )
        self.assertEqual(detail["sections"]["inventory"]["count"], 1)
        self.assertNotIn(self.other_product.name, str(detail))

    def test_inbound_prefixed_line_source_and_posting_are_required_for_recovery(self):
        from allapp.baseinfo.models import Supplier
        from allapp.inbound.models import InboundOrder, InboundOrderLine
        from allapp.tasking.models import WmsTaskLine

        order = InboundOrder.objects.create(
            order_no="FOCUS-RECEIVE",
            supplier=Supplier.objects.create(owner=self.owner, name="Focus supplier"),
            owner=self.owner,
            warehouse=self.warehouse,
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
            eta=timezone.now() - timedelta(hours=2),
        )
        line = InboundOrderLine.objects.create(
            order=order,
            line_no=1,
            product=self.product,
            base_qty=2,
            base_uom=self.uom.code,
        )
        task = WmsTask.objects.create(
            task_no="FOCUS-RECEIVE-TASK",
            owner=self.owner,
            warehouse=self.warehouse,
            task_type="RECEIVE",
            status="COMPLETED",
            review_status="APPROVED",
            posting_status="PENDING",
            source_model="inbound.InboundOrder",
            source_pk=str(order.pk),
        )
        WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            qty_plan=2,
            qty_done=2,
            src_model="inbound.InboundOrderLine",
            src_id=line.pk,
        )
        check_deadlines()
        self.assertTrue(
            AlertCase.objects.filter(alert_type="DEADLINE_INBOUND", status="OPEN").exists()
        )
        task.posting_status = "POSTED"
        task.save(update_fields=["posting_status"])
        check_deadlines()
        self.assertFalse(
            AlertCase.objects.filter(alert_type="DEADLINE_INBOUND", status="OPEN").exists()
        )
        detail = self.get_report(f"activities/inbound_document/{order.pk}")
        row = detail["sections"]["lines"]["results"][0]
        self.assertEqual(next(f["value"] for f in row["fields"] if f["label"] == "剩余待完成"), 0)

    def test_deadline_boundary_drafts_cancelled_and_missing_deadline_details(self):
        from allapp.reports.boss_attention import overdue_sources

        now = timezone.now()
        task = self.task(planned_end=now)
        self.assertEqual(list(overdue_sources("DEADLINE_TASK", now)), [])
        self.assertEqual(
            len(list(overdue_sources("DEADLINE_TASK", now + timedelta(microseconds=1)))), 1
        )
        for status in ("DRAFT", "CANCELLED", "COMPLETED"):
            task.status = status
            task.review_status = "PENDING" if status == "COMPLETED" else "NONE"
            task.save(update_fields=["status", "review_status"])
            self.assertEqual(list(overdue_sources("DEADLINE_TASK", now + timedelta(seconds=1))), [])
        task.status = "RELEASED"
        task.review_status = "NONE"
        task.planned_end = None
        task.save(update_fields=["status", "review_status", "planned_end"])
        detail = self.get_report(f"tasks/{task.pk}")
        values = {f["label"]: f["value"] for f in detail["header"]["fields"]}
        self.assertEqual(values["计划完成"], "未设期限")
        self.assertIn("自创建起", values["未完成时长"])

    def test_partial_timeout_scan_rolls_back_new_cases_and_does_not_recover_old_ones(self):
        from allapp.reports import boss_attention

        old = self.task(planned_end=timezone.now() - timedelta(hours=1))
        check_deadlines()
        old.status = "COMPLETED"
        old.review_status = "PENDING"
        old.save(update_fields=["status", "review_status"])
        new = WmsTask.objects.create(
            task_no="FOCUS-FAIL-NEW",
            task_type="PICK",
            owner=self.owner,
            warehouse=self.warehouse,
            status="RELEASED",
            planned_end=timezone.now() - timedelta(hours=1),
        )
        original = boss_attention.overdue_sources

        def interrupted(rule, now):
            for row in original(rule, now):
                yield row
                if rule == "DEADLINE_TASK":
                    raise RuntimeError("source failed after one row")

        with patch("allapp.reports.boss_attention.overdue_sources", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                check_deadlines()
        self.assertTrue(
            AlertCase.objects.filter(
                alert_type="DEADLINE_TASK", source_id=str(old.pk), status="OPEN"
            ).exists()
        )
        self.assertFalse(
            AlertCase.objects.filter(alert_type="DEADLINE_TASK", source_id=str(new.pk)).exists()
        )
