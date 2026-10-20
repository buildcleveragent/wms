from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from allapp.accounts.models import AuditEvent, UserRoleScope
from allapp.inventory.models import InventoryTransaction
from allapp.locations.models import Warehouse
from allapp.tasking.models import TaskAssignment, WmsTask

from .services import confirm_warehouse_order
from .test_warehouse_review import WarehouseReviewFixture


class PhysicalReviewFixture(WarehouseReviewFixture):
    def operator(self, name, warehouse=None):
        user = get_user_model().objects.create_user(username=name, warehouse=warehouse or self.wh)
        UserRoleScope.objects.create(
            user=user, role="warehouse_operator", warehouse=warehouse or self.wh
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="claim_task_as_wh_operator"
            )
        )
        return user

    def ready_pick(self):
        order = self.prepared()
        _, task = confirm_warehouse_order(order, by_user=self.wh_manager)
        self.picker = self.operator("physical-picker")
        self.reviewer = self.operator("physical-reviewer")
        TaskAssignment.objects.create(task=task, assignee=self.picker)
        self.client.force_authenticate(self.picker)
        scanned = self.client.post(
            f"/api/pda/pick-tasks/{task.pk}/scan/",
            {
                "barcode": self.product.sku,
                "qty": "2.125",
            },
            format="json",
        )
        self.assertEqual(scanned.status_code, 200, scanned.data)
        submitted = self.client.post(f"/api/pda/pick-tasks/{task.pk}/create-review-task/")
        self.assertEqual(submitted.status_code, 200, submitted.data)
        task.refresh_from_db()
        self.review = WmsTask.objects.get(pk=submitted.data["review_task_id"])
        self.client.force_authenticate(self.reviewer)
        return task

    def queue(self, **params):
        return self.client.get("/api/pda/pick-tasks/", {"for_review": 1, **params})

    def context(self, task):
        return self.client.get(f"/api/pda/pick-tasks/{task.pk}/review-context/")

    def post_review(self, task):
        return self.client.post(f"/api/pda/pick-tasks/{task.pk}/post/")


