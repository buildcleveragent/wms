"""Real MySQL contracts for stock alerts, evidence quality and configuration isolation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import Supplier
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.pos.models import PosSale, PosSaleLine, PosSaleOrder
from allapp.products.models import Product
from allapp.tasking.models import WmsTask

from . import test_owner_detail as fixtures
from .models import (
    InventoryDetail,
    InventoryTransaction,
    OwnerInventoryAlertPolicy,
    ProductInventoryAlertPolicy,
)
from .owner_alert_views import (
    OwnerAlertItemsView,
    OwnerAlertProductSettingsView,
    OwnerAlertProductsView,
    OwnerAlertSettingsView,
    OwnerAlertsView,
)

NOW = datetime(2026, 10, 20, 12)


class OwnerAlertTests(TestCase):
    setUpTestData = classmethod(fixtures.OwnerInventoryDetailTests.setUpTestData.__func__)
    stock = fixtures.OwnerInventoryDetailTests.stock
    tx = fixtures.OwnerInventoryDetailTests.tx
    task = fixtures.OwnerInventoryDetailTests.task

    def setUp(self):
        self.clock = patch("allapp.inventory.owner_alerts.timezone.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def call(self, view=OwnerAlertsView, data=None, user=None, method="get", **kwargs):
        request = getattr(APIRequestFactory(), method)("/", data or {}, format="json")
        force_authenticate(request, user=user or self.user)
        return view.as_view()(request, **kwargs)

    def counts(self):
        response = self.call()
        self.assertEqual(response.status_code, 200, response.data)
        return {x["code"]: x for x in response.data["categories"]}

    def items(self, category, **params):
        response = self.call(OwnerAlertItemsView, {"category": category, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def manager(self):
        user = get_user_model().objects.create_user(username="alert-manager", owner=self.owner)
        UserRoleScope.objects.create(user=user, role="owner_manager", owner=self.owner)
        user.user_permissions.add(Permission.objects.get(codename="access_owner_management"))
        return user

    def minimum(self, value):
        Product.objects.filter(pk=self.product.pk).update(min_stock=value)

    def receipt(self, qty="10", days=100, **kwargs):
        return self.tx(qty, posted_at=NOW - timedelta(days=days), **kwargs)

    def sale(self, qty="-1", days=100, **kwargs):
        order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            order_no=f"AL-{OutboundOrder.objects.count()}",
            customer=self.customer,
            created_by=self.user,
            **kwargs,
        )
        OutboundOrderLine.objects.create(
            order=order, product=self.product, base_qty=abs(Decimal(qty)), base_uom=self.unit
        )
        tx = self.tx(
            qty,
            kind="ISSUE",
            src_model="outbound.OutboundOrder",
            src_id=order.pk,
            posted_at=NOW - timedelta(days=days),
        )
        return order, tx

    def test_defaults_and_read_requests_have_no_writes(self):
        with CaptureQueriesContext(connection) as queries:
            data = self.call().data
            settings = self.call(OwnerAlertSettingsView).data
            self.call(OwnerAlertProductSettingsView, product_id=self.product.pk)
        self.assertEqual(data["settings"]["slow_days"], 90)
        self.assertEqual(settings["expiry_days"], 30)
        self.assertFalse(
            any(
                q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )
        self.assertEqual(OwnerInventoryAlertPolicy.objects.count(), 0)

    def test_low_zero_unconfigured_equal_threshold_and_multiwarehouse(self):
        self.assertEqual(self.counts()["low"]["count"], 0)
        self.minimum("0")
        self.assertEqual(self.counts()["low"]["count"], 1)
        self.stock("3.25", allocated_qty=1, locked_qty=1, damaged_qty=1)
        self.stock("2.75", self.remote)
        self.minimum("3")
        self.assertEqual(self.items("low")["results"][0]["available_qty"], "3.0000")
        self.minimum("2.99")
        self.assertEqual(self.counts()["low"]["count"], 0)

    def test_unit_mismatch_is_not_confirmed_low_or_slow(self):
        stock = self.stock()
        InventoryDetail.objects.filter(pk=stock.pk).update(base_unit="UNKNOWN")
        self.minimum("100")
        counts = self.counts()
        self.assertEqual(counts["low"]["count"], 0)
        self.assertEqual(counts["low"]["needs_review_count"], 1)
        self.assertEqual(counts["slow"]["needs_review_count"], 1)
        self.assertTrue(self.items("low", status="needs_review")["results"][0]["unit_mismatch"])

    def test_stopped_product_with_stock_still_has_expiry_and_slow_alerts(self):
        self.stock("10", expiry_date=NOW.date())
        self.receipt(days=100)
        Product.objects.filter(pk=self.product.pk).update(is_active=False, min_stock=20)
        counts = self.counts()
        self.assertEqual(counts["low"]["count"], 0)
        self.assertEqual(counts["expiry"]["count"], 1)
        self.assertEqual(counts["slow"]["count"], 1)
        self.assertEqual(
            self.call(OwnerAlertProductSettingsView, product_id=self.product.pk).status_code, 200
        )

    def test_expiry_boundaries_location_merge_unknown_and_zero_stock(self):
        today = NOW.date()
        for batch, days in [("expired", -1), ("today", 0), ("edge", 30), ("later", 31)]:
            self.stock("2", batch_no=batch, expiry_date=today + timedelta(days=days))
        self.stock("3", self.location2, batch_no="today", expiry_date=today)
        self.stock("0", batch_no="empty", expiry_date=today - timedelta(days=10))
        self.stock("1", batch_no="unknown")
        data = self.items("expiry")
        self.assertEqual(data["count"], 1)
        batches = data["results"][0]["batches"]
        self.assertEqual([b["remaining_days"] for b in batches], [-1, 0, 30])
        self.assertEqual(Decimal(batches[1]["onhand_qty"]), 5)
        self.assertTrue(data["results"][0]["missing_expiry"])
        self.assertEqual(len(self.items("expiry", expiry="expired")["results"][0]["batches"]), 1)
        self.assertEqual(self.counts()["expiry"]["needs_review_count"], 1)

    def test_never_sold_and_new_stock_observation_boundary(self):
        self.stock("10")
        tx = self.receipt(days=90)
        result = self.items("slow")["results"][0]
        self.assertEqual(result["inactive_days"], 90)
        self.assertIsNone(result["last_sale_at"])
        InventoryTransaction.objects.filter(pk=tx.pk).update(
            posted_at=NOW - timedelta(days=90) + timedelta(seconds=1)
        )
        self.assertEqual(self.counts()["slow"]["count"], 0)

    def test_sales_and_assisted_sales_reset_clock_but_supplier_return_does_not(self):
        self.stock("9")
        self.receipt(days=200)
        order, tx = self.sale(days=10, processing_mode="WAREHOUSE_ASSISTED")
        self.assertEqual(self.counts()["slow"]["count"], 0)
        OutboundOrder.objects.filter(pk=order.pk).update(outbound_type="OTHER_OUT")
        self.assertEqual(self.counts()["slow"]["count"], 1)
        supplier = Supplier.objects.create(owner=self.owner, code="AL-SUP", name="退货供应商")
        OutboundOrder.objects.filter(pk=order.pk).update(
            outbound_type="SUPPLIER_RETURN", supplier=supplier, customer=None
        )
        self.assertEqual(self.counts()["slow"]["count"], 1)
        OutboundOrder.objects.filter(pk=order.pk).update(
            outbound_type="SALES", supplier=None, customer=self.customer
        )
        InventoryTransaction.objects.filter(pk=tx.pk).update(posted_at=NOW - timedelta(days=100))
        self.assertEqual(self.items("slow")["results"][0]["inactive_days"], 100)

    def test_clear_then_restock_resets_observation(self):
        self.stock("5")
        self.receipt(days=200)
        self.sale("-10", 100)
        self.receipt("5", days=5)
        self.assertEqual(self.counts()["slow"]["count"], 0)

    def test_adjustments_and_transfer_are_not_sales(self):
        self.stock("11")
        self.receipt(days=200)
        self.tx("2", kind="ADJ_GAIN", posted_at=NOW - timedelta(days=10))
        self.tx("-1", kind="OWNER_XFER_OUT", posted_at=NOW - timedelta(days=5))
        self.assertEqual(self.counts()["slow"]["count"], 1)

    def test_move_pairs_do_not_reset_clock_even_when_entire_stock_moves(self):
        self.stock("10", self.location2)
        self.receipt(days=200)
        pair = uuid4()
        for qty, kind, loc in [("-10", "ISSUE", self.location), ("10", "RECEIVE", self.location2)]:
            self.tx(qty, kind=kind, location=loc, pair_id=pair, posted_at=NOW - timedelta(days=1))
        self.assertEqual(self.counts()["slow"]["count"], 1)
        InventoryTransaction.objects.filter(pair_id=pair, qty_delta__gt=0).update(batch_no="WRONG")
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)

    def test_incomplete_history_unknown_time_and_source_are_not_guessed(self):
        self.stock("9")
        receipt = self.receipt(days=200)
        issue = self.tx("-1", kind="ISSUE", posted_at=NOW - timedelta(days=1))
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)
        InventoryTransaction.objects.filter(pk=issue.pk).update(tx_type="ADJ_LOSS")
        self.assertEqual(self.counts()["slow"]["count"], 1)
        InventoryTransaction.objects.filter(pk=receipt.pk).update(posted_at=None)
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)
        InventoryTransaction.objects.filter(pk=receipt.pk).update(
            posted_at=NOW - timedelta(days=200), qty_delta=20
        )
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)

    def test_legacy_pos_and_void_do_not_expose_customer(self):
        self.stock("9")
        self.receipt(days=200)
        sale = PosSale.objects.create(sale_no="ALPOS", warehouse=self.warehouse)
        line = PosSaleLine.objects.create(
            sale=sale, owner=self.owner, product=self.product, line_no=1, qty=1, price=1, amount=1
        )
        self.tx(
            "-1",
            kind="ISSUE",
            src_model="PosSaleLine",
            src_id=line.pk,
            posted_at=NOW - timedelta(days=1),
        )
        self.assertEqual(self.counts()["slow"]["count"], 0)
        PosSale.objects.filter(pk=sale.pk).update(status="VOIDED")
        data = self.items("slow")
        self.assertEqual(data["count"], 1)
        self.assertNotIn("ALPOS", str(data))
        self.assertNotIn("保密客户", str(data))

    def test_pos_task_chain_uses_verified_sale_binding(self):
        self.stock("9")
        self.receipt(days=200)
        order, tx = self.sale(days=1)
        task = self.task(
            "PICK", source_model="outboundorder", source_app="pos", source_pk=str(order.pk)
        )
        InventoryTransaction.objects.filter(pk=tx.pk).update(src_model="WmsTask", src_id=task.pk)
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)
        sale = PosSale.objects.create(sale_no="ALPOS2", warehouse=self.warehouse)
        PosSaleOrder.objects.create(sale=sale, owner=self.owner, outbound_order=order, amount=1)
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 0)
        self.assertEqual(self.counts()["slow"]["count"], 0)
        PosSale.objects.filter(pk=sale.pk).update(status="VOIDED")
        self.assertEqual(self.counts()["slow"]["count"], 1)

    def test_wrong_warehouse_and_cycles_are_unknown_not_sales(self):
        self.stock("9")
        self.receipt(days=200)
        order, tx = self.sale(days=1)
        root = self.task("PICK", source_model="OutboundOrder", source_pk=str(order.pk))
        child = self.task("DISPATCH", parent=root)
        InventoryTransaction.objects.filter(pk=tx.pk).update(
            src_model="tasking.WmsTask", src_id=child.pk
        )
        self.assertEqual(self.counts()["slow"]["count"], 0)
        WmsTask.objects.filter(pk=root.pk).update(warehouse=self.warehouse2)
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)
        WmsTask.objects.filter(pk=root.pk).update(
            warehouse=self.warehouse, source_model="WmsTask", source_pk=str(child.pk)
        )
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)

    def test_roles_and_cross_owner_products_fail_closed(self):
        foreign = Product.objects.create(
            owner=self.other, code="AL-X", name="other", base_uom=self.unit
        )
        self.assertEqual(
            self.call(OwnerAlertProductSettingsView, product_id=foreign.pk).status_code, 404
        )
        user = get_user_model().objects.create_user(username="alert-wh", warehouse=self.warehouse)
        UserRoleScope.objects.create(user=user, role="warehouse_manager", warehouse=self.warehouse)
        self.assertEqual(self.call(user=user).status_code, 403)
        self.assertEqual(
            self.call(OwnerAlertSettingsView, {"version": "x"}, method="patch").status_code, 403
        )
        manager = self.manager()
        self.assertTrue(self.call(OwnerAlertSettingsView, user=manager).data["can_edit"])
        manager.user_permissions.clear()
        manager = get_user_model().objects.get(pk=manager.pk)
        self.assertEqual(
            self.call(
                OwnerAlertSettingsView, {"version": "x"}, user=manager, method="patch"
            ).status_code,
            403,
        )

    def test_owner_config_version_and_audit_rollback(self):
        manager = self.manager()
        version = self.call(OwnerAlertSettingsView, user=manager).data["version"]
        data = {"version": version, "expiry_days": 7, "slow_days": 30}
        response = self.call(OwnerAlertSettingsView, data, user=manager, method="patch")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self.call(OwnerAlertSettingsView, data, user=manager, method="patch").status_code, 409
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="inventory.alerts.settings.update", actor=manager
            ).exists()
        )
        with patch(
            "allapp.inventory.owner_alert_views.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.call(
                    OwnerAlertSettingsView,
                    {"version": response.data["version"], "expiry_days": 5},
                    user=manager,
                    method="patch",
                )
        self.assertEqual(OwnerInventoryAlertPolicy.objects.get(owner=self.owner).expiry_days, 7)

    def test_product_override_disable_inherit_minimum_and_conflict(self):
        manager = self.manager()

        def get():
            return self.call(
                OwnerAlertProductSettingsView, product_id=self.product.pk, user=manager
            )

        version = get().data["version"]
        response = self.call(
            OwnerAlertProductSettingsView,
            {
                "version": version,
                "min_stock": "2.25",
                "expiry_mode": "custom",
                "expiry_days": 5,
                "slow_mode": "off",
            },
            user=manager,
            method="patch",
            product_id=self.product.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["effective"]["expiry"]["days"], 5)
        self.assertFalse(response.data["effective"]["slow"]["enabled"])
        self.assertEqual(self.counts()["low"]["count"], 1)
        self.minimum("3")  # A concurrent external product edit must invalidate the token too.
        response2 = self.call(
            OwnerAlertProductSettingsView,
            {"version": response.data["version"], "min_stock": "4"},
            user=manager,
            method="patch",
            product_id=self.product.pk,
        )
        self.assertEqual(response2.status_code, 409)
        response3 = self.call(
            OwnerAlertProductSettingsView,
            {
                "version": get().data["version"],
                "expiry_mode": "inherit",
                "slow_mode": "inherit",
                "min_stock": None,
            },
            user=manager,
            method="patch",
            product_id=self.product.pk,
        )
        self.assertEqual(response3.data["effective"]["expiry"]["days"], 30)
        self.assertEqual(self.counts()["low"]["count"], 0)

    def test_invalid_thresholds_owner_disable_and_database_constraints(self):
        manager = self.manager()
        owner = self.call(OwnerAlertSettingsView, user=manager).data
        for days in (0, 3651):
            self.assertEqual(
                self.call(
                    OwnerAlertSettingsView,
                    {"version": owner["version"], "slow_days": days},
                    user=manager,
                    method="patch",
                ).status_code,
                400,
            )
        version = self.call(OwnerAlertProductSettingsView, product_id=self.product.pk).data[
            "version"
        ]
        for values in ({"min_stock": "-1"}, {"min_stock": "1.123"}, {"expiry_mode": "custom"}):
            self.assertEqual(
                self.call(
                    OwnerAlertProductSettingsView,
                    {"version": version, **values},
                    user=manager,
                    method="patch",
                    product_id=self.product.pk,
                ).status_code,
                400,
            )
        self.minimum("10")
        self.call(
            OwnerAlertSettingsView,
            {"version": owner["version"], "low_enabled": False},
            user=manager,
            method="patch",
        )
        self.assertEqual(self.counts()["low"]["count"], 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductInventoryAlertPolicy.objects.create(product=self.product, slow_mode="custom")

    def test_search_paging_count_and_batch_queries(self):
        self.minimum("0")
        for index in range(12):
            Product.objects.create(
                owner=self.owner,
                code=f"AL{index}",
                name=f"商品{index}",
                base_uom=self.unit,
                min_stock=0,
            )
        with patch("allapp.inventory.owner_alerts.BATCH_SIZE", 4):
            self.assertEqual(self.counts()["low"]["count"], 13)
            page = self.items("low", page=2, page_size=5)
            self.assertEqual(len(page["results"]), 5)
            self.assertEqual(page["next_page"], 3)
        self.assertEqual(self.items("low", search="AL11")["count"], 1)
        self.assertEqual(self.call(OwnerAlertProductsView, {"search": "AL11"}).data["count"], 1)
        with CaptureQueriesContext(connection) as queries:
            self.counts()
        self.assertLess(len(queries), 15)
        self.assertEqual(
            self.call(OwnerAlertItemsView, {"category": "low", "page_size": 101}).status_code, 400
        )

    def test_product_audit_failure_rolls_back_quantity_and_policy(self):
        manager = self.manager()
        version = self.call(OwnerAlertProductSettingsView, product_id=self.product.pk).data[
            "version"
        ]
        with patch(
            "allapp.inventory.owner_alert_views.record_audit_event",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.call(
                    OwnerAlertProductSettingsView,
                    {"version": version, "min_stock": "2"},
                    user=manager,
                    method="patch",
                    product_id=self.product.pk,
                )
        self.product.refresh_from_db()
        self.assertIsNone(self.product.min_stock)
        self.assertFalse(ProductInventoryAlertPolicy.objects.exists())
        Product.objects.filter(pk=self.product.pk).update(max_stock=5)
        version = self.call(OwnerAlertProductSettingsView, product_id=self.product.pk).data[
            "version"
        ]
        self.assertEqual(
            self.call(
                OwnerAlertProductSettingsView,
                {"version": version, "min_stock": "5"},
                user=manager,
                method="patch",
                product_id=self.product.pk,
            ).status_code,
            400,
        )

    def test_sale_facts_queries_do_not_grow_per_line(self):
        self.stock("20")
        self.receipt("21", days=200)
        self.sale(days=100)
        with CaptureQueriesContext(connection) as small:
            self.counts()
        for index in range(12):
            self.tx("1", kind="ADJ_GAIN", posted_at=NOW - timedelta(days=80 - index))
        InventoryDetail.objects.filter(product=self.product).update(onhand_qty=32, available_qty=32)
        # Compare fresh request users in both measurements (permission cache is per instance).
        self.user = get_user_model().objects.get(pk=self.user.pk)
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(self.counts()["slow"]["count"], 1)
        self.assertEqual(len(small), len(large))

    def test_shanghai_midnight_boundary_and_api_routes(self):
        from django.urls import resolve

        self.stock("2", expiry_date=NOW.date())
        with patch(
            "allapp.inventory.owner_alerts.timezone.now",
            return_value=datetime(2026, 10, 19, 16, tzinfo=datetime_timezone.utc),
        ):
            row = self.items("expiry")["results"][0]
            self.assertEqual(row["batches"][0]["remaining_days"], 0)
        for suffix, view in [
            ("", OwnerAlertsView),
            ("items/", OwnerAlertItemsView),
            ("settings/", OwnerAlertSettingsView),
            ("products/", OwnerAlertProductsView),
            (f"products/{self.product.pk}/settings/", OwnerAlertProductSettingsView),
        ]:
            self.assertEqual(resolve(f"/api/inventory/owner-alerts/{suffix}").func.view_class, view)

    def test_foreign_order_source_and_missing_product_do_not_prove_sales(self):
        self.stock("9")
        self.receipt(days=200)
        order, tx = self.sale(days=1)
        OutboundOrder.objects.filter(pk=order.pk).update(owner=self.other)
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)
        OutboundOrder.objects.filter(pk=order.pk).update(owner=self.owner)
        OutboundOrderLine.objects.filter(order=order).update(is_deleted=True)
        self.assertEqual(self.counts()["slow"]["needs_review_count"], 1)


class OwnerAlertConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from allapp.baseinfo.models import Owner

        self.owner = Owner.objects.create(code="ALCON", name="配置并发货主")
        self.user = get_user_model().objects.create_user(
            username="alert-concurrent", owner=self.owner
        )
        UserRoleScope.objects.create(user=self.user, role="owner_manager", owner=self.owner)
        self.user.user_permissions.add(Permission.objects.get(codename="access_owner_management"))

    def test_two_writers_with_same_version_only_one_can_save(self):
        request = APIRequestFactory().get("/")
        force_authenticate(request, user=self.user)
        version = OwnerAlertSettingsView.as_view()(request).data["version"]
        barrier = Barrier(2)

        def save(days):
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                request = APIRequestFactory().patch(
                    "/", {"version": version, "expiry_days": days}, format="json"
                )
                force_authenticate(request, user=user)
                barrier.wait(timeout=10)
                return OwnerAlertSettingsView.as_view()(request).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save, days) for days in (7, 14)]
            statuses = sorted(f.result(timeout=20) for f in futures)
        self.assertEqual(statuses, [200, 409])
        self.assertEqual(OwnerInventoryAlertPolicy.objects.get(owner=self.owner).revision, 1)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="inventory.alerts.settings.update", owner=self.owner
            ).count(),
            1,
        )
