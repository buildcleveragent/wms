"""SQL aggregates and bounded calendar pages, independent of selected range length."""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db.models import Case, CharField, Count, F, Max, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import (
    Cast,
    Coalesce,
    Concat,
    NullIf,
    TruncDate,
    TruncMonth,
    TruncWeek,
    TruncYear,
)
from rest_framework.exceptions import ValidationError

from allapp.billing.models import Bill, BillingAccrual, PaymentReceipt
from allapp.billing.services.ledger import financial_ledger_accruals
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.pos.models import PosAuditLog, PosReturnLine, PosSaleLine
from allapp.tasking.models import WmsTask

from .boss_activity import date_bounds, sources, summarize
from .boss_activity_detail import fields, inventory_row, task_row
from .boss_contract import trend_granularity
from .services_operations import OperationFilters, _inventory_transactions, _shipment_lines


@dataclass
class Metric:
    key: str
    label: str
    qs: object
    timestamp: str
    count_field: str = "pk"
    money: str = ""
    currency: str = "currency"
    source: object = None


def metric_sources(scope, start, end):
    events = {s.key: s for s in sources(scope)}
    for key, label, source, action in [
        ("orders", "出库正式提交", "order", None),
        ("pos_sales", "POS销售完成", "pos", "CHECKOUT"),
        ("pos_returns", "POS退货", "pos", "RETURN"),
        ("pos_voids", "POS作废", "pos", "VOID"),
        ("alerts", "异常变化", "alert", None),
    ]:
        s = events[source]
        qs = s.qs.filter(action=action) if action else s.qs
        if source == "pos":
            # Select log IDs first: permission joins must not multiply monetary sums.
            qs = PosAuditLog.objects.filter(pk__in=qs.values("pk")).select_related(
                "sale__warehouse", "actor", "return_order"
            )
            if action == "RETURN":
                lines = scope.apply(
                    PosReturnLine.objects.all(), warehouse="return_order__warehouse_id"
                )
                lines = (
                    lines.filter(return_order_id=OuterRef("return_order_id"))
                    .order_by()
                    .values("return_order_id")
                )
            else:
                lines = scope.apply(PosSaleLine.objects.all(), warehouse="sale__warehouse_id")
                lines = lines.filter(sale_id=OuterRef("sale_id")).order_by().values("sale_id")
            amount = lines.annotate(total=Sum("amount")).values("total")[:1]
            qs = qs.annotate(
                visible_amount=Subquery(amount),
                currency_code=Value("UNKNOWN", output_field=CharField()),
            )
            yield Metric(
                key,
                label,
                qs,
                s.timestamp,
                money="visible_amount",
                currency="currency_code",
                source=s,
            )
        else:
            yield Metric(key, label, qs, s.timestamp, source=s)
    tasks = scope.apply(WmsTask.objects.all())
    yield Metric("tasks_created", "新增任务", tasks, "created_at")
    yield Metric("tasks_completed", "完成任务", tasks.filter(status="COMPLETED"), "finished_at")
    stock = scope.apply(InventoryTransaction.objects.filter(posted_at__isnull=False))
    yield Metric("inventory", "实际库存流水（含POS）", stock, "posted_at")
    receipt = scope.apply(PaymentReceipt.objects.filter(posted_at__isnull=False)).annotate(
        signed_amount=Case(When(reversal_of__isnull=False, then=-F("amount")), default=F("amount"))
    )
    yield Metric("receipts", "回款净额（含冲销）", receipt, "posted_at", money="signed_amount")
    yield Metric(
        "accruals",
        "应计收入",
        financial_ledger_accruals(scope.apply(BillingAccrual.objects.all())),
        "service_date",
        money="amount",
    )
    yield Metric(
        "bills",
        "已开票",
        scope.apply(Bill.objects.filter(status__in=["ISSUED", "PAID"])),
        "issue_date",
        money="total",
    )
    filters = OperationFilters(
        start_date=start, end_date=end, owner_id=scope.owner_id, warehouse_id=scope.warehouse_id
    )
    inbound = _inventory_transactions("inbound", filters, scope.access, scope.user)
    tasks_for_tx = WmsTask.objects.filter(pk=OuterRef("src_id"))
    inbound = inbound.annotate(
        order_identity=Concat(
            Coalesce(Subquery(tasks_for_tx.values("source_model")[:1]), Value("WmsTask")),
            Value(":"),
            Coalesce(
                NullIf(Subquery(tasks_for_tx.values("source_pk")[:1]), Value("")),
                Cast("src_id", CharField()),
            ),
            output_field=CharField(),
        )
    )
    yield Metric("inbound", "实际收货单数", inbound, "posted_at", "order_identity")
    yield Metric(
        "outbound",
        "实际发运任务数",
        _shipment_lines(filters, scope.access, scope.user),
        "task__finished_at",
        "task_id",
    )


