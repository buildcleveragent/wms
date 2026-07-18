from __future__ import annotations

import datetime
from calendar import monthrange
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inbound.constants import PDA_NO_ORDER_RECEIVE_SOURCE_MODEL
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTask

from .services_operations import (
    OperationFilters,
    build_operations_detail_rows,
    build_operations_summary,
)

ZERO_QTY = Decimal("0.000")
DETAIL_METRICS = {"all", "inbound", "outbound"}
DETAIL_METRIC_ALIASES = {
    "receive": "inbound",
    "receiving": "inbound",
    "ship": "outbound",
    "shipping": "outbound",
}


def parse_pda_throughput_range(params):
    mode = (params.get("mode") or "month").strip().lower()
    today = datetime.date.today()

    if mode == "month":
        month = (params.get("month") or today.strftime("%Y-%m")).strip()
        try:
            year, month_no = [int(part) for part in month.split("-", 1)]
            start_date = datetime.date(year, month_no, 1)
        except (TypeError, ValueError):
            raise ValueError("month must use YYYY-MM format.")
        end_date = datetime.date(year, month_no, monthrange(year, month_no)[1])
        return mode, start_date, end_date

    if mode in {"range", "custom"}:
        try:
            start_raw = (params.get("start_date") or "").strip()
            end_raw = (params.get("end_date") or "").strip()
            start_date = datetime.date.fromisoformat(start_raw)
            end_date = datetime.date.fromisoformat(end_raw)
        except ValueError:
            raise ValueError("start_date and end_date must use YYYY-MM-DD format.")
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date.")
        if (end_date - start_date).days > 366:
            raise ValueError("date range cannot exceed 367 days.")
        return "range", start_date, end_date

    raise ValueError("mode must be month or range.")


def _decimal_to_text(value):
    return format(value or ZERO_QTY, ".3f")


def normalize_pda_throughput_metric(raw):
    metric = (raw or "all").strip().lower()
    metric = DETAIL_METRIC_ALIASES.get(metric, metric)
    if metric not in DETAIL_METRICS:
        raise ValueError("metric must be all, inbound, or outbound.")
    return metric


def _date_to_text(value):
    return value.isoformat() if value else ""


def _datetime_to_text(value):
    return value.isoformat() if value else ""


def _scoped_owner_warehouse(*, user, owner_id=None, warehouse_id=None):
    user_warehouse_id = getattr(user, "warehouse_id", None)
    scoped_warehouse_id = user_warehouse_id or warehouse_id
    if user_warehouse_id:
        scoped_owner_id = owner_id
    else:
        scoped_owner_id = getattr(user, "owner_id", None) or owner_id
    return scoped_owner_id, scoped_warehouse_id


def _owner_name(owner_id):
    if not owner_id:
        return ""
    owner = Owner.objects.filter(pk=owner_id).only("name").first()
    return getattr(owner, "name", "") or f"Owner #{owner_id}"


def _collect_owner_options(*, warehouse_id, user):
    user_owner_id = getattr(user, "owner_id", None)
    user_warehouse_id = getattr(user, "warehouse_id", None)
    if user_owner_id and not user_warehouse_id:
        return [{"id": user_owner_id, "name": _owner_name(user_owner_id)}]

    receive_tasks = WmsTask.objects.filter(
        task_type=WmsTask.TaskType.RECEIVE,
        posting_status=WmsTask.PostingStatus.POSTED,
    )
    outbound_lines = OutboundOrderLine.objects.select_related("order__owner")
    if warehouse_id:
        receive_tasks = receive_tasks.filter(warehouse_id=warehouse_id)
        outbound_lines = outbound_lines.filter(order__warehouse_id=warehouse_id)

    owner_map = {}
    for row in receive_tasks.values("owner_id", "owner__name").distinct():
        owner_id = row["owner_id"]
        if owner_id:
            owner_map[owner_id] = {
                "id": owner_id,
                "name": row["owner__name"] or f"Owner #{owner_id}",
            }
    for row in outbound_lines.values(
        "order__owner_id", "order__owner__name"
    ).distinct():
        owner_id = row["order__owner_id"]
        if owner_id:
            owner_map[owner_id] = {
                "id": owner_id,
                "name": row["order__owner__name"] or f"Owner #{owner_id}",
            }
    return sorted(owner_map.values(), key=lambda item: (item["name"], item["id"]))


