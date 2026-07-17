from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Abs, Coalesce, TruncDate
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.core.choices import InvTxType
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTask, WmsTaskLine


ZERO = Decimal("0")
VALID_DIRECTIONS = {"inbound", "outbound", "all"}
VALID_BASES = {"actual", "plan", "inventory", "shipment"}
VALID_EXCEPTIONS = {"", "overdue", "shortage", "difference"}


def _today() -> date:
    """Return the application date with either USE_TZ setting.

    ``timezone.localdate()`` raises for the project's current ``USE_TZ=False``
    setting because ``timezone.now()`` is then naive.
    """

    now = timezone.now()
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


@dataclass(frozen=True)
class OperationFilters:
    start_date: date
    end_date: date
    direction: str = "all"
    metric_basis: str = "actual"
    owner_id: int | None = None
    warehouse_id: int | None = None
    status: str = ""
    order_no: str = ""
    source_no: str = ""
    product: str = ""
    lot_no: str = ""
    task_no: str = ""
    operator: str = ""
    exception_type: str = ""

    def validate(self) -> None:
        if self.direction not in VALID_DIRECTIONS:
            raise ValueError("direction must be inbound, outbound or all.")
        if self.metric_basis not in VALID_BASES:
            raise ValueError("metric_basis must be actual, plan, inventory or shipment.")
        if self.exception_type not in VALID_EXCEPTIONS:
            raise ValueError("exception_type must be overdue, shortage or difference.")
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date.")
        if self.end_date - self.start_date > timedelta(days=366):
            raise ValueError("The maximum date range is 367 days.")
        if self.metric_basis == "shipment" and self.direction != "outbound":
            raise ValueError("shipment basis is only available with direction=outbound.")


def _decimal_text(value) -> str:
    decimal_value = Decimal(value or 0)
    if not decimal_value:
        return "0"
    # API quantities are numeric strings, not fixed-scale database dumps.
    # Keeping meaningful fractional digits while stripping storage padding
    # gives stable values across MySQL/SQLite decimal representations.
    return format(decimal_value.normalize(), "f")


def _scope_filter(scope: AccessScope, qs, owner_field: str, warehouse_field: str):
    return scope.filter_queryset(
        qs,
        owner_field=owner_field,
        warehouse_field=warehouse_field,
    )


def _apply_requested_scope(scope: AccessScope, qs, filters, owner_field, warehouse_field):
    qs = _scope_filter(scope, qs, owner_field, warehouse_field)
    if filters.owner_id:
        # Owner-bound roles must stay on their explicit owner. Warehouse-bound
        # roles may use owner as a narrowing dimension inside their warehouse.
        if scope.owner_ids and filters.owner_id not in scope.owner_ids:
            return qs.none()
        qs = qs.filter(**{owner_field: filters.owner_id})
    if filters.warehouse_id:
        # The inverse applies to owner roles: warehouse is a safe refinement,
        # not a new tenant grant.
        if scope.warehouse_ids and filters.warehouse_id not in scope.warehouse_ids:
            return qs.none()
        qs = qs.filter(**{warehouse_field: filters.warehouse_id})
    return qs


def _product_query(prefix: str, value: str) -> Q:
    if not value:
        return Q()
    return (
        Q(**{f"{prefix}code__icontains": value})
        | Q(**{f"{prefix}sku__icontains": value})
        | Q(**{f"{prefix}name__icontains": value})
        | Q(**{f"{prefix}gtin__icontains": value})
        | Q(**{f"{prefix}unit_barcode__icontains": value})
        | Q(**{f"{prefix}carton_barcode__icontains": value})
    )


