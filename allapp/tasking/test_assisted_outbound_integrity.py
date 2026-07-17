from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.products.models import Product, ProductUom
from allapp.tasking import services
from allapp.tasking.models import (
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.views import (
    TaskAssignmentViewSet,
    TaskScanLogViewSet,
    TaskStatusLogViewSet,
    WmsTaskViewSet,
    WmsTaskLineViewSet,
)


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
class PickScanIntegrityTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="PICK-INT", name="Pick integrity")
        self.warehouse = Warehouse.objects.create(code="PINT-WH", name="Pick WH")
        Subwarehouse.objects.create(
            warehouse=self.warehouse,
            code="PINT",
            name="Pick integrity subwarehouse",
        )
        self.location_1 = Location.objects.create(
            warehouse=self.warehouse, code="PINT-01-01-01", name="Pick L1"
        )
        self.location_2 = Location.objects.create(
            warehouse=self.warehouse, code="PINT-01-01-02", name="Pick L2"
        )
        uom = ProductUom.objects.create(code="PICK-INT-EA", name="EA", is_active=True)
        self.product = Product.objects.create(
            owner=self.owner,
            code="PICK-INT-SKU",
            sku="PICK-INT-SKU",
            name="Pick SKU",
            base_uom=uom,
            volume=Decimal("0.100000"),
            price=Decimal("10.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="pick-integrity-user", warehouse=self.warehouse
        )
        self.task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="PICK-INTEGRITY-1",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        self.line_1 = WmsTaskLine.objects.create(
            task=self.task,
            product=self.product,
            from_location=self.location_1,
            qty_plan=Decimal("1.000"),
            status=WmsTaskLine.Status.RELEASED,
        )
        self.line_2 = WmsTaskLine.objects.create(
            task=self.task,
            product=self.product,
            from_location=self.location_2,
            qty_plan=Decimal("2.000"),
            status=WmsTaskLine.Status.RELEASED,
        )

    def test_pick_scans_select_unfinished_location_lines_and_remain_append_only(self):
        def resolver(_owner_id, _barcode):
            return {
                "product_id": self.product.id,
                "pack_qty": 1,
                "code_type": "SKU",
            }

        with mock.patch("allapp.tasking.services._resolver", return_value=resolver):
            at_location = services.scan_task(
                self.task.id,
                self.product.sku,
                1,
                location_id=self.location_2.id,
                by_user=self.user,
                client_seq="pick-1",
            )
            first_unfinished = services.scan_task(
                self.task.id,
                self.product.sku,
                1,
                by_user=self.user,
                client_seq="pick-2",
            )
            next_unfinished = services.scan_task(
                self.task.id,
                self.product.sku,
                1,
                by_user=self.user,
                client_seq="pick-3",
            )
            retried = services.scan_task(
                self.task.id,
                self.product.sku,
                1,
                by_user=self.user,
                client_seq="pick-2",
            )

        services.adjust_pick_line_qty(
            self.task.id,
            self.line_2.id,
            Decimal("1.000"),
            by_user=self.user,
            client_seq="manual-1",
        )

        self.assertEqual(at_location["line_id"], self.line_2.id)
        self.assertEqual(first_unfinished["line_id"], self.line_1.id)
        self.assertEqual(next_unfinished["line_id"], self.line_2.id)
        self.assertTrue(retried["idempotent"])
        self.assertEqual(retried["line_id"], self.line_1.id)

        logs = list(TaskScanLog.objects.filter(task=self.task).order_by("id"))
        self.assertEqual(len(logs), 4)
        self.assertTrue(all(log.status == TaskScanLog.ScanStatus.OK for log in logs))
        self.assertEqual(
            sum((log.qty_base_delta for log in logs), Decimal("0")),
            Decimal("2.000000"),
        )
        self.line_1.refresh_from_db()
        self.line_2.refresh_from_db()
        self.assertEqual(self.line_1.qty_done + self.line_2.qty_done, Decimal("2.000"))


class TaskingRelatedScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="TASK-SCOPE", name="Task scope")
        self.warehouse = Warehouse.objects.create(code="TSCOPE-WH", name="Task scope WH")
        self.other_warehouse = Warehouse.objects.create(
            code="TSCOPE-O", name="Task scope other"
        )
        self.user = get_user_model().objects.create_user(
            username="task-scope-wh-user", warehouse=self.warehouse
        )
        self.plain_warehouse_user = get_user_model().objects.create_user(
            username="task-scope-plain-wh", warehouse=self.warehouse
        )
        self.assisted_user = get_user_model().objects.create_user(
            username="task-scope-assisted", warehouse=self.warehouse
        )
        self.unbound_user = get_user_model().objects.create_user(username="task-scope-none")
        self.task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-SCOPE-1",
            task_type=WmsTask.TaskType.RECEIVE,
            source_model="outboundorder",
            source_pk="99",
        )
        self.other_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            task_no="TASK-SCOPE-2",
            task_type=WmsTask.TaskType.RECEIVE,
        )
        self.line = WmsTaskLine.objects.create(task=self.task)
        self.other_line = WmsTaskLine.objects.create(task=self.other_task)
        TaskAssignment.objects.create(task=self.task, assignee=self.user)
        TaskAssignment.objects.create(task=self.other_task, assignee=self.user)
        TaskStatusLog.objects.create(
            task=self.task,
            old_status=WmsTask.Status.DRAFT,
            new_status=WmsTask.Status.READY,
        )
        TaskStatusLog.objects.create(
            task=self.other_task,
            old_status=WmsTask.Status.DRAFT,
            new_status=WmsTask.Status.READY,
        )
        TaskScanLog.objects.create(
            owner=self.owner,
            task=self.task,
            task_line=self.line,
            qty_base_delta=1,
            fp="task-scope-scan-1",
            scan_snapshot_rev=0,
        )
        TaskScanLog.objects.create(
            owner=self.owner,
            task=self.other_task,
            task_line=self.other_line,
            qty_base_delta=1,
            fp="task-scope-scan-2",
            scan_snapshot_rev=0,
        )
        for model in (
            WmsTask,
            WmsTaskLine,
            TaskAssignment,
            TaskStatusLog,
            TaskScanLog,
        ):
            content_type = ContentType.objects.get_for_model(model)
            self.user.user_permissions.add(
                Permission.objects.get(
                    content_type=content_type,
                    codename=f"view_{model._meta.model_name}",
                )
            )
        self.assisted_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="outbound",
                codename="process_warehouse_assisted_outbound",
            ),
            Permission.objects.get(
                content_type__app_label="tasking",
                codename="claim_task_as_wh_operator",
            ),
        )
        self.factory = APIRequestFactory()

    def _ids(self, viewset, user):
        request = self.factory.get("/api/tasking/resource/")
        force_authenticate(request, user=user)
        response = viewset.as_view({"get": "list"})(request)
        rows = (
            response.data.get("results", response.data)
            if isinstance(response.data, dict)
            else response.data
        )
        return {row["id"] for row in rows}

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_related_task_resources_follow_task_warehouse_and_fail_closed(self):
        cases = (
            (WmsTaskViewSet, self.task.id),
            (WmsTaskLineViewSet, self.line.id),
            (TaskAssignmentViewSet, TaskAssignment.objects.get(task=self.task).id),
            (TaskStatusLogViewSet, TaskStatusLog.objects.get(task=self.task).id),
            (TaskScanLogViewSet, TaskScanLog.objects.get(task=self.task).id),
        )
        for viewset, allowed_id in cases:
            with self.subTest(viewset=viewset.__name__):
                self.assertEqual(self._ids(viewset, self.user), {allowed_id})
                self.assertEqual(
                    self._ids(viewset, self.plain_warehouse_user), set()
                )
                self.assertEqual(self._ids(viewset, self.unbound_user), set())

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_complete_assisted_operator_without_view_permissions_sees_only_assisted_source(self):
        cases = (
            (WmsTaskViewSet, self.task.id),
            (WmsTaskLineViewSet, self.line.id),
            (TaskAssignmentViewSet, TaskAssignment.objects.get(task=self.task).id),
            (TaskStatusLogViewSet, TaskStatusLog.objects.get(task=self.task).id),
            (TaskScanLogViewSet, TaskScanLog.objects.get(task=self.task).id),
        )
        with mock.patch(
            "allapp.tasking.views.assisted_order_source_ids",
            return_value=["99"],
        ):
            for viewset, allowed_id in cases:
                with self.subTest(viewset=viewset.__name__):
                    self.assertEqual(
                        self._ids(viewset, self.assisted_user),
                        {allowed_id},
                    )

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_mode_logs_would_deny_but_keeps_legacy_rows(self):
        with self.assertLogs("allapp.tasking.views", level="WARNING") as captured:
            ids = self._ids(WmsTaskLineViewSet, self.unbound_user)

        self.assertEqual(ids, {self.line.id, self.other_line.id})
        self.assertTrue(any("tasking.authz.would_deny" in line for line in captured.output))

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_mode_never_exposes_assisted_task_resources(self):
        customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.user,
            code="TASK-SCOPE-ASSISTED-CUSTOMER",
            name="Task scope assisted customer",
        )
        assisted_order = OutboundOrder.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            customer=customer,
            processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
        )
        WmsTask.objects.filter(pk=self.task.pk).update(
            source_model="outboundorder", source_pk=str(assisted_order.pk)
        )

        with self.assertLogs("allapp.tasking.views", level="WARNING"):
            task_ids = self._ids(WmsTaskViewSet, self.unbound_user)
            line_ids = self._ids(WmsTaskLineViewSet, self.unbound_user)

        self.assertEqual(task_ids, {self.other_task.id})
        self.assertEqual(line_ids, {self.other_line.id})
        self.assertEqual(
            self._ids(WmsTaskViewSet, self.assisted_user),
            {self.task.id},
        )

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_generic_scan_requires_operator_permission(self):
        request = self.factory.post(
            f"/api/tasking/tasks/{self.task.id}/scan/", {}, format="json"
        )
        force_authenticate(request, user=self.user)

        response = WmsTaskViewSet.as_view({"post": "scan"})(
            request, pk=self.task.id
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_bind_cannot_bypass_task_line_change_permission(self):
        request = self.factory.post(
            f"/api/tasking/task-lines/{self.line.id}/bind/",
            {"content_type_id": None, "object_id": None},
            format="json",
        )
        force_authenticate(request, user=self.user)

        response = WmsTaskLineViewSet.as_view({"post": "bind"})(
            request, pk=self.line.id
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_generic_task_create_rejects_cross_warehouse_even_with_add_permission(self):
        self.plain_warehouse_user.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTask),
                codename="add_wmstask",
            )
        )
        request = self.factory.post(
            "/api/tasking/tasks/",
            {
                "owner": self.owner.id,
                "warehouse": self.other_warehouse.id,
                "task_no": "TASK-SCOPE-CROSS-CREATE",
                "task_type": WmsTask.TaskType.RECEIVE,
            },
            format="json",
        )
        force_authenticate(request, user=self.plain_warehouse_user)

        response = WmsTaskViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            WmsTask.objects.filter(task_no="TASK-SCOPE-CROSS-CREATE").exists()
        )
