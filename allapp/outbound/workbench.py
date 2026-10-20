"""Live owner reminders: scoped, read-only, and batch-loaded fulfilment evidence."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.models import AuditEvent
from allapp.tasking.models import WmsTask, WmsTaskLine

from .authz import strict_order_queryset
from .models import OutboundOrder, OutboundOrderLine
from .progress import (
    LINE_NAMES,
    ORDER_NAMES,
    TASK_LINE_NAMES,
    TASK_NAMES,
    _names,
    fulfilment_quantities,
    fulfilment_status,
    timestamp,
)

CATEGORIES = {
    "pending": "待审核",
    "returned": "被退回",
    "overdue": "超期未发货",
    "shortage": "库存不足",
}
NOTES = [
    "各类按订单计数，同一订单可能同时属于多个类别。",
    "超期未发货不含未设置预计发货时间的订单。",
    "库存不足仅包含已发生的订单缺货，不代表下单前库存预测。",
]
BATCH_SIZE = 200
ZERO = Decimal("0")


class WorkbenchFilters(serializers.Serializer):
    category = serializers.ChoiceField(choices=tuple(CATEGORIES), required=True)
    search = serializers.CharField(required=False, allow_blank=True, max_length=100)
    page = serializers.IntegerField(default=1, min_value=1)
    page_size = serializers.IntegerField(default=20, min_value=1, max_value=100)


def workbench_scope(user):
    scope = AccessScope.for_user(user)
    roles = scope.roles & {"owner_manager", "owner_salesperson"}
    if not scope.is_valid or not scope.single_owner_id or len(roles) != 1:
        raise PermissionDenied("当前账号无货主待办访问权限")
    if not any(
        user.has_perm(p)
        for p in (
            "outbound.view_outboundorder",
            "outbound.view_all_outbound_orders",
            "outbound.submit_outbound_as_owner_buyers",
            "outbound.approve_outbound_as_owner_manager",
        )
    ):
        raise PermissionDenied("订单访问权限已失效")
    role = next(iter(roles))
    can_review = role == "owner_manager" and user.has_perm(
        "outbound.approve_outbound_as_owner_manager"
    )
    available = [
        key for key in CATEGORIES if key != "pending" or role == "owner_salesperson" or can_review
    ]
    return scope, role, can_review, available


def _batch_evidence(orders):
    order_map = {(o.owner_id, o.warehouse_id, str(o.pk)): o.pk for o in orders}
    scopes = Q(pk__in=[])
    for owner_id, warehouse_id in {(o.owner_id, o.warehouse_id) for o in orders}:
        scopes |= Q(owner_id=owner_id, warehouse_id=warehouse_id)
    base = WmsTask.objects.filter(scopes, is_active=True).exclude(status="CANCELLED")
    tasks = defaultdict(dict)
    ownership = {}
    for task in base.filter(
        _names("source_model", ORDER_NAMES),
        source_app__in=("", "outbound"),
        source_pk__in=[str(o.pk) for o in orders],
    ):
        order_id = order_map.get((task.owner_id, task.warehouse_id, task.source_pk))
        if order_id:
            tasks[order_id][task.pk] = task
            ownership[task.pk] = order_id
    frontier = {
        pk for group in tasks.values() for pk, t in group.items() if t.task_type != "REPLEN"
    }
    while frontier:
        parents = Q(pk__in=[])
        for pk in frontier:
            parent = tasks[ownership[pk]][pk]
            parents |= Q(
                source_pk=str(pk), owner_id=parent.owner_id, warehouse_id=parent.warehouse_id
            )
        children = base.filter(
            parents,
            _names("source_model", TASK_NAMES),
            source_app__in=("", "tasking"),
        )
        new = set()
        for task in children:
            parent_id = int(task.source_pk)
            order_id = ownership.get(parent_id)
            parent = tasks[order_id].get(parent_id)
            if not parent or (parent.owner_id, parent.warehouse_id) != (
                task.owner_id,
                task.warehouse_id,
            ):
                continue
            if task.pk not in ownership:
                tasks[order_id][task.pk] = task
                ownership[task.pk] = order_id
                if task.task_type != "REPLEN":
                    new.add(task.pk)
        frontier = new
    shared = Q(pk__in=[])
    for order in orders:
        shared |= Q(
            owner_id=order.owner_id,
            warehouse_id=order.warehouse_id,
            replenishtaskextra__demand_order_ids__contains=[order.pk],
        )
    for task in base.filter(shared, task_type="REPLEN").select_related("replenishtaskextra"):
        for order_id in task.replenishtaskextra.demand_order_ids or []:
            scoped_id = order_map.get((task.owner_id, task.warehouse_id, str(order_id)))
            if scoped_id:
                tasks[scoped_id][task.pk] = task
    lines = defaultdict(list)
    # Shared REPLEN lines are deliberately never traversed or returned.
    for line in (
        WmsTaskLine.objects.filter(task_id__in=ownership, is_active=True)
        .exclude(status="CANCELLED")
        .select_related("product__base_uom", "dispatchlineextra", "picklineextra")
    ):
        lines[ownership[line.task_id]].append(line)
    order_lines = defaultdict(dict)
    for line in OutboundOrderLine.objects.filter(
        order_id__in=[o.pk for o in orders], is_active=True
    ).select_related("base_uom", "product__base_uom"):
        order_lines[line.order_id][str(line.pk)] = line
    return tasks, lines, order_lines


def _order_line(line, task_lines, order_lines):
    """Resolve a unique order line; never allocate legacy product totals by guess."""
    if not line.product_id:
        return None
    seen = set()
    metadata_ids = set()
    current = line
    while current and current.pk not in seen:
        seen.add(current.pk)
        meta = (current.plan_meta or {}).get("outbound_order_line_id")
        if meta:
            metadata_ids.add(str(meta))
        name = (current.src_model or "").lower()
        if name in LINE_NAMES:
            target = order_lines.get(str(current.src_id))
            if meta and str(meta) != str(current.src_id):
                return None
            break
        if not name and current.src_id is None:
            if meta:
                target = order_lines.get(str(meta))
            else:
                matches = [o for o in order_lines.values() if o.product_id == line.product_id]
                target = matches[0] if len(matches) == 1 else None
            break
        if name not in TASK_LINE_NAMES:
            return None
        parent = task_lines.get(current.src_id)
        if not parent or parent.product_id != line.product_id:
            return None
        current = parent
    else:
        return None
    if target and metadata_ids and metadata_ids != {str(target.pk)}:
        return None
    extra = getattr(line, "dispatchlineextra", None)
    unit_id = extra.base_uom_id if extra else line.product.base_uom_id
    if (
        target
        and target.product_id == line.product_id
        and (target.base_uom_id or target.product.base_uom_id) == unit_id
    ):
        return target
    return None


def _shortages(order, tasks, lines, order_lines):
    by_id = {line.pk: line for line in lines}
    facts = defaultdict(
        lambda: {"allocated": ZERO, "picked": ZERO, "shipped": ZERO, "short": ZERO, "reasons": []}
    )
    unreliable = set()
    for line in lines:
        task = tasks[line.task_id]
        if task.task_type not in {"PICK", "DISPATCH"}:
            continue
        target = _order_line(line, by_id, order_lines)
        if not target:
            products = {o.product_id for o in order_lines.values()}
            unreliable.update({line.product_id} if line.product_id in products else products)
            continue
        item = facts[target.pk]
        if task.task_type == "DISPATCH":
            if task.status == "COMPLETED":
                item["shipped"] += line.qty_done
            continue
        item["allocated"] += line.qty_plan
        item["picked"] += line.qty_done
        extra = getattr(line, "picklineextra", None)
        if extra and extra.qty_short > 0:
            item["short"] += extra.qty_short
            item["reasons"].append(extra.short_reason or "原因未记录")
    attempted = bool(order.approved_at_warehouse) or any(
        t.task_type == "PICK" for t in tasks.values()
    )
    waiting = any(t.task_type == "REPLEN" and t.status != "COMPLETED" for t in tasks.values())
    allocation_stage = order.approval_status in {"OWNER_APPROVED", "WHS_PENDING"} and attempted
    results = []
    for target in order_lines.values():
        if target.product_id in unreliable:
            continue
        item = facts[target.pk]
        remaining = max(ZERO, target.base_qty - max(item["picked"], item["shipped"]))
        gap = max(ZERO, target.base_qty - item["allocated"]) if allocation_stage else ZERO
        short = min(item["short"], remaining)
        quantity = min(remaining, max(gap, short))
        if quantity <= 0:
            continue
        reason = "；".join(dict.fromkeys(item["reasons"])) if short else "分配数量不足"
        if waiting:
            reason = "等待仓库补货；" + reason
        results.append(
            {
                "product_id": target.product_id,
                "product_name": target.product.name,
                "quantity": str(quantity),
                "unit": (target.base_uom or target.product.base_uom).name,
                "reason": reason,
            }
        )
    return results


def classify_orders(orders, now):
    """Single classification path for both counts and the drill-down list."""
    tasks, lines, order_lines = _batch_evidence(orders)
    results = []
    for order in orders:
        categories = []
        reasons = {}
        shortage = []
        if order.approval_status in {"OWNER_REJECTED", "WHS_REJECTED"}:
            categories.append("returned")
            reasons["returned"] = (
                order.owner_reject_reason if order.approval_status == "OWNER_REJECTED" else ""
            ) or "原因未记录"
        if order.submit_status == "SUBMITTED":
            if order.approval_status == "OWNER_PENDING":
                categories.append("pending")
                reasons["pending"] = "等待货主管理员审核"
            quantities, exceptions, completed = fulfilment_quantities(
                order, tasks[order.pk], lines[order.pk], order_lines[order.pk]
            )
            order.workbench_status, order.workbench_status_label = fulfilment_status(
                order, tasks[order.pk], quantities, exceptions
            )
            if not completed:
                if order.etd and order.etd < now:
                    categories.append("overdue")
                    reasons["overdue"] = "已超过预计发货时间，尚未全部发运"
                    if any(e["code"] == "data_mismatch" for e in exceptions):
                        reasons["overdue"] += "；数据待核对"
                if order.approval_status in {"OWNER_APPROVED", "WHS_PENDING", "WHS_APPROVED"}:
                    shortage = _shortages(
                        order, tasks[order.pk], lines[order.pk], order_lines[order.pk]
                    )
                    if shortage:
                        categories.append("shortage")
                        reasons["shortage"] = "存在未解决的订单缺货"
        if categories:
            results.append((order, categories, reasons, shortage))
    return results


def _rejection_reasons(rows):
    """Read only current-cycle business reasons, not audit payloads."""
    candidates = {
        str(o.pk): o for o, _, reasons, _ in rows if reasons.get("returned") == "原因未记录"
    }
    result = {}
    if not candidates:
        return result
    events = AuditEvent.objects.filter(
        object_type="outbound.outboundorder",
        object_id__in=candidates,
        succeeded=True,
        action__in=(
            "outbound.order.submit",
            "outbound.order.owner_reject",
            "outbound.order.warehouse_reject",
        ),
    ).order_by("-occurred_at", "-id")
    ended = set()
    for event in events:
        order = candidates[event.object_id]
        if event.object_id in ended or (event.owner_id, event.warehouse_id) != (
            order.owner_id,
            order.warehouse_id,
        ):
            continue
        if event.action == "outbound.order.submit":
            ended.add(event.object_id)
            continue
        expected = (
            "outbound.order.owner_reject"
            if order.approval_status == "OWNER_REJECTED"
            else "outbound.order.warehouse_reject"
        )
        if event.action == expected:
            ended.add(event.object_id)
            reason = (event.metadata or {}).get("reason")
            if isinstance(reason, str) and reason.strip():
                result[order.pk] = reason
    return result


def workbench_response(user, filters=None):
    scope, role, can_review, available = workbench_scope(user)
    if filters and filters["category"] not in available:
        raise PermissionDenied("当前账号没有该待办的处理权限")
    qs = strict_order_queryset(
        OutboundOrder.objects.filter(is_active=True, is_closed=False).exclude(
            approval_status="CANCELLED"
        ),
        user,
        scope=scope,
    )
    qs = qs.filter(
        Q(submit_status="SUBMITTED") | Q(approval_status__in=("OWNER_REJECTED", "WHS_REJECTED"))
    )
    if filters and filters.get("search"):
        q = filters["search"]
        qs = qs.filter(Q(order_no__icontains=q) | Q(customer__name__icontains=q))
    qs = (
        qs.select_related("customer", "warehouse").order_by("etd", "id")
        if filters and filters["category"] == "overdue"
        else qs.select_related("customer", "warehouse").order_by("-biz_date", "-id")
    )
    now = timezone.now()
    counts = dict.fromkeys(CATEGORIES, 0)
    selected = []
    offset = (filters["page"] - 1) * filters["page_size"] if filters else 0
    batch = []

    def consume(batch):
        for row in classify_orders(batch, now):
            _, categories, _, _ = row
            for key in categories:
                if key in available:
                    counts[key] += 1
            if filters and filters["category"] in categories:
                index = counts[filters["category"]] - 1
                if offset <= index < offset + filters["page_size"]:
                    selected.append(row)

    for order in qs.iterator(chunk_size=BATCH_SIZE):
        batch.append(order)
        if len(batch) == BATCH_SIZE:
            consume(batch)
            batch = []
    if batch:
        consume(batch)
    response = {
        "role": role,
        "scope_label": "本货主全部订单" if role == "owner_manager" else "我创建的订单",
        "as_of": timestamp(now),
        "available_categories": available,
        "notes": NOTES,
    }
    if filters:
        category = filters["category"]
        recorded = _rejection_reasons(selected) if category == "returned" else {}
        response.update(
            category=category,
            label=CATEGORIES[category],
            count=counts[category],
            next_page=filters["page"] + 1 if offset + len(selected) < counts[category] else None,
            results=[
                {
                    "id": o.pk,
                    "order_no": o.order_no,
                    "customer_name": o.customer.name if o.customer else "",
                    "warehouse_name": o.warehouse.name,
                    "approval_status": o.approval_status,
                    "current_status": getattr(o, "workbench_status", o.approval_status),
                    "status_label": getattr(
                        o, "workbench_status_label", o.get_approval_status_display()
                    ),
                    "etd": timestamp(o.etd),
                    "reason": recorded.get(o.pk, reasons[category]),
                    "shortages": shortage if category == "shortage" else [],
                    "action": "review" if category == "pending" and can_review else "view",
                }
                for o, _, reasons, shortage in selected
            ],
        )
    else:
        response["categories"] = [
            {
                "code": key,
                "label": "待我审核" if key == "pending" and can_review else label,
                "count": counts[key] if key in available else None,
                "available": key in available,
            }
            for key, label in CATEGORIES.items()
        ]
    return response


class OwnerWorkbenchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(workbench_response(request.user))


class OwnerWorkbenchItemsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        filters = WorkbenchFilters(data=request.query_params)
        filters.is_valid(raise_exception=True)
        return Response(workbench_response(request.user, filters.validated_data))