def _plan_lines(direction: str, filters: OperationFilters, scope: AccessScope, user):
    if direction == "inbound":
        qs = InboundOrderLine.objects.select_related(
            "order", "order__owner", "order__warehouse", "product"
        ).filter(
            order__is_deleted=False,
            order__biz_date__range=(filters.start_date, filters.end_date),
        )
        qs = _apply_requested_scope(
            scope, qs, filters, "order__owner_id", "order__warehouse_id"
        )
        if filters.status:
            qs = qs.filter(order__approval_status=filters.status)
        else:
            qs = qs.exclude(order__approval_status="CANCELLED")
        if filters.order_no:
            qs = qs.filter(order__order_no__icontains=filters.order_no)
        if filters.source_no:
            qs = qs.filter(order__src_bill_no__icontains=filters.source_no)
        if filters.lot_no:
            qs = qs.filter(lot_no__icontains=filters.lot_no)
        if filters.operator:
            qs = qs.filter(
                Q(order__created_by__username__icontains=filters.operator)
                | Q(order__created_by__name__icontains=filters.operator)
            )
        if filters.exception_type == "overdue":
            qs = qs.filter(order__eta__date__lt=_today(), order__is_closed=False)
    else:
        qs = OutboundOrderLine.objects.select_related(
            "order", "order__owner", "order__warehouse", "product"
        ).filter(
            order__is_deleted=False,
            order__biz_date__range=(filters.start_date, filters.end_date),
        )
        qs = _apply_requested_scope(
            scope, qs, filters, "order__owner_id", "order__warehouse_id"
        )
        if filters.status:
            qs = qs.filter(order__approval_status=filters.status)
        else:
            qs = qs.exclude(order__approval_status="CANCELLED")
        if filters.order_no:
            qs = qs.filter(order__order_no__icontains=filters.order_no)
        if filters.source_no:
            qs = qs.filter(order__src_bill_no__icontains=filters.source_no)
        if filters.lot_no:
            qs = qs.filter(lot_no__icontains=filters.lot_no)
        if filters.operator:
            qs = qs.filter(
                Q(order__created_by__username__icontains=filters.operator)
                | Q(order__created_by__name__icontains=filters.operator)
            )
        if filters.exception_type == "overdue":
            qs = qs.filter(order__etd__date__lt=_today(), order__is_closed=False)
    if filters.product:
        qs = qs.filter(_product_query("product__", filters.product))
    if filters.task_no:
        task_order_ids = WmsTask.objects.filter(
            task_no__icontains=filters.task_no,
            source_pk__isnull=False,
        ).values_list("source_pk", flat=True)
        qs = qs.filter(order_id__in=task_order_ids)
    if "owner_salesperson" in scope.roles:
        qs = qs.filter(order__created_by=user)
    return qs.order_by("order__biz_date", "order_id", "line_no", "id")


def _task_filter(
    filters: OperationFilters,
    scope: AccessScope,
    user,
    task_type: str | None = None,
):
    qs = WmsTask.objects.all()
    if task_type:
        qs = qs.filter(task_type=task_type)
    qs = _apply_requested_scope(scope, qs, filters, "owner_id", "warehouse_id")
    if filters.status:
        qs = qs.filter(status=filters.status)
    if filters.order_no:
        qs = qs.filter(ref_no__icontains=filters.order_no)
    if filters.task_no:
        qs = qs.filter(task_no__icontains=filters.task_no)
    if filters.operator:
        qs = qs.filter(
            Q(created_by__username__icontains=filters.operator)
            | Q(picked_by__username__icontains=filters.operator)
            | Q(posted_by__username__icontains=filters.operator)
        )
    if "warehouse_operator" in scope.roles:
        qs = qs.filter(
            Q(created_by=user)
            | Q(picked_by=user)
            | Q(posted_by=user)
            | Q(assignments__assignee=user)
            | Q(lines__finished_by=user)
        )
    elif "owner_salesperson" in scope.roles:
        inbound_numbers = InboundOrder.objects.filter(created_by=user).values_list(
            "order_no", flat=True
        )
        outbound_numbers = OutboundOrder.objects.filter(created_by=user).values_list(
            "order_no", flat=True
        )
        qs = qs.filter(Q(ref_no__in=inbound_numbers) | Q(ref_no__in=outbound_numbers))
    return qs.distinct()


