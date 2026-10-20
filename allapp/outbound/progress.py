"""Read-only, tenant-bounded projections of outbound fulfilment evidence."""

from collections import defaultdict
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from allapp.accounts.models import AuditEvent
from allapp.tasking.models import TaskStatusLog, WmsTask, WmsTaskLine

ZERO = Decimal("0")
ORDER_NAMES = ("outboundorder", "outbound.outboundorder")
TASK_NAMES = ("wmstask", "tasking.wmstask")
LINE_NAMES = ("outboundorderline", "outbound.outboundorderline")
TASK_LINE_NAMES = ("wmstaskline", "tasking.wmstaskline")
STAGE_NAMES = {"PICK": "拣货", "REVIEW": "复核", "PACK": "打包", "DISPATCH": "发运"}


def timestamp(value):
    """Serialize local legacy datetimes with an explicit business timezone."""
    if value is None:
        return None
    zone = ZoneInfo(settings.TIME_ZONE)
    if timezone.is_naive(value):
        value = timezone.make_aware(value, zone)
    return value.astimezone(zone).isoformat()


def _time_key(value):
    return timestamp(value) or ""


def _names(field, names):
    query = Q()
    for name in names:
        query |= Q(**{f"{field}__iexact": name})
    return query


def _tasks_for_order(order):
    base = WmsTask.objects.filter(owner_id=order.owner_id, warehouse_id=order.warehouse_id)
    roots = base.filter(
        _names("source_model", ORDER_NAMES),
        source_pk=str(order.pk),
        source_app__in=["", "outbound"],
    )
    tasks = {task.pk: task for task in roots}
    frontier = {pk for pk, task in tasks.items() if task.task_type != "REPLEN"}
    while frontier:
        children = base.filter(
            _names("source_model", TASK_NAMES), source_pk__in=[str(pk) for pk in frontier]
        ).filter(Q(source_app__in=["", "tasking"]))
        frontier = set()
        for task in children:
            if task.pk not in tasks:
                tasks[task.pk] = task
                if task.task_type != "REPLEN":
                    frontier.add(task.pk)
    # Shared replenishments can serve several orders. Show the wait, but never
    # traverse their descendants into another order's work or quantities.
    for task in base.filter(
        task_type="REPLEN", replenishtaskextra__demand_order_ids__contains=[order.pk]
    ):
        tasks.setdefault(task.pk, task)
    return tasks


def _exception(code, message, *, stage=None, task=None, product_id=None, at=None):
    return {
        "code": code,
        "message": message,
        "stage": stage,
        "task_id": task.pk if task else None,
        "task_no": task.task_no if task else None,
        "product_id": product_id,
        "occurred_at": timestamp(at),
    }


def _stage(code, name, state="pending", started=None, completed=None, tasks=None):
    return {
        "code": code,
        "name": name,
        "state": state,
        "started_at": timestamp(started),
        "completed_at": timestamp(completed),
        "tasks": tasks or [],
    }


