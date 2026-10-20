"""Warehouse-scoped physical review, separate from the personal PICK work pool."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.tasking.models import TaskScanLog, WmsTask

from .authz import can_self_review_assisted_task


class PhysicalReviewPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def review_pick_queryset(qs, user):
    """Require an explicit warehouse role and its matching action permission."""
    scope = AccessScope.for_user(user)
    allowed = scope.is_valid and (
        scope.is_global
        or (
            scope.source == "user_role_scope"
            and scope.warehouse_ids
            and (
                (
                    UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles
                    and user.has_perm("tasking.claim_task_as_wh_operator")
                )
                or (
                    UserRoleScope.Role.WAREHOUSE_MANAGER in scope.roles
                    and user.has_perm("tasking.taskconfirm_as_wh_manager")
                )
            )
        )
    )
    if not allowed:
        raise PermissionDenied("需要有效仓库范围及复核操作权限。")
    return scope.filter_queryset(
        qs.filter(task_type=WmsTask.TaskType.PICK, status=WmsTask.Status.COMPLETED)
    )


def pending_reviews(qs, user):
    return (
        qs.filter(status="COMPLETED")
        .filter(
            Q(review_status="PENDING", posting_status__in=["NOT_READY", "PENDING", "FAILED"])
            | Q(review_status="APPROVED", posting_status__in=["PENDING", "FAILED"])
        )
        .exclude(picked_by=user)
    )


def review_summary(task, user, *, content=None):
    """Describe existing links only; reading never creates or repairs a REVIEW."""
    reviews = list(
        WmsTask.objects.filter(
            task_type="REVIEW",
            source_model="WmsTask",
            source_pk=str(task.pk),
            warehouse_id=task.warehouse_id,
            owner_id=task.owner_id,
        )
        .exclude(status="CANCELLED")
        .order_by("pk")[:2]
    )
    review = reviews[0] if len(reviews) == 1 else None
    reason = ""
    retry = task.review_status == "APPROVED" and task.posting_status in {"PENDING", "FAILED"}
    self_allowed = can_self_review_assisted_task(user, task)
    if len(reviews) != 1:
        reason = (
            "缺少实际复核任务，请联系仓库管理员。"
            if not reviews
            else "关联多个复核任务，请联系仓库管理员。"
        )
    elif task.picked_by_id == user.pk and not self_allowed:
        reason = "拣货人不能复核自己的标准出库任务。"
    elif task.status != "COMPLETED":
        reason = "拣货尚未完成。"
    elif task.posting_status == "POSTED":
        reason = "任务已完成复核并过账。"
    elif not (
        (task.review_status == "PENDING" and review.status in {"RELEASED", "IN_PROGRESS"})
        or (retry and review.status == "COMPLETED" and review.review_status == "APPROVED")
    ):
        reason = "任务状态不允许复核，请刷新后核对。"
    else:
        reason = (content if content is not None else review_content(task))["error"]
    picker = task.picked_by
    return {
        "ref_no": review.ref_no if review else task.ref_no,
        "picked_by_name": (picker.get_full_name() or picker.username) if picker else "",
        "finished_at": task.finished_at,
        "review_task": (
            {
                "id": review.pk,
                "task_no": review.task_no,
                "status": review.status,
                "review_status": review.review_status,
                "posting_status": review.posting_status,
            }
            if review
            else None
        ),
        "can_review": not reason and not retry,
        "can_retry_post": not reason and retry,
        "blocked_reason": reason,
    }


def _source_order(task):
    """Follow explicit links inside one tenant boundary, rejecting cycles."""
    from .models import OutboundOrder

    current, visited = task, set()
    while current.pk not in visited:
        visited.add(current.pk)
        try:
            source_id = int(current.source_pk)
        except (TypeError, ValueError):
            return None
        model = (current.source_model or "").lower()
        if model == "outboundorder":
            return (
                OutboundOrder.objects.select_related("customer", "owner", "warehouse")
                .filter(pk=source_id, owner_id=task.owner_id, warehouse_id=task.warehouse_id)
                .first()
            )
        if model != "wmstask":
            return None
        current = WmsTask.objects.filter(
            pk=source_id, owner_id=task.owner_id, warehouse_id=task.warehouse_id
        ).first()
        if current is None:
            return None
    return None


def review_content(task, *, lock=False):
    """Load and validate the order/line evidence without repairing business data."""
    order = _source_order(task)
    qs = (
        task.lines.exclude(status="CANCELLED")
        .select_related(
            "product__base_uom",
            "from_location",
            "to_location",
            "transfer_reservation__transfer_line__source_product",
        )
        .order_by("id")
    )
    if lock:
        qs = qs.select_for_update()
    lines = list(qs)
    order_lines = {}
    error = ""
    if order is None:
        error = "来源出库订单缺失或归属不一致，请联系仓库管理员。"
    elif not lines:
        error = "没有有效拣货明细，不能复核。"
    else:
        order_lines = {
            row.pk: row for row in order.lines.select_related("base_uom", "product__base_uom")
        }
        for line in lines:
            origin = order_lines.get(line.src_id)
            if (
                (line.src_model or "").lower()
                not in {"outboundorderline", "outbound.outboundorderline"}
                or origin is None
                or not line.product_id
                or line.product.owner_id != task.owner_id
                or origin.product_id != line.product_id
                or not (origin.base_uom_id or line.product.base_uom_id)
                or not line.from_location_id
                or line.from_location.warehouse_id != task.warehouse_id
            ):
                error = "拣货明细的订单行、商品、单位或库位关联异常，不能复核。"
                break
    return {"order": order, "lines": lines, "order_lines": order_lines, "error": error}


def validate_review_content(task):
    """Require the same essential evidence for submission as for detail display."""
    content = review_content(task, lock=True)
    if content["error"]:
        raise ValidationError(content["error"])


def review_order_data(content):
    order = content["order"]
    if order is None:
        return None
    return {
        "id": order.pk,
        "order_no": order.order_no,
        "customer_name": order.customer.name if order.customer else None,
        "owner_name": order.owner.name,
        "warehouse_name": order.warehouse.name,
        "biz_date": order.biz_date,
        "delivery_method": order.get_delivery_method_display(),
        "ship_to": order.ship_to,
        "memo": order.memo,
    }


def review_line_details(task, content):
    """Expose recorded pick facts, including posted facts and signed manual corrections."""
    from allapp.inventory.services import _qty_for_type

    lines = {line.pk: line for line in content["lines"]}
    groups = {pk: {} for pk in lines}
    scans = (
        TaskScanLog.objects.filter(
            task=task,
            task_line_id__in=lines,
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            status="OK",
        )
        .exclude(review_status="REJECTED")
        .select_related("location", "by_user")
        .order_by("id")
    )
    for scan in scans:
        line = lines[scan.task_line_id]
        # Match posting's product/location fallback without leaking a foreign association.
        if scan.product_id and scan.product_id != line.product_id:
            continue
        location = scan.location or line.from_location or line.to_location
        if location is None or location.warehouse_id != task.warehouse_id:
            continue
        try:
            qty = -_qty_for_type("PICK", scan)
        except ValidationError:
            continue
        key = (
            location.pk,
            scan.lot_no,
            scan.mfg_date,
            scan.exp_date,
            scan.container_no,
            scan.serial_no,
        )
        group = groups[line.pk].setdefault(
            key,
            {
                "location_code": location.code,
                "lot_no": scan.lot_no,
                "mfg_date": scan.mfg_date,
                "exp_date": scan.exp_date,
                "container_no": scan.container_no,
                "serial_no": scan.serial_no,
                "qty": Decimal("0"),
                "operators": {},
                "first_at": scan.created_at,
                "last_at": scan.created_at,
                "record_count": 0,
            },
        )
        group["qty"] += qty
        group["last_at"] = scan.created_at
        group["record_count"] += 1
        if scan.by_user:
            group["operators"][scan.by_user_id] = (
                scan.by_user.get_full_name() or scan.by_user.username
            )
    result = {}
    for line in lines.values():
        origin = content["order_lines"].get(line.src_id)
        if (
            origin is None
            or origin.product_id != line.product_id
            or (line.src_model or "").lower()
            not in {"outboundorderline", "outbound.outboundorderline"}
        ):
            origin = None
        product = (
            line.product if line.product_id and line.product.owner_id == task.owner_id else None
        )
        unit = (origin.base_uom if origin else None) or (product.base_uom if product else None)
        details = []
        for group in groups[line.pk].values():
            details.append(
                {
                    **group,
                    "qty": str(group["qty"]),
                    "operators": list(group["operators"].values()),
                }
            )
        result[line.pk] = {
            "product_code": product.code if product else None,
            "product_spec": product.spec if product else None,
            "base_uom_id": unit.pk if unit else None,
            "base_uom_name": unit.name if unit else None,
            "order_line_id": origin.pk if origin else None,
            "order_line_no": origin.line_no if origin else None,
            "order_qty": str(origin.base_qty) if origin else None,
            "qty_difference": str(line.qty_done - line.qty_plan),
            "finished_at": line.finished_at,
            "picking_details": details,
        }
    return result


def review_quantity_totals(content, details):
    """Sum decimals by unit; count each source order line once across split picks."""
    totals, seen = {}, set()
    for line in content["lines"]:
        detail = details[line.pk]
        unit_id = detail["base_uom_id"]
        group = totals.setdefault(
            unit_id,
            {
                "base_uom_id": unit_id,
                "base_uom_name": detail["base_uom_name"],
                "order_qty": Decimal("0"),
                "qty_plan": Decimal("0"),
                "qty_done": Decimal("0"),
            },
        )
        origin_id = detail["order_line_id"]
        if origin_id not in seen:
            group["order_qty"] += Decimal(detail["order_qty"])
            seen.add(origin_id)
        group["qty_plan"] += line.qty_plan
        group["qty_done"] += line.qty_done
    return [
        {**group, **{key: str(group[key]) for key in ("order_qty", "qty_plan", "qty_done")}}
        for group in totals.values()
    ]
