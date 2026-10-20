"""Owner reminders must follow order scope and actual fulfilment evidence."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.locations.models import Warehouse
from allapp.tasking.models import PickLineExtra, ReplenishTaskExtra, WmsTask, WmsTaskLine

from . import test_order_progress as fixtures
from .models import OutboundOrder
from .workbench import OwnerWorkbenchItemsView, OwnerWorkbenchView


class OwnerWorkbenchTests(TestCase):
    setUpTestData = classmethod(fixtures.OrderProgressTests.setUpTestData.__func__)
    setUp = fixtures.OrderProgressTests.setUp
    task = fixtures.OrderProgressTests.task
    set_order = fixtures.OrderProgressTests.set_order

    def api(self, category=None, user=None, **params):
        if category:
            params["category"] = category
        request = APIRequestFactory().get("/", params)
        force_authenticate(request, user=user or self.user)
        view = OwnerWorkbenchItemsView if category else OwnerWorkbenchView
        return view.as_view()(request)

    def counts(self, user=None):
        response = self.api(user=user)
        self.assertEqual(response.status_code, 200, response.data)
        return {row["code"]: row["count"] for row in response.data["categories"]}

    def manager(self):
        user = get_user_model().objects.create_user(username="workbench-manager", owner=self.owner)
        UserRoleScope.objects.create(user=user, role="owner_manager", owner=self.owner)
        user.user_permissions.add(
            Permission.objects.get(codename="view_outboundorder"),
            Permission.objects.get(codename="approve_outbound_as_owner_manager"),
        )
        return user

    def test_pending_role_scope_and_review_action(self):
        self.set_order(approval_status="OWNER_PENDING")
        self.assertEqual(self.counts()["pending"], 1)
        self.assertEqual(self.api("pending").data["results"][0]["action"], "view")
        manager = self.manager()
        self.assertEqual(self.api("pending", user=manager).data["results"][0]["action"], "review")
        self.set_order(created_by=manager)
        self.assertEqual(self.counts()["pending"], 0)
        self.assertEqual(self.counts(manager)["pending"], 1)

    def test_manager_without_approval_permission_cannot_open_pending(self):
        manager = self.manager()
        manager.user_permissions.remove(
            Permission.objects.get(codename="approve_outbound_as_owner_manager")
        )
        self.set_order(approval_status="OWNER_PENDING")
        self.assertIsNone(self.counts(manager)["pending"])
        self.assertEqual(self.api("pending", user=manager).status_code, 403)

    def test_current_return_survives_draft_then_leaves_on_resubmission(self):
        self.set_order(
            approval_status="OWNER_REJECTED",
            submit_status="DRAFT",
            owner_reject_reason="修改收货地址",
        )
        self.assertEqual(self.counts()["returned"], 1)
        self.assertEqual(self.api("returned").data["results"][0]["reason"], "修改收货地址")
        self.set_order(approval_status="OWNER_PENDING", submit_status="SUBMITTED")
        self.assertEqual(self.counts()["returned"], 0)
        self.assertEqual(self.counts()["pending"], 1)

    def test_warehouse_rejection_reads_only_current_cycle_reason(self):
        self.set_order(approval_status="WHS_REJECTED")
        record_audit_event(
            action="outbound.order.warehouse_reject",
            module="outbound",
            obj=self.order,
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
            metadata={"reason": "请核对批次", "debug": "secret"},
        )
        data = self.api("returned").data
        self.assertEqual(data["results"][0]["reason"], "请核对批次")
        self.assertNotIn("secret", str(data))
        record_audit_event(
            action="outbound.order.submit",
            module="outbound",
            obj=self.order,
            owner_id=self.owner.pk,
            warehouse_id=self.warehouse.pk,
        )
        self.assertEqual(self.api("returned").data["results"][0]["reason"], "原因未记录")

    def test_overdue_boundary_missing_time_partial_full_and_pending_dispatch(self):
        now = timezone.now()
        for etd, expected in (
            (None, 0),
            (now, 0),
            (now + timedelta(seconds=1), 0),
            (now - timedelta(seconds=1), 1),
        ):
            self.set_order(etd=etd)
            with patch("allapp.outbound.workbench.timezone.now", return_value=now):
                self.assertEqual(self.counts()["overdue"], expected)
        task = self.task("DISPATCH", "COMPLETED", 4)
        self.assertEqual(self.counts()["overdue"], 1)
        task.lines.update(qty_done=10)
        self.assertEqual(self.counts()["overdue"], 0)
        self.task("DISPATCH", "RELEASED", 0)
        self.assertEqual(self.counts()["overdue"], 1)

    def test_closed_cancelled_inactive_and_assisted_completion_are_excluded(self):
        self.set_order(etd=timezone.now() - timedelta(days=2), approval_status="OWNER_PENDING")
        for attrs in (
            {"is_closed": True},
            {"approval_status": "CANCELLED"},
            {"is_active": False},
            {"is_deleted": True},
        ):
            self.set_order(**attrs)
            self.assertTrue(all(v == 0 for v in self.counts().values()))
            OutboundOrder.all_objects.filter(pk=self.order.pk).update(
                is_closed=False, is_active=True, is_deleted=False, approval_status="OWNER_PENDING"
            )
        self.set_order(processing_mode="WAREHOUSE_ASSISTED", is_closed=True)
        self.task("PICK", "COMPLETED", 10)
        self.assertEqual(self.counts()["overdue"], 0)

    def test_missing_allocation_is_not_shortage_until_attempted(self):
        self.set_order(approval_status="OWNER_APPROVED")
        self.assertEqual(self.counts()["shortage"], 0)
        self.set_order(approval_status="WHS_PENDING", approved_at_warehouse=timezone.now())
        self.assertEqual(self.counts()["shortage"], 1)
        task = self.task()
        task.lines.update(qty_plan=4)
        item = self.api("shortage").data["results"][0]["shortages"][0]
        self.assertEqual(item["quantity"], "6.000")
        self.assertEqual(item["unit"], "瓶")
        task.lines.update(qty_plan=10)
        self.assertEqual(self.counts()["shortage"], 0)

    def test_short_pick_disappears_after_supplementary_pick_or_dispatch(self):
        pick = self.task("PICK", "COMPLETED", 4)
        PickLineExtra.objects.create(
            line=pick.lines.get(), qty_picked=4, qty_short=6, short_reason="拣货位缺货"
        )
        self.assertEqual(self.counts()["shortage"], 1)
        supplement = self.task("PICK", "COMPLETED", 6)
        self.assertEqual(self.counts()["shortage"], 0)
        WmsTask.objects.filter(pk=supplement.pk).update(
            status="CANCELLED", review_status="NOT_READY", posting_status="NOT_READY"
        )
        self.assertEqual(self.counts()["shortage"], 1)
        self.task("DISPATCH", "COMPLETED", 10)
        self.assertEqual(self.counts()["shortage"], 0)

    def test_unreliable_sources_do_not_manufacture_shortage_or_completion(self):
        self.set_order(approval_status="WHS_PENDING", etd=timezone.now() - timedelta(days=1))
        pick = self.task("PICK", "COMPLETED", 3)
        line = pick.lines.get()
        PickLineExtra.objects.create(line=line, qty_short=7, short_reason="缺货")
        WmsTaskLine.objects.filter(pk=line.pk).update(src_id=999999999)
        self.assertEqual(self.counts()["shortage"], 0)
        dispatch = self.task("DISPATCH", "COMPLETED", 10)
        dispatch.lines.update(src_id=999999999)
        item = self.api("overdue").data["results"][0]
        self.assertIn("数据待核对", item["reason"])

    def test_same_order_is_counted_once_per_category(self):
        self.set_order(etd=timezone.now() - timedelta(days=1))
        for _ in range(2):
            pick = self.task("PICK", "IN_PROGRESS", 1)
            PickLineExtra.objects.create(line=pick.lines.get(), qty_short=4, short_reason="缺货")
        counts = self.counts()
        self.assertEqual(counts["overdue"], 1)
        self.assertEqual(counts["shortage"], 1)
        for category in ("overdue", "shortage"):
            data = self.api(category).data
            self.assertEqual(data["count"], counts[category])
            self.assertEqual(len(data["results"]), 1)

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_foreign_owner_and_warehouse_roles_never_bypass_scope(self):
        self.set_order(approval_status="OWNER_PENDING")
        other = Owner.objects.create(code="WB-X", name="其他货主")
        foreign = get_user_model().objects.create_user(username="wb-foreign", owner=other)
        UserRoleScope.objects.create(user=foreign, role="owner_salesperson", owner=other)
        foreign.user_permissions.add(Permission.objects.get(codename="view_outboundorder"))
        self.assertEqual(self.counts(foreign)["pending"], 0)
        warehouse_user = get_user_model().objects.create_user(username="wb-warehouse")
        UserRoleScope.objects.create(
            user=warehouse_user, role="warehouse_operator", warehouse=self.warehouse
        )
        self.assertEqual(self.api(user=warehouse_user).status_code, 403)

    def test_derived_tasks_scope_cancelled_and_deleted_dispatches(self):
        self.set_order(etd=timezone.now() - timedelta(days=1))
        pick = self.task("PICK", "COMPLETED", 10)
        dispatch = self.task("DISPATCH", "COMPLETED", 10, parent=pick)
        self.assertEqual(self.counts()["overdue"], 0)
        remote = Warehouse.objects.create(code="WB-W", name="其他仓库")
        WmsTask.objects.filter(pk=dispatch.pk).update(warehouse=remote)
        self.assertEqual(self.counts()["overdue"], 1)
        WmsTask.objects.filter(pk=dispatch.pk).update(
            warehouse=self.warehouse,
            status="CANCELLED",
            review_status="NOT_READY",
            posting_status="NOT_READY",
        )
        self.assertEqual(self.counts()["overdue"], 1)
        WmsTask.objects.filter(pk=dispatch.pk).update(
            status="COMPLETED", review_status="APPROVED", posting_status="POSTED", is_deleted=True
        )
        self.assertEqual(self.counts()["overdue"], 1)

    def test_pagination_sorting_search_and_no_writes_or_n_plus_one(self):
        self.set_order(approval_status="OWNER_PENDING", etd=timezone.now() - timedelta(days=1))
        self.counts()  # Warm Django permission caches.
        with CaptureQueriesContext(connection) as small:
            self.counts()
        for i in range(5):
            OutboundOrder.objects.create(
                owner=self.owner,
                warehouse=self.warehouse,
                customer=self.customer,
                created_by=self.user,
                submit_status="SUBMITTED",
                approval_status="OWNER_PENDING",
                etd=timezone.now() - timedelta(days=i + 2),
            )
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(self.counts()["pending"], 6)
        self.assertEqual(len(small), len(large))
        self.assertFalse(
            any(q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for q in large)
        )
        first, second = (
            self.api("pending", page_size=3).data,
            self.api("pending", page_size=3, page=2).data,
        )
        self.assertEqual(first["count"], 6)
        self.assertFalse({r["id"] for r in first["results"]} & {r["id"] for r in second["results"]})
        self.assertIsNone(second["next_page"])
        self.assertEqual(self.api("pending", search=self.order.order_no).data["count"], 1)
        dates = [r["etd"] for r in self.api("overdue").data["results"]]
        self.assertEqual(dates, sorted(dates))
        with patch("allapp.outbound.workbench.BATCH_SIZE", 2):
            self.assertEqual(self.counts()["pending"], 6)

    def test_routes_and_bad_filters(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get("/api/outbound/owner-workbench/").status_code, 200)
        self.assertEqual(
            client.get("/api/outbound/owner-workbench/items/", {"category": "pending"}).status_code,
            200,
        )
        self.assertEqual(self.api("bad").status_code, 400)
        self.assertEqual(self.api("pending", page_size=101).status_code, 400)

    def test_shared_replenishment_keeps_only_current_order_and_clears_after_refill(self):
        self.set_order(approval_status="WHS_PENDING", approved_at_warehouse=timezone.now())
        pick = self.task()
        pick.lines.update(qty_plan=2)
        replen = WmsTask.objects.create(
            task_no="WB-REPLEN",
            task_type="REPLEN",
            status="RELEASED",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        ReplenishTaskExtra.objects.create(task=replen, demand_order_ids=[self.order.pk, 999999])
        data = self.api("shortage").data
        self.assertEqual(data["count"], 1)
        self.assertIn("等待仓库补货", data["results"][0]["shortages"][0]["reason"])
        self.assertNotIn("999999", str(data))
        pick.lines.update(qty_plan=10)
        self.assertEqual(self.counts()["shortage"], 0)

    def test_line_cycles_metadata_conflicts_and_cancelled_short_lines_are_ignored(self):
        pick = self.task("PICK", "IN_PROGRESS", 2)
        line = pick.lines.get()
        PickLineExtra.objects.create(line=line, qty_picked=2, qty_short=8, short_reason="缺货")
        WmsTaskLine.objects.filter(pk=line.pk).update(src_model="WmsTaskLine", src_id=line.pk)
        self.assertEqual(self.counts()["shortage"], 0)
        WmsTaskLine.objects.filter(pk=line.pk).update(
            src_model="OutboundOrderLine",
            src_id=self.line.pk,
            plan_meta={"outbound_order_line_id": 999999},
        )
        self.assertEqual(self.counts()["shortage"], 0)
        WmsTaskLine.objects.filter(pk=line.pk).update(plan_meta={}, status="CANCELLED")
        self.assertEqual(self.counts()["shortage"], 0)

    def test_duplicate_order_lines_keep_shortages_separate(self):
        second = self.order.lines.create(product=self.product, base_qty=5, base_price=10)
        self.set_order(approval_status="WHS_PENDING")
        self.task()
        item = self.api("shortage").data["results"][0]["shortages"]
        self.assertEqual(len(item), 1)
        self.assertEqual(Decimal(item[0]["quantity"]), second.base_qty)

    def test_missing_task_product_is_unreliable_not_confirmed_shortage(self):
        self.set_order(approval_status="WHS_PENDING", approved_at_warehouse=timezone.now())
        task = self.task()
        task.lines.update(product=None)
        self.assertEqual(self.counts()["shortage"], 0)