def _daily_map(queryset, *, date_field):
    rows = (
        queryset.values(date_field)
        .annotate(
            orders=Count("order_id", distinct=True),
            lines=Count("id"),
            qty=Coalesce(
                Sum("base_qty"),
                Value(ZERO_QTY),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ),
        )
        .order_by(date_field)
    )
    return {
        row[date_field].isoformat(): {
            "orders": row["orders"] or 0,
            "lines": row["lines"] or 0,
            "qty": row["qty"] or ZERO_QTY,
        }
        for row in rows
    }


def _tx_daily_map(queryset):
    rows = (
        queryset.values("posted_at__date")
        .annotate(
            orders=Count("src_id", distinct=True),
            lines=Count("id"),
            qty=Coalesce(
                Sum("qty_delta"),
                Value(ZERO_QTY),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ),
        )
        .order_by("posted_at__date")
    )
    return {
        row["posted_at__date"].isoformat(): {
            "orders": row["orders"] or 0,
            "lines": row["lines"] or 0,
            "qty": row["qty"] or ZERO_QTY,
        }
        for row in rows
        if row["posted_at__date"]
    }


def _summary(queryset):
    return queryset.aggregate(
        orders=Count("order_id", distinct=True),
        lines=Count("id"),
        qty=Coalesce(
            Sum("base_qty"),
            Value(ZERO_QTY),
            output_field=DecimalField(max_digits=18, decimal_places=3),
        ),
    )


def _tx_summary(queryset):
    return queryset.aggregate(
        orders=Count("src_id", distinct=True),
        lines=Count("id"),
        qty=Coalesce(
            Sum("qty_delta"),
            Value(ZERO_QTY),
            output_field=DecimalField(max_digits=18, decimal_places=3),
        ),
    )


def _tx_owner_map(queryset):
    rows = (
        queryset.values("owner_id", "owner__name")
        .annotate(
            orders=Count("src_id", distinct=True),
            lines=Count("id"),
            qty=Coalesce(
                Sum("qty_delta"),
                Value(ZERO_QTY),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ),
        )
        .order_by("owner__name", "owner_id")
    )
    return {
        row["owner_id"]: {
            "owner": row["owner_id"],
            "owner_name": row["owner__name"] or f"Owner #{row['owner_id']}",
            "orders": row["orders"] or 0,
            "lines": row["lines"] or 0,
            "qty": row["qty"] or ZERO_QTY,
        }
        for row in rows
        if row["owner_id"]
    }


def _line_owner_map(queryset):
    rows = (
        queryset.values("order__owner_id", "order__owner__name")
        .annotate(
            orders=Count("order_id", distinct=True),
            lines=Count("id"),
            qty=Coalesce(
                Sum("base_qty"),
                Value(ZERO_QTY),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ),
        )
        .order_by("order__owner__name", "order__owner_id")
    )
    return {
        row["order__owner_id"]: {
            "owner": row["order__owner_id"],
            "owner_name": row["order__owner__name"]
            or f"Owner #{row['order__owner_id']}",
            "orders": row["orders"] or 0,
            "lines": row["lines"] or 0,
            "qty": row["qty"] or ZERO_QTY,
        }
        for row in rows
        if row["order__owner_id"]
    }