class PhysicalReviewTests(PhysicalReviewFixture, TestCase):
    def test_other_operator_can_review_without_expanding_pick_actions(self):
        task = self.ready_pick()
        listed = self.queue(page_size=1)
        self.assertEqual(listed.status_code, 200, listed.data)
        row = listed.data["results"][0]
        self.assertEqual(row["review_task"]["id"], self.review.pk)
        self.assertEqual(row["picked_by_name"], self.picker.username)
        self.assertTrue(row["can_review"])
        context = self.context(task)
        self.assertEqual(context.status_code, 200, context.data)
        self.assertEqual(context.data["lines"][0]["qty_done"], "2.125")
        self.assertEqual(self.client.get(f"/api/pda/pick-tasks/{task.pk}/").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/pda/pick-tasks/{task.pk}/scan/", {"barcode": "x"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/api/pda/pick-tasks/{task.pk}/adjust-line-qty/",
                {"line_id": task.lines.get().pk, "final_qty_done": 1},
            ).status_code,
            404,
        )
        result = self.post_review(task)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.queue().data["count"], 0)
        self.assertEqual(self.post_review(task).status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.onhand_qty, Decimal("7.875"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_id=task.pk, src_model="WmsTask", tx_type="ISSUE"
            ).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.review.approve_post", object_id=str(task.pk)
            ).count(),
            1,
        )
        self.assertEqual(
            WmsTask.objects.filter(task_type="DISPATCH", task_group_no=self.review.task_no).count(),
            1,
        )

    def test_self_review_is_hidden_and_blocked(self):
        task = self.ready_pick()
        self.client.force_authenticate(self.picker)
        self.assertEqual(self.queue().data["count"], 0)
        self.assertFalse(self.context(task).data["task"]["can_review"])
        self.assertEqual(self.post_review(task).status_code, 400)

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_cross_warehouse_and_forged_permissions_are_denied(self):
        task = self.ready_pick()
        elsewhere = Warehouse.objects.create(code="PHY-OTHER", name="其他仓")
        self.client.force_authenticate(self.operator("physical-other-wh", elsewhere))
        self.assertEqual(self.queue().data["count"], 0)
        self.assertEqual(self.context(task).status_code, 404)
        self.assertEqual(self.post_review(task).status_code, 404)
        self.reviewer.user_permissions.clear()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.reviewer.pk))
        self.assertEqual(self.queue().status_code, 403)
        self.assertEqual(self.context(task).status_code, 403)
        self.assertEqual(self.post_review(task).status_code, 403)
        self.sales.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="claim_task_as_wh_operator"
            )
        )
        self.client.force_authenticate(self.sales)
        self.assertEqual(self.queue().status_code, 403)

    def test_manager_can_review_and_pending_rows_are_paginated(self):
        task = self.ready_pick()
        self.wh_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="taskconfirm_as_wh_manager"
            )
        )
        self.client.force_authenticate(self.wh_manager)
        self.assertTrue(self.context(task).data["task"]["can_review"])
        another = WmsTask.objects.create(
            task_no="PHYSICAL-ORPHAN",
            task_type="PICK",
            status="COMPLETED",
            review_status="PENDING",
            owner=self.owner,
            warehouse=self.wh,
            picked_by=self.picker,
        )
        page = self.queue(page_size=1)
        self.assertEqual(page.data["count"], 2)
        self.assertIsNotNone(page.data["next"])
        self.assertEqual(self.queue(page_size=1, page=2).data["results"][0]["id"], task.pk)
        self.assertFalse(self.context(another).data["task"]["can_review"])
        self.assertEqual(self.post_review(task).status_code, 200)

    def test_missing_or_duplicate_review_blocks_without_read_side_effects(self):
        task = self.ready_pick()
        WmsTask.objects.filter(pk=self.review.pk).update(status="CANCELLED")
        count = WmsTask.objects.count()
        self.assertFalse(self.context(task).data["task"]["can_review"])
        self.assertTrue(self.queue().data["results"][0]["blocked_reason"])
        self.assertEqual(WmsTask.objects.count(), count)
        self.assertEqual(self.post_review(task).status_code, 400)
        WmsTask.objects.filter(pk=self.review.pk).update(status="RELEASED")
        WmsTask.objects.create(
            task_no="PHYSICAL-DUP",
            task_type="REVIEW",
            status="RELEASED",
            owner=self.owner,
            warehouse=self.wh,
            source_model="WmsTask",
            source_pk=str(task.pk),
        )
        self.assertFalse(self.context(task).data["task"]["can_review"])
        self.assertEqual(self.post_review(task).status_code, 400)

    def test_failed_posting_remains_visible_and_can_retry(self):
        task = self.ready_pick()
        with patch(
            "allapp.tasking.plugins.handlers.DefaultPostingHandler._handle_atomic",
            side_effect=ValueError("physical-post-failure"),
        ):
            with self.assertRaisesRegex(ValueError, "physical-post-failure"):
                self.post_review(task)
        task.refresh_from_db()
        self.assertEqual(task.posting_status, "FAILED")
        self.assertEqual(task.approved_by_id, self.reviewer.pk)
        self.assertTrue(self.queue().data["results"][0]["can_retry_post"])
        self.assertTrue(self.context(task).data["task"]["can_retry_post"])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.onhand_qty, 10)
        other = self.operator("physical-retry")
        self.client.force_authenticate(other)
        self.assertEqual(self.post_review(task).status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.approved_by_id, self.reviewer.pk)
        self.assertEqual(self.queue().data["count"], 0)


class PhysicalReviewConcurrencyTests(PhysicalReviewFixture, TransactionTestCase):
    def test_two_reviewers_post_inventory_and_audit_only_once(self):
        task = self.ready_pick()
        other = self.operator("physical-concurrent")
        barrier = Barrier(2)

        def perform(user_id):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=user_id))
                barrier.wait(timeout=10)
                result = client.post(f"/api/pda/pick-tasks/{task.pk}/post/")
                return result.status_code, result.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(perform, [self.reviewer.pk, other.pk]))
        self.assertEqual([row[0] for row in results], [200, 200], results)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.onhand_qty, Decimal("7.875"))
        self.assertEqual(
            InventoryTransaction.objects.filter(
                src_id=task.pk, src_model="WmsTask", tx_type="ISSUE"
            ).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                action="outbound.review.approve_post", object_id=str(task.pk)
            ).count(),
            1,
        )
        self.assertEqual(
            WmsTask.objects.filter(task_type="DISPATCH", task_group_no=self.review.task_no).count(),
            1,
        )
