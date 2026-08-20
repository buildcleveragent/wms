"""Authenticated warehouse operations dashboard and its summary API."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.generic import TemplateView, View

from allapp.accounts.access import AccessScope
from allapp.inbound.models import InboundOrder
from allapp.outbound.models import OutboundOrder
from allapp.pos.stats import build_pos_dashboard_payload
from allapp.reports.services_operations import (
    OperationFilters,
    build_operations_summary,
)
from allapp.tasking.models import WmsTask, WmsTaskLine

DASHBOARD_DAYS = 30


def _today() -> date:
    now = timezone.now()
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


def dashboard_range() -> tuple[date, date]:
    end = _today()
    return end - timedelta(days=DASHBOARD_DAYS - 1), end


def _quantity_text(value) -> str:
    return format(Decimal(value or 0), ".3f")


def _can_view_operations(user) -> bool:
    return bool(
        user.is_superuser
        or user.has_perm("reports.view_warehouse_operations")
        or user.has_perm("reports.view_owner_operations")
    )


def _empty_no_order_receive(*, start: date, end: date) -> dict[str, Any]:
    return {
        "available": False,
        "reason": "permission_denied",
        "period": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": DASHBOARD_DAYS,
        },
        "summary": {"orders": 0, "lines": 0, "qty": "0.000"},
        "trend": {"dates": [], "qty": []},
    }


def no_order_receive_overview(user, *, start: date, end: date) -> dict[str, Any]:
    operations = build_operations_summary(
        user=user,
        filters=OperationFilters(
            start_date=start,
            end_date=end,
            direction="inbound",
            metric_basis="actual",
            receive_source="no_order",
        ),
    )
    summary = operations["summary"]["inbound"]
    return {
        "available": True,
        "reason": "",
        "period": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": DASHBOARD_DAYS,
        },
        "summary": {
            "orders": int(summary["orders"]),
            "lines": int(summary["lines"]),
            "qty": _quantity_text(summary["qty"]),
        },
        "trend": {
            "dates": [row["date"] for row in operations["trend"]],
            "qty": [_quantity_text(row["inbound_qty"]) for row in operations["trend"]],
        },
    }


def _empty_pos(*, start: date, today: date) -> dict[str, Any]:
    return {
        "available": False,
        "reason": "permission_denied",
        "today": {
            "date": today.isoformat(),
            "summary": {
                "completed_count": 0,
                "net_amount": "0.00",
                "received_amount": "0.00",
                "return_count": 0,
                "return_amount": "0.00",
            },
            "cashiers": [],
        },
        "trend_30d": {"dates": [], "net_amount": []},
        "period": {
            "start_date": start.isoformat(),
            "end_date": today.isoformat(),
            "days": DASHBOARD_DAYS,
        },
    }


def tasks_overview(
    scope: AccessScope,
    *,
    start: date,
    end: date,
) -> dict[str, dict[str, int]]:
    """Count non-cancelled tasks created in the dashboard date cohort."""

    queryset = scope.filter_queryset(
        WmsTask.objects.filter(created_at__date__range=(start, end))
    ).exclude(status=WmsTask.Status.CANCELLED)
    result: dict[str, dict[str, int]] = {}
    for key, task_type in (
        ("putaway", WmsTask.TaskType.PUTAWAY),
        ("pick", WmsTask.TaskType.PICK),
    ):
        task_queryset = queryset.filter(task_type=task_type)
        result[key] = {
            "total": task_queryset.count(),
            "done": task_queryset.filter(status=WmsTask.Status.COMPLETED).count(),
        }
    return result


def orders_timeseries(
    model,
    scope: AccessScope,
    *,
    start: date,
    end: date,
) -> dict[str, list[Any]]:
    """Return daily order cohorts using biz_date and the authoritative close flag."""

    queryset = scope.filter_queryset(model.objects.filter(biz_date__range=(start, end))).exclude(
        approval_status="CANCELLED"
    )
    rows = (
        queryset.values("biz_date")
        .annotate(
            total_count=Count("id"),
            closed_count=Count("id", filter=Q(is_closed=True)),
        )
        .order_by("biz_date")
    )
    totals = {row["biz_date"]: row["total_count"] for row in rows}
    closed = {row["biz_date"]: row["closed_count"] for row in rows}

    dates: list[str] = []
    total_values: list[int] = []
    finished_values: list[int] = []
    current = start
    while current <= end:
        dates.append(current.isoformat())
        total_values.append(int(totals.get(current, 0)))
        finished_values.append(int(closed.get(current, 0)))
        current += timedelta(days=1)
    return {
        "dates": dates,
        "total": total_values,
        # Keep the existing response key for compatibility; the UI labels it 已关闭.
        "finished": finished_values,
    }


def backlog_by_status(model, scope: AccessScope) -> dict[str, list[Any]]:
    """Group all currently open, non-cancelled orders by approval status."""

    rows = (
        scope.filter_queryset(model.objects.filter(is_closed=False))
        .exclude(approval_status="CANCELLED")
        .values("approval_status")
        .annotate(count=Count("id"))
        .order_by("-count", "approval_status")
    )
    status_labels = dict(model.APPROVAL_CHOICES)
    return {
        "statuses": [row["approval_status"] for row in rows],
        "labels": [
            status_labels.get(row["approval_status"], row["approval_status"]) for row in rows
        ],
        "values": [int(row["count"]) for row in rows],
    }


def efficiency_ranking(
    task_type: str,
    scope: AccessScope,
    *,
    day: date,
) -> dict[str, Any]:
    """Rank operators by task lines they actually completed on the given day."""

    queryset = scope.filter_queryset(
        WmsTaskLine.objects.filter(
            task__task_type=task_type,
            status=WmsTaskLine.Status.COMPLETED,
            finished_at__date=day,
            finished_by__isnull=False,
        ).exclude(task__status=WmsTask.Status.CANCELLED),
        owner_field="task__owner_id",
        warehouse_field="task__warehouse_id",
    )
    rows = (
        queryset.values("finished_by_id", "finished_by__username")
        .annotate(count=Count("id"))
        .order_by("-count", "finished_by__username", "finished_by_id")[:10]
    )
    return {
        "labels": [
            row["finished_by__username"] or f"用户 #{row['finished_by_id']}" for row in rows
        ],
        "values": [int(row["count"]) for row in rows],
        "unit": "完成任务行数",
    }


class DashboardHomeView(LoginRequiredMixin, TemplateView):
    template_name = "console/home.html"


class DashboardSummaryApi(LoginRequiredMixin, View):
    """Return the real operational facts rendered by the console homepage."""

    def get(self, request: HttpRequest):
        scope = AccessScope.for_user(request.user)
        if not scope.is_valid:
            raise PermissionDenied("当前账号没有有效的 WMS 数据范围。")

        start, end = dashboard_range()
        no_order_receive = (
            no_order_receive_overview(request.user, start=start, end=end)
            if _can_view_operations(request.user)
            else _empty_no_order_receive(start=start, end=end)
        )
        if request.user.has_perm("pos.view_possale"):
            pos = {
                "available": True,
                "reason": "",
                "period": {
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "days": DASHBOARD_DAYS,
                },
                **build_pos_dashboard_payload(
                    access_scope=scope,
                    today=end,
                    trend_start=start,
                ),
            }
        else:
            pos = _empty_pos(start=start, today=end)
        data = {
            "data_as_of": timezone.now().isoformat(),
            "range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": DASHBOARD_DAYS,
            },
            "scope": scope.as_dict(),
            "definitions": {
                "task_total": "近30天创建且未取消的任务",
                "task_done": "上述任务中状态为已完成的任务",
                "order_total": "按业务日期统计且未取消的订单",
                "order_finished": "上述订单中已关闭的订单",
                "backlog": "当前未关闭且未取消的订单",
                "efficiency": "当日由作业员实际完成的任务行数",
                "no_order_receive": "近30天无订单收货产生的已过账库存收货事实",
                "pos_net_sales": "已完成POS销售行金额减已完成退货行金额",
                "pos_received": "非赊销支付净额加可归属的客户还款",
            },
            "kpi": tasks_overview(scope, start=start, end=end),
            "inbound_ts": orders_timeseries(
                InboundOrder,
                scope,
                start=start,
                end=end,
            ),
            "outbound_ts": orders_timeseries(
                OutboundOrder,
                scope,
                start=start,
                end=end,
            ),
            "inbound_backlog": backlog_by_status(InboundOrder, scope),
            "outbound_backlog": backlog_by_status(OutboundOrder, scope),
            "eff_putaway": efficiency_ranking(
                WmsTask.TaskType.PUTAWAY,
                scope,
                day=end,
            ),
            "eff_pick": efficiency_ranking(
                WmsTask.TaskType.PICK,
                scope,
                day=end,
            ),
            "eff_pack": efficiency_ranking(
                WmsTask.TaskType.PACK,
                scope,
                day=end,
            ),
            "no_order_receive": no_order_receive,
            "pos": pos,
        }
        return JsonResponse({"ok": True, "data": data})