def _owner_rows(*, inbound_transactions, outbound_lines, owner_options):
    inbound_map = _tx_owner_map(inbound_transactions)
    outbound_map = _line_owner_map(outbound_lines)
    owner_name_map = {item["id"]: item["name"] for item in owner_options}
    owner_ids = sorted(set(inbound_map) | set(outbound_map))

    rows = []
    for owner_id in owner_ids:
        inbound = inbound_map.get(owner_id, {"orders": 0, "lines": 0, "qty": ZERO_QTY})
        outbound = outbound_map.get(
            owner_id, {"orders": 0, "lines": 0, "qty": ZERO_QTY}
        )
        rows.append(
            {
                "owner": owner_id,
                "owner_name": owner_name_map.get(owner_id)
                or inbound.get("owner_name")
                or outbound.get("owner_name")
                or f"Owner #{owner_id}",
                "inbound_orders": inbound["orders"],
                "inbound_lines": inbound["lines"],
                "inbound_qty": _decimal_to_text(inbound["qty"]),
                "outbound_orders": outbound["orders"],
                "outbound_lines": outbound["lines"],
                "outbound_qty": _decimal_to_text(outbound["qty"]),
            }
        )

    rows.sort(
        key=lambda row: (
            -(Decimal(row["inbound_qty"]) + Decimal(row["outbound_qty"])),
            row["owner_name"],
            row["owner"],
        )
    )
    return rows


def _posted_receive_task_ids(*, owner_id, warehouse_id):
    tasks = WmsTask.objects.filter(
        task_type=WmsTask.TaskType.RECEIVE,
        posting_status=WmsTask.PostingStatus.POSTED,
    )
    if owner_id:
        tasks = tasks.filter(owner_id=owner_id)
    if warehouse_id:
        tasks = tasks.filter(warehouse_id=warehouse_id)
    return tasks.values_list("id", flat=True)