def ranged(metric, start, end):
    # Datetime bounds are exclusive at the end; DateField ranges are inclusive.
    if metric.timestamp in {"service_date", "issue_date"}:
        return metric.qs.filter(**{f"{metric.timestamp}__range": (start, end)})
    left, right = date_bounds(start, end)
    return metric.qs.filter(**{f"{metric.timestamp}__gte": left, f"{metric.timestamp}__lt": right})


def totals(scope, start, end):
    result = []
    for metric in metric_sources(scope, start, end):
        qs = ranged(metric, start, end).order_by()
        row = {
            "key": metric.key,
            "label": metric.label,
            "count": qs.values(metric.count_field).distinct().count(),
        }
        if metric.money:
            row["money"] = [
                {"currency": item[metric.currency], "amount": item["amount"]}
                for item in qs.values(metric.currency)
                .annotate(amount=Sum(metric.money))
                .order_by(metric.currency)
            ]
        row["basis"] = metric.timestamp
        result.append(row)
    return result


def floor_period(day, granularity):
    if granularity == "day":
        return day
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1, **({"month": 1} if granularity == "year" else {}))


def shift_period(day, granularity, count):
    if granularity in {"day", "week"}:
        return day + timedelta(days=count * (7 if granularity == "week" else 1))
    months = day.year * 12 + day.month - 1 + count * (12 if granularity == "year" else 1)
    year, month0 = divmod(months, 12)
    return date(year, month0 + 1, 1)


def period_page(scope, start, end, granularity="auto", page=1, size=100):
    if granularity not in {"auto", "day", "week", "month", "year"}:
        raise ValidationError({"granularity": "未知统计粒度。"})
    resolved = trend_granularity(start, end) if granularity == "auto" else granularity
    first, last = floor_period(start, resolved), floor_period(end, resolved)
    if resolved in {"day", "week"}:
        count = (last - first).days // (7 if resolved == "week" else 1) + 1
    elif resolved == "month":
        count = (last.year - first.year) * 12 + last.month - first.month + 1
    else:
        count = last.year - first.year + 1
    offset = (page - 1) * size
    response = {
        "granularity": resolved,
        "requested_granularity": granularity,
        "coverage": {
            "date_from": start,
            "date_to": end,
            "completeness": "UNKNOWN",
            "zero_semantics": "NO_MATCHING_RECORDS",
        },
        "notice": "空周期表示未查到业务记录，不代表已证明该段历史数据完整。",
        "page": page,
        "page_size": size,
        "count": count,
        "results": [],
    }
    if offset >= count:
        return response
    days = [shift_period(first, resolved, i) for i in range(offset, min(offset + size, count))]
    left = max(start, days[0])
    right = min(end, shift_period(days[-1], resolved, 1) - timedelta(days=1))
    rows = {
        d: {
            "date": str(d),
            "date_from": str(max(start, d)),
            "date_to": str(min(end, shift_period(d, resolved, 1) - timedelta(days=1))),
            "metrics": {},
        }
        for d in days
    }
    trunc = {"day": TruncDate, "week": TruncWeek, "month": TruncMonth, "year": TruncYear}[resolved]
    for metric in metric_sources(scope, left, right):
        qs = ranged(metric, left, right).order_by().annotate(bucket=trunc(metric.timestamp))
        grouped = qs.values("bucket").annotate(value=Count(metric.count_field, distinct=True))
        for d in days:
            rows[d]["metrics"][metric.key] = {"count": 0, "money": []}
        for item in grouped:
            d = item["bucket"].date() if isinstance(item["bucket"], datetime) else item["bucket"]
            if d in rows:
                rows[d]["metrics"][metric.key]["count"] = item["value"]
        if metric.money:
            money_qs = qs.values("bucket", metric.currency).annotate(amount=Sum(metric.money))
            if metric.key == "accruals":
                money_qs = money_qs.annotate(tax=Sum("tax_amount"))
            for item in money_qs:
                d = (
                    item["bucket"].date()
                    if isinstance(item["bucket"], datetime)
                    else item["bucket"]
                )
                if d in rows:
                    rows[d]["metrics"][metric.key]["money"].append(
                        {
                            "currency": item[metric.currency],
                            "amount": item["amount"],
                            "total": (item["amount"] or 0) + (item.get("tax") or 0),
                        }
                    )
    response["results"] = list(rows.values())
    return response


