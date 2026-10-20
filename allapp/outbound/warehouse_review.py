"""Strict authorization and read contracts for warehouse sales-order review."""

from decimal import Decimal

from django.db.models import Sum
from rest_framework.exceptions import APIException, PermissionDenied

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope

REVIEW_PERMISSION = "outbound.approve_outbound_as_wh_manager"
REVIEW_STATES = {"OWNER_APPROVED", "WHS_PENDING"}


class WarehouseReviewConflict(APIException):
    status_code = 409
    default_detail = "订单状态已变化，请刷新后重试。"


def can_review_warehouse_outbound(user, *, scope=None):
    scope = scope or AccessScope.for_user(user)
    return bool(
        scope.is_valid
        and (
            scope.is_global
            or (
                scope.source == "user_role_scope"
                and scope.warehouse_ids
                and UserRoleScope.Role.WAREHOUSE_MANAGER in scope.roles
                and user.has_perm(REVIEW_PERMISSION)
            )
        )
    )


def require_warehouse_reviewer(user, order=None):
    scope = AccessScope.for_user(user)
    if not can_review_warehouse_outbound(user, scope=scope) or (
        order and not scope.allows(owner_id=order.owner_id, warehouse_id=order.warehouse_id)
    ):
        raise PermissionDenied("需要当前仓库的出库审核权限。")
    return scope


def review_orders(qs):
    from .reorder import non_pos_orders

    return non_pos_orders(qs.filter(processing_mode="STANDARD", outbound_type="SALES"))


def scoped_review_orders(qs, user):
    scope = require_warehouse_reviewer(user)
    return review_orders(
        scope.filter_queryset(qs, owner_field="owner_id", warehouse_field="warehouse_id")
    )


def review_state_allows_action(order):
    return (
        order.processing_mode == "STANDARD"
        and order.outbound_type == "SALES"
        and order.submit_status == "SUBMITTED"
        and not order.is_closed
        and order.approval_status in REVIEW_STATES
    )


def review_detail(order, user, *, request=None):
    from allapp.tasking.models import WmsTask, WmsTaskLine

    from .serializers import OutboundOrderReadSerializer
    from .services import _task_source_q, replenishment_waiting_detail

    data = dict(OutboundOrderReadSerializer(order, context={"request": request}).data)
    tasks = (
        WmsTask.objects.filter(task_type="PICK")
        .filter(_task_source_q(order))
        .exclude(status="CANCELLED")
    )
    task_lines = WmsTaskLine.objects.filter(task__in=tasks).exclude(status="CANCELLED")
    allocated = {
        row["src_id"]: row["quantity"]
        for row in task_lines.values("src_id").annotate(quantity=Sum("qty_plan"))
    }
    started = tasks.filter(status__in=["IN_PROGRESS", "COMPLETED"]).exists() or (
        task_lines.filter(qty_done__gt=0).exists()
    )
    allowed = (
        can_review_warehouse_outbound(user) and review_state_allows_action(order) and not started
    )
    data.update(
        can_warehouse_confirm=allowed,
        can_warehouse_reject=allowed,
        replenishment_wait=replenishment_waiting_detail(order),
    )
    saved_lines = {
        line.pk: line for line in order.lines.select_related("base_uom", "product__base_uom")
    }
    for line in data["lines"]:
        quantity = allocated.get(line["id"], 0)
        line["allocated_qty"] = str(quantity)
        saved = saved_lines[line["id"]]
        unit = saved.base_uom or saved.product.base_uom
        line["base_unit_name"] = unit.name if unit else ""
        line["shortage_qty"] = str(max(Decimal(str(line["base_qty"])) - quantity, 0))
    return data