def _order_stages(order, events):
    submits = [e for e in events if e.action == "outbound.order.submit"]
    submitted = submits[-1].occurred_at if submits else None

    def event_time(*actions):
        matches = [
            e.occurred_at
            for e in events
            if e.action in actions
            and (not submitted or _time_key(e.occurred_at) >= _time_key(submitted))
        ]
        return matches[-1] if matches else None

    def current_time(value, *actions):
        if value and (not submitted or _time_key(value) >= _time_key(submitted)):
            return value
        return event_time(*actions)

    approval = order.approval_status
    owner_done = approval in {"OWNER_APPROVED", "WHS_PENDING", "WHS_APPROVED", "WHS_REJECTED"}
    wh_done = approval == "WHS_APPROVED"
    owner_at = current_time(order.approved_at_ownermanager, "outbound.order.owner_approve")
    wh_at = current_time(
        order.approved_at_warehouse,
        "outbound.order.warehouse_confirm",
        "outbound.order.warehouse_approve",
    )
    if approval == "CANCELLED":
        owner_done = bool(event_time("outbound.order.owner_approve"))
        wh_done = bool(
            event_time("outbound.order.warehouse_confirm", "outbound.order.warehouse_approve")
        )
    submitted_state = "completed" if order.submit_status == "SUBMITTED" else "pending"
    stages = [
        _stage(
            "SUBMIT",
            "订单提交",
            submitted_state,
            completed=submitted if submitted_state == "completed" else None,
        ),
        _stage(
            "OWNER",
            "货主审核",
            (
                "completed"
                if owner_done
                else (
                    "exception"
                    if approval == "OWNER_REJECTED"
                    else (
                        "active"
                        if order.submit_status == "SUBMITTED" and approval != "CANCELLED"
                        else "pending"
                    )
                )
            ),
            completed=owner_at if owner_done else None,
        ),
        _stage(
            "WAREHOUSE",
            "仓库确认",
            (
                "completed"
                if wh_done
                else (
                    "exception"
                    if approval == "WHS_REJECTED"
                    else "active" if owner_done and approval != "CANCELLED" else "pending"
                )
            ),
            completed=wh_at if wh_done else None,
        ),
    ]
    return stages


def _task_stages(tasks, logs):
    stages = []
    for kind, name in STAGE_NAMES.items():
        members = [t for t in tasks.values() if t.task_type == kind and t.status != "CANCELLED"]
        if kind == "PACK" and not members:
            continue
        summaries = []
        for task in members:
            task_logs = logs.get(task.pk, [])
            started = task.started_at or next(
                (log.changed_at for log in task_logs if log.new_status == "IN_PROGRESS"), None
            )
            done = task.status == "COMPLETED" and (
                kind != "REVIEW" or task.review_status == "APPROVED"
            )
            finished = task.finished_at or next(
                (log.changed_at for log in reversed(task_logs) if log.new_status == "COMPLETED"),
                None,
            )
            blocked = task.posting_status == "FAILED" or task.review_status in {
                "REJECTED",
                "NEED_RECOUNT",
            }
            summaries.append(
                {
                    "id": task.pk,
                    "task_no": task.task_no,
                    "status": task.status,
                    "state": (
                        "exception"
                        if blocked
                        else (
                            "completed"
                            if done
                            else (
                                "active"
                                if task.status in {"IN_PROGRESS", "COMPLETED"}
                                else "pending"
                            )
                        )
                    ),
                    "started_at": timestamp(started),
                    "completed_at": timestamp(finished) if done else None,
                }
            )
        states = [item["state"] for item in summaries]
        state = "pending"
        if "exception" in states:
            state = "exception"
        elif states and all(s == "completed" for s in states):
            state = "completed"
        elif "completed" in states:
            state = "partial"
        elif "active" in states:
            state = "active"
        starts = [s["started_at"] for s in summaries if s["started_at"]]
        ends = [s["completed_at"] for s in summaries if s["completed_at"]]
        stage = _stage(kind, name, state, tasks=summaries)
        stage["started_at"] = min(starts) if starts else None
        # A missing completion time must not be concealed by another task's time.
        stage["completed_at"] = (
            max(ends) if state == "completed" and len(ends) == len(summaries) else None
        )
        stages.append(stage)
    return stages


def _source_matches(line, task_lines, order_lines, unit_id):
    """Validate explicit source chains; product-level legacy rows need no allocation."""
    seen = set()
    current = line
    while current:
        if current.pk in seen:
            return False
        seen.add(current.pk)
        meta_id = (current.plan_meta or {}).get("outbound_order_line_id")
        if meta_id:
            source = order_lines.get(str(meta_id))
            if source is None or source.product_id != line.product_id:
                return False
        name = (current.src_model or "").lower()
        if not name and current.src_id is None:
            return True
        if name in LINE_NAMES:
            source = order_lines.get(str(current.src_id))
            return (
                source is not None
                and source.product_id == line.product_id
                and (source.base_uom_id or source.product.base_uom_id) == unit_id
            )
        if name not in TASK_LINE_NAMES:
            return False
        current = task_lines.get(current.src_id)
        if (
            current is None
            or current.product_id != line.product_id
            or current.status == "CANCELLED"
        ):
            return False
    return False


