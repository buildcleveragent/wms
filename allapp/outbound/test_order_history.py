"""History API contracts: isolation, immutable text, grouping and pagination."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.products.models import Product, ProductUom

from . import test_assisted_history as fixtures
from .models import OutboundOrder, OutboundOrderLine
from .order_history import OutboundHistoryViewSet
from .views import AssistedOutboundOrderViewSet


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
class OrderHistoryTests(TestCase):
    _operator = fixtures.AssistedOutboundHistoryTests._operator
    _request = fixtures.AssistedOutboundHistoryTests._request
    _order = fixtures.AssistedOutboundHistoryTests._order
    _task = fixtures.AssistedOutboundHistoryTests._task

    def setUp(self):
        fixtures.AssistedOutboundHistoryTests.setUp(self)
        self.operator.user_permissions.add(fixtures._permission("outbound", "view_outboundorder"))

    def call(self, action="list", *, user=None, pk=None, **params):
        request = self.factory.get("/api/outbound/order-history/", params)
        from rest_framework.test import force_authenticate

        force_authenticate(request, user=user or self.operator)
        return OutboundHistoryViewSet.as_view({"get": action})(
            request, **({"pk": pk} if pk else {})
        )

    def assisted(self, action, **params):
        request = self.factory.get("/api/outbound/assisted-orders/", params)
        from rest_framework.test import force_authenticate

        force_authenticate(request, user=self.operator)
        return AssistedOutboundOrderViewSet.as_view({"get": action})(request)

    def test_routes_and_read_only_contract(self):
        for path, action in [
            ("", "list"),
            ("options/", "history_options"),
            ("stats/", "stats"),
            ("1/", "retrieve"),
        ]:
            self.assertEqual(
                resolve(f"/api/outbound/order-history/{path}").func.actions["get"], action
            )
        response = OutboundHistoryViewSet.as_view({"get": "list"})(
            self.factory.post("/api/outbound/order-history/")
        )
        self.assertIn(response.status_code, (401, 403, 405))

    def test_list_stats_options_and_detail_share_strict_scope(self):
        visible = self._order(suffix="visible", processing_mode="STANDARD")
        self._order(suffix="assisted")
        hidden = self._order(suffix="hidden", warehouse=self.other_warehouse)
        self.assertEqual(self.call().data["count"], 2)
        self.assertEqual(self.call("stats").data["summary"]["order_count"], 2)
        self.assertEqual(self.call("retrieve", pk=hidden.pk).status_code, 404)
        self.assertEqual(self.call("retrieve", pk=visible.pk).status_code, 200)
        self.assertEqual(
            [x["id"] for x in self.call("history_options").data["warehouses"]], [self.warehouse.pk]
        )
        self.assertEqual(self.call(warehouse_id=self.other_warehouse.pk).data["count"], 0)
        self.assertEqual(
            self.call("stats", warehouse_id=self.other_warehouse.pk).data["summary"]["order_count"],
            0,
        )
        # Assisted-only operators do not gain ordinary order access through history.
        self.assertEqual(self.call(user=self.shift_operator).data["count"], 1)
        self.assertEqual(
            self.call("retrieve", user=self.shift_operator, pk=visible.pk).status_code, 404
        )

    def test_owner_and_salesperson_scope_and_warehouse_reviewer(self):
        own = self._order(suffix="owned", processing_mode="STANDARD")
        other = self._order(suffix="colleague", processing_mode="STANDARD")
        owner2 = Owner.objects.create(code="HST2", name="Other tenant")
        customer2 = Customer.objects.create(
            owner=owner2, salesperson=self.operator, code="HST2C", name="Other customer"
        )
        OutboundOrder.objects.create(
            owner=owner2, warehouse=self.warehouse, customer=customer2, order_no="HIST-OTHER-OWNER"
        )
        user = get_user_model().objects.create_user(username="history-sales")
        user.user_permissions.add(fixtures._permission("outbound", "view_outboundorder"))
        UserRoleScope.objects.create(
            user=user, role=UserRoleScope.Role.OWNER_SALESPERSON, owner=self.owner
        )
        OutboundOrder.objects.filter(pk=own.pk).update(created_by=user)
        self.assertEqual([x["id"] for x in self.call(user=user).data["results"]], [own.pk])
        self.assertEqual(self.call("stats", user=user).data["summary"]["order_count"], 1)
        self.assertEqual(self.call("retrieve", user=user, pk=other.pk).status_code, 404)
        self.assertEqual(
            [x["id"] for x in self.call("history_options", user=user).data["owners"]],
            [self.owner.pk],
        )
        reviewer = self.shift_operator
        reviewer.user_permissions.remove(
            fixtures._permission("outbound", "process_warehouse_assisted_outbound")
        )
        reviewer.user_permissions.add(
            fixtures._permission("outbound", "approve_outbound_as_wh_manager")
        )
        self._order(suffix="assist-reviewer")
        OutboundOrder.objects.filter(pk=other.pk).update(memo="[POS] test")
        ids = [x["id"] for x in self.call(user=reviewer).data["results"]]
        self.assertIn(own.pk, ids)
        self.assertNotIn(other.pk, ids)
        self.assertEqual(self.call("stats", user=reviewer).data["summary"]["order_count"], len(ids))
        unscoped = get_user_model().objects.create_user(username="history-unscoped")
        unscoped.user_permissions.add(fixtures._permission("outbound", "view_outboundorder"))
        self.assertEqual(self.call(user=unscoped).data["count"], 0)

    def test_snapshot_search_and_detail_survive_product_rename(self):
        order = self._order(suffix="snapshot")
        self._task(order)
        self.product.name = "新大米"
        self.product.model = "新型号"
        self.product.spec = "新规格"
        self.product.save()
        for keyword in ["历史大米", "新大米", "新型号", "新规格", self.product.sku]:
            self.assertEqual(self.call(search=keyword).data["count"], 1, keyword)
            self.assertEqual(self.assisted("history", search=keyword).data["count"], 1, keyword)
            self.assertEqual(self.call("stats", search=keyword).data["summary"]["order_count"], 1)
            self.assertEqual(
                self.assisted("stats", search=keyword).data["summary"]["order_count"], 1
            )
        detail = self.call("retrieve", pk=order.pk).data
        self.assertEqual(detail["lines"][0]["product_name"], "历史大米")
        self.assertNotEqual(detail["lines"][0]["product_spec"], "新规格")
        self.assertEqual(detail["lines"][0]["base_uom_name"], "件")
        ranking = self.call("stats").data["product_rows"][0]
        self.assertEqual(ranking["product_name"], "新大米")
        self.assertEqual(ranking["brand_model"], "新型号")
        self.assertEqual(ranking["product_spec"], "新规格")

    def test_empty_snapshot_fallback_is_read_only_and_unpriced_is_explicit(self):
        order = self._order(suffix="legacy")
        OutboundOrderLine.objects.filter(order=order).update(product_snapshot={}, is_priced=False)
        data = self.call("retrieve", pk=order.pk).data
        self.assertEqual(data["amount_status"], "UNPRICED")
        self.assertFalse(data["lines"][0]["is_priced"])
        self.assertEqual(data["lines"][0]["product_name"], self.product.name)
        self.assertEqual(order.lines.get().product_snapshot, {})

    def test_exact_aggregates_ignore_deleted_lines_multiple_tasks_and_pagination(self):
        first = self._order(suffix="first", processing_mode="STANDARD")
        self._order(suffix="second")
        self._task(first, suffix="a")
        self._task(first, suffix="b")
        unit = ProductUom.objects.create(code="HIST-BOX", name="箱")
        # A later catalogue unit must not merge earlier document units.
        self.product.base_uom = unit
        self.product.save()
        OutboundOrderLine.objects.create(
            order=first,
            product=self.product,
            line_no=20,
            base_uom=unit,
            base_qty=Decimal("2.125"),
            base_price=0,
        )
        self.product.base_uom = self.uom
        self.product.save()
        OutboundOrderLine.objects.create(
            order=first,
            product=self.product,
            line_no=30,
            base_uom=self.uom,
            base_qty=99,
            base_price=0,
            is_deleted=True,
        )
        listed = self.call(page_size=1)
        self.assertEqual(listed.data["count"], 2)
        self.assertIsNotNone(listed.data["next"])
        self.assertEqual(len(self.call(page_size=1, page=2).data["results"]), 1)
        stats = self.call("stats", page_size=1).data
        self.assertEqual(stats["summary"]["order_count"], 2)
        self.assertEqual(stats["summary"]["line_count"], 3)
        self.assertEqual(Decimal(stats["summary"]["total_base_qty"]), Decimal("8.125"))
        self.assertEqual(len(stats["product_rows"]), 2)
        self.assertEqual(stats["product_rows"][0]["order_count"], 2)
        self.assertEqual(stats["daily_rows"][0]["order_count"], 2)
        self.assertEqual(stats["owner_rows"][0]["line_count"], 3)
        for filters in (
            {"processing_mode": "STANDARD"},
            {"outbound_type": "SALES"},
            {"approval_status": "WHS_APPROVED"},
            {"is_closed": "false"},
            {"search": first.order_no},
        ):
            self.assertEqual(
                self.call(**filters).data["count"],
                self.call("stats", **filters).data["summary"]["order_count"],
            )

    def test_grouping_keeps_identical_names_separate_and_merges_renamed_product(self):
        first = self._order(suffix="before")
        self.product.name = "更新大米"
        self.product.save()
        second = self._order(suffix="after")
        twin = Product.objects.create(
            owner=self.owner,
            code="HIST-TWIN",
            sku="HIST-TWIN-SKU",
            name=self.product.name,
            base_uom=self.uom,
            volume=Decimal("0.1"),
        )
        OutboundOrderLine.objects.create(
            order=second, product=twin, line_no=20, base_uom=self.uom, base_qty=1, base_price=0
        )
        for response in (self.call("stats"), self.assisted("stats")):
            rows = response.data["product_rows"]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["product_id"], self.product.pk)
            self.assertEqual(rows[0]["order_count"], 2)
            self.assertEqual(Decimal(rows[0]["total_base_qty"]), Decimal("6"))
        self.assertEqual(first.lines.get().frozen_product_name, "历史大米")

    def test_business_date_bounds_empty_and_parameter_validation(self):
        old = self._order(suffix="old")
        OutboundOrder.objects.filter(pk=old.pk).update(
            biz_date=timezone.now().date() - timedelta(days=40)
        )
        today = timezone.now().date().isoformat()
        self.assertEqual(self.call().data["count"], 0)
        self.assertEqual(self.call("stats").data["summary"]["line_count"], 0)
        self.assertEqual(self.call("stats").data["product_rows"], [])
        self.assertEqual(self.call(start_date=today, end_date=today).data["count"], 0)
        # Assisted time is independent of the business date.
        self.assertEqual(
            self.assisted("history", start_date=today, end_date=today).data["count"], 1
        )
        for params in (
            {"start_date": "2026-02-30"},
            {"start_date": "2026-03-01", "end_date": "2026-02-01"},
            {"owner_id": "bad"},
            {"page": "0"},
            {"page_size": "101"},
            {"processing_mode": "bad"},
            {"is_closed": "bad"},
        ):
            self.assertEqual(self.call(**params).status_code, 400, params)
        self.assertEqual(
            self.call("stats", start_date="2024-01-01", end_date="2025-01-01").status_code, 400
        )
        self.assertEqual(self.assisted("stats", start_date="2026-02-30").status_code, 400)

    def test_assisted_stats_respect_status_and_search_filters(self):
        ready = self._order(suffix="ready")
        self._task(ready, suffix="ready")
        cancelled = self._order(suffix="cancelled")
        OutboundOrder.objects.filter(pk=cancelled.pk).update(approval_status="CANCELLED")
        for status in ("READY_TO_PICK", "CANCELLED"):
            stats = self.assisted("stats", status=status)
            self.assertEqual(
                stats.data["summary"]["order_count"],
                self.assisted("history", status=status).data["count"],
            )
            self.assertEqual(stats.data["summary"]["order_count"], 1)