def _inventory_transactions(
    direction: str,
    filters: OperationFilters,
    scope: AccessScope,
    user,
):
    tx_type = InvTxType.RECEIVE if direction == "inbound" else InvTxType.ISSUE
    qs = InventoryTransaction.objects.select_related(
        "owner", "warehouse", "product", "product__base_uom", "location"
    ).filter(
        tx_type=tx_type,
        posted_at__date__range=(filters.start_date, filters.end_date),
    )
    qs = _apply_requested_scope(scope, qs, filters, "owner_id", "warehouse_id")
    tasks = _task_filter(filters, scope, user)
    if direction == "inbound":
        # A RECEIVE transaction is operational inbound throughput only when it
        # came from a posted RECEIVE task.  This excludes pending receipts,
        # putaway-side bookkeeping and arbitrary inventory adjustments that
        # happen to share the RECEIVE transaction code.
        posted_receive_tasks = tasks.filter(
            task_type=WmsTask.TaskType.RECEIVE,
            posting_status=WmsTask.PostingStatus.POSTED,
        ).values_list("id", flat=True)
        qs = qs.filter(
            src_model__iexact="WmsTask",
            src_id__in=posted_receive_tasks,
        )
    if direction != "inbound":
        # A stock ISSUE becomes outbound inventory throughput only when it is
        # posted by a completed outbound execution task.  PUTAWAY/RELOC also
        # write an ISSUE leg, but are internal movements and must never be
        # reported as warehouse dispatches.
        outbound_issue_tasks = tasks.filter(
            task_type__in=[
                WmsTask.TaskType.PICK,
                WmsTask.TaskType.LOAD,
                WmsTask.TaskType.DISPATCH,
            ],
            status=WmsTask.Status.COMPLETED,
            posting_status=WmsTask.PostingStatus.POSTED,
        )
        qs = qs.filter(
            src_model__iexact="WmsTask",
            src_id__in=outbound_issue_tasks.values_list("id", flat=True),
        )
    if filters.source_no:
        qs = qs.filter(src_no__icontains=filters.source_no)
    if filters.product:
        qs = qs.filter(_product_query("product__", filters.product))
    if filters.lot_no:
        qs = qs.filter(batch_no__icontains=filters.lot_no)
    if filters.exception_type:
        # Stock transactions are posted facts; workflow exceptions are represented by tasks.
        if filters.exception_type == "overdue":
            overdue_ids = tasks.filter(
                planned_end__lt=timezone.now(), status__in=["DRAFT", "READY", "RELEASED", "IN_PROGRESS"]
            ).values_list("id", flat=True)
            qs = qs.filter(src_model__iexact="WmsTask", src_id__in=overdue_ids)
        else:
            qs = qs.none()
    return qs.order_by("posted_at", "id")


def _shipment_lines(filters: OperationFilters, scope: AccessScope, user):
    tasks = _task_filter(filters, scope, user, WmsTask.TaskType.DISPATCH).filter(
        status=WmsTask.Status.COMPLETED,
        finished_at__date__range=(filters.start_date, filters.end_date),
    )
    if filters.source_no:
        tasks = tasks.filter(ref_no__icontains=filters.source_no)
    if filters.exception_type == "overdue":
        tasks = tasks.filter(planned_end__lt=F("finished_at"))
    elif filters.exception_type in {"shortage", "difference"}:
        # The line comparison is applied below.
        pass
    qs = WmsTaskLine.objects.select_related(
        "task", "task__owner", "task__warehouse", "product", "finished_by"
    ).filter(task_id__in=tasks.values_list("id", flat=True))
    if filters.product:
        qs = qs.filter(_product_query("product__", filters.product))
    if filters.lot_no:
        qs = qs.filter(plan_meta__lot_no__icontains=filters.lot_no)
    if filters.exception_type in {"shortage", "difference"}:
        qs = qs.filter(qty_done__lt=F("qty_plan"))
    return qs.order_by("task__finished_at", "task_id", "id")


def _actual_basis(direction: str, requested: str) -> str:
    if requested != "actual":
        return requested
    return "inventory" if direction == "inbound" else "shipment"


def _summary_for(direction: str, filters: OperationFilters, scope: AccessScope, user) -> dict:
    basis = _actual_basis(direction, filters.metric_basis)
    if basis == "plan":
        qs = _plan_lines(direction, filters, scope, user)
        values = qs.aggregate(
            orders=Count("order_id", distinct=True),
            lines=Count("id"),
            qty=Coalesce(Sum("base_qty"), ZERO),
        )
    elif basis == "inventory":
        qs = _inventory_transactions(direction, filters, scope, user)
        values = qs.aggregate(lines=Count("id"), qty=Coalesce(Sum(Abs("qty_delta")), ZERO))
        task_ids = set(
            qs.filter(src_model__iexact="WmsTask").values_list("src_id", flat=True)
        )
        tasks = WmsTask.objects.filter(id__in=task_ids).values(
            "id", "source_model", "source_pk"
        )
        task_keys = {
            (row["source_model"], row["source_pk"])
            if row["source_pk"]
            else ("WmsTask", str(row["id"]))
            for row in tasks
        }
        direct_keys = set(
            qs.exclude(src_model__iexact="WmsTask").values_list("src_model", "src_id")
        )
        values["orders"] = len(task_keys | direct_keys)
    elif basis == "shipment" and direction == "outbound":
        qs = _shipment_lines(filters, scope, user)
        values = qs.aggregate(
            lines=Count("id"),
            qty=Coalesce(Sum("qty_done"), ZERO),
        )
        values["orders"] = qs.values("task_id").distinct().count()
    else:
        values = {"orders": 0, "lines": 0, "qty": ZERO}
    return {
        "metric_basis": basis,
        "orders": int(values.get("orders") or 0),
        "lines": int(values.get("lines") or 0),
        "qty": _decimal_text(values.get("qty")),
    }