def _quantities(order, tasks, lines, order_lines, exceptions):
    grouped = {}
    for line in order_lines.values():
        unit = line.base_uom or line.product.base_uom
        key = (line.product_id, unit.pk)
        if key not in grouped:
            grouped[key] = {
                "product_id": line.product_id,
                "product_name": line.product.name,
                "sku": line.product.sku,
                "unit_id": unit.pk,
                "unit": unit.name,
                "planned_qty": ZERO,
                "shipped_qty": ZERO,
                "reliable": True,
            }
        grouped[key]["planned_qty"] += line.base_qty
    task_lines = {line.pk: line for line in lines}
    for line in lines:
        task = tasks[line.task_id]
        if task.task_type != "DISPATCH" or task.status != "COMPLETED" or line.status == "CANCELLED":
            continue
        extra = getattr(line, "dispatchlineextra", None)
        unit_id = (
            extra.base_uom_id if extra else (line.product.base_uom_id if line.product else None)
        )
        key = (line.product_id, unit_id)
        if key not in grouped or not _source_matches(line, task_lines, order_lines, unit_id):
            exceptions.append(
                _exception(
                    "data_mismatch",
                    "发运明细与订单商品、单位或来源不一致，数据待核对。",
                    stage="DISPATCH",
                    task=task,
                    product_id=line.product_id,
                )
            )
            for item in grouped.values():
                if item["product_id"] == line.product_id:
                    item["reliable"] = False
            continue
        grouped[key]["shipped_qty"] += line.qty_done
    assisted_no_dispatch = order.processing_mode == "WAREHOUSE_ASSISTED" and not any(
        t.task_type == "DISPATCH" and t.status != "CANCELLED" for t in tasks.values()
    )
    result = []
    for item in grouped.values():
        planned, shipped = item["planned_qty"], item["shipped_qty"]
        if shipped > planned:
            exceptions.append(
                _exception(
                    "over_shipped",
                    "实际发运数量超过订单计划，请核对。",
                    stage="DISPATCH",
                    product_id=item["product_id"],
                )
            )
        known = item.pop("reliable") and not assisted_no_dispatch
        item.update(
            planned_qty=str(planned),
            shipped_qty=str(shipped) if known else None,
            remaining_qty=str(max(planned - shipped, ZERO)) if known else None,
        )
        result.append(item)
    return result


def _business_exceptions(order, tasks, lines):
    result = []
    if order.approval_status == "OWNER_REJECTED":
        result.append(
            _exception(
                "owner_rejected",
                order.owner_reject_reason or "原因未记录",
                stage="OWNER",
                at=order.approved_at_ownermanager,
            )
        )
    if order.approval_status == "WHS_REJECTED":
        result.append(
            _exception(
                "warehouse_rejected",
                "原因未记录",
                stage="WAREHOUSE",
                at=order.approved_at_warehouse,
            )
        )
    for task in tasks.values():
        if task.status == "CANCELLED":
            continue
        if task.posting_status == "FAILED":
            result.append(
                _exception(
                    "posting_failed",
                    "仓库库存处理未成功，请联系仓库核对。",
                    task=task,
                    stage=task.task_type,
                )
            )
        if task.review_status in {"REJECTED", "NEED_RECOUNT"}:
            result.append(
                _exception(
                    "review_rejected",
                    task.approval_note or "原因未记录",
                    task=task,
                    stage=task.task_type,
                    at=task.approved_at,
                )
            )
        if task.task_type == "REPLEN" and task.status != "COMPLETED":
            result.append(
                _exception("replenishment_wait", "等待仓库补货。", task=task, stage="WAREHOUSE")
            )
    for line in lines:
        task = tasks[line.task_id]
        if task.status == "CANCELLED" or line.status == "CANCELLED":
            continue
        pick = getattr(line, "picklineextra", None)
        review = getattr(line, "reviewlineextra", None)
        if pick and pick.qty_short > 0:
            result.append(
                _exception(
                    "short_pick",
                    f"短拣 {pick.qty_short}：{pick.short_reason or '原因未记录'}",
                    task=task,
                    stage="PICK",
                    product_id=line.product_id,
                    at=line.finished_at,
                )
            )
        if review and (review.qty_discrepancy_plan or review.qty_discrepancy_picked):
            result.append(
                _exception(
                    "review_difference",
                    review.discrepancy_reason or "原因未记录",
                    task=task,
                    stage="REVIEW",
                    product_id=line.product_id,
                    at=line.finished_at,
                )
            )
    return result


