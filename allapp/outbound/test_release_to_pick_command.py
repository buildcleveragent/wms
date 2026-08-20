from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from allapp.outbound.management.commands.release_to_pick import Command
from allapp.outbound.models import OutboundOrder


class ReleaseToPickCommandTests(SimpleTestCase):
    def run_command(self, order, *, no_backorder=False):
        command = Command()
        operator = SimpleNamespace(pk=11, username="warehouse-manager")
        with (
            patch.object(command, "_get_order", return_value=order),
            patch.object(command, "_get_operator", return_value=operator),
            patch.object(command, "_validate_scope") as validate_scope,
            patch(
                "allapp.outbound.management.commands.release_to_pick.record_audit_event"
            ) as audit,
            patch(
                "allapp.outbound.management.commands.release_to_pick."
                "outbound_services.confirm_warehouse_order"
            ) as confirm,
            patch(
                "allapp.outbound.management.commands.release_to_pick."
                "outbound_services.approve_and_release_order"
            ) as assisted_release,
            patch(
                "allapp.outbound.management.commands.release_to_pick."
                "outbound_services.require_assisted_owner_warehouse"
            ) as require_binding,
        ):
            confirm.return_value = (order, SimpleNamespace(pk=21))
            assisted_release.return_value = SimpleNamespace(pk=22)
            command.handle(
                order=order.pk,
                order_no=None,
                operator=operator.username,
                no_backorder=no_backorder,
            )
        return validate_scope, audit, confirm, assisted_release, require_binding

    def test_standard_order_only_calls_warehouse_confirmation(self):
        order = SimpleNamespace(
            pk=1,
            processing_mode=OutboundOrder.ProcessingMode.STANDARD,
            owner=SimpleNamespace(pk=3),
            warehouse_id=4,
        )

        validate_scope, audit, confirm, assisted_release, require_binding = self.run_command(order)

        validate_scope.assert_called_once()
        confirm.assert_called_once()
        assisted_release.assert_not_called()
        require_binding.assert_not_called()
        self.assertTrue(audit.call_args.kwargs["succeeded"])

    def test_assisted_order_requires_binding_and_honors_no_backorder(self):
        owner = SimpleNamespace(pk=3)
        order = SimpleNamespace(
            pk=2,
            processing_mode=OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED,
            owner=owner,
            warehouse_id=4,
        )

        _, audit, confirm, assisted_release, require_binding = self.run_command(
            order, no_backorder=True
        )

        require_binding.assert_called_once_with(owner, 4)
        assisted_release.assert_called_once()
        self.assertFalse(assisted_release.call_args.kwargs["allow_backorder"])
        confirm.assert_not_called()
        self.assertTrue(audit.call_args.kwargs["succeeded"])

    def test_no_backorder_is_rejected_for_standard_order(self):
        command = Command()
        order = SimpleNamespace(
            pk=1,
            processing_mode=OutboundOrder.ProcessingMode.STANDARD,
        )
        operator = SimpleNamespace(pk=11)
        with (
            patch.object(command, "_get_order", return_value=order),
            patch.object(command, "_get_operator", return_value=operator),
            patch.object(command, "_validate_scope"),
            self.assertRaisesMessage(CommandError, "仅适用于仓库代办出库"),
        ):
            command.handle(
                order=1,
                order_no=None,
                operator="warehouse-manager",
                no_backorder=True,
            )

    def test_domain_failure_is_audited_and_returned_as_command_error(self):
        command = Command()
        order = SimpleNamespace(
            pk=1,
            processing_mode=OutboundOrder.ProcessingMode.STANDARD,
        )
        operator = SimpleNamespace(pk=11)
        with (
            patch.object(command, "_get_order", return_value=order),
            patch.object(command, "_get_operator", return_value=operator),
            patch.object(command, "_validate_scope"),
            patch(
                "allapp.outbound.management.commands.release_to_pick."
                "outbound_services.confirm_warehouse_order",
                side_effect=ValidationError("invalid state"),
            ),
            patch(
                "allapp.outbound.management.commands.release_to_pick.record_audit_event"
            ) as audit,
            self.assertRaises(CommandError),
        ):
            command.handle(
                order=1,
                order_no=None,
                operator="warehouse-manager",
                no_backorder=False,
            )
        self.assertFalse(audit.call_args.kwargs["succeeded"])