def _trend_for(
    direction: str,
    filters: OperationFilters,
    scope: AccessScope,
    user,
) -> dict[date, Decimal]:
    basis = _actual_basis(direction, filters.metric_basis)
    if basis == "plan":
        rows = (
            _plan_lines(direction, filters, scope, user)
            .order_by()
            .values(day=F("order__biz_date"))
            .annotate(qty=Coalesce(Sum("base_qty"), ZERO))
        )
    elif basis == "inventory":
        rows = (
            _inventory_transactions(direction, filters, scope, user)
            .order_by()
            .annotate(day=TruncDate("posted_at"))
            .values("day")
            .annotate(qty=Coalesce(Sum(Abs("qty_delta")), ZERO))
        )
    elif basis == "shipment" and direction == "outbound":
        rows = (
            _shipment_lines(filters, scope, user)
            .order_by()
            .annotate(day=TruncDate("task__finished_at"))
            .values("day")
            .annotate(qty=Coalesce(Sum("qty_done"), ZERO))
        )
    else:
        return {}
    return {row["day"]: Decimal(row["qty"] or 0) for row in rows}


def build_operations_summary(*, user, filters: OperationFilters) -> dict:
    filters.validate()
    scope = AccessScope.for_user(user)
    directions = ["inbound", "outbound"] if filters.direction == "all" else [filters.direction]
    summaries = {
        direction: _summary_for(direction, filters, scope, user)
        for direction in directions
    }
    trends = {
        direction: _trend_for(direction, filters, scope, user)
        for direction in directions
    }
    rows = []
    current = filters.start_date
    while current <= filters.end_date:
        row = {"date": current.isoformat()}
        for direction in directions:
            row[f"{direction}_qty"] = _decimal_text(trends[direction].get(current, ZERO))
        rows.append(row)
        current += timedelta(days=1)
    scope_payload = scope.as_dict()
    scope_payload["actor_only"] = bool(
        {"warehouse_operator", "owner_salesperson"}.intersection(scope.roles)
    )
    return {
        "metric_basis": filters.metric_basis,
        "data_as_of": timezone.now().isoformat(),
        "scope": scope_payload,
        "range": {"start": filters.start_date.isoformat(), "end": filters.end_date.isoformat()},
        "summary": summaries,
        "trend": rows,
    }


def _plan_row(direction: str, line, basis: str) -> dict:
    order = line.order
    return {
        "detail_id": f"plan:{direction}:{line.id}",
        "direction": direction,
        "metric_basis": basis,
        "event_at": order.biz_date.isoformat(),
        "order_id": order.id,
        "order_no": order.order_no,
        "source_no": order.src_bill_no or "",
        "task_id": None,
        "task_no": "",
        "owner": {"id": order.owner_id, "name": order.owner.name},
        "warehouse": {"id": order.warehouse_id, "name": order.warehouse.name},
        "product": {
            "id": line.product_id,
            "code": line.product.code,
            "sku": line.product.sku,
            "name": line.product.name,
        },
        "lot_no": line.lot_no or "",
        "status": order.approval_status,
        "operator": getattr(order.created_by, "username", "") if order.created_by_id else "",
        "planned_qty": _decimal_text(line.base_qty),
        "actual_qty": "0",
        "exception_type": "overdue" if (
            getattr(order, "eta" if direction == "inbound" else "etd", None)
            and getattr(order, "eta" if direction == "inbound" else "etd").date() < _today()
            and not order.is_closed
        ) else "",
    }


