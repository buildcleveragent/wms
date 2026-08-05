import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from allapp.accounts.models import UserRoleScope
from allapp.accounts.roles import ROLE_GROUP_TEMPLATES
from allapp.baseinfo.models import Customer, Owner, OwnerWarehouseBinding, Supplier
from allapp.billing.enums import AccrualStatus, BillStatus, CalcMethod, ChargeType
from allapp.billing.models import (
    Bill,
    BillingAccrual,
    BillingJobRun,
    BillingPeriod,
    BillingRule,
)
from allapp.billing.services import unlock_period
from allapp.core.choices import InvTxType
from allapp.inbound.constants import (
    PDA_NO_ORDER_RECEIVE_SOURCE_APP,
    PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
)
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import (
    InventoryDetail,
    InventorySnapshotDaily,
    InventorySummary,
    InventoryTransaction,
    ReviewDifference,
)
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product, ProductUom
from allapp.reports.models import ReportSnapshot
from allapp.tasking.models import WmsTask


def _assign_report_role(user, role, *, owner=None, warehouse=None):
    user.groups.add(Group.objects.get(name=ROLE_GROUP_TEMPLATES[role].group_name))
    return UserRoleScope.objects.create(
        user=user,
        role=role,
        owner=owner if role in UserRoleScope.OWNER_ROLES else None,
        warehouse=warehouse if role in UserRoleScope.WAREHOUSE_ROLES else None,
    )


def _current_test_date(now=None):
    current = now or timezone.now()
    if timezone.is_naive(current):
        return current.date()
    return timezone.localtime(current).date()


def _currency_total(groups, currency="CNY"):
    row = next((item for item in groups if item["currency"] == currency), None)
    return Decimal(str(row["total"])) if row else Decimal("0.00")


class ReportsWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Report", code="OWN-RPT")
        self.warehouse = Warehouse.objects.create(
            code="WH-RPT", name="Warehouse Report"
        )
        self.user = get_user_model().objects.create_user(
            username="report-user", password="x"
        )
        self.staff_user = get_user_model().objects.create_superuser(
            username="report-staff",
            email="report-staff@example.com",
            password="x",
        )

    def test_report_snapshot_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            ReportSnapshot.objects.create(
                owner=self.owner,
                src_model="ReportSource",
                src_id=1,
                doc_type="DISPATCH_NOTE",
                payload={"header": {}, "items": []},
                fp="report-snapshot-no-warehouse",
                created_by=self.user,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_dispatch_note_html_renders_task_lines_for_staff_user(self):
        uom = ProductUom.objects.create(code="PCS-RPT", name="件", is_active=True)
        product = Product.objects.create(
            owner=self.owner,
            code="SKU-RPT-DISP",
            name="配送单商品",
            sku="SKU-RPT-DISP",
            base_uom=uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="DISPATCH-RPT-1",
            task_type=WmsTask.TaskType.DISPATCH,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        task.lines.create(
            product=product, qty_plan=Decimal("2.000"), qty_done=Decimal("2.000")
        )
        self.client.force_login(self.staff_user)

        response = self.client.get(f"/reports/dispatch/{task.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner Report")
        self.assertContains(response, "配送单商品")

    def test_dispatch_note_html_returns_404_for_missing_task(self):
        self.client.force_login(self.staff_user)

        response = self.client.get("/reports/dispatch/999999/")

        self.assertEqual(response.status_code, 404)


class BossDashboardApiTests(TestCase):
    def setUp(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        self.today = _current_test_date()
        self.month_start = self.today.replace(day=1)

        self.owner = Owner.objects.create(name="Owner Boss A", code="OWBSA")
        self.other_owner = Owner.objects.create(name="Owner Boss B", code="OWBSB")
        self.warehouse = Warehouse.objects.create(
            code="WHBOSS1", name="Warehouse Boss 1"
        )
        self.other_warehouse = Warehouse.objects.create(
            code="WHBOSS2", name="Warehouse Boss 2"
        )
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.warehouse)
        OwnerWarehouseBinding.objects.create(
            owner=self.other_owner, warehouse=self.warehouse
        )

        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWBOS1",
            name="Boss Subwarehouse 1",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="SWBOS2",
            name="Boss Subwarehouse 2",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWBOS1-01-01-01",
            name="Boss Location 1",
            max_volume_m3=Decimal("10.000"),
        )
        self.location_2 = Location.objects.create(
            warehouse=self.warehouse,
            code="SWBOS1-01-01-02",
            name="Boss Location 2",
            max_volume_m3=Decimal("5.000"),
        )
        Location.objects.filter(pk=self.location_2.pk).update(
            max_volume_m3=Decimal("2.000")
        )
        self.location_2.max_volume_m3 = Decimal("2.000")
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWBOS2-01-01-01",
            name="Boss Other Location",
            max_volume_m3=Decimal("7.000"),
        )

        self.uom_a = ProductUom.objects.create(
            code="PCS-BOSA", name="件-A", is_active=True
        )
        self.uom_b = ProductUom.objects.create(
            code="PCS-BOSB", name="件-B", is_active=True
        )
        self.product_a = Product.objects.create(
            owner=self.owner,
            code="SKU-BOSS-A",
            name="Boss Product A",
            sku="SKU-BOSS-A",
            base_uom=self.uom_a,
            volume=Decimal("0.500000"),
            price=Decimal("10.00"),
        )
        self.product_b = Product.objects.create(
            owner=self.other_owner,
            code="SKU-BOSS-B",
            name="Boss Product B",
            sku="SKU-BOSS-B",
            base_uom=self.uom_b,
            volume=Decimal("0.250000"),
            price=Decimal("20.00"),
        )

        self.user = get_user_model().objects.create_user(
            username="boss-dashboard-user",
            password="x",
            warehouse=self.warehouse,
        )
        _assign_report_role(
            self.user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUST-BOSS-A",
            name="Boss Customer A",
        )
        self.supplier = Supplier.objects.create(
            owner=self.owner, code="SUP-BOSS-A", name="Boss Supplier A"
        )

        inbound_order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            order_no="INB-BOSS-1",
            biz_date=self.today,
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        inbound_line = InboundOrderLine.objects.create(
            order=inbound_order,
            product=self.product_a,
            base_uom=self.uom_a.code,
            base_qty=Decimal("1.000"),
            base_price=Decimal("1.0000"),
            line_no=10,
        )
        receive_at = timezone.now()
        receive_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-BOSS-RECEIVE-ACTUAL",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
            posted_at=receive_at,
            finished_at=receive_at,
            ref_no=inbound_order.order_no,
            source_app="inbound",
            source_model="InboundOrder",
            source_pk=str(inbound_order.pk),
        )
        receive_task.lines.create(
            product=self.product_a,
            qty_plan=Decimal("1.000"),
            qty_done=Decimal("1.000"),
            status=WmsTask.Status.COMPLETED,
            src_model="InboundOrderLine",
            src_id=inbound_line.pk,
        )
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=self.owner,
            product=self.product_a,
            warehouse=self.warehouse,
            location=self.location,
            qty_delta=Decimal("1.000"),
            src_model="WmsTask",
            src_id=receive_task.pk,
            src_line_id=inbound_line.pk,
            src_no=inbound_order.order_no,
            posted_at=receive_at,
            posting_batch="TASK-BOSS-RECEIVE-ACTUAL",
        )
        outbound_order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            order_no="OUT-BOSS-1",
            biz_date=self.today,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
            created_by=self.user,
        )
        outbound_line = OutboundOrderLine.objects.create(
            order=outbound_order,
            product=self.product_a,
            base_uom=self.uom_a,
            base_qty=Decimal("1.000"),
            base_price=Decimal("1.0000"),
            line_no=10,
        )
        dispatch_at = timezone.now()
        dispatch_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-BOSS-DISPATCH-ACTUAL",
            task_type=WmsTask.TaskType.DISPATCH,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
            posted_at=dispatch_at,
            finished_at=dispatch_at,
            ref_no=outbound_order.order_no,
            source_app="outbound",
            source_model="OutboundOrder",
            source_pk=str(outbound_order.pk),
        )
        dispatch_task.lines.create(
            product=self.product_a,
            qty_plan=Decimal("1.000"),
            qty_done=Decimal("1.000"),
            status=WmsTask.Status.COMPLETED,
            src_model="OutboundOrderLine",
            src_id=outbound_line.pk,
        )

        WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-BOSS-OVERDUE",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
            planned_end=timezone.now() - datetime.timedelta(hours=4),
        )
        WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-BOSS-REVIEW",
            task_type=WmsTask.TaskType.REVIEW,
            status=WmsTask.Status.RELEASED,
        )
        WmsTask.objects.create(
            owner=self.other_owner,
            warehouse=self.warehouse,
            task_no="TASK-BOSS-RECEIVE-DONE",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            task_no="TASK-BOSS-OTHER-WH",
            task_type=WmsTask.TaskType.REVIEW,
            status=WmsTask.Status.RELEASED,
            planned_end=timezone.now() - datetime.timedelta(hours=2),
        )

        self.inventory_a = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product_a,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("1.0000"),
            damaged_qty=Decimal("0.0000"),
        )
        self.inventory_b = InventoryDetail.objects.create(
            owner=self.other_owner,
            product=self.product_b,
            location=self.location_2,
            onhand_qty=Decimal("8.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
            expiry_date=self.today + datetime.timedelta(days=3),
        )
        self.inventory_other = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product_a,
            location=self.other_location,
            onhand_qty=Decimal("99.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
        )
        InventoryDetail.objects.filter(pk=self.inventory_a.pk).update(
            updated_at=timezone.now() - datetime.timedelta(days=45)
        )
        InventoryDetail.objects.filter(pk=self.inventory_b.pk).update(
            updated_at=timezone.now() - datetime.timedelta(days=5)
        )

        self.rule_a = BillingRule.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            charge_type=ChargeType.DISPATCH,
            calc_method=CalcMethod.PER_ORDER,
            unit_price=Decimal("10.00"),
        )
        self.rule_b = BillingRule.objects.create(
            owner=self.other_owner,
            warehouse=self.warehouse,
            charge_type=ChargeType.DISPATCH,
            calc_method=CalcMethod.PER_ORDER,
            unit_price=Decimal("10.00"),
        )
        self.period_a = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label=f"{self.today:%Y%m}-A",
            start_date=self.month_start,
            end_date=self.today,
        )
        self.period_b = BillingPeriod.objects.create(
            owner=self.other_owner,
            warehouse=self.warehouse,
            label=f"{self.today:%Y%m}-B",
            start_date=self.month_start,
            end_date=self.today,
        )
        BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period_a,
            charge_type=ChargeType.DISPATCH,
            rule=self.rule_a,
            service_date=self.today,
            currency="CNY",
            quantity=Decimal("1.0000"),
            unit_price=Decimal("100.0000"),
            amount=Decimal("100.00"),
            tax_amount=Decimal("10.00"),
            status=AccrualStatus.OPEN,
            acc_fingerprint="boss-home-acc-a",
            created_by=self.user,
        )
        BillingAccrual.objects.create(
            owner=self.other_owner,
            warehouse=self.warehouse,
            period=self.period_b,
            charge_type=ChargeType.DISPATCH,
            rule=self.rule_b,
            service_date=self.today,
            currency="CNY",
            quantity=Decimal("1.0000"),
            unit_price=Decimal("50.0000"),
            amount=Decimal("50.00"),
            tax_amount=Decimal("5.00"),
            status=AccrualStatus.OPEN,
            acc_fingerprint="boss-home-acc-b",
            created_by=self.user,
        )
        Bill.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period_a,
            invoice_no="BILL-BOSS-A",
            issue_date=self.today - datetime.timedelta(days=2),
            due_date=self.today - datetime.timedelta(days=1),
            currency="CNY",
            subtotal=Decimal("100.00"),
            tax_total=Decimal("10.00"),
            total=Decimal("110.00"),
            status=BillStatus.ISSUED,
        )
        Bill.objects.create(
            owner=self.other_owner,
            warehouse=self.warehouse,
            period=self.period_b,
            invoice_no="BILL-BOSS-B",
            issue_date=self.today,
            due_date=self.today + datetime.timedelta(days=7),
            currency="CNY",
            subtotal=Decimal("50.00"),
            tax_total=Decimal("5.00"),
            total=Decimal("55.00"),
            status=BillStatus.DRAFT,
        )
        BillingJobRun.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            job_name=BillingJobRun.JobName.DAILY_METRIC_GENERATION,
            service_date=self.today,
            status=BillingJobRun.Status.FAILED,
            started_at=timezone.now() - datetime.timedelta(minutes=5),
            finished_at=timezone.now(),
            message="metric failed",
        )
        ReviewDifference.objects.create(
            order_no="RD-BOSS-1",
            warehouse=self.warehouse,
            status=ReviewDifference.Status.PENDING,
        )
        ReviewDifference.objects.create(
            order_no="RD-BOSS-2",
            warehouse=self.other_warehouse,
            status=ReviewDifference.Status.PENDING,
        )

    def test_boss_home_api_returns_scoped_summary(self):
        response = self.client.get("/api/reports/boss/home/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["mode"], "ALL_AUTHORIZED")
        self.assertIsNone(response.data["scope"]["warehouse"])
        self.assertEqual(response.data["summary"]["today_inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["today_outbound_orders"], 1)
        self.assertEqual(
            _currency_total(response.data["summary"]["accruals_by_currency"]),
            Decimal("165.00"),
        )
        self.assertEqual(
            Decimal(
                str(
                    response.data["summary"]["overdue_receivables_by_currency"][0][
                        "total"
                    ]
                )
            ),
            Decimal("110.00"),
        )
        self.assertGreater(response.data["summary"]["open_alert_count"], 0)
        self.assertEqual(len(response.data["owner_options"]), 2)
        self.assertEqual(
            response.data["rankings"]["revenue_contribution_by_currency"][0]["rows"][0][
                "owner"
            ],
            self.owner.id,
        )
        attention_keys = [item["key"] for item in response.data["attention_items"]]
        self.assertIn("overdue_tasks", attention_keys)

    def test_boss_home_keeps_currencies_and_bill_statuses_separate(self):
        _assign_report_role(
            self.user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.other_warehouse,
        )
        OwnerWarehouseBinding.objects.create(
            owner=self.owner, warehouse=self.other_warehouse
        )
        usd_rule = BillingRule.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            charge_type=ChargeType.DISPATCH,
            calc_method=CalcMethod.PER_ORDER,
            currency="USD",
            unit_price=Decimal("7.00"),
        )
        usd_period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            label=f"{self.today:%Y%m}-USD",
            start_date=self.month_start,
            end_date=self.today,
            currency="USD",
        )
        BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            period=usd_period,
            charge_type=ChargeType.DISPATCH,
            rule=usd_rule,
            service_date=self.today,
            currency="USD",
            quantity=Decimal("1.0000"),
            unit_price=Decimal("7.0000"),
            amount=Decimal("7.00"),
            tax_amount=Decimal("0.70"),
            status=AccrualStatus.OPEN,
            acc_fingerprint="boss-home-usd",
            created_by=self.user,
        )
        Bill.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            period=usd_period,
            invoice_no="BILL-BOSS-USD-DRAFT",
            issue_date=self.today,
            due_date=self.today + datetime.timedelta(days=7),
            currency="USD",
            subtotal=Decimal("7.00"),
            tax_total=Decimal("0.70"),
            total=Decimal("7.70"),
            status=BillStatus.DRAFT,
        )

        response = self.client.get("/api/reports/boss/home/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {
                row["currency"]
                for row in response.data["summary"]["accruals_by_currency"]
            },
            {"CNY", "USD"},
        )
        self.assertEqual(
            {
                row["currency"]
                for row in response.data["summary"]["issued_bills_by_currency"]
            },
            {"CNY"},
        )
        self.assertEqual(
            _currency_total(response.data["summary"]["draft_bills_by_currency"], "USD"),
            Decimal("7.70"),
        )
        self.assertEqual(
            {
                row["currency"]
                for row in response.data["summary"]["overdue_receivables_by_currency"]
            },
            {"CNY"},
        )

    def test_boss_home_uses_signed_reversal_net_and_excludes_positive_void(self):
        original = BillingAccrual.objects.get(acc_fingerprint="boss-home-acc-a")
        original.status = AccrualStatus.INVOICED
        original.save(update_fields=["status"])
        self.period_a.status = "INVOICED"
        self.period_a.save(update_fields=["status"])

        BillingAccrual.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period_a,
            charge_type=ChargeType.DISPATCH,
            rule=self.rule_a,
            service_date=self.today,
            currency="CNY",
            quantity=Decimal("1.0000"),
            unit_price=Decimal("999.0000"),
            amount=Decimal("999.00"),
            tax_amount=Decimal("99.00"),
            status=AccrualStatus.VOID,
            is_reversal=True,
            reversal_of=original,
            acc_fingerprint="boss-home-positive-dedup",
            created_by=self.user,
        )

        unlock_period(self.period_a, by_user=self.user, reason="boss net test")

        response = self.client.get("/api/reports/boss/home/", {"owner": self.owner.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Decimal(str(response.data["summary"]["accruals_by_currency"][0]["total"])),
            Decimal("0.00"),
        )
        self.assertEqual(
            _currency_total(response.data["summary"]["issued_bills_by_currency"]),
            Decimal("0.00"),
        )
        self.assertEqual(
            _currency_total(
                response.data["summary"]["overdue_receivables_by_currency"]
            ),
            Decimal("0.00"),
        )
        today_trend = next(
            row
            for row in response.data["trend_7d"]
            if str(row["date"]) == self.today.isoformat()
        )
        self.assertEqual(
            _currency_total(today_trend["accruals_by_currency"]),
            Decimal("0.00"),
        )
        owner_ranking = next(
            row
            for group in response.data["rankings"]["revenue_contribution_by_currency"]
            for row in group["rows"]
            if row["owner"] == self.owner.id
        )
        self.assertEqual(Decimal(str(owner_ranking["subtotal"])), Decimal("0.00"))
        self.assertEqual(Decimal(str(owner_ranking["tax_total"])), Decimal("0.00"))
        self.assertEqual(Decimal(str(owner_ranking["total"])), Decimal("0.00"))
        self.assertEqual(owner_ranking["accrual_count"], 1)

    def test_paid_bill_is_issued_but_never_overdue_and_missing_due_date_warns(self):
        bill = Bill.objects.get(invoice_no="BILL-BOSS-A")
        Bill.objects.filter(pk=bill.pk).update(
            status=BillStatus.PAID,
            due_date=None,
        )

        home = self.client.get("/api/reports/boss/home/")
        alerts = self.client.get("/api/reports/boss/alerts/")

        self.assertEqual(home.status_code, 200)
        self.assertEqual(
            _currency_total(home.data["summary"]["issued_bills_by_currency"]),
            Decimal("110.00"),
        )
        self.assertEqual(home.data["summary"]["overdue_receivables_by_currency"], [])
        self.assertEqual(alerts.status_code, 200)
        missing = alerts.data["sections"]["bills_missing_due_date"]
        self.assertEqual(missing["count"], 1)
        self.assertEqual(missing["items"][0]["status"], BillStatus.PAID)

    def test_boss_alert_api_respects_owner_filter(self):
        response = self.client.get(
            "/api/reports/boss/alerts/", {"owner": self.owner.id}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["sections"]["overdue_tasks"]["count"], 1)
        self.assertEqual(response.data["sections"]["pending_review_tasks"]["count"], 1)
        self.assertEqual(response.data["sections"]["overdue_bills"]["count"], 1)
        self.assertEqual(response.data["sections"]["failed_billing_jobs"]["count"], 1)
        self.assertEqual(response.data["sections"]["expiring_inventory"]["count"], 0)
        self.assertEqual(response.data["sections"]["review_differences"]["count"], 0)
        self.assertEqual(response.data["summary"]["high_risk_items"], 3)

    def test_alert_pagination_and_read_only_detail_cover_review_difference(self):
        response = self.client.get(
            "/api/reports/boss/alerts/sections/review_differences/",
            {"page": 1, "page_size": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)
        item = response.data["results"][0]
        detail = self.client.get(
            f"/api/reports/boss/alerts/sections/review_differences/review_difference/{item['id']}/"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["detail"]["legacy_owner_unknown"])

    def test_boss_inventory_api_returns_expiring_stale_and_hot_cold_locations(self):
        response = self.client.get("/api/reports/boss/inventory/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["mode"], "ALL_AUTHORIZED")
        self.assertEqual(len(response.data["owner_options"]), 2)
        self.assertEqual(response.data["summary"]["expiring_sku_count_7d"], 1)
        self.assertEqual(response.data["summary"]["stale_sku_count_30d"], 1)
        self.assertEqual(response.data["summary"]["sku_count"], 2)
        self.assertEqual(response.data["summary"]["owner_count"], 2)
        self.assertEqual(
            {row["unit_code"] for row in response.data["summary"]["quantity_by_uom"]},
            {self.uom_a.code, self.uom_b.code},
        )
        self.assertEqual(response.data["summary"]["hot_location_count"], 1)
        self.assertEqual(response.data["summary"]["cold_location_count"], 1)
        self.assertEqual(response.data["owner_rankings"][0]["owner"], self.owner.id)
        self.assertEqual(
            response.data["expiring_items"][0]["location_code"], self.location_2.code
        )
        self.assertEqual(
            response.data["stale_items"][0]["location_code"], self.location.code
        )
        self.assertEqual(
            response.data["high_heat_locations"][0]["location_code"],
            self.location_2.code,
        )
        self.assertEqual(
            response.data["cold_locations"][0]["location_code"], self.location.code
        )

    def test_boss_inventory_detail_uses_global_scope_and_base_unit(self):
        response = self.client.get(
            "/api/reports/boss/inventory/details/",
            {"owner": self.owner.id, "page": 1, "page_size": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["scope"]["mode"], "ALL_AUTHORIZED")
        self.assertEqual(response.data["count"], 1)
        self.assertIsNone(response.data["next_page"])
        self.assertEqual(response.data["results"][0]["base_unit"], self.uom_a.code)
        self.assertEqual(response.data["results"][0]["warehouse_id"], self.warehouse.id)

    def test_historical_inventory_never_falls_back_to_current_inventory(self):
        historical_date = self.today - datetime.timedelta(days=1)
        response = self.client.get(
            "/api/reports/boss/inventory/",
            {
                "date_from": historical_date.isoformat(),
                "date_to": historical_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["meta"]["data_status"], "UNAVAILABLE")
        self.assertEqual(response.data["summary"]["quantity_by_uom"], [])
        self.assertEqual(response.data["summary"]["sku_count"], 0)

    def test_historical_inferred_unit_is_warned_and_excluded_from_unit_totals(self):
        historical_date = self.today - datetime.timedelta(days=1)
        InventorySnapshotDaily.objects.create(
            snapshot_date=historical_date,
            owner=self.owner,
            warehouse=self.warehouse,
            location=self.location,
            product=self.product_a,
            onhand_qty=Decimal("5.0000"),
            available_qty=Decimal("4.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("1.0000"),
            damaged_qty=Decimal("0.0000"),
            base_unit_code=self.uom_a.code,
            base_unit_source=InventorySnapshotDaily.UnitSource.LEGACY_INFERRED,
            snapshot_source=InventorySnapshotDaily.Source.BOOTSTRAP_DETAIL,
        )

        response = self.client.get(
            "/api/reports/boss/inventory/",
            {
                "date_from": historical_date.isoformat(),
                "date_to": historical_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["meta"]["data_status"], "WARNING")
        warning_codes = {row["code"] for row in response.data["meta"]["warnings"]}
        self.assertIn("HISTORICAL_INVENTORY_APPROXIMATE", warning_codes)
        self.assertIn("HISTORICAL_INVENTORY_UNIT_INFERRED", warning_codes)
        self.assertEqual(response.data["summary"]["quantity_by_uom"], [])

    def test_boss_inventory_api_respects_owner_filter(self):
        response = self.client.get(
            "/api/reports/boss/inventory/", {"owner": self.owner.id}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["summary"]["owner_count"], 1)
        self.assertEqual(response.data["summary"]["expiring_sku_count_7d"], 0)
        self.assertEqual(response.data["summary"]["stale_sku_count_30d"], 1)
        self.assertEqual(response.data["summary"]["hot_location_count"], 0)
        self.assertEqual(response.data["summary"]["cold_location_count"], 1)
        self.assertEqual(len(response.data["owner_rankings"]), 1)

    def test_boss_inventory_api_does_not_use_warehouse_less_summary_as_fallback(
        self,
    ):
        InventoryDetail.objects.filter(warehouse=self.warehouse).delete()
        InventorySummary.objects.create(
            owner=self.owner,
            product=self.product_a,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("1.0000"),
            damaged_qty=Decimal("0.0000"),
        )
        InventorySummary.objects.create(
            owner=self.other_owner,
            product=self.product_b,
            onhand_qty=Decimal("8.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
        )

        response = self.client.get("/api/reports/boss/inventory/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["owner_options"]), 2)
        self.assertNotIn("current_onhand_qty", response.data["summary"])
        self.assertEqual(response.data["summary"]["quantity_by_uom"], [])
        self.assertEqual(response.data["summary"]["owner_count"], 0)
        self.assertEqual(response.data["owner_rankings"], [])

    def test_boss_home_api_does_not_use_warehouse_less_summary_as_fallback(self):
        InventoryDetail.objects.filter(warehouse=self.warehouse).delete()
        InventorySummary.objects.create(
            owner=self.owner,
            product=self.product_a,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("1.0000"),
            damaged_qty=Decimal("0.0000"),
        )
        InventorySummary.objects.create(
            owner=self.other_owner,
            product=self.product_b,
            onhand_qty=Decimal("8.0000"),
            allocated_qty=Decimal("0.0000"),
            locked_qty=Decimal("0.0000"),
            damaged_qty=Decimal("0.0000"),
        )

        response = self.client.get("/api/reports/boss/home/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("current_onhand_qty", response.data["summary"])
        self.assertEqual(response.data["summary"]["quantity_by_uom"], [])
        self.assertEqual(response.data["rankings"]["inventory_top_owners"], [])

    def test_boss_pages_use_warehouse_scope_even_when_user_is_bound_to_owner(self):
        scoped_user = get_user_model().objects.create_user(
            username="boss-dashboard-owner-bound",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        _assign_report_role(
            scoped_user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        client = APIClient()
        client.force_authenticate(scoped_user)

        home_response = client.get("/api/reports/boss/home/")
        inventory_response = client.get("/api/reports/boss/inventory/")
        alerts_response = client.get("/api/reports/boss/alerts/")

        self.assertEqual(home_response.status_code, 200)
        self.assertEqual(len(home_response.data["owner_options"]), 2)
        self.assertEqual(len(home_response.data["rankings"]["inventory_top_owners"]), 2)

        self.assertEqual(inventory_response.status_code, 200)
        self.assertEqual(len(inventory_response.data["owner_options"]), 2)
        self.assertEqual(inventory_response.data["summary"]["owner_count"], 2)

        self.assertEqual(alerts_response.status_code, 200)
        self.assertEqual(len(alerts_response.data["owner_options"]), 2)

    def test_boss_inventory_owner_filter_allows_other_owner_for_owner_bound_warehouse_user(
        self,
    ):
        scoped_user = get_user_model().objects.create_user(
            username="boss-dashboard-owner-bound-filter",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        _assign_report_role(
            scoped_user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        client = APIClient()
        client.force_authenticate(scoped_user)

        response = client.get(
            "/api/reports/boss/inventory/", {"owner": self.other_owner.id}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.other_owner.id)
        self.assertEqual(response.data["summary"]["owner_count"], 1)
        self.assertEqual(len(response.data["owner_options"]), 2)

    def test_multi_warehouse_boss_owner_options_fail_closed_without_activity(self):
        """No fact rows must not turn an authorized warehouse list into all owners."""

        empty_warehouse_a = Warehouse.objects.create(
            code="WHBEMPA", name="Boss Empty Warehouse A"
        )
        empty_warehouse_b = Warehouse.objects.create(
            code="WHBEMPB", name="Boss Empty Warehouse B"
        )
        Owner.objects.create(name="Unrelated Owner", code="OWBUNR")
        scoped_user = get_user_model().objects.create_user(
            username="boss-dashboard-empty-multi",
            password="x",
        )
        _assign_report_role(
            scoped_user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=empty_warehouse_a,
        )
        _assign_report_role(
            scoped_user,
            UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=empty_warehouse_b,
        )
        client = APIClient()
        client.force_authenticate(scoped_user)

        response = client.get("/api/reports/boss/home/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["owner_options"], [])


class PdaThroughputApiTests(TestCase):
    def setUp(self):
        call_command("sync_wms_role_groups", stdout=StringIO())
        self.owner = Owner.objects.create(name="Owner PDA Report", code="OWPDA")
        self.other_owner = Owner.objects.create(
            name="Owner PDA Report Other", code="OWPDO"
        )
        self.warehouse = Warehouse.objects.create(code="WHPDA1", name="Warehouse PDA 1")
        self.other_warehouse = Warehouse.objects.create(
            code="WHPDA2", name="Warehouse PDA 2"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWPDA1",
            name="PDA Report Subwarehouse",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="SWPDA2",
            name="Other PDA Report Subwarehouse",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWPDA1-01-01-01",
            name="PDA Report Location",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWPDA2-01-01-01",
            name="Other PDA Report Location",
        )
        self.uom = ProductUom.objects.create(code="PCS-PDA", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-PDA-RPT",
            name="PDA Report Product",
            sku="SKU-PDA-RPT",
            base_uom=self.uom,
            price=Decimal("10.00"),
        )
        self.other_owner_product = Product.objects.create(
            owner=self.other_owner,
            code="SKU-PDA-RPT-OTH",
            name="Other Owner PDA Report Product",
            sku="SKU-PDA-RPT-OTH",
            base_uom=self.uom,
            price=Decimal("12.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="pda-report-user",
            password="x",
            warehouse=self.warehouse,
        )
        _assign_report_role(
            self.user,
            UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUST-PDA-RPT",
            name="PDA Report Customer",
        )
        self.supplier = Supplier.objects.create(
            owner=self.owner,
            code="SUP-PDA-RPT",
            name="PDA Report Supplier",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        inbound_order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            order_no="INB-PDA-RPT-1",
            biz_date=datetime.date(2026, 5, 5),
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        InboundOrderLine.objects.create(
            order=inbound_order,
            product=self.product,
            base_qty=Decimal("10.000"),
            base_price=Decimal("1.0000"),
            line_no=10,
        )
        self._create_receive_tx(
            task_no="RK-PDA-RPT-FORMAL",
            qty=Decimal("10.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
            source_pk=str(inbound_order.pk),
            ref_no=inbound_order.order_no,
        )

        outbound_order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            order_no="OUT-PDA-RPT-1",
            biz_date=datetime.date(2026, 5, 6),
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        outbound_line = OutboundOrderLine.objects.create(
            order=outbound_order,
            product=self.product,
            base_qty=Decimal("4.000"),
            base_price=Decimal("2.0000"),
            line_no=10,
        )
        self._create_dispatch_for_line(outbound_line)

        other_warehouse_inbound = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.other_warehouse,
            order_no="INB-PDA-RPT-OTHER-WH",
            biz_date=datetime.date(2026, 5, 5),
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        InboundOrderLine.objects.create(
            order=other_warehouse_inbound,
            product=self.product,
            base_qty=Decimal("99.000"),
            base_price=Decimal("1.0000"),
            line_no=10,
        )
        self._create_receive_tx(
            task_no="RK-PDA-RPT-FORMAL-OTHER-WH",
            qty=Decimal("99.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
            warehouse=self.other_warehouse,
            location=self.other_location,
            source_pk=str(other_warehouse_inbound.pk),
            ref_no=other_warehouse_inbound.order_no,
        )

        june_inbound = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            order_no="INB-PDA-RPT-JUNE",
            biz_date=datetime.date(2026, 6, 1),
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        InboundOrderLine.objects.create(
            order=june_inbound,
            product=self.product,
            base_qty=Decimal("7.000"),
            base_price=Decimal("1.0000"),
            line_no=10,
        )
        self._create_receive_tx(
            task_no="RK-PDA-RPT-FORMAL-JUNE",
            qty=Decimal("7.000"),
            posted_at=datetime.datetime(2026, 6, 1, 9, 0, 0),
            source_pk=str(june_inbound.pk),
            ref_no=june_inbound.order_no,
        )

    def _create_receive_tx(
        self,
        *,
        task_no,
        qty,
        posted_at,
        owner=None,
        product=None,
        warehouse=None,
        location=None,
        posting_status=None,
        task_type=None,
        source_app="inbound",
        source_model="InboundOrder",
        source_pk="",
        ref_no="",
        posting_note="入库订单收货",
        src_line_id=1,
    ):
        owner = owner or self.owner
        product = product or self.product
        warehouse = warehouse or self.warehouse
        location = location or self.location
        task_type = task_type or WmsTask.TaskType.RECEIVE
        posting_status = posting_status or WmsTask.PostingStatus.POSTED
        task = WmsTask.objects.create(
            owner=owner,
            warehouse=warehouse,
            task_no=task_no,
            task_type=task_type,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=posting_status,
            posted_at=(
                posted_at if posting_status == WmsTask.PostingStatus.POSTED else None
            ),
            ref_no=ref_no,
            source_app=source_app,
            source_model=source_model,
            source_pk=source_pk,
            posting_note=posting_note,
        )
        InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            owner=owner,
            product=product,
            warehouse=warehouse,
            location=location,
            qty_delta=Decimal(str(qty)),
            src_model="WmsTask",
            src_id=task.id,
            src_line_id=src_line_id,
            src_no=task.task_no,
            posted_at=posted_at,
            posting_batch=task.task_no[:40],
        )
        return task

    def _create_pda_no_order_receive_tx(self, **kwargs):
        kwargs.setdefault("source_app", PDA_NO_ORDER_RECEIVE_SOURCE_APP)
        kwargs.setdefault("source_model", PDA_NO_ORDER_RECEIVE_SOURCE_MODEL)
        kwargs.setdefault("posting_note", "PDA无ASN收货")
        return self._create_receive_tx(**kwargs)

    def _create_outbound_line(
        self,
        *,
        order_no,
        qty,
        biz_date,
        owner=None,
        product=None,
        warehouse=None,
    ):
        owner = owner or self.owner
        product = product or self.product
        warehouse = warehouse or self.warehouse
        customer = Customer.objects.create(
            owner=owner,
            salesperson=self.user,
            code=f"CUST-{order_no[-18:]}",
            name=f"Customer {order_no}",
        )
        order = OutboundOrder.objects.create(
            owner=owner,
            customer=customer,
            warehouse=warehouse,
            order_no=order_no,
            biz_date=biz_date,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        line = OutboundOrderLine.objects.create(
            order=order,
            product=product,
            base_qty=Decimal(str(qty)),
            base_price=Decimal("2.0000"),
            line_no=10,
        )
        self._create_dispatch_for_line(line)
        return line

    def _create_dispatch_for_line(self, line):
        finished_at = datetime.datetime.combine(
            line.order.biz_date, datetime.time(hour=15)
        )
        task = WmsTask.objects.create(
            owner=line.order.owner,
            warehouse=line.order.warehouse,
            task_no=f"DSP-{line.order.order_no}",
            task_type=WmsTask.TaskType.DISPATCH,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
            posted_at=finished_at,
            finished_at=finished_at,
            ref_no=line.order.order_no,
            source_app="outbound",
            source_model="OutboundOrder",
            source_pk=str(line.order_id),
        )
        task.lines.create(
            product=line.product,
            qty_plan=line.base_qty,
            qty_done=line.base_qty,
            status=WmsTask.Status.COMPLETED,
            finished_at=finished_at,
            finished_by=self.user,
            src_model="OutboundOrderLine",
            src_id=line.pk,
            plan_meta={"lot_no": line.lot_no or ""},
        )
        return task

    def test_month_throughput_returns_scoped_summary_and_days(self):
        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["warehouse"], self.warehouse.id)
        self.assertEqual(response.data["period"]["start_date"], "2026-05-01")
        self.assertEqual(response.data["period"]["end_date"], "2026-05-31")
        self.assertEqual(response.data["summary"]["inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["inbound_lines"], 1)
        self.assertEqual(response.data["summary"]["inbound_qty"], "10.000")
        self.assertEqual(response.data["summary"]["outbound_orders"], 1)
        self.assertEqual(response.data["summary"]["outbound_qty"], "4.000")

        day_map = {row["date"]: row for row in response.data["days"]}
        self.assertEqual(day_map["2026-05-05"]["inbound_qty"], "10.000")
        self.assertEqual(day_map["2026-05-06"]["outbound_qty"], "4.000")
        self.assertEqual(
            {item["id"] for item in response.data["owner_options"]},
            {self.owner.id},
        )
        self.assertEqual(len(response.data["by_owner"]), 1)
        self.assertEqual(response.data["by_owner"][0]["owner"], self.owner.id)
        self.assertEqual(response.data["by_owner"][0]["inbound_qty"], "10.000")
        self.assertEqual(response.data["by_owner"][0]["outbound_qty"], "4.000")

    def test_unposted_inbound_order_lines_are_not_counted_as_received(self):
        unposted_order = InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            order_no="INB-PDA-RPT-UNPOSTED",
            biz_date=datetime.date(2026, 5, 5),
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        InboundOrderLine.objects.create(
            order=unposted_order,
            product=self.product,
            base_qty=Decimal("11.000"),
            base_price=Decimal("1.0000"),
            line_no=10,
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["inbound_lines"], 1)
        self.assertEqual(response.data["summary"]["inbound_qty"], "10.000")

    def test_month_throughput_includes_pda_no_order_receive(self):
        self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-NO-ORDER",
            qty=Decimal("3.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["inbound_orders"], 2)
        self.assertEqual(response.data["summary"]["inbound_lines"], 2)
        self.assertEqual(response.data["summary"]["inbound_qty"], "13.000")
        day_map = {row["date"]: row for row in response.data["days"]}
        self.assertEqual(day_map["2026-05-05"]["inbound_orders"], 2)
        self.assertEqual(day_map["2026-05-05"]["inbound_lines"], 2)
        self.assertEqual(day_map["2026-05-05"]["inbound_qty"], "13.000")

    def test_month_throughput_returns_owner_options_and_owner_breakdown(self):
        self._create_receive_tx(
            task_no="RK-PDA-RPT-OTHER-OWNER-IN",
            qty=Decimal("5.000"),
            posted_at=datetime.datetime(2026, 5, 7, 10, 0, 0),
            owner=self.other_owner,
            product=self.other_owner_product,
            src_line_id=2,
        )
        self._create_outbound_line(
            order_no="OUT-PDA-RPT-OTHER-OWNER",
            qty=Decimal("2.000"),
            biz_date=datetime.date(2026, 5, 8),
            owner=self.other_owner,
            product=self.other_owner_product,
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["inbound_qty"], "15.000")
        self.assertEqual(response.data["summary"]["outbound_qty"], "6.000")
        self.assertEqual(
            {item["id"] for item in response.data["owner_options"]},
            {self.owner.id, self.other_owner.id},
        )
        owner_rows = {row["owner"]: row for row in response.data["by_owner"]}
        self.assertEqual(set(owner_rows), {self.owner.id, self.other_owner.id})
        self.assertEqual(owner_rows[self.owner.id]["inbound_qty"], "10.000")
        self.assertEqual(owner_rows[self.owner.id]["outbound_qty"], "4.000")
        self.assertEqual(owner_rows[self.other_owner.id]["inbound_qty"], "5.000")
        self.assertEqual(owner_rows[self.other_owner.id]["outbound_qty"], "2.000")

    def test_range_throughput_owner_filter_narrows_owner_breakdown(self):
        self._create_receive_tx(
            task_no="RK-PDA-RPT-RANGE-OTHER-IN",
            qty=Decimal("5.000"),
            posted_at=datetime.datetime(2026, 5, 7, 10, 0, 0),
            owner=self.other_owner,
            product=self.other_owner_product,
            src_line_id=2,
        )
        self._create_outbound_line(
            order_no="OUT-PDA-RPT-RANGE-OTHER",
            qty=Decimal("2.000"),
            biz_date=datetime.date(2026, 5, 8),
            owner=self.other_owner,
            product=self.other_owner_product,
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {
                "mode": "range",
                "start_date": "2026-05-07",
                "end_date": "2026-05-08",
                "owner": self.other_owner.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.other_owner.id)
        self.assertEqual(response.data["summary"]["inbound_qty"], "5.000")
        self.assertEqual(response.data["summary"]["outbound_qty"], "2.000")
        self.assertEqual(
            {item["id"] for item in response.data["owner_options"]},
            {self.owner.id, self.other_owner.id},
        )
        self.assertEqual(len(response.data["by_owner"]), 1)
        self.assertEqual(response.data["by_owner"][0]["owner"], self.other_owner.id)
        day_map = {row["date"]: row for row in response.data["days"]}
        self.assertEqual(day_map["2026-05-07"]["inbound_qty"], "5.000")
        self.assertEqual(day_map["2026-05-08"]["outbound_qty"], "2.000")

    def test_throughput_details_return_inbound_and_outbound_sources(self):
        response = self.client.get(
            "/api/reports/pda/throughput/details/",
            {"mode": "month", "month": "2026-05", "owner": self.owner.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["summary"]["inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["inbound_lines"], 1)
        self.assertEqual(response.data["summary"]["inbound_qty"], "10.000")
        self.assertEqual(response.data["summary"]["outbound_orders"], 1)
        self.assertEqual(response.data["summary"]["outbound_lines"], 1)
        self.assertEqual(response.data["summary"]["outbound_qty"], "4.000")
        self.assertEqual(response.data["summary"]["item_count"], 2)

        inbound = [
            item for item in response.data["items"] if item["kind"] == "inbound"
        ][0]
        outbound = [
            item for item in response.data["items"] if item["kind"] == "outbound"
        ][0]
        self.assertEqual(inbound["source_type"], "收货任务")
        self.assertEqual(inbound["source_no"], "INB-PDA-RPT-1")
        self.assertEqual(inbound["task_no"], "RK-PDA-RPT-FORMAL")
        self.assertEqual(inbound["product_code"], self.product.code)
        self.assertEqual(inbound["location_code"], self.location.code)
        self.assertEqual(inbound["qty"], "10.000")
        self.assertEqual(outbound["source_type"], "出库订单")
        self.assertEqual(outbound["source_no"], "OUT-PDA-RPT-1")
        self.assertEqual(outbound["counterparty_name"], self.customer.name)
        self.assertEqual(outbound["product_code"], self.product.code)
        self.assertEqual(outbound["qty"], "4.000")

    def test_throughput_details_include_pda_no_order_receive_source(self):
        task = self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-NO-ORDER",
            qty=Decimal("3.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
        )

        response = self.client.get(
            "/api/reports/pda/throughput/details/",
            {
                "mode": "month",
                "month": "2026-05",
                "metric": "inbound",
                "owner": self.owner.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["metric"], "inbound")
        self.assertEqual(response.data["summary"]["inbound_orders"], 2)
        self.assertEqual(response.data["summary"]["inbound_lines"], 2)
        self.assertEqual(response.data["summary"]["inbound_qty"], "13.000")
        self.assertEqual(response.data["summary"]["outbound_qty"], "0.000")
        self.assertEqual(response.data["summary"]["item_count"], 2)
        no_order = [
            item for item in response.data["items"] if item["task_no"] == task.task_no
        ][0]
        self.assertEqual(no_order["source_type"], "无订单收货")
        self.assertEqual(no_order["source_no"], task.task_no)
        self.assertEqual(no_order["qty"], "3.000")

    def test_throughput_details_respect_metric_owner_and_date_filters(self):
        self._create_receive_tx(
            task_no="RK-PDA-RPT-DETAIL-OTHER-IN",
            qty=Decimal("5.000"),
            posted_at=datetime.datetime(2026, 5, 7, 10, 0, 0),
            owner=self.other_owner,
            product=self.other_owner_product,
            src_line_id=2,
        )
        self._create_outbound_line(
            order_no="OUT-PDA-RPT-DETAIL-OTHER",
            qty=Decimal("2.000"),
            biz_date=datetime.date(2026, 5, 8),
            owner=self.other_owner,
            product=self.other_owner_product,
        )

        inbound_response = self.client.get(
            "/api/reports/pda/throughput/details/",
            {
                "mode": "range",
                "start_date": "2026-05-07",
                "end_date": "2026-05-08",
                "metric": "inbound",
                "owner": self.other_owner.id,
            },
        )
        outbound_response = self.client.get(
            "/api/reports/pda/throughput/details/",
            {
                "mode": "range",
                "start_date": "2026-05-07",
                "end_date": "2026-05-08",
                "metric": "outbound",
                "owner": self.other_owner.id,
            },
        )

        self.assertEqual(inbound_response.status_code, 200)
        self.assertEqual(inbound_response.data["scope"]["owner"], self.other_owner.id)
        self.assertEqual(inbound_response.data["summary"]["inbound_qty"], "5.000")
        self.assertEqual(inbound_response.data["summary"]["outbound_qty"], "0.000")
        self.assertEqual(inbound_response.data["summary"]["item_count"], 1)
        self.assertEqual(inbound_response.data["items"][0]["kind"], "inbound")
        self.assertEqual(
            inbound_response.data["items"][0]["owner"], self.other_owner.id
        )

        self.assertEqual(outbound_response.status_code, 200)
        self.assertEqual(outbound_response.data["summary"]["inbound_qty"], "0.000")
        self.assertEqual(outbound_response.data["summary"]["outbound_qty"], "2.000")
        self.assertEqual(outbound_response.data["summary"]["item_count"], 1)
        self.assertEqual(outbound_response.data["items"][0]["kind"], "outbound")
        self.assertEqual(
            outbound_response.data["items"][0]["source_no"],
            "OUT-PDA-RPT-DETAIL-OTHER",
        )

    def test_receive_stats_require_posted_receive_task_and_transaction_date(self):
        self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-PENDING",
            qty=Decimal("3.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
            posting_status=WmsTask.PostingStatus.PENDING,
        )
        self._create_receive_tx(
            task_no="RK-PDA-RPT-PUTAWAY",
            qty=Decimal("4.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
            task_type=WmsTask.TaskType.PUTAWAY,
            source_app="tasking",
            source_model="WmsTask",
            src_line_id=2,
        )
        task = self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-NO-TX-DATE",
            qty=Decimal("5.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
            src_line_id=3,
        )
        InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.id).update(
            posted_at=None
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["inbound_lines"], 1)
        self.assertEqual(response.data["summary"]["inbound_qty"], "10.000")

    def test_pda_no_order_receive_respects_warehouse_scope(self):
        self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-OTHER-WH",
            qty=Decimal("3.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
            warehouse=self.other_warehouse,
            location=self.other_location,
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["inbound_lines"], 1)
        self.assertEqual(response.data["summary"]["inbound_qty"], "10.000")

    def test_pda_no_order_receive_respects_owner_filter(self):
        self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-OWNER",
            qty=Decimal("3.000"),
            posted_at=datetime.datetime(2026, 5, 5, 14, 0, 0),
        )
        self._create_pda_no_order_receive_tx(
            task_no="RK-PDA-RPT-OTHER-OWNER",
            qty=Decimal("4.000"),
            posted_at=datetime.datetime(2026, 5, 5, 15, 0, 0),
            owner=self.other_owner,
            product=self.other_owner_product,
            src_line_id=2,
        )

        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05", "owner": self.owner.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["summary"]["inbound_orders"], 2)
        self.assertEqual(response.data["summary"]["inbound_lines"], 2)
        self.assertEqual(response.data["summary"]["inbound_qty"], "13.000")
        self.assertEqual(
            {item["id"] for item in response.data["owner_options"]},
            {self.owner.id, self.other_owner.id},
        )
        self.assertEqual(len(response.data["by_owner"]), 1)
        self.assertEqual(response.data["by_owner"][0]["owner"], self.owner.id)

    def test_range_throughput_filters_dates(self):
        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "range", "start_date": "2026-05-06", "end_date": "2026-06-01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["inbound_qty"], "7.000")
        self.assertEqual(response.data["summary"]["outbound_qty"], "4.000")

    def test_throughput_rejects_other_warehouse_for_scoped_user(self):
        response = self.client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05", "warehouse": self.other_warehouse.id},
        )

        self.assertEqual(response.status_code, 403)

    def test_warehouse_scoped_user_is_not_limited_by_user_owner_without_filter(self):
        owner_bound_user = get_user_model().objects.create_user(
            username="pda-report-owner-bound",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        _assign_report_role(
            owner_bound_user,
            UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        client = APIClient()
        client.force_authenticate(owner_bound_user)

        response = client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["scope"]["owner"])
        self.assertEqual(response.data["scope"]["warehouse"], self.warehouse.id)

    def test_owner_scoped_user_cannot_filter_to_other_owner(self):
        owner_only_user = get_user_model().objects.create_user(
            username="pda-report-owner-only",
            password="x",
            owner=self.owner,
        )
        _assign_report_role(
            owner_only_user,
            UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        client = APIClient()
        client.force_authenticate(owner_only_user)

        response = client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05", "owner": self.other_owner.id},
        )

        self.assertEqual(response.status_code, 403)

        own_response = client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(
            own_response.data["owner_options"],
            [{"id": self.owner.id, "name": self.owner.name}],
        )
        self.assertEqual(
            {row["owner"] for row in own_response.data["by_owner"]}, {self.owner.id}
        )

    def test_throughput_details_reject_other_owner_for_owner_scoped_user(self):
        owner_only_user = get_user_model().objects.create_user(
            username="pda-detail-owner-only",
            password="x",
            owner=self.owner,
        )
        _assign_report_role(
            owner_only_user,
            UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )
        client = APIClient()
        client.force_authenticate(owner_only_user)

        response = client.get(
            "/api/reports/pda/throughput/details/",
            {
                "mode": "month",
                "month": "2026-05",
                "owner": self.other_owner.id,
            },
        )

        self.assertEqual(response.status_code, 403)
