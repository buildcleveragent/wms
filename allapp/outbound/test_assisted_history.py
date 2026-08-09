from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.views import AssistedOutboundOrderViewSet
from allapp.products.models import Product, ProductUom
from allapp.tasking.models import WmsTask


def _permission(app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
class AssistedOutboundHistoryTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.warehouse = Warehouse.objects.create(code="HIST-WH", name="History WH")
        self.other_warehouse = Warehouse.objects.create(
            code="HIST-WH-2", name="Other History WH"
        )
        self.operator = self._operator("history-operator", self.warehouse)
        self.shift_operator = self._operator("history-shift", self.warehouse)
        self.other_operator = self._operator("history-other", self.other_warehouse)
        self.owner = Owner.objects.create(
            code="HIST-OWN",
            name="History Owner",
            allow_warehouse_assisted_outbound=False,
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.operator,
            code="HIST-CUST",
            name="History Customer",
        )
        self.uom = ProductUom.objects.create(
            code="HIST-EA", name="件", is_active=True
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="HIST-PRODUCT",
            sku="HIST-SKU",
            name="历史大米",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
        )

    def _operator(self, username, warehouse):
        user = get_user_model().objects.create_user(
            username=username,
            password="x",
            warehouse=warehouse,
        )
        user.user_permissions.add(
            _permission("outbound", "process_warehouse_assisted_outbound"),
            _permission("tasking", "claim_task_as_wh_operator"),
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=warehouse,
        )
        return user

    def _request(self, path, user=None):
        request = self.factory.get(path)
        force_authenticate(request, user=user or self.operator)
        return request

    def _view(self, action_name, path, user=None):
        view = AssistedOutboundOrderViewSet.as_view({"get": action_name})
        return view(self._request(path, user=user))

    def _order(
        self,
        *,
        assisted_by=None,
        warehouse=None,
        processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
        assisted_at=None,
        is_closed=False,
        suffix="1",
    ):
        warehouse = warehouse or self.warehouse
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=warehouse,
            order_no=f"HIST-ORDER-{suffix}",
            submit_status="SUBMITTED",
            approval_status="WHS_APPROVED",
            outbound_type="SALES",
            processing_mode=processing_mode,
            assisted_by=assisted_by or self.operator,
            assisted_at=assisted_at or timezone.now(),
            contact="历史收件人",
            contact_phone="13800000000",
            is_closed=is_closed,
            close_reason="WAREHOUSE_ASSISTED_POSTED" if is_closed else None,
        )
        OutboundOrderLine.objects.create(
            order=order,
            product=self.product,
            base_qty=Decimal("3.000"),
            base_price=Decimal("0"),
            base_uom=self.uom,
            line_no=10,
        )
        return order

    def _task(
        self,
        order,
        *,
        suffix="1",
        owner=None,
        warehouse=None,
        status=WmsTask.Status.RELEASED,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
    ):
        return WmsTask.objects.create(
            owner=owner or order.owner,
            warehouse=warehouse or order.warehouse,
            task_no=f"HIST-TASK-{suffix}",
            task_type=WmsTask.TaskType.PICK,
            status=status,
            review_status=review_status,
            posting_status=posting_status,
            source_app="outbound",
            source_model="outboundorder",
            source_pk=str(order.pk),
            ref_no=order.order_no,
        )

    def test_history_is_shared_within_warehouse_but_strict_across_warehouses(self):
        order = self._order(suffix="shared")
        task = self._task(order, suffix="shared")
        standard = self._order(
            suffix="standard", processing_mode=OutboundOrder.ProcessingMode.STANDARD
        )
        self._task(standard, suffix="standard")

        response = self._view(
            "history", "/api/outbound/assisted-orders/history/?page_size=20", self.shift_operator
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(row["id"], order.id)
        self.assertEqual(row["task"]["id"], task.id)
        self.assertEqual(row["business_status"], "READY_TO_PICK")
        self.assertTrue(row["can_reprint"])

        cross_warehouse = self._view(
            "history", "/api/outbound/assisted-orders/history/", self.other_operator
        )
        self.assertEqual(cross_warehouse.status_code, 200, cross_warehouse.data)
        self.assertEqual(cross_warehouse.data["count"], 0)

        unprivileged = get_user_model().objects.create_user(
            username="history-unprivileged",
            password="x",
            warehouse=self.warehouse,
        )
        denied = self._view(
            "history", "/api/outbound/assisted-orders/history/", unprivileged
        )
        self.assertEqual(denied.status_code, 403, denied.data)

    def test_history_search_filters_and_disabled_owner_options(self):
        recent = self._order(assisted_by=self.shift_operator, suffix="recent")
        self._task(recent, suffix="recent")
        old = self._order(
            suffix="old", assisted_at=timezone.now() - timedelta(days=40)
        )
        self._task(old, suffix="old")

        response = self._view(
            "history",
            (
                "/api/outbound/assisted-orders/history/?search=%E5%A4%A7%E7%B1%B3"
                f"&operator_id={self.shift_operator.id}"
            ),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["id"] for row in response.data["results"]], [recent.id])

        today = timezone.now().date().isoformat()
        dated = self._view(
            "history",
            f"/api/outbound/assisted-orders/history/?start_date={today}&end_date={today}",
        )
        self.assertEqual(dated.status_code, 200, dated.data)
        self.assertEqual([row["id"] for row in dated.data["results"]], [recent.id])

        options = self._view(
            "history_options", "/api/outbound/assisted-orders/history-options/"
        )
        self.assertEqual(options.status_code, 200, options.data)
        self.assertIn(self.owner.id, [row["id"] for row in options.data["owners"]])
        self.assertIn(
            self.shift_operator.id,
            [row["id"] for row in options.data["operators"]],
        )

    def test_inconsistent_task_sources_are_not_printable(self):
        missing = self._order(suffix="missing")
        duplicate = self._order(suffix="duplicate")
        self._task(duplicate, suffix="duplicate-a")
        self._task(duplicate, suffix="duplicate-b")
        wrong_owner = Owner.objects.create(code="HIST-WRONG", name="Wrong owner")
        wrong = self._order(suffix="wrong")
        self._task(wrong, suffix="wrong", owner=wrong_owner)

        response = self._view("history", "/api/outbound/assisted-orders/history/?page_size=20")
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row["id"]: row for row in response.data["results"]}
        for order in (missing, duplicate, wrong):
            self.assertEqual(rows[order.id]["business_status"], "INCONSISTENT")
            self.assertFalse(rows[order.id]["can_reprint"])
            self.assertIsNone(rows[order.id]["task"])

    def test_stats_use_same_status_model_and_exact_totals(self):
        ready = self._order(suffix="ready")
        self._task(ready, suffix="ready")
        completed = self._order(
            suffix="completed", assisted_by=self.shift_operator, is_closed=True
        )
        self._task(
            completed,
            suffix="completed",
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        failed = self._order(suffix="failed")
        self._task(
            failed,
            suffix="failed",
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.FAILED,
        )

        today = timezone.now().date().isoformat()
        response = self._view(
            "stats",
            f"/api/outbound/assisted-orders/stats/?start_date={today}&end_date={today}",
        )
        self.assertEqual(response.status_code, 200, response.data)
        summary = response.data["summary"]
        self.assertEqual(summary["order_count"], 3)
        self.assertEqual(summary["completed_count"], 1)
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["exception_count"], 1)
        self.assertEqual(summary["line_count"], 3)
        self.assertEqual(Decimal(summary["total_base_qty"]), Decimal("9.000"))
        statuses = {row["status"]: row["order_count"] for row in response.data["status_rows"]}
        self.assertEqual(statuses["READY_TO_PICK"], 1)
        self.assertEqual(statuses["COMPLETED"], 1)
        self.assertEqual(statuses["POSTING_FAILED"], 1)
        self.assertEqual(response.data["product_rows"][0]["order_count"], 3)

    def test_stats_without_dates_really_limits_query_to_today(self):
        today_order = self._order(suffix="today-default-period")
        self._task(today_order, suffix="today-default-period")
        old_order = self._order(
            suffix="old-default-period",
            assisted_at=timezone.now() - timedelta(days=2),
        )
        self._task(old_order, suffix="old-default-period")

        response = self._view("stats", "/api/outbound/assisted-orders/stats/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["order_count"], 1)
        today = timezone.now().date().isoformat()
        self.assertEqual(
            response.data["period"],
            {"start_date": today, "end_date": today},
        )

    def test_stats_reject_more_than_366_days(self):
        response = self._view(
            "stats",
            "/api/outbound/assisted-orders/stats/?start_date=2024-01-01&end_date=2025-01-01",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("end_date", response.data)
