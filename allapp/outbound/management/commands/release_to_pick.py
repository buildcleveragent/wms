"""Audited command-line entrypoint for releasing outbound orders."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.outbound import services as outbound_services
from allapp.outbound.models import OutboundOrder


class Command(BaseCommand):
    help = "以明确操作者执行出库单仓库确认，并生成或发布拣货任务"

    def add_arguments(self, parser):
        selector = parser.add_mutually_exclusive_group(required=True)
        selector.add_argument("--order", type=int, help="出库单 ID")
        selector.add_argument("--order-no", type=str, help="出库单号")
        parser.add_argument(
            "--operator",
            required=True,
            help="执行人的用户名；必须为有效用户且具备该仓库确认权限",
        )
        parser.add_argument(
            "--no-backorder",
            action="store_true",
            help="仅代办出库有效：库存不足即失败",
        )

    def _get_order(self, options):
        queryset = OutboundOrder.objects.select_related("owner", "warehouse")
        try:
            if options.get("order") is not None:
                return queryset.get(pk=options["order"])
            return queryset.get(order_no=options["order_no"])
        except OutboundOrder.DoesNotExist as exc:
            value = options.get("order") or options.get("order_no")
            raise CommandError(f"未找到出库单：{value}") from exc

    def _get_operator(self, username):
        try:
            user = get_user_model().objects.get(username=username, is_active=True)
        except get_user_model().DoesNotExist as exc:
            raise CommandError("--operator 必须是有效用户的用户名。") from exc
        if not (
            user.is_superuser
            or user.has_perm("outbound.approve_outbound_as_wh_manager")
            or user.has_perm("tasking.taskconfirm_as_wh_manager")
        ):
            raise CommandError("操作者没有仓库确认权限。")
        return user

    def _validate_scope(self, order, operator):
        if operator.is_superuser:
            return
        scope = AccessScope.for_user(operator)
        if not scope.is_valid or order.warehouse_id not in scope.warehouse_ids:
            raise CommandError("操作者不在该出库单仓库范围内。")

    def handle(self, *args, **options):
        order = self._get_order(options)
        operator = self._get_operator(options["operator"])
        self._validate_scope(order, operator)
        assisted = order.processing_mode == OutboundOrder.ProcessingMode.WAREHOUSE_ASSISTED
        if options["no_backorder"] and not assisted:
            raise CommandError("--no-backorder 仅适用于仓库代办出库。")

        try:
            if assisted:
                outbound_services.require_assisted_owner_warehouse(order.owner, order.warehouse_id)
                task = outbound_services.approve_and_release_order(
                    order,
                    by_user=operator,
                    allow_backorder=not options["no_backorder"],
                )
            else:
                _, task = outbound_services.confirm_warehouse_order(order, by_user=operator)
        except (PermissionDenied, ValidationError) as exc:
            record_audit_event(
                action="outbound.release_to_pick.cli",
                module="outbound",
                user=operator,
                obj=order,
                succeeded=False,
                metadata={"error": str(exc)[:200]},
            )
            raise CommandError(str(exc)) from exc

        record_audit_event(
            action="outbound.release_to_pick.cli",
            module="outbound",
            user=operator,
            obj=order,
            succeeded=True,
            metadata={
                "task_id": getattr(task, "pk", None),
                "processing_mode": order.processing_mode,
            },
        )
        task_label = getattr(task, "pk", None) or "等待补货"
        self.stdout.write(self.style.SUCCESS(f"OK: 拣货任务={task_label}"))