def comparison_ranges(start, end, preset="custom"):
    if preset not in {
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "this_month",
        "this_year",
        "custom",
    }:
        raise ValidationError({"preset": "未知日期选择。"})
    try:
        if preset in {"this_week", "last_week"}:
            previous = (start - timedelta(days=7), end - timedelta(days=7))
        elif preset == "this_month":
            prev_end = start.replace(day=1) - timedelta(days=1)
            previous = (prev_end.replace(day=1), prev_end.replace(day=min(end.day, prev_end.day)))
        elif preset == "this_year":
            previous = (
                start.replace(year=start.year - 1),
                end.replace(
                    year=end.year - 1,
                    day=min(end.day, calendar.monthrange(end.year - 1, end.month)[1]),
                ),
            )
        else:
            previous = (start - timedelta(days=(end - start).days + 1), start - timedelta(days=1))
        yoy = tuple(
            d.replace(year=d.year - 1, day=min(d.day, calendar.monthrange(d.year - 1, d.month)[1]))
            for d in (start, end)
        )
    except (ValueError, OverflowError):
        return {"previous": None, "year_over_year": None}
    return {
        "previous": previous if min(d.year for d in previous) >= 1000 else None,
        "year_over_year": yoy if min(d.year for d in yoy) >= 1000 else None,
    }


def metric_details(scope, start, end, key, page, size, params=None):
    metric = next((m for m in metric_sources(scope, start, end) if m.key == key), None)
    if metric is None:
        raise ValidationError({"metric": "未知统计指标。"})
    qs = ranged(metric, start, end)
    params = params or {}
    if key == "inventory" and not params.get("product"):
        return stock_groups(scope, qs, page, size)
    if key == "inventory" and params.get("product"):
        try:
            product = int(params["product"])
        except (TypeError, ValueError):
            raise ValidationError({"product": "商品编号必须是整数。"}) from None
        qs = qs.filter(product_id=product, base_unit=params.get("unit", ""))
    count = qs.count()
    rows = []
    for row in qs.order_by("-" + metric.timestamp, "-pk")[(page - 1) * size : page * size]:
        if metric.source:
            event = summarize(metric.source, row, scope)
            value = {
                "id": event["id"],
                "title": event["title"] + " · " + event["record_no"],
                "fields": fields(时间=event["occurred_at"], 摘要=event["summary"]),
                "target": event["target"],
            }
        elif key.startswith("tasks_"):
            value = task_row(row)
        elif key in {"inventory", "inbound"}:
            value = inventory_row(row)
            value["target"] = {"kind": "inventory", "id": row.pk}
        elif key == "outbound":
            value = {
                "id": row.pk,
                "title": row.task.task_no,
                "fields": fields(
                    商品=row.product.name, 实际数量=row.qty_done, 单位="历史单位见来源单据"
                ),
                "target": {"kind": "task_detail", "id": row.task_id},
            }
        else:
            target = {"accruals": "accrual", "bills": "bill", "receipts": "receipt"}[key]
            value = {
                "id": row.pk,
                "title": str(
                    getattr(row, "invoice_no", None) or getattr(row, "receipt_no", None) or row.pk
                ),
                "fields": fields(
                    金额=getattr(row, metric.money),
                    币种=row.currency,
                    日期=getattr(row, metric.timestamp),
                ),
                "target": {"kind": target, "id": row.pk},
            }
        rows.append(value)
    return {
        "count": count,
        "metric": key,
        "metric_count": qs.values(metric.count_field).distinct().count(),
        "notices": (
            ["汇总按业务单据／任务去重，以下展开其实际明细行。"]
            if key in {"inbound", "outbound"}
            else []
        ),
        "label": metric.label,
        "page": page,
        "page_size": size,
        "results": rows,
    }


