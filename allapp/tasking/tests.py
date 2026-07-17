import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail, PostingJournal
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product, ProductUom
from allapp.tasking import views as tasking_views
from allapp.tasking.counting import create_lines_from_scope
from allapp.tasking.models import (
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.services_posting import post_task
from allapp.tasking.views import WmsTaskLineViewSet, WmsTaskViewSet


class TaskingWarehouseScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Tasking", code="OWN-TASK")
        self.warehouse = Warehouse.objects.create(
            code="WH-TASK-1", name="Warehouse Tasking 1"
        )
        self.other_warehouse = Warehouse.objects.create(
            code="WH-TASK-2", name="Warehouse Tasking 2"
        )
        self.subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWTASK1",
            name="Subwarehouse Tasking 1",
        )
        self.alt_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="SWTASK3",
            name="Subwarehouse Tasking 3",
        )
        self.other_subwarehouse = Subwarehouse.objects.create(
            warehouse=self.other_warehouse,
            code="SWTASK2",
            name="Subwarehouse Tasking 2",
        )
        self.location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWTASK1-01-01-01",
            name="Tasking Location 1",
        )
        self.alt_location = Location.objects.create(
            warehouse=self.warehouse,
            code="SWTASK3-01-01-01",
            name="Tasking Location 3",
        )
        self.other_location = Location.objects.create(
            warehouse=self.other_warehouse,
            code="SWTASK2-01-01-01",
            name="Tasking Location 2",
        )
        self.uom = ProductUom.objects.create(code="PCS-TASK", name="件", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-TASK",
            name="Tasking Product",
            sku="SKU-TASK",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.superuser = get_user_model().objects.create_superuser(
            username="tasking-admin",
            email="tasking-admin@example.com",
            password="x",
        )

    def test_wms_task_requires_explicit_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            WmsTask.objects.create(
                owner=self.owner,
                task_no="TASK-NO-WH",
                task_type=WmsTask.TaskType.RECEIVE,
            )

        self.assertIn("warehouse", exc.exception.message_dict)

    def test_task_scan_log_derives_warehouse_from_task(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-SCAN-1",
            task_type=WmsTask.TaskType.RECEIVE,
        )
        line = WmsTaskLine.objects.create(task=task)

        scan = TaskScanLog.objects.create(
            owner=self.owner,
            task=task,
            task_line=line,
            qty_base_delta=Decimal("1.000000"),
            fp="task-scan-fp-1",
            scan_snapshot_rev=0,
        )

        self.assertEqual(scan.warehouse_id, self.warehouse.id)

    def test_task_scan_log_rejects_location_warehouse_mismatch(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-SCAN-2",
            task_type=WmsTask.TaskType.RECEIVE,
        )
        line = WmsTaskLine.objects.create(task=task)

        with self.assertRaises(ValidationError) as exc:
            TaskScanLog.objects.create(
                owner=self.owner,
                task=task,
                task_line=line,
                location=self.other_location,
                qty_base_delta=Decimal("1.000000"),
                fp="task-scan-fp-2",
                scan_snapshot_rev=0,
            )

        self.assertIn("location", exc.exception.message_dict)

    def test_count_scope_filters_by_subwarehouse_inside_warehouse(self):
        selected_detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.location,
            onhand_qty=Decimal("5.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )
        InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            location=self.alt_location,
            onhand_qty=Decimal("7.0000"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
        )

        task, created_count, truncated, notes = create_lines_from_scope(
            created_by=self.superuser,
            owner_id=self.owner.id,
            warehouse_id=self.warehouse.id,
            subwarehouse_id=self.subwarehouse.id,
        )

        self.assertIsNotNone(task)
        self.assertEqual(created_count, 1)
        self.assertFalse(truncated)
        self.assertEqual(notes, [])
        self.assertEqual(task.warehouse_id, self.warehouse.id)
        self.assertEqual(task.lines.get().src_id, selected_detail.id)

    def test_count_scope_rejects_subwarehouse_from_other_warehouse(self):
        with self.assertRaises(ValidationError) as exc:
            create_lines_from_scope(
                created_by=self.superuser,
                owner_id=self.owner.id,
                warehouse_id=self.warehouse.id,
                subwarehouse_id=self.other_subwarehouse.id,
            )

        self.assertIn("subwarehouse_id", exc.exception.message_dict)

    def test_post_task_is_idempotent(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-POST-1",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )

        with mock.patch(
            "allapp.tasking.services_posting.execute_posting_handler",
            return_value=2,
        ) as mocked_handler:
            first = post_task(task.id, by_user=self.superuser, note="first post")
            second = post_task(task.id, by_user=self.superuser, note="second post")

        task.refresh_from_db()
        journal = PostingJournal.objects.get(
            src_model="WmsTask", src_id=task.id, tx_type="POST"
        )
        self.assertEqual(mocked_handler.call_count, 1)
        self.assertEqual(first["tx_created"], 2)
        self.assertEqual(second["tx_created"], 0)
        self.assertEqual(journal.status, "POSTED")
        self.assertEqual(journal.attempt_count, 1)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
        mocked_handler.assert_called_once_with(
            task=mock.ANY,
            note="first post",
            by_user=self.superuser,
        )


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
class TaskingApiContractTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Owner Tasking API", code="OTASKAPI")
        self.other_owner = Owner.objects.create(
            name="Owner Tasking API Other", code="OTASKAPIO"
        )
        self.warehouse = Warehouse.objects.create(
            code="WTASKAPI", name="Warehouse Tasking API"
        )
        self.other_warehouse = Warehouse.objects.create(
            code="WTASKAPIO",
            name="Warehouse Tasking API Other",
        )
        self.user = get_user_model().objects.create_user(
            username="tasking-api-user",
            password="x",
            warehouse=self.warehouse,
        )
        self.operator = get_user_model().objects.create_user(
            username="tasking-api-operator",
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTask),
                codename="add_wmstask",
            ),
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTask),
                codename="view_wmstask",
            ),
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTask),
                codename="taskconfirm_as_wh_manager",
            ),
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTaskLine),
                codename="change_wmstaskline",
            ),
        )
        self.operator.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTask),
                codename="claim_task_as_wh_operator",
            ),
        )
        self.assignee = get_user_model().objects.create_user(
            username="tasking-api-assignee",
            password="x",
            owner=self.owner,
            warehouse=self.warehouse,
        )
        self.uom = ProductUom.objects.create(
            code="PCS-TASK-API", name="件", is_active=True
        )
        self.product = Product.objects.create(
            owner=self.owner,
            code="SKU-TASK-API",
            name="Tasking API Product",
            sku="SKU-TASK-API",
            base_uom=self.uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-API-1",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.READY,
        )
        self.line = WmsTaskLine.objects.create(
            task=self.task,
            product=self.product,
            qty_plan=Decimal("2.000"),
        )
        TaskAssignment.objects.create(task=self.task, assignee=self.operator)
        WmsTask.objects.create(
            owner=self.other_owner,
            warehouse=self.other_warehouse,
            task_no="TASK-API-OTHER",
            task_type=WmsTask.TaskType.RECEIVE,
        )
        self.factory = APIRequestFactory()

    def _request(self, method, path, data=None, *, user=None):
        req = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(req, user=user or self.user)
        return req

    def _rows(self, response):
        return (
            response.data.get("results", response.data)
            if isinstance(response.data, dict)
            else response.data
        )

    def test_task_list_and_retrieve_are_scoped_by_owner_and_warehouse(self):
        list_view = WmsTaskViewSet.as_view({"get": "list"})
        response = list_view(self._request("get", "/api/tasking/tasks/"))

        self.assertEqual(response.status_code, 200)
        task_nos = {item["task_no"] for item in self._rows(response)}
        self.assertIn("TASK-API-1", task_nos)
        self.assertNotIn("TASK-API-OTHER", task_nos)

        detail_view = WmsTaskViewSet.as_view({"get": "retrieve"})
        response = detail_view(
            self._request("get", f"/api/tasking/tasks/{self.task.id}/"),
            pk=self.task.id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["task_no"], "TASK-API-1")

    def test_task_create_binds_explicit_owner_and_warehouse(self):
        view = WmsTaskViewSet.as_view({"post": "create"})
        response = view(
            self._request(
                "post",
                "/api/tasking/tasks/",
                {
                    "owner": self.owner.id,
                    "warehouse": self.warehouse.id,
                    "task_no": "TASK-API-CREATED",
                    "task_type": WmsTask.TaskType.RECEIVE,
                },
            )
        )

        self.assertEqual(response.status_code, 201, response.data)
        created = WmsTask.objects.get(task_no="TASK-API-CREATED")
        self.assertEqual(created.owner_id, self.owner.id)
        self.assertEqual(created.warehouse_id, self.warehouse.id)

    def test_task_lifecycle_actions_delegate_to_service_layer(self):
        action_cases = [
            ("release", "task_release", {}),
            ("start", "task_start", {}),
            ("complete", "task_complete", {}),
            ("cancel", "task_cancel", {"reason": "bad pick"}),
        ]

        for action, service_name, payload in action_cases:
            actor = self.user if action == "release" else self.operator
            with self.subTest(action=action), mock.patch.object(
                tasking_views.task_svc,
                service_name,
                return_value={"ok": True},
                create=True,
            ) as mocked_service:
                view = WmsTaskViewSet.as_view({"post": action})
                response = view(
                    self._request(
                        "post",
                        f"/api/tasking/tasks/{self.task.id}/{action}/",
                        payload,
                        user=actor,
                    ),
                    pk=self.task.id,
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(mocked_service.called)

    def test_task_assignment_actions_delegate_to_service_layer(self):
        with mock.patch.object(
            tasking_views.task_svc,
            "assign_task",
            return_value={"ok": True},
            create=True,
        ) as mocked_assign:
            assign_view = WmsTaskViewSet.as_view({"post": "assign"})
            response = assign_view(
                self._request(
                    "post",
                    f"/api/tasking/tasks/{self.task.id}/assign/",
                    {"user_id": self.assignee.id},
                ),
                pk=self.task.id,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(mocked_assign.called)

        with mock.patch.object(
            tasking_views.task_svc,
            "unassign_task",
            return_value={"ok": True},
            create=True,
        ) as mocked_unassign:
            unassign_view = WmsTaskViewSet.as_view({"post": "unassign"})
            response = unassign_view(
                self._request(
                    "post",
                    f"/api/tasking/tasks/{self.task.id}/unassign/",
                    {"user_id": self.assignee.id},
                ),
                pk=self.task.id,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(mocked_unassign.called)

    def test_task_scan_and_related_log_endpoints_are_exposed(self):
        TaskStatusLog.objects.create(
            task=self.task,
            old_status=WmsTask.Status.READY,
            new_status=WmsTask.Status.RELEASED,
            changed_by=self.user,
        )
        TaskAssignment.objects.create(task=self.task, assignee=self.assignee)

        with mock.patch.object(
            tasking_views.task_svc,
            "post_scan",
            return_value={"detail": "scanned", "task_id": self.task.id},
            create=True,
        ):
            scan_view = WmsTaskViewSet.as_view({"post": "scan"})
            response = scan_view(
                self._request(
                    "post",
                    f"/api/tasking/tasks/{self.task.id}/scan/",
                    {"barcode": "SKU-TASK-API"},
                    user=self.operator,
                ),
                pk=self.task.id,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], "scanned")

        for action in ("status_logs", "assignments", "scan_logs"):
            view = WmsTaskViewSet.as_view({"get": action})
            response = view(
                self._request("get", f"/api/tasking/tasks/{self.task.id}/{action}/"),
                pk=self.task.id,
            )
            self.assertEqual(response.status_code, 200)

    def test_task_line_bind_and_unbind_updates_generic_binding(self):
        content_type = ContentType.objects.get_for_model(Product)

        bind_view = WmsTaskLineViewSet.as_view({"post": "bind"})
        response = bind_view(
            self._request(
                "post",
                f"/api/tasking/task-lines/{self.line.id}/bind/",
                {
                    "content_type_id": content_type.id,
                    "object_id": self.product.id,
                },
            ),
            pk=self.line.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.line.refresh_from_db()
        self.assertEqual(self.line.bound_content_type_id, content_type.id)
        self.assertEqual(self.line.bound_object_id, self.product.id)

        unbind_view = WmsTaskLineViewSet.as_view({"post": "unbind"})
        response = unbind_view(
            self._request("post", f"/api/tasking/task-lines/{self.line.id}/unbind/"),
            pk=self.line.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.line.refresh_from_db()
        self.assertIsNone(self.line.bound_content_type_id)
        self.assertIsNone(self.line.bound_object_id)


class TaskPostingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = Owner.objects.create(
            name="Owner Tasking Concurrent", code="OWN-TASK-C"
        )
        self.warehouse = Warehouse.objects.create(
            code="WH-TASK-C", name="Warehouse Tasking Concurrent"
        )
        self.superuser = get_user_model().objects.create_superuser(
            username="tasking-concurrent-admin",
            email="tasking-concurrent-admin@example.com",
            password="x",
        )

    def test_post_task_executes_once_under_concurrency(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-POST-CONCURRENT-1",
            task_type=WmsTask.TaskType.RECEIVE,
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.APPROVED,
            posting_status=WmsTask.PostingStatus.PENDING,
        )

        handler_entered = threading.Event()
        release_handler = threading.Event()
        handler_calls = 0
        handler_lock = threading.Lock()
        results = [None, None]
        errors = []

        def fake_execute_posting_handler(*, task, note, by_user):
            nonlocal handler_calls
            self.assertEqual(by_user.pk, self.superuser.pk)
            with handler_lock:
                handler_calls += 1
            handler_entered.set()
            if not release_handler.wait(timeout=5):
                raise AssertionError(
                    "timed out waiting to release task posting concurrent test"
                )
            return 2

        def invoke(index, note):
            close_old_connections()
            try:
                results[index] = post_task(task.id, by_user=self.superuser, note=note)
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch(
            "allapp.tasking.services_posting.execute_posting_handler",
            side_effect=fake_execute_posting_handler,
        ):
            thread1 = threading.Thread(target=invoke, args=(0, "first concurrent post"))
            thread1.start()
            self.assertTrue(handler_entered.wait(timeout=5))

            thread2 = threading.Thread(
                target=invoke, args=(1, "second concurrent post")
            )
            thread2.start()

            release_handler.set()
            thread1.join(timeout=5)
            thread2.join(timeout=5)

        if thread1.is_alive() or thread2.is_alive():
            self.fail("concurrent task posting threads did not finish")
        if errors:
            raise errors[0]

        task.refresh_from_db()
        journal = PostingJournal.objects.get(
            src_model="WmsTask", src_id=task.id, tx_type="POST"
        )
        self.assertEqual(handler_calls, 1)
        self.assertEqual(sorted(result["tx_created"] for result in results), [0, 2])
        self.assertEqual(journal.status, "POSTED")
        self.assertEqual(journal.attempt_count, 1)
        self.assertEqual(task.posting_status, WmsTask.PostingStatus.POSTED)
