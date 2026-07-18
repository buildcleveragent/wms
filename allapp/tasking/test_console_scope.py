from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Customer, Owner
from allapp.console import views_op
from allapp.locations.models import Warehouse
from allapp.outbound.models import OutboundOrder
from allapp.tasking import views_console
from allapp.tasking.models import TaskAssignment, WmsTask


def _permission(app_label, codename):
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


class ConsoleTaskScopeTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(name="Console Owner", code="CONOWN")
        self.warehouse = Warehouse.objects.create(code="CONWH", name="Console Warehouse")
        self.other_warehouse = Warehouse.objects.create(
            code="CONWH2", name="Other Console Warehouse"
        )
        self.unbound_user = get_user_model().objects.create_user(
            username="console-unbound", password="x"
        )
        self.customer = Customer.objects.create(
            owner=self.owner,
            salesperson=self.unbound_user,
            code="CONCUST",
            name="Console Customer",
        )
        self.standard_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="CONSOLE-STANDARD",
            task_type=WmsTask.TaskType.PICK,
        )
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            order_no="CONSOLE-ASSISTED",
            processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
        )
        self.assisted_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            task_no="CONSOLE-ASSISTED-TASK",
            task_type=WmsTask.TaskType.PICK,
            source_model="outboundorder",
            source_pk=str(order.pk),
        )

    def _assisted_operator(self, username):
        user = get_user_model().objects.create_user(
            username=username,
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_OPERATOR,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(
            _permission("tasking", "claim_task_as_wh_operator"),
            _permission("outbound", "process_warehouse_assisted_outbound"),
        )
        TaskAssignment.objects.create(task=self.assisted_task, assignee=user)
        return user

    def _warehouse_manager(self, username):
        user = get_user_model().objects.create_user(
            username=username,
            password="x",
            warehouse=self.warehouse,
        )
        UserRoleScope.objects.create(
            user=user,
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            warehouse=self.warehouse,
        )
        user.user_permissions.add(
            _permission("tasking", "view_wmstask"),
            _permission("tasking", "taskconfirm_as_wh_manager"),
        )
        return user

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_is_telemetry_only_and_never_expands_task_visibility(self):
        assisted_operator = self._assisted_operator(
            "console-shadow-assisted-operator"
        )
        for module in (views_console, views_op):
            visible = module._scope_task_queryset(
                WmsTask.objects.all(),
                self.unbound_user,
                endpoint="test.console.scope",
            )
            self.assertFalse(visible.exists())
            self.assertNotIn(self.assisted_task, visible)
            assisted_visible = module._scope_task_queryset(
                WmsTask.objects.all(),
                assisted_operator,
                endpoint="test.console.scope.assisted",
            )
            self.assertNotIn(self.standard_task, assisted_visible)
            self.assertIn(self.assisted_task, assisted_visible)

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="enforce")
    def test_enforce_is_fail_closed_and_warehouse_view_is_scoped(self):
        warehouse_user = self._warehouse_manager("console-warehouse-view")
        assisted_operator = self._assisted_operator("console-assisted-operator")
        other_task = WmsTask.objects.create(
            owner=self.owner,
            warehouse=self.other_warehouse,
            task_no="CONSOLE-OTHER-WH",
            task_type=WmsTask.TaskType.PICK,
        )

        for module in (views_console, views_op):
            self.assertFalse(
                module._scope_task_queryset(
                    WmsTask.objects.all(),
                    self.unbound_user,
                    endpoint="test.console.enforce",
                ).exists()
            )
            visible = module._scope_task_queryset(
                WmsTask.objects.all(),
                warehouse_user,
                endpoint="test.console.enforce",
            )
            self.assertIn(self.standard_task, visible)
            self.assertIn(self.assisted_task, visible)
            self.assertNotIn(other_task, visible)
            assisted_visible = module._scope_task_queryset(
                WmsTask.objects.all(),
                assisted_operator,
                endpoint="test.console.enforce.assisted",
            )
            self.assertNotIn(self.standard_task, assisted_visible)
            self.assertIn(self.assisted_task, assisted_visible)
            self.assertFalse(
                module._task_action_allowed(
                    self.unbound_user,
                    self.standard_task,
                    endpoint="test.console.enforce.action",
                )
            )

    @override_settings(OUTBOUND_LEGACY_AUTHZ_MODE="shadow")
    def test_shadow_action_compatibility_never_applies_to_assisted_task(self):
        for module in (views_console, views_op):
            self.assertFalse(
                module._task_action_allowed(
                    self.unbound_user,
                    self.standard_task,
                    endpoint="test.console.action",
                )
            )
            self.assertFalse(
                module._task_action_allowed(
                    self.unbound_user,
                    self.assisted_task,
                    endpoint="test.console.action",
                )
            )

    def test_line_edit_warehouse_check_does_not_allow_unbound_user(self):
        line = SimpleNamespace(task=SimpleNamespace(warehouse_id=self.warehouse.pk))
        view = views_op.OpLineEditView()

        self.assertFalse(view._check_wh(self.unbound_user, line))
