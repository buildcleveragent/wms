from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from allapp.baseinfo.models import Supplier
from allapp.inbound.models import InboundOrder
from allapp.locations.models import Warehouse
from allapp.outbound.test_physical_review import PhysicalReviewFixture
from allapp.tasking.models import TaskAssignment, WmsTask


@override_settings(REPLENISHMENT_PDA_ENABLED=True)
class HomeTodosTests(PhysicalReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.operator_user = self.operator("todos-operator")
        self.wh_manager.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="tasking", codename="taskconfirm_as_wh_manager"
            )
        )
        self.client.force_authenticate(self.wh_manager)
        self.serial = 0

    def task(self, kind="PICK", status="RELEASED", **kwargs):
        self.serial += 1
        values = dict(
            owner=self.owner,
            warehouse=self.wh,
            task_type=kind,
            task_no=f"TODO-{self.serial}",
            status=status,
        )
        if status == "COMPLETED":
            values.update(review_status="PENDING", posting_status="NOT_READY")
        values.update(kwargs)
        return WmsTask.objects.create(**values)

    def todos(self):
        result = self.client.get("/api/pda/home-todos/")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result["Cache-Control"], "no-store")
        return {item["key"]: item["count"] for item in result.data["items"]}

    def test_review_total_exceeds_page_and_matches_queue(self):
        for _ in range(22):
            self.make_order()
        for changes in (
            {"approval_status": "WHS_PENDING"},
            {"is_closed": True, "close_reason": "测试关闭"},
            {"submit_status": "DRAFT"},
            {"warehouse": Warehouse.objects.create(code="TODO2", name="Other")},
        ):
            self.make_order(**changes)
        queue = self.client.get("/api/outbound/warehouse-review/")
        self.assertEqual(self.todos()["outbound_review"], 23)
        self.assertEqual(self.todos()["outbound_review"], queue.data["count"])
        self.assertLess(len(queue.data["results"]), queue.data["count"])

    def test_operator_pool_and_assignments_and_statuses(self):
        other = self.operator("todos-other")
        for kind in ("PICK", "REPLEN"):
            self.task(kind)
            mine = self.task(kind, "IN_PROGRESS")
            TaskAssignment.objects.create(task=mine, assignee=self.operator_user)
            theirs = self.task(kind)
            TaskAssignment.objects.create(task=theirs, assignee=other)
            self.task(kind, "COMPLETED")
            self.task(kind, "CANCELLED")
            self.task(
                kind,
                warehouse=Warehouse.objects.get_or_create(code="TODO2", defaults={"name": "Other"})[
                    0
                ],
            )
        self.client.force_authenticate(self.operator_user)
        counts = self.todos()
        self.assertNotIn("outbound_review", counts)
        self.assertEqual(counts["pick"], 2)
        self.assertEqual(counts["replenish"], 2)
        for url, key in (
            ("/api/pda/pick-tasks/", "pick"),
            ("/api/replenishment/pda/tasks/?pending=1", "replenish"),
        ):
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200, result.data)
            total = result.data["count"] if isinstance(result.data, dict) else len(result.data)
            self.assertEqual(total, counts[key])

    def test_inbound_counts_match_personal_and_pool_queues(self):
        order = InboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.wh,
            inbound_type="PURCHASE",
            supplier=Supplier.objects.create(owner=self.owner, code="TODOS", name="Supplier"),
        )
        receive = self.task(
            "RECEIVE", source_app="inbound", source_model="InboundOrder", source_pk=str(order.pk)
        )
        self.task(
            "PUTAWAY", source_app="tasking", source_model="WmsTask", source_pk=str(receive.pk)
        )
        self.task(
            "RECEIVE",
            "COMPLETED",
            source_app="inbound",
            source_model="InboundOrder",
            source_pk=str(order.pk),
        )
        self.client.force_authenticate(self.operator_user)
        counts = self.todos()
        for kind in ("RECEIVE", "PUTAWAY"):
            result = self.client.get("/api/inbound/pda/tasks/", {"task_type": kind})
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(counts[kind.lower()], 1)
            self.assertEqual(counts[kind.lower()], result.data["count"])

    def test_physical_review_includes_retry_and_excludes_self_and_posted(self):
        self.task(status="COMPLETED", review_status="PENDING", posting_status="NOT_READY")
        self.task(status="COMPLETED", review_status="APPROVED", posting_status="FAILED")
        self.task(status="COMPLETED", review_status="APPROVED", posting_status="POSTED")
        self.task(
            status="COMPLETED",
            review_status="PENDING",
            posting_status="NOT_READY",
            picked_by=self.operator_user,
        )
        self.client.force_authenticate(self.operator_user)
        self.assertEqual(self.todos()["review"], 2)
        result = self.client.get("/api/pda/pick-tasks/?for_review=1")
        self.assertEqual(self.todos()["review"], result.data["count"])

    def test_feature_permission_and_scope_changes(self):
        before = self.client.get("/api/pda/home-todos/").data["scope"]
        with override_settings(REPLENISHMENT_PDA_ENABLED=False):
            self.assertNotIn("replenish", self.todos())
        self.wh_manager.user_permissions.clear()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.wh_manager.pk))
        self.assertNotIn("outbound_review", self.todos())
        self.assertNotEqual(before, self.client.get("/api/pda/home-todos/").data["scope"])
        self.client.force_authenticate(self.sales)
        self.assertEqual(self.client.get("/api/pda/home-todos/").status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get("/api/pda/home-todos/").status_code, (401, 403))