def _task_map_for_transactions(transactions: Iterable[InventoryTransaction]) -> dict[int, WmsTask]:
    task_ids = {
        tx.src_id for tx in transactions if (tx.src_model or "").lower() == "wmstask" and tx.src_id
    }
    return {
        task.id: task
        for task in WmsTask.objects.select_related("created_by", "picked_by", "posted_by").filter(id__in=task_ids)
    }


def _inventory_rows(direction: str, qs, basis: str) -> list[dict]:
    transactions = list(qs)
    tasks = _task_map_for_transactions(transactions)
    rows = []
    for tx in transactions:
        task = tasks.get(tx.src_id)
        rows.append(
            {
                "detail_id": f"inventory:{tx.id}",
                "direction": direction,
                "metric_basis": basis,
                "event_at": tx.posted_at.isoformat() if tx.posted_at else None,
                "order_id": int(task.source_pk) if task and str(task.source_pk).isdigit() else None,
                "order_no": (task.ref_no or tx.src_no or task.task_no) if task else tx.src_no,
                "source_no": tx.src_no or "",
                "task_id": task.id if task else None,
                "task_no": task.task_no if task else "",
                "owner": {"id": tx.owner_id, "name": tx.owner.name},
                "warehouse": {"id": tx.warehouse_id, "name": tx.warehouse.name},
                "product": {
                    "id": tx.product_id,
                    "code": tx.product.code,
                    "sku": tx.product.sku,
                    "name": tx.product.name,
                },
                "location": {
                    "id": tx.location_id,
                    "code": getattr(tx.location, "code", ""),
                    "name": getattr(tx.location, "name", ""),
                },
                "base_uom": getattr(getattr(tx.product, "base_uom", None), "code", ""),
                "lot_no": tx.batch_no or "",
                "status": task.status if task else "POSTED",
                "operator": (
                    getattr(task.posted_by, "username", "")
                    if task and task.posted_by_id
                    else ""
                ),
                "planned_qty": "0",
                "actual_qty": _decimal_text(abs(tx.qty_delta)),
                "exception_type": "",
            }
        )
    return rows


def _shipment_row(line, basis: str) -> dict:
    task = line.task
    return {
        "detail_id": f"shipment:{line.id}",
        "direction": "outbound",
        "metric_basis": basis,
        "event_at": task.finished_at.isoformat() if task.finished_at else None,
        "order_id": int(task.source_pk) if str(task.source_pk).isdigit() else None,
        "order_no": task.ref_no or "",
        "source_no": "",
        "task_id": task.id,
        "task_no": task.task_no,
        "owner": {"id": task.owner_id, "name": task.owner.name},
        "warehouse": {"id": task.warehouse_id, "name": task.warehouse.name},
        "product": {
            "id": line.product_id,
            "code": getattr(line.product, "code", ""),
            "sku": getattr(line.product, "sku", ""),
            "name": getattr(line.product, "name", ""),
        },
        "lot_no": (line.plan_meta or {}).get("lot_no", ""),
        "status": task.status,
        "operator": getattr(line.finished_by, "username", "") if line.finished_by_id else "",
        "planned_qty": _decimal_text(line.qty_plan),
        "actual_qty": _decimal_text(line.qty_done),
        "exception_type": "shortage" if line.qty_done < line.qty_plan else "",
    }


def build_operations_detail_rows(*, user, filters: OperationFilters) -> list[dict]:
    filters.validate()
    scope = AccessScope.for_user(user)
    directions = ["inbound", "outbound"] if filters.direction == "all" else [filters.direction]
    rows: list[dict] = []
    for direction in directions:
        basis = _actual_basis(direction, filters.metric_basis)
        if basis == "plan":
            rows.extend(
                _plan_row(direction, line, basis)
                for line in _plan_lines(direction, filters, scope, user)
            )
        elif basis == "inventory":
            rows.extend(
                _inventory_rows(
                    direction,
                    _inventory_transactions(direction, filters, scope, user),
                    basis,
                )
            )
        elif basis == "shipment" and direction == "outbound":
            rows.extend(
                _shipment_row(line, basis)
                for line in _shipment_lines(filters, scope, user)
            )
    rows.sort(
        key=lambda row: (
            row.get("event_at") or "",
            row.get("task_id") or 0,
            row.get("order_id") or 0,
            row["detail_id"],
        ),
        reverse=True,
    )
    return rows
