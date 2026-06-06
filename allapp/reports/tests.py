import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from rest_framework.test import APIClient

from allapp.baseinfo.models import Customer, Owner, Supplier
from allapp.billing.enums import AccrualStatus, BillStatus, CalcMethod, ChargeType
from allapp.billing.models import Bill, BillingAccrual, BillingJobRun, BillingPeriod, BillingRule
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryDetail, InventorySummary, ReviewDifference
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.products.models import Product, ProductUom
from allapp.reports.models import ReportSnapshot
from allapp.tasking.models import WmsTask


def _current_test_date(now=None):
    current = now or timezone.now()
    if timezone.is_naive(current):
        return current.date()
    return timezone.localtime(current).date()


class ReportsWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Report", code="OWN-RPT")
        self.user = get_user_model().objects.create_user(username="report-user", password="x")

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


class BossDashboardApiTests(TestCase):
    def setUp(self):
        self.today = _current_test_date()
        self.month_start = self.today.replace(day=1)

        self.owner = Owner.objects.create(name="Owner Boss A", code="OWBSA")
        self.other_owner = Owner.objects.create(name="Owner Boss B", code="OWBSB")
        self.warehouse = Warehouse.objects.create(code="WHBOSS1", name="Warehouse Boss 1")
        self.other_warehouse = Warehouse.objects.create(code="WHBOSS2", name="Warehouse Boss 2")

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
        Location.objects.filter(pk=self.location_2.pk).update(max_volume_m3=Decimal("2.000"))
        self.location_2.max_volume_m3 = Decimal("2.000")
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWBOS2-01-01-01",
            name="Boss Other Location",
            max_volume_m3=Decimal("7.000"),
        )

        self.uom_a = ProductUom.objects.create(code="PCS-BOSA", name="件-A", is_active=True)
        self.uom_b = ProductUom.objects.create(code="PCS-BOSB", name="件-B", is_active=True)
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
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="CUST-BOSS-A",
            name="Boss Customer A",
        )
        self.supplier = Supplier.objects.create(owner=self.owner, code="SUP-BOSS-A", name="Boss Supplier A")

        InboundOrder.objects.create(
            owner=self.owner,
            supplier=self.supplier,
            warehouse=self.warehouse,
            order_no="INB-BOSS-1",
            biz_date=self.today,
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
        )
        OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            order_no="OUT-BOSS-1",
            biz_date=self.today,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
            created_by=self.user,
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
        self.assertEqual(response.data["scope"]["warehouse"], self.warehouse.id)
        self.assertEqual(response.data["summary"]["today_inbound_orders"], 1)
        self.assertEqual(response.data["summary"]["today_outbound_orders"], 1)
        self.assertEqual(Decimal(str(response.data["summary"]["today_accrual_total"])), Decimal("165.00"))
        self.assertEqual(Decimal(str(response.data["summary"]["overdue_receivable_total"])), Decimal("110.00"))
        self.assertEqual(response.data["summary"]["open_alert_count"], 6)
        self.assertEqual(len(response.data["owner_options"]), 2)
        self.assertEqual(response.data["rankings"]["revenue_top_owners"][0]["owner"], self.owner.id)
        attention_keys = [item["key"] for item in response.data["attention_items"]]
        self.assertIn("overdue_tasks", attention_keys)

    def test_boss_alert_api_respects_owner_filter(self):
        response = self.client.get("/api/reports/boss/alerts/", {"owner": self.owner.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["sections"]["overdue_tasks"]["count"], 1)
        self.assertEqual(response.data["sections"]["pending_review_tasks"]["count"], 1)
        self.assertEqual(response.data["sections"]["overdue_bills"]["count"], 1)
        self.assertEqual(response.data["sections"]["failed_billing_jobs"]["count"], 1)
        self.assertEqual(response.data["sections"]["expiring_inventory"]["count"], 0)
        self.assertEqual(response.data["sections"]["review_differences"]["count"], 0)
        self.assertEqual(response.data["summary"]["high_risk_items"], 3)

    def test_boss_inventory_api_returns_expiring_stale_and_hot_cold_locations(self):
        response = self.client.get("/api/reports/boss/inventory/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["warehouse"], self.warehouse.id)
        self.assertEqual(len(response.data["owner_options"]), 2)
        self.assertEqual(Decimal(str(response.data["summary"]["expiring_qty_7d"])), Decimal("8.0000"))
        self.assertEqual(Decimal(str(response.data["summary"]["stale_qty_30d"])), Decimal("5.0000"))
        self.assertEqual(response.data["summary"]["sku_count"], 2)
        self.assertEqual(response.data["summary"]["owner_count"], 2)
        self.assertEqual(response.data["summary"]["hot_location_count"], 1)
        self.assertEqual(response.data["summary"]["cold_location_count"], 1)
        self.assertEqual(response.data["owner_rankings"][0]["owner"], self.owner.id)
        self.assertEqual(response.data["expiring_items"][0]["location_code"], self.location_2.code)
        self.assertEqual(response.data["stale_items"][0]["location_code"], self.location.code)
        self.assertEqual(response.data["high_heat_locations"][0]["location_code"], self.location_2.code)
        self.assertEqual(response.data["cold_locations"][0]["location_code"], self.location.code)

    def test_boss_inventory_api_respects_owner_filter(self):
        response = self.client.get("/api/reports/boss/inventory/", {"owner": self.owner.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.owner.id)
        self.assertEqual(response.data["summary"]["owner_count"], 1)
        self.assertEqual(Decimal(str(response.data["summary"]["expiring_qty_7d"])), Decimal("0.0000"))
        self.assertEqual(Decimal(str(response.data["summary"]["stale_qty_30d"])), Decimal("5.0000"))
        self.assertEqual(response.data["summary"]["hot_location_count"], 0)
        self.assertEqual(response.data["summary"]["cold_location_count"], 1)
        self.assertEqual(len(response.data["owner_rankings"]), 1)

    def test_boss_inventory_api_falls_back_to_inventory_summary_when_detail_missing(self):
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
        self.assertEqual(Decimal(str(response.data["summary"]["current_onhand_qty"])), Decimal("13.0000"))
        self.assertEqual(Decimal(str(response.data["summary"]["current_available_qty"])), Decimal("12.0000"))
        self.assertEqual(response.data["summary"]["owner_count"], 2)
        self.assertEqual(len(response.data["owner_rankings"]), 2)
        self.assertEqual(response.data["owner_rankings"][0]["owner"], self.owner.id)

    def test_boss_home_api_falls_back_to_inventory_summary_when_detail_missing(self):
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
        self.assertEqual(Decimal(str(response.data["summary"]["current_onhand_qty"])), Decimal("13.0000"))
        self.assertEqual(Decimal(str(response.data["summary"]["current_available_qty"])), Decimal("12.0000"))
        self.assertEqual(len(response.data["rankings"]["inventory_top_owners"]), 2)

    def test_boss_pages_use_warehouse_scope_even_when_user_is_bound_to_owner(self):
        scoped_user = get_user_model().objects.create_user(
            username="boss-dashboard-owner-bound",
            password="x",
            owner=self.owner,
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

    def test_boss_inventory_owner_filter_allows_other_owner_for_owner_bound_warehouse_user(self):
        scoped_user = get_user_model().objects.create_user(
            username="boss-dashboard-owner-bound-filter",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        client = APIClient()
        client.force_authenticate(scoped_user)

        response = client.get("/api/reports/boss/inventory/", {"owner": self.other_owner.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"]["owner"], self.other_owner.id)
        self.assertEqual(response.data["summary"]["owner_count"], 1)
        self.assertEqual(len(response.data["owner_options"]), 2)


class PdaThroughputApiTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner PDA Report", code="OWPDA")
        self.warehouse = Warehouse.objects.create(code="WHPDA1", name="Warehouse PDA 1")
        self.other_warehouse = Warehouse.objects.create(code="WHPDA2", name="Warehouse PDA 2")
        self.uom = ProductUom.objects.create(code="PCS-PDA", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-PDA-RPT",
            name="PDA Report Product",
            sku="SKU-PDA-RPT",
            base_uom=self.uom,
            price=Decimal("10.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="pda-report-user",
            password="x",
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

        outbound_order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            order_no="OUT-PDA-RPT-1",
            biz_date=datetime.date(2026, 5, 6),
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
        )
        OutboundOrderLine.objects.create(
            order=outbound_order,
            product=self.product,
            base_qty=Decimal("4.000"),
            base_price=Decimal("2.0000"),
            line_no=10,
        )

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
        client = APIClient()
        client.force_authenticate(owner_bound_user)

        response = client.get(
            "/api/reports/pda/throughput/",
            {"mode": "month", "month": "2026-05"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["scope"]["owner"])
        self.assertEqual(response.data["scope"]["warehouse"], self.warehouse.id)
