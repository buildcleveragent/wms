from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.baseinfo.models import (
    Customer,
    Owner,
    OwnerCustomerAddress,
    OwnerCustomerBinding,
    OwnerWarehouseBinding,
)
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.reorder import SHANGHAI, datetime
from allapp.products.models import Product, ProductUom


class ReorderFixture:
    def setUp(self):
        self.owner = Owner.objects.create(code="REORDER", name="再次下单货主")
        self.wh = Warehouse.objects.create(code="REORDERWH", name="仓库")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=self.wh)
        self.sub = Subwarehouse.objects.create(warehouse=self.wh, code="REORDERS", name="子仓")
        self.loc = Location.objects.create(
            warehouse=self.wh, subwarehouse=self.sub, code="REORDERS-01-01-01", name="库位"
        )
        self.uom = ProductUom.objects.create(code="REORDERPC", name="件")
        self.product = Product.objects.create(
            owner=self.owner,
            code="0001",
            sku="0002",
            name="常购商品",
            base_uom=self.uom,
            price=Decimal("12.5000"),
            min_price=Decimal("8"),
            batch_control=False,
            expiry_control=False,
        )
        self.stock = InventoryDetail.objects.create(
            owner=self.owner,
            warehouse=self.wh,
            subwarehouse=self.sub,
            location=self.loc,
            product=self.product,
            onhand_qty=Decimal("10"),
            damaged_qty=Decimal("1"),
            locked_qty=Decimal("1"),
            allocated_qty=Decimal("1"),
        )
        self.sales = self.actor(
            "reorder-sales", "owner_salesperson", "outbound.submit_outbound_as_owner_buyers"
        )
        self.other = self.actor(
            "reorder-other", "owner_salesperson", "outbound.submit_outbound_as_owner_buyers"
        )
        self.manager = self.actor(
            "reorder-manager", "owner_manager", "accounts.access_owner_management"
        )
        self.customer = Customer.objects.create(
            owner=self.owner, salesperson=self.sales, code="REORDERC", name="客户"
        )
        self.binding = OwnerCustomerBinding.objects.get(owner=self.owner, customer=self.customer)
        self.cash = Customer.objects.create(
            owner=self.owner, salesperson=self.other, code="CASH", name="散客"
        )
        self.order = self.make_order()
        self.line = OutboundOrderLine.objects.create(
            order=self.order,
            product=self.product,
            base_qty=Decimal("2.125"),
            base_price=Decimal("9.0000"),
            note="不自动复制的备注",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.sales)

    def actor(self, name, role, permission):
        actor = get_user_model().objects.create_user(username=name, owner=self.owner)
        UserRoleScope.objects.create(user=actor, owner=self.owner, role=role)
        app, code = permission.split(".")
        actor.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=code)
        )
        return actor

    def make_order(self, **kwargs):
        values = dict(
            owner=self.owner,
            warehouse=self.wh,
            customer=self.customer,
            created_by=self.sales,
            submit_status="SUBMITTED",
            approval_status="OWNER_APPROVED",
            contact="张三",
            contact_phone="13800000000",
            ship_to="原收货地址",
            biz_date=datetime.now(SHANGHAI).date(),
        )
        values.update(kwargs)
        return OutboundOrder.objects.create(**values)

    def payload(self, qty="2.125"):
        return {
            "warehouse_id": self.wh.pk,
            "customer_id": self.customer.pk,
            "reorder_source_id": self.order.pk,
            "items": [
                {
                    "product_id": self.product.pk,
                    "qty": qty,
                    "price": "12.5000",
                    "base_uom_id": self.uom.pk,
                    "line_key": "first",
                }
            ],
        }

    def create(self, data=None, key="reorder-request-1"):
        return self.client.post(
            "/api/outbound/orders/",
            data or self.payload(),
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def preview(self):
        return self.client.get(f"/api/outbound/orders/{self.order.pk}/reorder-context/")

    def addresses(self, customer=None):
        return f"/api/outbound/customers/{(customer or self.customer).pk}/addresses/"

    def save_address(self, *, key="address-request-1", customer=None, **kwargs):
        data = dict(contact="张三", phone="13800000000", address="常用地址", is_default=True)
        data.update(kwargs)
        return self.client.post(
            self.addresses(customer), data, format="json", HTTP_IDEMPOTENCY_KEY=key
        )


class OwnerReorderTests(ReorderFixture, TestCase):
    def test_preview_preserves_lines_and_uses_current_price_without_writes(self):
        OutboundOrderLine.objects.create(
            order=self.order, product=self.product, base_qty=1, base_price=10
        )
        before = (
            OutboundOrder.objects.count(),
            AuditEvent.objects.count(),
            InventoryTransaction.objects.count(),
        )
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(len(data["items"]), 2)
        self.assertNotEqual(data["items"][0]["line_key"], data["items"][1]["line_key"])
        self.assertEqual(data["items"][0]["price"], "12.5000")
        self.assertEqual(data["items"][0]["historical_price"], "9.0000")
        self.assertEqual(data["items"][0]["qty"], "2.125")
        self.assertEqual(data["items"][0]["available"], "7.0000")
        self.assertNotIn("src_bill_no", data["header"])
        self.assertNotIn("etd", data["header"])
        self.assertEqual(
            before,
            (
                OutboundOrder.objects.count(),
                AuditEvent.objects.count(),
                InventoryTransaction.objects.count(),
            ),
        )

    def test_repeated_items_cannot_bypass_stock_at_precheck_or_create(self):
        payload = self.payload("4")
        payload["items"].append({**payload["items"][0], "line_key": "second"})
        response = self.client.post("/api/outbound/orders/validate/", payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["can_submit"])
        self.assertTrue(all("缺口" in str(row["issues"]) for row in response.data["items"]))
        created = self.create(payload)
        self.assertEqual(created.status_code, 409, created.data)
        self.assertIs(created.data["validation"]["can_submit"], False)
        self.assertEqual(OutboundOrder.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.allocated_qty, 1)

    def test_source_scope_and_pos_are_enforced_even_in_shadow_mode(self):
        self.client.force_authenticate(self.other)
        with self.settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow"):
            self.assertEqual(self.preview().status_code, 404)
        self.client.force_authenticate(self.sales)
        self.order.memo = "[POS] 历史记录"
        self.order.save()
        self.assertEqual(self.preview().status_code, 400)
        self.assertEqual(self.create().status_code, 400)

    def test_unlinked_pos_rows_do_not_hide_sales_and_both_pos_links_are_excluded(self):
        from allapp.pos.models import PosSale, PosSaleLine, PosSaleOrder

        sale = PosSale.objects.create(sale_no="REORDER-POS", warehouse=self.wh)
        line = PosSaleLine.objects.create(
            sale=sale,
            owner=self.owner,
            product=self.product,
            line_no=1,
            qty=1,
            price=12,
            amount=12,
        )
        url = f"/api/outbound/customers/{self.customer.pk}/frequent-products/"
        self.assertEqual(self.preview().status_code, 200)
        self.assertEqual(self.client.get(url, {"warehouse_id": self.wh.pk}).data["count"], 1)
        line.outbound_order_line = self.line
        line.save()
        self.assertEqual(self.preview().status_code, 400)
        self.assertEqual(self.client.get(url, {"warehouse_id": self.wh.pk}).data["count"], 0)
        line.outbound_order_line = None
        line.save()
        PosSaleOrder.objects.create(sale=sale, owner=self.owner, outbound_order=self.order)
        self.assertEqual(self.preview().status_code, 400)

    def test_manager_cannot_create_or_reorder(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.preview().status_code, 403)
        self.assertEqual(self.create().status_code, 403)

    def test_customer_assignment_revocation_blocks_copy_and_submission(self):
        self.binding.salesperson = self.other
        self.binding.save()
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["can_copy"])
        self.assertIsNone(response.data["customer"])
        self.assertEqual(self.create().status_code, 400)

    def test_stock_units_and_missing_historical_unit_do_not_guess(self):
        InventoryDetail.objects.filter(pk=self.stock.pk).update(base_unit="OLD")
        response = self.preview()
        self.assertIn("数据待核对", str(response.data["items"][0]["issues"]))
        self.assertIsNone(response.data["items"][0]["available"])
        InventoryDetail.objects.filter(pk=self.stock.pk).update(base_unit=self.uom.code)
        OutboundOrderLine.objects.filter(pk=self.line.pk).update(base_uom=None)
        self.assertIn("未记录", str(self.preview().data["items"][0]["issues"]))

    def test_version_change_fails_atomically_and_idempotent_success_replays(self):
        payload = self.payload()
        payload["items"][0]["catalog_version"] = self.preview().data["items"][0]["catalog_version"]
        Product.objects.filter(pk=self.product.pk).update(price=13)
        response = self.create(payload)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(OutboundOrder.objects.count(), 1)
        payload["items"][0]["catalog_version"] = self.preview().data["items"][0]["catalog_version"]
        response = self.create(payload)
        self.assertEqual(response.status_code, 201, response.data)
        new_id = response.data["id"]
        Product.objects.filter(pk=self.product.pk).update(is_active=False, min_price=100)
        self.stock.onhand_qty = 3
        self.stock.save()
        replay = self.create(payload)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["id"], new_id)
        self.assertEqual(OutboundOrder.objects.count(), 2)
        event = AuditEvent.objects.filter(action="outbound.order.create").latest("id")
        self.assertIn(str(self.order.pk), str(event.after))

    def test_audit_failure_rolls_back_order(self):
        with patch("allapp.outbound.views.record_audit_event", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertEqual(OutboundOrder.objects.count(), 1)

    def test_deactivated_product_is_visible_in_preview_but_not_purchasable(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        data = self.preview().data
        self.assertFalse(data["can_submit"])
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(self.create().status_code, 400)

    def test_missing_reference_price_and_changed_base_unit_require_correction(self):
        Product.objects.filter(pk=self.product.pk).update(price=None)
        preview = self.preview().data
        self.assertIsNone(preview["items"][0]["price"])
        self.assertIn("参考价未配置", str(preview["items"][0]["issues"]))
        self.assertFalse(preview["can_submit"])
        other_unit = ProductUom.objects.create(code="REORDERBOX", name="箱")
        Product.objects.filter(pk=self.product.pk).update(price=12, base_uom=other_unit)
        item = self.preview().data["items"][0]
        self.assertIn("历史基本单位与当前单位不同", str(item["issues"]))
        self.assertEqual(item["historical_unit_name"], "件")
        self.assertEqual(item["current_base_unit_name"], "箱")
        self.assertEqual(item["base_unit_name"], "件")
        self.assertEqual(self.create().status_code, 409)

    def test_unknown_unit_demand_is_not_added_to_current_unit_shortage(self):
        box = ProductUom.objects.create(code="HISTBOX", name="箱")
        OutboundOrderLine.objects.filter(pk=self.line.pk).update(base_uom=box, base_qty=9)
        item = self.preview().data["items"][0]
        self.assertIn("数据待核对", str(item["issues"]))
        self.assertNotIn("缺口", str(item["issues"]))
        self.assertEqual(item["qty"], "9.000")
        self.assertEqual(item["current_base_unit_name"], "件")

    def test_assisted_history_creates_standard_order_without_old_workflow(self):
        self.order.processing_mode = "WAREHOUSE_ASSISTED"
        self.order.is_closed = True
        self.order.close_reason = "仓库代办出库完成"
        self.order.src_bill_no = "OLD-PLATFORM"
        self.order.save()
        self.assertTrue(self.preview().data["can_copy"])
        result = self.create()
        self.assertEqual(result.status_code, 201, result.data)
        created = OutboundOrder.objects.get(pk=result.data["id"])
        self.assertEqual(created.processing_mode, "STANDARD")
        self.assertEqual(created.approval_status, "OWNER_PENDING")
        self.assertEqual(created.submit_status, "SUBMITTED")
        self.assertFalse(created.is_closed)
        self.assertFalse(created.src_bill_no)
        self.assertIsNone(created.etd)

    def test_address_disable_is_private_and_does_not_touch_order_snapshot(self):
        saved = self.save_address()
        result = self.client.delete(
            self.addresses() + f"{saved.data['id']}/",
            {"version": saved.data["version"]},
            format="json",
        )
        self.assertEqual(result.status_code, 204)
        self.assertEqual(self.client.get(self.addresses()).data["results"], [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.ship_to, "原收货地址")
        self.assertTrue(OwnerCustomerAddress.all_objects.get().is_deleted)

    def test_mini_program_private_addresses_are_not_imported_or_changed(self):
        from allapp.salesapp.models import MiniCustomerAddress

        private = MiniCustomerAddress.objects.create(
            owner=self.owner,
            customer=self.customer,
            contact="小程序收件人",
            phone="13800000002",
            detail="小程序私有地址",
            is_default=True,
        )
        self.assertEqual(self.client.get(self.addresses()).data["results"], [])
        self.assertEqual(OwnerCustomerAddress.objects.count(), 0)
        self.save_address()
        private.refresh_from_db()
        self.assertEqual(private.detail, "小程序私有地址")
        self.assertTrue(private.is_default)

    def test_frequent_window_distinct_count_closed_orders_and_scope(self):
        today = datetime.now(SHANGHAI).date()
        for order_kwargs in (
            {"biz_date": today - timedelta(days=89), "is_closed": True, "close_reason": "完成"},
            {"biz_date": today - timedelta(days=90)},
            {"created_by": self.other},
            {"approval_status": "OWNER_PENDING"},
            {"approval_status": "CANCELLED"},
            {"submit_status": "DRAFT"},
            {"memo": "[POS] 销售"},
        ):
            order = self.make_order(**order_kwargs)
            OutboundOrderLine.objects.create(
                order=order, product=self.product, base_qty=1, base_price=12
            )
        OutboundOrderLine.objects.create(
            order=self.order, product=self.product, base_qty=1, base_price=10
        )
        response = self.client.get(
            f"/api/outbound/customers/{self.customer.pk}/frequent-products/",
            {"warehouse_id": self.wh.pk, "search": self.product.sku, "page_size": 1},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["purchase_count"], 2)
        self.assertNotIn("last_order_id", response.data["results"][0])

    def test_precheck_has_no_per_line_queries(self):
        with CaptureQueriesContext(connection) as single:
            self.client.post("/api/outbound/orders/validate/", self.payload(), format="json")
        data = self.payload()
        data["items"] = [{**data["items"][0], "line_key": str(i)} for i in range(30)]
        with CaptureQueriesContext(connection) as many:
            self.client.post("/api/outbound/orders/validate/", data, format="json")
        self.assertLessEqual(len(many), len(single) + 1)
        self.assertFalse(
            any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in many)
        )

    def test_addresses_default_version_and_snapshots(self):
        first = self.save_address()
        self.assertEqual(first.status_code, 201, first.data)
        second = self.save_address(key="address-request-2", address="另一个地址")
        self.assertEqual(second.status_code, 201, second.data)
        rows = self.client.get(self.addresses()).data["results"]
        self.assertEqual(sum(r["is_default"] for r in rows), 1)
        stale = self.client.patch(
            self.addresses() + f"{first.data['id']}/",
            {"version": first.data["version"], "contact": "新收件人"},
            format="json",
        )
        self.assertEqual(stale.status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual(self.order.ship_to, "原收货地址")
        self.assertEqual(
            self.save_address(key="address-request-2", address="另一个地址").status_code, 200
        )

    def test_address_permissions_customer_transfer_and_cash_privacy(self):
        self.save_address()
        self.save_address(customer=self.cash, key="cash-address-001")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.addresses()).status_code, 404)
        self.assertEqual(self.client.get(self.addresses(self.cash)).data["results"], [])
        self.client.force_authenticate(self.manager)
        self.assertEqual(len(self.client.get(self.addresses()).data["results"]), 1)
        self.assertEqual(self.client.get(self.addresses(self.cash)).status_code, 403)
        self.binding.salesperson = self.other
        self.binding.save()
        self.client.force_authenticate(self.sales)
        self.assertEqual(self.client.get(self.addresses()).status_code, 404)

    def test_address_audit_failure_rolls_back_default_switch(self):
        self.save_address()
        with patch("allapp.outbound.reorder_api.record_audit_event", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.save_address(key="address-request-2")
        self.assertEqual(OwnerCustomerAddress.objects.count(), 1)
        self.assertTrue(OwnerCustomerAddress.objects.get().is_default)

    def test_stock_and_price_problems_can_be_saved_but_not_resubmitted(self):
        self.order.submit_status = "DRAFT"
        self.order.approval_status = "OWNER_PENDING"
        self.order.save()
        data = self.payload("20")
        data["items"][0]["price"] = "1.0000"
        data["expected_updated_at"] = self.order.updated_at.isoformat()
        response = self.client.put(f"/api/outbound/orders/{self.order.pk}/", data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(
            f"/api/outbound/orders/{self.order.pk}/submit/", {}, format="json"
        )
        self.assertEqual(response.status_code, 409, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.submit_status, "DRAFT")
        self.assertEqual(self.order.lines.get().base_qty, 20)

    def test_draft_save_does_not_silently_relabel_quantities_in_a_different_unit(self):
        self.order.refresh_from_db()
        self.order.submit_status = "DRAFT"
        self.order.approval_status = "OWNER_PENDING"
        self.order.save()
        box = ProductUom.objects.create(code="SAVEBOX", name="箱")
        payload = self.payload()
        payload["items"][0]["base_uom_id"] = box.pk
        payload["expected_updated_at"] = self.order.updated_at.isoformat()
        result = self.client.put(f"/api/outbound/orders/{self.order.pk}/", payload, format="json")
        self.assertEqual(result.status_code, 400, result.data)
        self.assertIn("基本单位", str(result.data))
        self.assertEqual(self.order.lines.get().pk, self.line.pk)

    def test_draft_resubmission_retry_replays_before_new_stock_or_price_checks(self):
        self.order.submit_status = "DRAFT"
        self.order.approval_status = "OWNER_REJECTED"
        self.order.owner_reject_reason = "核对数量后重新提交"
        self.order.save()
        url = f"/api/outbound/orders/{self.order.pk}/submit/"
        payload = {"expected_updated_at": self.order.updated_at.isoformat()}
        first = self.client.post(
            url, payload, format="json", HTTP_IDEMPOTENCY_KEY="resubmit-request-1"
        )
        self.assertEqual(first.status_code, 200, first.data)
        Product.objects.filter(pk=self.product.pk).update(is_active=False, price=999)
        self.stock.onhand_qty = 3
        self.stock.save()
        replay = self.client.post(
            url, payload, format="json", HTTP_IDEMPOTENCY_KEY="resubmit-request-1"
        )
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data["idempotent"])
        self.assertEqual(AuditEvent.objects.filter(action="outbound.order.submit").count(), 1)
        changed = self.client.post(
            url, {}, format="json", HTTP_IDEMPOTENCY_KEY="resubmit-request-1"
        )
        self.assertEqual(changed.status_code, 409)

    def test_warehouse_revoked_stock_hidden_and_authorized_reselection(self):
        OwnerWarehouseBinding.objects.filter(owner=self.owner, warehouse=self.wh).update(
            is_active=False
        )
        response = self.preview()
        self.assertFalse(response.data["can_copy"])
        self.assertEqual(response.data["items"][0]["available"], "0.0000")
        other = Warehouse.objects.create(code="REORDNEW", name="新授权仓库")
        OwnerWarehouseBinding.objects.create(owner=self.owner, warehouse=other)
        response = self.client.get(
            f"/api/outbound/orders/{self.order.pk}/reorder-context/", {"warehouse_id": other.pk}
        )
        self.assertTrue(response.data["can_copy"], response.data)
        self.assertEqual(response.data["warehouse"]["id"], other.pk)
        self.assertEqual(response.data["items"][0]["qty"], "2.125")

    def test_shared_customer_does_not_share_owner_address_book(self):
        self.save_address()
        owner2 = Owner.objects.create(code="READDRESS2", name="第二货主")
        person = get_user_model().objects.create_user(username="address-owner2", owner=owner2)
        UserRoleScope.objects.create(user=person, owner=owner2, role="owner_salesperson")
        person.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound", codename="submit_outbound_as_owner_buyers"
            )
        )
        OwnerCustomerBinding.objects.create(
            owner=owner2, customer=self.customer, salesperson=person, code="SHARED"
        )
        self.client.force_authenticate(person)
        self.assertEqual(self.client.get(self.addresses()).data["results"], [])
        address_id = OwnerCustomerAddress.objects.get().pk
        response = self.client.patch(
            self.addresses() + f"{address_id}/", {"version": 1, "contact": "越权"}, format="json"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.preview().status_code, 404)

    def test_frequent_pagination_and_inactive_or_zero_stock_products(self):
        for index in range(22):
            product = Product.objects.create(
                owner=self.owner,
                code=f"COMMON{index}",
                sku=f"COMMON{index}",
                name=f"常购{index}",
                base_uom=self.uom,
                price=10,
                batch_control=False,
                expiry_control=False,
                is_active=index != 0,
            )
            OutboundOrderLine.objects.create(
                order=self.order, product=product, base_qty=1, base_price=10
            )
        url = f"/api/outbound/customers/{self.customer.pk}/frequent-products/"
        first = self.client.get(url, {"warehouse_id": self.wh.pk, "page_size": 20}).data
        second = self.client.get(url, {"warehouse_id": self.wh.pk, "page_size": 20, "page": 2}).data
        self.assertEqual(first["count"], 23)
        self.assertEqual(len(first["results"]), 20)
        self.assertEqual(len(second["results"]), 3)
        rows = first["results"] + second["results"]
        self.assertEqual(len({row["id"] for row in rows}), 23)
        self.assertEqual(sum(row["can_add"] for row in rows), 1)

    def test_warehouse_operator_and_permission_revocation_are_denied(self):
        operator = get_user_model().objects.create_user(username="reorder-wh", warehouse=self.wh)
        UserRoleScope.objects.create(user=operator, warehouse=self.wh, role="warehouse_operator")
        self.client.force_authenticate(operator)
        self.assertEqual(self.preview().status_code, 403)
        self.assertEqual(self.client.get(self.addresses()).status_code, 403)
        self.sales.user_permissions.clear()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.sales.pk))
        self.assertEqual(self.preview().status_code, 403)
        self.assertEqual(self.create().status_code, 403)


class OwnerAddressConcurrencyTests(ReorderFixture, TransactionTestCase):
    def test_concurrent_defaults_and_duplicate_create(self):
        def save(index):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.sales.pk))
                result = client.post(
                    self.addresses(),
                    {
                        "contact": "收件人",
                        "phone": "13800000000",
                        "address": f"地址{index}",
                        "is_default": True,
                    },
                    format="json",
                    HTTP_IDEMPOTENCY_KEY=f"concurrent-address-{index}",
                )
                return result.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(save, (1, 2)))
        self.assertEqual(statuses, [201, 201])
        self.assertEqual(OwnerCustomerAddress.objects.filter(is_default=True).count(), 1)
