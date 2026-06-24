from __future__ import annotations

import datetime
from calendar import monthrange
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from allapp.baseinfo.models import Owner
from allapp.core.choices import InvTxType
from allapp.inbound.constants import PDA_NO_ORDER_RECEIVE_SOURCE_MODEL
from allapp.inventory.models import InventoryTransaction
from allapp.outbound.models import OutboundOrderLine
from allapp.tasking.models import WmsTask

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


def build_pda_throughput_payload(
    *, user, mode, start_date, end_date, owner_id=None, warehouse_id=None
):
    scoped_owner_id, scoped_warehouse_id = _scoped_owner_warehouse(
        user=user,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )

    owner_options = _collect_owner_options(
        warehouse_id=scoped_warehouse_id,
        user=user,
    )

    outbound_lines = _outbound_lines(
        start_date=start_date,
        end_date=end_date,
        owner_id=scoped_owner_id,
        warehouse_id=scoped_warehouse_id,
    )

    inbound_transactions = _posted_receive_transactions(
        start_date=start_date,
        end_date=end_date,
        owner_id=scoped_owner_id,
        warehouse_id=scoped_warehouse_id,
    )

    inbound_summary = _tx_summary(inbound_transactions)
    outbound_summary = _summary(outbound_lines)
    inbound_daily = _tx_daily_map(inbound_transactions)
    outbound_daily = _daily_map(outbound_lines, date_field="order__biz_date")
    by_owner = _owner_rows(
        inbound_transactions=inbound_transactions,
        outbound_lines=outbound_lines,
        owner_options=owner_options,
    )

    days = []
    current = start_date
    while current <= end_date:
        day_key = current.isoformat()
        inbound = inbound_daily.get(day_key, {"orders": 0, "lines": 0, "qty": ZERO_QTY})
        outbound = outbound_daily.get(
            day_key, {"orders": 0, "lines": 0, "qty": ZERO_QTY}
        )
        days.append(
            {
                "date": day_key,
                "inbound_orders": inbound["orders"],
                "inbound_lines": inbound["lines"],
                "inbound_qty": _decimal_to_text(inbound["qty"]),
                "outbound_orders": outbound["orders"],
                "outbound_lines": outbound["lines"],
                "outbound_qty": _decimal_to_text(outbound["qty"]),
            }
        )
        current += datetime.timedelta(days=1)

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
        "summary": {
            "inbound_orders": inbound_summary["orders"] or 0,
            "inbound_lines": inbound_summary["lines"] or 0,
            "inbound_qty": _decimal_to_text(inbound_summary["qty"]),
            "outbound_orders": outbound_summary["orders"] or 0,
            "outbound_lines": outbound_summary["lines"] or 0,
            "outbound_qty": _decimal_to_text(outbound_summary["qty"]),
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
    scoped_owner_id, scoped_warehouse_id = _scoped_owner_warehouse(
        user=user,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )

    owner_options = _collect_owner_options(
        warehouse_id=scoped_warehouse_id,
        user=user,
    )

    inbound_transactions = InventoryTransaction.objects.none()
    inbound_summary = _empty_summary()
    inbound_items = []
    if metric in {"all", "inbound"}:
        inbound_transactions = _posted_receive_transactions(
            start_date=start_date,
            end_date=end_date,
            owner_id=scoped_owner_id,
            warehouse_id=scoped_warehouse_id,
        )
        inbound_summary = _tx_summary(inbound_transactions)
        inbound_items = _receive_detail_items(inbound_transactions)

    outbound_lines = OutboundOrderLine.objects.none()
    outbound_summary = _empty_summary()
    outbound_items = []
    if metric in {"all", "outbound"}:
        outbound_lines = _outbound_lines(
            start_date=start_date,
            end_date=end_date,
            owner_id=scoped_owner_id,
            warehouse_id=scoped_warehouse_id,
        )
        outbound_summary = _summary(outbound_lines)
        outbound_items = _outbound_detail_items(outbound_lines)

    items = inbound_items + outbound_items
    items.sort(key=_detail_sort_key, reverse=True)

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
        "summary": {
            "inbound_orders": inbound_summary["orders"] or 0,
            "inbound_lines": inbound_summary["lines"] or 0,
            "inbound_qty": _decimal_to_text(inbound_summary["qty"]),
            "outbound_orders": outbound_summary["orders"] or 0,
            "outbound_lines": outbound_summary["lines"] or 0,
            "outbound_qty": _decimal_to_text(outbound_summary["qty"]),
            "item_count": len(items),
        },
        "owner_options": owner_options,
        "items": items,
    }