def shipment_is_complete(tasks, quantities, exceptions):
    """Shared completion rule for progress and owner reminders; no database reads."""
    dispatch = next(stage for stage in _task_stages(tasks, {}) if stage["code"] == "DISPATCH")
    return (
        dispatch["code"] == "DISPATCH"
        and dispatch["state"] == "completed"
        and bool(quantities)
        and all(
            q["shipped_qty"] is not None and Decimal(q["shipped_qty"]) >= Decimal(q["planned_qty"])
            for q in quantities
        )
        and not any(e["code"] == "data_mismatch" for e in exceptions)
    )


def fulfilment_quantities(order, tasks, lines, order_lines):
    """Compute shipment facts from batch-loaded evidence without audit/time queries."""
    exceptions = []
    quantities = _quantities(order, tasks, lines, order_lines, exceptions)
    return quantities, exceptions, shipment_is_complete(tasks, quantities, exceptions)


def fulfilment_status(order, tasks, quantities, exceptions):
    """Current business label from loaded facts; no audit or timeline queries."""
    return _current_status(
        order, _order_stages(order, []) + _task_stages(tasks, {}), quantities, tasks, exceptions
    )


def _current_status(order, stages, quantities, tasks, exceptions):
    if order.approval_status == "CANCELLED":
        return "CANCELLED", "已取消"
    assisted_done = (
        order.processing_mode == "WAREHOUSE_ASSISTED"
        and order.is_closed
        and any(
            t.task_type == "PICK" and t.status == "COMPLETED" and t.posting_status == "POSTED"
            for t in tasks.values()
        )
    )
    if assisted_done and not any(
        t.task_type == "DISPATCH" and t.status != "CANCELLED" for t in tasks.values()
    ):
        return "ASSISTED_COMPLETED", "代办出库完成"
    by_code = {s["code"]: s for s in stages}
    dispatch = by_code["DISPATCH"]
    has_shipped = any(Decimal(q["shipped_qty"] or "0") > 0 for q in quantities)
    fulfilled = bool(quantities) and all(
        q["shipped_qty"] is not None and Decimal(q["shipped_qty"]) >= Decimal(q["planned_qty"])
        for q in quantities
    )
    invalid = any(e["code"] == "data_mismatch" for e in exceptions)
    shipped = shipment_is_complete(tasks, quantities, exceptions)
    if dispatch["state"] == "completed" and not fulfilled:
        dispatch["state"] = "partial" if has_shipped else "exception"
        dispatch["completed_at"] = None
        exceptions.append(
            _exception(
                "short_shipped",
                "已完成发运任务的数量不足订单计划，请联系仓库核对剩余数量及原因。",
                stage="DISPATCH",
            )
        )
    if invalid:
        dispatch["state"] = "exception"
        dispatch["completed_at"] = None
    if order.is_closed and (not shipped or order.close_reason != "全部发运完成"):
        return "CLOSED", "已关闭"
    if invalid:
        return "DATA_MISMATCH", "数据待核对"
    if order.approval_status == "OWNER_REJECTED":
        return "OWNER_REJECTED", "已退回修改"
    if order.approval_status == "WHS_REJECTED":
        return "WHS_REJECTED", "仓库已驳回"
    if order.submit_status != "SUBMITTED":
        return "DRAFT", "待提交"
    if order.approval_status == "OWNER_PENDING":
        return "OWNER_PENDING", "待审核"
    if shipped:
        return "SHIPPED", "已发运"
    if has_shipped:
        dispatch["state"] = "partial"
        dispatch["completed_at"] = None
        return "PARTIALLY_SHIPPED", "部分发运"
    if order.approval_status in {"OWNER_APPROVED", "WHS_PENDING"}:
        return "WAREHOUSE_PENDING", "待仓库确认"
    if any(s["state"] == "exception" for s in stages):
        return "EXCEPTION", "履约异常"
    if by_code["PICK"]["state"] in {"active", "partial"}:
        return "PICKING", "拣货中"
    if by_code["REVIEW"]["state"] == "completed":
        pack = by_code.get("PACK")
        return (
            ("PACKING", "打包中")
            if pack and pack["state"] != "completed"
            else ("AWAITING_DISPATCH", "已复核／待发运")
        )
    if by_code["REVIEW"]["state"] in {"active", "partial"}:
        return "REVIEWING", "复核中"
    if by_code["PICK"]["state"] == "completed":
        return "AWAITING_REVIEW", "待复核"
    return "AWAITING_PICK", "待拣货"


