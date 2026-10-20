from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connections
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.tasking import tests as fixtures
from allapp.tasking.models import TaskAssignment, WmsTask
from allapp.tasking.views import WmsTaskViewSet


class AssignmentServiceTests(TestCase):
    def setUp(self):
        fixtures.TaskingApiContractTests.setUp(self)
        WmsTask.objects.filter(pk=self.task.pk).update(status="RELEASED")
        self.task.refresh_from_db()
        # Retain the fixture's real active assignment; explicit unassign is required.

    def call(self, action, assignee=None, actor=None):
        request = APIRequestFactory().post(
            "/", {"user_id": (assignee or self.operator).pk}, format="json"
        )
        force_authenticate(request, user=actor or self.user)
        return WmsTaskViewSet.as_view({"post": action})(request, pk=self.task.pk)

    def test_same_operator_idempotent_unassign_preserves_history_and_reassign_works(self):
        self.assertEqual(self.call("assign").status_code, 200)
        self.assertEqual(self.call("assign").status_code, 200)
        self.assertEqual(TaskAssignment.objects.filter(task=self.task).count(), 1)
        self.assertEqual(self.call("unassign").status_code, 200)
        old = TaskAssignment.objects.get(task=self.task)
        self.assertIsNotNone(old.finished_at)
        self.assertEqual(self.call("assign").status_code, 200)
        self.assertEqual(TaskAssignment.objects.filter(task=self.task).count(), 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "RELEASED")

    def test_conflicting_assignment_invalid_operator_and_started_task_rejected(self):
        UserRoleScope.objects.create(
            user=self.assignee, role="warehouse_operator", warehouse=self.warehouse
        )
        self.assignee.user_permissions.add(
            Permission.objects.get(codename="claim_task_as_wh_operator")
        )
        self.assertEqual(self.call("assign", self.assignee).status_code, 409)
        self.assertEqual(self.call("unassign").status_code, 200)
        self.assignee.is_active = False
        self.assignee.save(update_fields=["is_active"])
        self.assertEqual(self.call("assign", self.assignee).status_code, 400)
        WmsTask.objects.filter(pk=self.task.pk).update(status="IN_PROGRESS")
        self.assertEqual(self.call("assign").status_code, 409)
        self.assertEqual(self.call("unassign").status_code, 409)

    def test_operator_cannot_assign_and_audit_failure_rolls_back(self):
        self.assertEqual(self.call("assign", actor=self.operator).status_code, 403)
        with patch(
            "allapp.accounts.audit.record_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.call("unassign")
        self.assertEqual(
            TaskAssignment.objects.filter(task=self.task, finished_at__isnull=True).count(), 1
        )


class ConcurrentAssignmentTests(TransactionTestCase):
    setUp = AssignmentServiceTests.setUp
    call = AssignmentServiceTests.call

    def test_two_assignments_serialize_on_task_and_only_one_operator_wins(self):
        self.assertEqual(self.call("unassign").status_code, 200)
        UserRoleScope.objects.create(
            user=self.assignee, role="warehouse_operator", warehouse=self.warehouse
        )
        self.assignee.user_permissions.add(
            Permission.objects.get(codename="claim_task_as_wh_operator")
        )
        barrier = Barrier(2)

        def assign(pk):
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                operator = get_user_model().objects.get(pk=pk)
                barrier.wait(timeout=10)
                return self.call("assign", operator, actor).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(assign, [self.operator.pk, self.assignee.pk]))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(
            TaskAssignment.objects.filter(task=self.task, finished_at__isnull=True).count(), 1
        )