def _posted_receive_transactions(*, start_date, end_date, owner_id, warehouse_id):
    task_ids = _posted_receive_task_ids(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    transactions = InventoryTransaction.objects.filter(
        tx_type=InvTxType.RECEIVE,
        src_model="WmsTask",
        src_id__in=task_ids,
        posted_at__date__gte=start_date,
        posted_at__date__lte=end_date,
    )
    if owner_id:
        transactions = transactions.filter(owner_id=owner_id)
    if warehouse_id:
        transactions = transactions.filter(warehouse_id=warehouse_id)
    return transactions


def _outbound_lines(*, start_date, end_date, owner_id, warehouse_id):
    lines = OutboundOrderLine.objects.filter(
        order__biz_date__gte=start_date,
        order__biz_date__lte=end_date,
    )
    if owner_id:
        lines = lines.filter(order__owner_id=owner_id)
    if warehouse_id:
        lines = lines.filter(order__warehouse_id=warehouse_id)
    return lines


def _empty_summary():
    return {"orders": 0, "lines": 0, "qty": ZERO_QTY}


def _receive_source_type(task):
    if task and task.source_model == PDA_NO_ORDER_RECEIVE_SOURCE_MODEL:
        return "无订单收货"
    return "收货任务"


def _receive_detail_items(transactions):
    rows = list(
        transactions.select_related(
            "owner",
            "warehouse",
            "product",
            "product__base_uom",
            "location",
        ).order_by("-posted_at", "-id")
    )
    tasks = WmsTask.objects.filter(
        id__in={row.src_id for row in rows if row.src_id}
    ).in_bulk()

    items = []
    for tx in rows:
        task = tasks.get(tx.src_id)
        product = tx.product
        owner = tx.owner
        warehouse = tx.warehouse
        location = tx.location
        task_no = getattr(task, "task_no", "") or tx.src_no
        ref_no = getattr(task, "ref_no", "") or ""
        source_no = ref_no or task_no or tx.src_no
        items.append(
            {
                "id": f"inbound-{tx.id}",
                "kind": "inbound",
                "kind_label": "收货",
                "source_type": _receive_source_type(task),
                "source_no": source_no,
                "task_no": task_no,
                "ref_no": ref_no,
                "date": _date_to_text(tx.posted_at.date() if tx.posted_at else None),
                "posted_at": _datetime_to_text(tx.posted_at),
                "owner": tx.owner_id,
                "owner_name": getattr(owner, "name", "") or f"Owner #{tx.owner_id}",
                "warehouse": tx.warehouse_id,
                "warehouse_name": getattr(warehouse, "name", "")
                or f"Warehouse #{tx.warehouse_id}",
                "product": tx.product_id,
                "product_code": getattr(product, "code", ""),
                "product_name": getattr(product, "name", ""),
                "product_sku": getattr(product, "sku", ""),
                "base_uom": getattr(getattr(product, "base_uom", None), "name", "")
                or getattr(getattr(product, "base_uom", None), "code", ""),
                "location": tx.location_id,
                "location_code": getattr(location, "code", ""),
                "line_no": tx.src_line_id,
                "qty": _decimal_to_text(tx.qty_delta),
                "counterparty_name": "",
                "memo": tx.memo or getattr(task, "posting_note", "") or "",
            }
        )
    return items


def _outbound_detail_items(lines):
    rows = list(
        lines.select_related(
            "order",
            "order__owner",
            "order__warehouse",
            "order__customer",
            "product",
            "product__base_uom",
        ).order_by("-order__biz_date", "-order_id", "line_no")
    )

    items = []
    for line in rows:
        order = line.order
        product = line.product
        owner = order.owner
        warehouse = order.warehouse
        customer = order.customer
        items.append(
            {
                "id": f"outbound-{line.id}",
                "kind": "outbound",
                "kind_label": "出货",
                "source_type": "出库订单",
                "source_no": order.order_no,
                "task_no": "",
                "ref_no": order.src_bill_no or "",
                "date": _date_to_text(order.biz_date),
                "posted_at": "",
                "owner": order.owner_id,
                "owner_name": getattr(owner, "name", "") or f"Owner #{order.owner_id}",
                "warehouse": order.warehouse_id,
                "warehouse_name": getattr(warehouse, "name", "")
                or f"Warehouse #{order.warehouse_id}",
                "product": line.product_id,
                "product_code": getattr(product, "code", ""),
                "product_name": getattr(product, "name", ""),
                "product_sku": getattr(product, "sku", ""),
                "base_uom": getattr(getattr(product, "base_uom", None), "name", "")
                or getattr(getattr(product, "base_uom", None), "code", ""),
                "location": None,
                "location_code": "",
                "line_no": line.line_no,
                "qty": _decimal_to_text(line.base_qty),
                "counterparty_name": getattr(customer, "name", "") if customer else "",
                "memo": line.note or order.memo or "",
            }
        )
    return items


def _detail_sort_key(item):
    return (
        item["posted_at"] or item["date"],
        item["kind"],
        item["source_no"],
        item["line_no"] or 0,
        item["product_code"],
    )


def _scope_owner_options(access_scope):
    """List owners inside the effective role boundary, before report filters.

    This keeps selector options useful without allowing an owner filter to
    reveal tenants outside an authorized warehouse (or outside an owner role).
    """

    if not access_scope.is_valid:
        return []
    if access_scope.owner_ids:
        owners = Owner.objects.filter(id__in=access_scope.owner_ids)
    elif access_scope.warehouse_ids:
        owner_ids = set(
            WmsTask.objects.filter(warehouse_id__in=access_scope.warehouse_ids)
            .values_list("owner_id", flat=True)
            .distinct()
        )
        owner_ids.update(
            InventoryTransaction.objects.filter(
                warehouse_id__in=access_scope.warehouse_ids
            )
            .values_list("owner_id", flat=True)
            .distinct()
        )
        owner_ids.update(
            OutboundOrderLine.objects.filter(
                order__warehouse_id__in=access_scope.warehouse_ids
            )
            .values_list("order__owner_id", flat=True)
            .distinct()
        )
        owners = Owner.objects.filter(id__in=owner_ids)
    else:
        return []
    return [
        {"id": owner.id, "name": owner.name}
        for owner in owners.order_by("name", "id")
    ]


def build_pda_throughput_payload(
    *, user, mode, start_date, end_date, owner_id=None, warehouse_id=None
):
    access_scope = AccessScope.for_user(user)
    scoped_owner_id = owner_id or (
        next(iter(access_scope.owner_ids)) if len(access_scope.owner_ids) == 1 else None
    )
    scoped_warehouse_id = warehouse_id or (
        next(iter(access_scope.warehouse_ids))
        if len(access_scope.warehouse_ids) == 1
        else None
    )
    filters = OperationFilters(
        start_date=start_date,
        end_date=end_date,
        direction="all",
        metric_basis="actual",
        owner_id=scoped_owner_id,
        warehouse_id=scoped_warehouse_id,
    )
    operations = build_operations_summary(user=user, filters=filters)
    details = build_operations_detail_rows(user=user, filters=filters)
    owner_map = {}
    owner_buckets = {}
    day_buckets = {}
    for item in details:
        owner = item["owner"]
        owner_map[owner["id"]] = {"id": owner["id"], "name": owner["name"]}
        bucket = owner_buckets.setdefault(
            owner["id"],
            {
                "owner": owner["id"],
                "owner_name": owner["name"],
                "inbound_orders_set": set(),
                "inbound_lines": 0,
                "inbound_qty": ZERO_QTY,
                "outbound_orders_set": set(),
                "outbound_lines": 0,
                "outbound_qty": ZERO_QTY,
            },
        )
        direction = item["direction"]
        key = item.get("order_id") or item.get("task_id")
        if key:
            bucket[f"{direction}_orders_set"].add(key)
        bucket[f"{direction}_lines"] += 1
        bucket[f"{direction}_qty"] += Decimal(item["actual_qty"])

        day_key = (item.get("event_at") or "")[:10]
        if day_key:
            day = day_buckets.setdefault(
                day_key,
                {
                    "inbound_orders": set(), "inbound_lines": 0,
                    "outbound_orders": set(), "outbound_lines": 0,
                },
            )
            if key:
                day[f"{direction}_orders"].add(key)
            day[f"{direction}_lines"] += 1

    owner_options = _scope_owner_options(access_scope)
    by_owner = []
    for bucket in owner_buckets.values():
        by_owner.append(
            {
                "owner": bucket["owner"],
                "owner_name": bucket["owner_name"],
                "inbound_orders": len(bucket["inbound_orders_set"]),
                "inbound_lines": bucket["inbound_lines"],
                "inbound_qty": _decimal_to_text(bucket["inbound_qty"]),
                "outbound_orders": len(bucket["outbound_orders_set"]),
                "outbound_lines": bucket["outbound_lines"],
                "outbound_qty": _decimal_to_text(bucket["outbound_qty"]),
            }
        )
    by_owner.sort(
        key=lambda row: (-(Decimal(row["inbound_qty"]) + Decimal(row["outbound_qty"])), row["owner_name"])
    )
    days = []
    for trend in operations["trend"]:
        day = day_buckets.get(trend["date"], {})
        days.append(
            {
                "date": trend["date"],
                "inbound_orders": len(day.get("inbound_orders", set())),
                "inbound_lines": day.get("inbound_lines", 0),
                "inbound_qty": _decimal_to_text(
                    Decimal(trend.get("inbound_qty", "0"))
                ),
                "outbound_orders": len(day.get("outbound_orders", set())),
                "outbound_lines": day.get("outbound_lines", 0),
                "outbound_qty": _decimal_to_text(
                    Decimal(trend.get("outbound_qty", "0"))
                ),
            }
        )

    return {
        "scope": {"owner": scoped_owner_id, "warehouse": scoped_warehouse_id},
        "period": {
            "mode": mode,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "metric_basis": "actual",
        "data_as_of": operations["data_as_of"],
        "summary": {
            "inbound_orders": operations["summary"]["inbound"]["orders"],
            "inbound_lines": operations["summary"]["inbound"]["lines"],
            "inbound_qty": _decimal_to_text(
                Decimal(operations["summary"]["inbound"]["qty"])
            ),
            "outbound_orders": operations["summary"]["outbound"]["orders"],
            "outbound_lines": operations["summary"]["outbound"]["lines"],
            "outbound_qty": _decimal_to_text(
                Decimal(operations["summary"]["outbound"]["qty"])
            ),
        },
        "owner_options": owner_options,
        "by_owner": by_owner,
        "days": days,
    }


def build_pda_throughput_detail_payload(
    *,
    user,
    mode,
    start_date,
    end_date,
    metric="all",
    owner_id=None,
    warehouse_id=None,
):
    metric = normalize_pda_throughput_metric(metric)
    access_scope = AccessScope.for_user(user)
    scoped_owner_id = owner_id or (
        next(iter(access_scope.owner_ids)) if len(access_scope.owner_ids) == 1 else None
    )
    scoped_warehouse_id = warehouse_id or (
        next(iter(access_scope.warehouse_ids))
        if len(access_scope.warehouse_ids) == 1
        else None
    )
    filters = OperationFilters(
        start_date=start_date,
        end_date=end_date,
        direction=metric,
        metric_basis="actual",
        owner_id=scoped_owner_id,
        warehouse_id=scoped_warehouse_id,
    )
    rows = build_operations_detail_rows(user=user, filters=filters)
    tasks = WmsTask.objects.in_bulk(
        {row["task_id"] for row in rows if row.get("task_id")}
    )
    outbound_orders = OutboundOrder.objects.select_related("customer").in_bulk(
        {
            row["order_id"]
            for row in rows
            if row["direction"] == "outbound" and row.get("order_id")
        }
    )
    owner_map = {}
    items = []
    order_sets = {"inbound": set(), "outbound": set()}
    quantities = {"inbound": ZERO_QTY, "outbound": ZERO_QTY}
    line_counts = {"inbound": 0, "outbound": 0}
    for row in rows:
        direction = row["direction"]
        task = tasks.get(row.get("task_id"))
        owner_map[row["owner"]["id"]] = row["owner"]
        key = row.get("order_id") or row.get("task_id")
        if key:
            order_sets[direction].add(key)
        line_counts[direction] += 1
        quantities[direction] += Decimal(row["actual_qty"])
        location = row.get("location") or {}
        if direction == "inbound":
            source_type = _receive_source_type(task)
            source_no = row["order_no"] or row["task_no"]
        else:
            # The fact is a completed dispatch task, but the business source
            # shown to operators remains the outbound order reference.
            source_type = "出库订单"
            source_no = row["order_no"] or row["task_no"]
        outbound_order = outbound_orders.get(row.get("order_id"))
        items.append(
            {
                "id": f"{direction}-{row.get('task_id') or row.get('order_id')}-{row['product']['id']}",
                "kind": direction,
                "kind_label": "收货" if direction == "inbound" else "发运",
                "source_type": source_type,
                "source_no": source_no,
                "task_no": row["task_no"],
                "ref_no": row["source_no"],
                "date": (row.get("event_at") or "")[:10],
                "posted_at": row.get("event_at") or "",
                "owner": row["owner"]["id"],
                "owner_name": row["owner"]["name"],
                "warehouse": row["warehouse"]["id"],
                "warehouse_name": row["warehouse"]["name"],
                "product": row["product"]["id"],
                "product_code": row["product"]["code"],
                "product_name": row["product"]["name"],
                "product_sku": row["product"]["sku"],
                "base_uom": row.get("base_uom") or "",
                "location": location.get("id"),
                "location_code": location.get("code") or "",
                "line_no": None,
                "qty": _decimal_to_text(Decimal(row["actual_qty"])),
                "counterparty_name": (
                    getattr(getattr(outbound_order, "customer", None), "name", "")
                    if direction == "outbound"
                    else ""
                ),
                "memo": row["exception_type"],
            }
        )

    return {
        "scope": {
            "owner": scoped_owner_id,
            "warehouse": scoped_warehouse_id,
        },
        "period": {
            "mode": mode,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "metric": metric,
        "metric_basis": "actual",
        "data_as_of": timezone.now().isoformat(),
        "summary": {
            "inbound_orders": len(order_sets["inbound"]),
            "inbound_lines": line_counts["inbound"],
            "inbound_qty": _decimal_to_text(quantities["inbound"]),
            "outbound_orders": len(order_sets["outbound"]),
            "outbound_lines": line_counts["outbound"],
            "outbound_qty": _decimal_to_text(quantities["outbound"]),
            "item_count": len(items),
        },
        "owner_options": _scope_owner_options(access_scope),
        "items": items,
    }
