from __future__ import annotations

import datetime
from calendar import monthrange
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from allapp.inbound.models import InboundOrderLine
from allapp.outbound.models import OutboundOrderLine


ZERO_QTY = Decimal("0.000")


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


def build_pda_throughput_payload(
    *, user, mode, start_date, end_date, owner_id=None, warehouse_id=None
):
    user_warehouse_id = getattr(user, "warehouse_id", None)
    scoped_warehouse_id = user_warehouse_id or warehouse_id
    if user_warehouse_id:
        scoped_owner_id = owner_id
    else:
        scoped_owner_id = getattr(user, "owner_id", None) or owner_id

    inbound_lines = InboundOrderLine.objects.filter(
        order__biz_date__gte=start_date,
        order__biz_date__lte=end_date,
    )
    outbound_lines = OutboundOrderLine.objects.filter(
        order__biz_date__gte=start_date,
        order__biz_date__lte=end_date,
    )

    if scoped_owner_id:
        inbound_lines = inbound_lines.filter(order__owner_id=scoped_owner_id)
        outbound_lines = outbound_lines.filter(order__owner_id=scoped_owner_id)
    if scoped_warehouse_id:
        inbound_lines = inbound_lines.filter(order__warehouse_id=scoped_warehouse_id)
        outbound_lines = outbound_lines.filter(order__warehouse_id=scoped_warehouse_id)

    inbound_summary = _summary(inbound_lines)
    outbound_summary = _summary(outbound_lines)
    inbound_daily = _daily_map(inbound_lines, date_field="order__biz_date")
    outbound_daily = _daily_map(outbound_lines, date_field="order__biz_date")

    days = []
    current = start_date
    while current <= end_date:
        day_key = current.isoformat()
        inbound = inbound_daily.get(day_key, {"orders": 0, "lines": 0, "qty": ZERO_QTY})
        outbound = outbound_daily.get(day_key, {"orders": 0, "lines": 0, "qty": ZERO_QTY})
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
        "days": days,
    }