def build_order_progress(order):
    """Build progress from persisted evidence; this function never changes workflow state."""
    tasks = _tasks_for_order(order)
    lines = list(
        WmsTaskLine.objects.filter(task_id__in=tasks).select_related(
            "product__base_uom", "dispatchlineextra", "picklineextra", "reviewlineextra"
        )
    )
    order_lines = {
        str(line.pk): line for line in order.lines.select_related("product__base_uom", "base_uom")
    }
    events = list(
        AuditEvent.objects.filter(
            object_type="outbound.outboundorder",
            object_id=str(order.pk),
            owner_id=order.owner_id,
            warehouse_id=order.warehouse_id,
            succeeded=True,
            action__startswith="outbound.order.",
        ).order_by("occurred_at", "id")
    )
    logs = defaultdict(list)
    for log in TaskStatusLog.objects.filter(task_id__in=tasks).order_by("changed_at", "id"):
        logs[log.task_id].append(log)
    stages = _order_stages(order, events) + _task_stages(tasks, logs)
    exceptions = _business_exceptions(order, tasks, lines)
    quantities = _quantities(order, tasks, lines, order_lines, exceptions)
    current, label = _current_status(order, stages, quantities, tasks, exceptions)
    if current in {"CANCELLED", "CLOSED"}:
        exceptions.insert(0, _exception(current.lower(), order.close_reason or "原因未记录"))
    for exception in exceptions:
        action = {
            "owner_rejected": "outbound.order.owner_reject",
            "warehouse_rejected": "outbound.order.warehouse_reject",
            "cancelled": "outbound.order.cancel",
        }.get(exception["code"])
        if not action:
            continue
        for event in reversed(events):
            if event.action == "outbound.order.submit":
                break
            if event.action == action:
                exception["occurred_at"] = exception["occurred_at"] or timestamp(event.occurred_at)
                reason = event.metadata.get("reason")
                if (
                    exception["message"] == "原因未记录"
                    and isinstance(reason, str)
                    and reason.strip()
                ):
                    exception["message"] = reason
                break
    if current == "ASSISTED_COMPLETED":
        stages[-1]["state"] = "not_applicable"
    # Only explicitly recorded business reasons leave the audit boundary.
    history = [
        {
            "code": "owner_rejected",
            "message": e.metadata.get("reason")
            or e.after.get("owner_reject_reason")
            or "原因未记录",
            "occurred_at": timestamp(e.occurred_at),
        }
        for e in events
        if e.action == "outbound.order.owner_reject"
    ]
    if order.owner_reject_reason and not history and order.approval_status != "OWNER_REJECTED":
        history.append(
            {"code": "owner_rejected", "message": order.owner_reject_reason, "occurred_at": None}
        )
    return {
        "order_id": order.pk,
        "processing_mode": order.processing_mode,
        "current_status": current,
        "current_status_label": label,
        "as_of": timestamp(timezone.now()),
        "stages": stages,
        "quantities": quantities,
        "exceptions": exceptions,
        "history": history,
    }
