from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.products.identifier_services import add_product_barcode
from allapp.products.models import (
    Product,
    ProductBarcode,
    ProductPackage,
    ProductUom,
)
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
    WmsTaskLineViewSet,
    WmsTaskViewSet,
)


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
class PickScanIntegrityTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="PICK-INT", name="Pick integrity")
        self.warehouse = Warehouse.objects.create(code="PICKWH", name="Pick WH")
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

    def test_pack_multiplier_applies_to_pick_and_is_returned_and_logged(self):
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="PICK-INTEGRITY-PACK",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.location_1,
            qty_plan=Decimal("20.000"),
            status=WmsTaskLine.Status.RELEASED,
        )

        def resolver(_owner_id, _barcode):
            return {
                "product_id": self.product.id,
                "product_package_id": None,
                "pack_qty": Decimal("4"),
                "code_type": "CARTON",
                "matched_fields": ["carton_barcode"],
                "uom_code": "CTN",
                "uom_name": "箱",
            }

        with mock.patch("allapp.tasking.services._resolver", return_value=resolver):
            result = services.scan_task(
                task.id,
                "BOX-4",
                2,
                location_id=self.location_1.id,
                by_user=self.user,
                client_seq="pack-1",
            )

        line.refresh_from_db()
        scan = TaskScanLog.objects.get(pk=result["scan_id"])
        self.assertEqual(line.qty_done, Decimal("8.000"))
        self.assertEqual(scan.qty_aux, Decimal("2.000"))
        self.assertEqual(scan.qty_base_delta, Decimal("8.000000"))
        self.assertEqual(scan.matched_fields, ["carton_barcode"])
        self.assertIsNone(scan.label_key)
        self.assertEqual(result["resolved"]["effective_qty"], Decimal("8.000"))
        self.assertEqual(result["resolved"]["uom_name"], "箱")

    def test_real_package_and_other_barcodes_apply_snapshot_and_remain_repeatable(self):
        carton_uom = ProductUom.objects.create(
            code="PICK-INT-CTN", name="箱", is_active=True
        )
        package = ProductPackage.objects.create(
            product=self.product,
            uom=carton_uom,
            qty_in_base=12,
        )
        package_barcode = add_product_barcode(
            product=self.product,
            barcode="PICK-REAL-PACKAGE",
            barcode_type=ProductBarcode.BarcodeType.PACKAGE,
            package=package,
        )
        other_barcode = add_product_barcode(
            product=self.product,
            barcode="PICK-REAL-OTHER",
            barcode_type=ProductBarcode.BarcodeType.OTHER,
        )
        task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="PICK-INTEGRITY-REAL-IDENTIFIER",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        line = WmsTaskLine.objects.create(
            task=task,
            product=self.product,
            from_location=self.location_1,
            qty_plan=Decimal("40.000"),
            status=WmsTaskLine.Status.RELEASED,
        )

        package_result = services.scan_task(
            task.id,
            package_barcode.barcode,
            2,
            location_id=self.location_1.id,
            by_user=self.user,
            client_seq="real-package",
        )
        explicit_result = services.scan_task(
            task.id,
            f"{package_barcode.barcode}*5",
            1,
            location_id=self.location_1.id,
            by_user=self.user,
            client_seq="real-package-explicit",
        )
        services.scan_task(
            task.id,
            other_barcode.barcode,
            1,
            location_id=self.location_1.id,
            by_user=self.user,
            client_seq="real-other-1",
        )
        services.scan_task(
            task.id,
            other_barcode.barcode,
            1,
            location_id=self.location_1.id,
            by_user=self.user,
            client_seq="real-other-2",
        )

        line.refresh_from_db()
        package_scan = TaskScanLog.objects.get(pk=package_result["scan_id"])
        other_scans = TaskScanLog.objects.filter(
            task=task,
            code_type=ProductBarcode.BarcodeType.OTHER,
        )
        self.assertEqual(line.qty_done, Decimal("31.000"))
        self.assertEqual(package_result["resolved"]["code_type"], "PACKAGE")
        self.assertEqual(package_result["resolved"]["pack_qty"], Decimal("12"))
        self.assertEqual(package_result["resolved"]["effective_qty"], Decimal("24"))
        self.assertEqual(explicit_result["resolved"]["pack_qty"], Decimal("5"))
        self.assertEqual(explicit_result["resolved"]["effective_qty"], Decimal("5"))
        self.assertEqual(package_scan.product_package_id, package.pk)
        self.assertEqual(package_scan.qty_base_delta, Decimal("24.000000"))
        self.assertEqual(other_scans.count(), 2)
        self.assertFalse(other_scans.exclude(label_key__isnull=True).exists())

    def test_manual_pick_quantity_can_be_corrected_after_saving_zero(self):
        services.adjust_pick_line_qty(
            self.task.id,
            self.line_1.id,
            Decimal("1.000"),
            by_user=self.user,
            client_seq="manual-correction-1",
        )
        zeroed = services.adjust_pick_line_qty(
            self.task.id,
            self.line_1.id,
            Decimal("0.000"),
            by_user=self.user,
            client_seq="manual-correction-2",
        )
        corrected = services.adjust_pick_line_qty(
            self.task.id,
            self.line_1.id,
            Decimal("1.000"),
            by_user=self.user,
            client_seq="manual-correction-3",
        )

        self.line_1.refresh_from_db()
        self.assertEqual(zeroed["qty_done"], Decimal("0.000"))
        self.assertEqual(corrected["qty_done"], Decimal("1.000"))
        self.assertEqual(self.line_1.qty_done, Decimal("1.000"))
        self.assertEqual(
            list(
                TaskScanLog.objects.filter(
                    task=self.task,
                    task_line=self.line_1,
                    reason_code="MANUAL_ADJUST",
                )
                .order_by("id")
                .values_list("qty_base_delta", flat=True)
            ),
            [Decimal("1.000000"), Decimal("-1.000000"), Decimal("1.000000")],
        )


class TaskingRelatedScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="TASK-SCOPE", name="Task scope")
        self.warehouse = Warehouse.objects.create(code="TSCOPEWH", name="Task scope WH")
        self.other_warehouse = Warehouse.objects.create(
            code="TSCOPEOTH", name="Task scope other"
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
        self.unbound_user = get_user_model().objects.create_user(
            username="task-scope-none"
        )
        UserRoleScope.objects.create(
            user=self.user,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.assisted_user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
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
        TaskAssignment.objects.create(task=self.task, assignee=self.assisted_user)
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
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(WmsTask),
                codename="taskconfirm_as_wh_manager",
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

    def _list_response(self, viewset, user):
        request = self.factory.get("/api/tasking/resource/")
        force_authenticate(request, user=user)
        return viewset.as_view({"get": "list"})(request)

    def _ids(self, viewset, user):
        response = self._list_response(viewset, user)
        self.assertEqual(response.status_code, 200, response.data)
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
                    self._list_response(viewset, self.plain_warehouse_user).status_code,
                    403,
                )
                self.assertEqual(
                    self._list_response(viewset, self.unbound_user).status_code,
                    403,
                )

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_complete_assisted_operator_without_view_permissions_sees_only_assisted_source(
        self,
    ):
        cases = (
            (WmsTaskViewSet, self.task.id),
            (WmsTaskLineViewSet, self.line.id),
            (TaskAssignmentViewSet, TaskAssignment.objects.get(task=self.task).id),
            (TaskStatusLogViewSet, TaskStatusLog.objects.get(task=self.task).id),
            (TaskScanLogViewSet, TaskScanLog.objects.get(task=self.task).id),
        )
        for viewset, allowed_id in cases:
            with self.subTest(viewset=viewset.__name__):
                self.assertEqual(
                    self._ids(viewset, self.assisted_user),
                    {allowed_id},
                )

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_mode_does_not_reenable_task_api_for_unscoped_user(self):
        response = self._list_response(WmsTaskLineViewSet, self.unbound_user)
        self.assertEqual(response.status_code, 403)

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

        self.assertEqual(
            self._list_response(WmsTaskViewSet, self.unbound_user).status_code,
            403,
        )
        self.assertEqual(
            self._list_response(WmsTaskLineViewSet, self.unbound_user).status_code,
            403,
        )
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

        response = WmsTaskViewSet.as_view({"post": "scan"})(request, pk=self.task.id)

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


@override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
class TaskingRoleCapabilityApiTests(TestCase):
    """Raw task APIs are for warehouse operations, not boss/owner roles."""

    def setUp(self):
        self.owner = Owner.objects.create(code="TASK-ROLE", name="Task role owner")
        self.warehouse = Warehouse.objects.create(
            code="TASKROLEWH", name="Task role warehouse"
        )
        self.manager = get_user_model().objects.create_user(
            username="task-role-manager", password="x", warehouse=self.warehouse
        )
        self.operator = get_user_model().objects.create_user(
            username="task-role-operator", password="x", warehouse=self.warehouse
        )
        self.other_operator = get_user_model().objects.create_user(
            username="task-role-other-operator", password="x", warehouse=self.warehouse
        )
        self.boss = get_user_model().objects.create_user(
            username="task-role-boss", password="x", warehouse=self.warehouse
        )
        self.owner_manager = get_user_model().objects.create_user(
            username="task-role-owner-manager", password="x", owner=self.owner
        )
        UserRoleScope.objects.create(
            user=self.manager,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.other_operator,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.boss,
            role=UserRoleScope.Role.WAREHOUSE_BOSS,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=self.owner_manager,
            role=UserRoleScope.Role.OWNER_MANAGER,
            owner=self.owner,
        )

        task_ct = ContentType.objects.get_for_model(WmsTask)
        self.manager.user_permissions.add(
            Permission.objects.get(content_type=task_ct, codename="view_wmstask"),
            Permission.objects.get(content_type=task_ct, codename="add_wmstask"),
            Permission.objects.get(
                content_type=task_ct, codename="taskconfirm_as_wh_manager"
            ),
        )
        for user in (self.operator, self.other_operator):
            user.user_permissions.add(
                Permission.objects.get(
                    content_type=task_ct, codename="claim_task_as_wh_operator"
                )
            )
        # A plain model read grant must not turn a boss or owner manager into
        # an operational task user.
        self.boss.user_permissions.add(
            Permission.objects.get(content_type=task_ct, codename="view_wmstask")
        )
        self.owner_manager.user_permissions.add(
            Permission.objects.get(content_type=task_ct, codename="view_wmstask")
        )

        self.own_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-ROLE-OWN",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        self.other_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-ROLE-OTHER",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        self.pool_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="TASK-ROLE-POOL",
            task_type=WmsTask.TaskType.PICK,
            status=WmsTask.Status.RELEASED,
        )
        TaskAssignment.objects.create(task=self.own_task, assignee=self.operator)
        TaskAssignment.objects.create(
            task=self.other_task, assignee=self.other_operator
        )
        self.factory = APIRequestFactory()

    def _request(self, method, path, data=None, *, user):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=user)
        return request

    @staticmethod
    def _rows(response):
        return (
            response.data.get("results", response.data)
            if isinstance(response.data, dict)
            else response.data
        )

    def test_boss_and_owner_manager_are_denied_raw_task_api_even_with_view_permission(
        self,
    ):
        view = WmsTaskViewSet.as_view({"get": "list"})
        for user in (self.boss, self.owner_manager):
            with self.subTest(user=user.username):
                response = view(self._request("get", "/api/tasking/tasks/", user=user))
                self.assertEqual(response.status_code, 403)

    def test_operator_sees_only_own_or_pool_tasks_and_cannot_operate_other_assignment(
        self,
    ):
        list_view = WmsTaskViewSet.as_view({"get": "list"})
        response = list_view(
            self._request("get", "/api/tasking/tasks/", user=self.operator)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["id"] for row in self._rows(response)},
            {self.own_task.id, self.pool_task.id},
        )

        scan_view = WmsTaskViewSet.as_view({"post": "scan"})
        other_response = scan_view(
            self._request(
                "post",
                f"/api/tasking/tasks/{self.other_task.id}/scan/",
                {"barcode": "blocked"},
                user=self.operator,
            ),
            pk=self.other_task.id,
        )
        self.assertEqual(other_response.status_code, 404)

        with mock.patch(
            "allapp.tasking.views.task_svc.post_scan",
            return_value={"detail": "scanned"},
            create=True,
        ):
            own_response = scan_view(
                self._request(
                    "post",
                    f"/api/tasking/tasks/{self.own_task.id}/scan/",
                    {"barcode": "allowed"},
                    user=self.operator,
                ),
                pk=self.own_task.id,
            )
        self.assertEqual(own_response.status_code, 200)

    def test_operator_cannot_create_or_release_tasks_with_only_model_permissions(self):
        task_ct = ContentType.objects.get_for_model(WmsTask)
        self.operator.user_permissions.add(
            Permission.objects.get(content_type=task_ct, codename="add_wmstask")
        )
        create_view = WmsTaskViewSet.as_view({"post": "create"})
        create_response = create_view(
            self._request(
                "post",
                "/api/tasking/tasks/",
                {
                    "owner": self.owner.id,
                    "warehouse": self.warehouse.id,
                    "task_no": "TASK-ROLE-OP-CREATE",
                    "task_type": WmsTask.TaskType.RECEIVE,
                },
                user=self.operator,
            )
        )
        self.assertEqual(create_response.status_code, 403)

        release_view = WmsTaskViewSet.as_view({"post": "release"})
        release_response = release_view(
            self._request(
                "post",
                f"/api/tasking/tasks/{self.own_task.id}/release/",
                user=self.operator,
            ),
            pk=self.own_task.id,
        )
        self.assertEqual(release_response.status_code, 403)