def sla_details(scope, start, end, ratio, page, size):
    """Show the exact eligible denominator and numerator for a processed SLA ratio."""
    from django.db.models import Q

    from .models import FactOutboundOrderSLA

    if ratio not in {"on_time", "in_full", "otif", "coverage"}:
        raise ValidationError({"ratio": "未知 SLA 指标。"})
    qs = scope.apply(
        FactOutboundOrderSLA.objects.filter(order_date__date__range=(start, end)),
        owner="owner__owner_id",
        warehouse="warehouse__warehouse_id",
    )
    if ratio != "coverage":
        qs = qs.filter(sla_eligible=True)
    match = Q(sla_eligible=True) if ratio == "coverage" else Q(**{ratio: True})
    count = qs.count()
    numerator = qs.filter(match).count()
    processed_at = qs.aggregate(latest=Max("updated_at"))["latest"]
    return {
        "count": count,
        "page": page,
        "page_size": size,
        "processed_at": processed_at,
        "notices": [f"分子 {numerator} 单 / 分母 {count} 单；下面列出分母全部订单及是否计入分子。"],
        "results": [
            {
                "id": row.pk,
                "title": f"出库单 #{row.order_id}",
                "target": {"kind": "outbound_document", "id": row.order_id},
                "fields": fields(
                    计入分子=(
                        "是"
                        if (row.sla_eligible if ratio == "coverage" else getattr(row, ratio))
                        else "否"
                    ),
                    预计发货=row.etd,
                    实际发货=row.shipped_at,
                    实际加工时间=row.updated_at,
                ),
            }
            for row in qs.order_by("order_date", "id")[(page - 1) * size : page * size]
        ],
    }


def stock_groups(scope, qs, page, size):
    grouped = (
        qs.order_by()
        .values("product_id", "product__name", "owner_id", "warehouse_id", "base_unit")
        .annotate(delta=Sum("qty_delta"), records=Count("id"))
        .order_by("product_id", "owner_id", "warehouse_id", "base_unit")
    )
    count = grouped.count()
    records = qs.count()
    rows = []
    for row in grouped[(page - 1) * size : page * size]:
        current = (
            scope.apply(InventoryDetail.objects.all())
            .filter(
                product_id=row["product_id"],
                owner_id=row["owner_id"],
                warehouse_id=row["warehouse_id"],
                base_unit=row["base_unit"],
            )
            .aggregate(qty=Sum("onhand_qty"))["qty"]
        )
        rows.append(
            {
                "id": f"{row['warehouse_id']}:{row['owner_id']}:{row['product_id']}:{row['base_unit']}",
                "title": row["product__name"],
                "fields": fields(
                    本期实际净增减=row["delta"],
                    单位=row["base_unit"] or "未记录",
                    当前库存=current,
                    流水条数=row["records"],
                ),
                "drill": {
                    "type": "periods",
                    "metric": "inventory",
                    "product": row["product_id"],
                    "unit": row["base_unit"],
                    "owner": row["owner_id"],
                    "warehouse": row["warehouse_id"],
                },
            }
        )
    return {
        "count": count,
        "page": page,
        "page_size": size,
        "results": rows,
        "notices": [
            f"{records} 条实际流水按商品、单位、仓库和货主分组。库内移位净变化为零；当前库存不是历史截止余额。"
        ],
    }
