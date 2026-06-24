from __future__ import annotations

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import PosPayment, PosSale, PosSaleLine

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.000")
MAX_TOP_N = 50


def _money_sum(field="amount", *, filter_expr=None):
    return Coalesce(
        Sum(field, filter=filter_expr),
        Value(ZERO_MONEY),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )


def _qty_sum(field="qty"):
    return Coalesce(
        Sum(field),
        Value(ZERO_QTY),
        output_field=DecimalField(max_digits=18, decimal_places=3),
    )


def _money_text(value):
    return format((value or ZERO_MONEY).quantize(Decimal("0.01")), ".2f")


def _qty_text(value):
    return format((value or ZERO_QTY).quantize(Decimal("0.001")), ".3f")


def _parse_date(value, name):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must use YYYY-MM-DD format.")


def _parse_positive_int(params, *names, default=None, maximum=None):
    raw = ""
    for name in names:
        raw = (params.get(name) or "").strip()
        if raw:
            break
    if not raw:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f"{names[0]} must be a positive integer.")
    value = int(raw)
    if maximum is not None:
        return min(value, maximum)
    return value


def parse_pos_stats_params(params):
    now = timezone.now()
    today = timezone.localtime(now).date() if timezone.is_aware(now) else now.date()
    start_raw = (params.get("start_date") or "").strip()
    end_raw = (params.get("end_date") or "").strip()
    start_date = _parse_date(start_raw, "start_date") if start_raw else today
    end_date = _parse_date(end_raw, "end_date") if end_raw else start_date
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date.")

    return {
        "start_date": start_date,
        "end_date": end_date,
        "owner_id": _parse_positive_int(params, "owner_id", "owner"),
        "cashier_id": _parse_positive_int(params, "cashier_id", "cashier"),
        "top_n": _parse_positive_int(params, "top_n", default=10, maximum=MAX_TOP_N),
    }


def _date_bounds(start_date, end_date):
    start_at = datetime.datetime.combine(start_date, datetime.time.min)
    end_at = datetime.datetime.combine(
        end_date + datetime.timedelta(days=1), datetime.time.min
    )
    if settings.USE_TZ:
        tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(start_at, tz)
        end_at = timezone.make_aware(end_at, tz)
    return start_at, end_at


def _payment_rows(line_qs):
    labels = dict(PosPayment.Method.choices)
    rows = (
        line_qs.filter(sale__status=PosSale.Status.COMPLETED)
        .values("sale__payment__method")
        .annotate(
            sale_count=Count("sale_id", distinct=True),
            amount=_money_sum(),
        )
        .order_by("-amount", "sale__payment__method")
    )
    return [
        {
            "method": row["sale__payment__method"] or "",
            "method_label": labels.get(row["sale__payment__method"], "未收款"),
            "sale_count": row["sale_count"] or 0,
            "amount": _money_text(row["amount"]),
        }
        for row in rows
    ]


def _owner_rows(line_qs):
    rows = (
        line_qs.filter(sale__status=PosSale.Status.COMPLETED)
        .values("owner_id", "owner__code", "owner__name")
        .annotate(
            sale_count=Count("sale_id", distinct=True),
            line_count=Count("id"),
            qty=_qty_sum(),
            amount=_money_sum(),
        )
        .order_by("-amount", "owner_id")
    )
    return [
        {
            "owner_id": row["owner_id"],
            "owner_code": row["owner__code"] or "",
            "owner_name": row["owner__name"] or "",
            "sale_count": row["sale_count"] or 0,
            "line_count": row["line_count"] or 0,
            "qty": _qty_text(row["qty"]),
            "amount": _money_text(row["amount"]),
        }
        for row in rows
    ]


def _product_rows(line_qs, *, top_n):
    rows = (
        line_qs.filter(sale__status=PosSale.Status.COMPLETED)
        .values(
            "product_id",
            "product__code",
            "product__sku",
            "product__name",
            "owner_id",
            "owner__name",
        )
        .annotate(
            sale_count=Count("sale_id", distinct=True),
            line_count=Count("id"),
            qty=_qty_sum(),
            amount=_money_sum(),
        )
        .order_by("-amount", "-qty", "product_id")[:top_n]
    )
    return [
        {
            "product_id": row["product_id"],
            "product_code": row["product__code"] or "",
            "product_sku": row["product__sku"] or "",
            "product_name": row["product__name"] or "",
            "owner_id": row["owner_id"],
            "owner_name": row["owner__name"] or "",
            "sale_count": row["sale_count"] or 0,
            "line_count": row["line_count"] or 0,
            "qty": _qty_text(row["qty"]),
            "amount": _money_text(row["amount"]),
        }
        for row in rows
    ]


def _cashier_rows(line_qs):
    rows = (
        line_qs.values("sale__cashier_id", "sale__cashier__username")
        .annotate(
            sale_count=Count("sale_id", distinct=True),
            completed_count=Count(
                "sale_id",
                filter=Q(sale__status=PosSale.Status.COMPLETED),
                distinct=True,
            ),
            voided_count=Count(
                "sale_id",
                filter=Q(sale__status=PosSale.Status.VOIDED),
                distinct=True,
            ),
            completed_amount=_money_sum(
                filter_expr=Q(sale__status=PosSale.Status.COMPLETED)
            ),
            voided_amount=_money_sum(filter_expr=Q(sale__status=PosSale.Status.VOIDED)),
        )
        .order_by("-completed_amount", "sale__cashier_id")
    )
    return [
        {
            "cashier_id": row["sale__cashier_id"],
            "cashier_username": row["sale__cashier__username"] or "",
            "sale_count": row["sale_count"] or 0,
            "completed_count": row["completed_count"] or 0,
            "voided_count": row["voided_count"] or 0,
            "completed_amount": _money_text(row["completed_amount"]),
            "voided_amount": _money_text(row["voided_amount"]),
        }
        for row in rows
    ]


def build_pos_stats_payload(*, user, params):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        raise ValidationError("当前用户未绑定仓库(warehouse)，无法查询 POS 统计。")

    parsed = parse_pos_stats_params(params)
    start_at, end_at = _date_bounds(parsed["start_date"], parsed["end_date"])

    line_qs = PosSaleLine.objects.filter(
        sale__warehouse_id=warehouse_id,
        sale__created_at__gte=start_at,
        sale__created_at__lt=end_at,
    )
    if parsed["owner_id"]:
        line_qs = line_qs.filter(owner_id=parsed["owner_id"])
    if parsed["cashier_id"]:
        line_qs = line_qs.filter(sale__cashier_id=parsed["cashier_id"])

    sale_qs = PosSale.objects.filter(id__in=line_qs.values("sale_id").distinct())
    counts = sale_qs.aggregate(
        sale_count=Count("id"),
        completed_count=Count("id", filter=Q(status=PosSale.Status.COMPLETED)),
        voided_count=Count("id", filter=Q(status=PosSale.Status.VOIDED)),
    )
    amounts = line_qs.aggregate(
        line_count=Count("id"),
        total_qty=_qty_sum(),
        gross_amount=_money_sum(),
        net_amount=_money_sum(filter_expr=Q(sale__status=PosSale.Status.COMPLETED)),
        voided_amount=_money_sum(filter_expr=Q(sale__status=PosSale.Status.VOIDED)),
    )

    return {
        "scope": {
            "warehouse_id": warehouse_id,
            "owner_id": parsed["owner_id"],
            "cashier_id": parsed["cashier_id"],
        },
        "period": {
            "start_date": parsed["start_date"].isoformat(),
            "end_date": parsed["end_date"].isoformat(),
        },
        "summary": {
            "sale_count": counts["sale_count"] or 0,
            "completed_count": counts["completed_count"] or 0,
            "voided_count": counts["voided_count"] or 0,
            "line_count": amounts["line_count"] or 0,
            "total_qty": _qty_text(amounts["total_qty"]),
            "gross_amount": _money_text(amounts["gross_amount"]),
            "voided_amount": _money_text(amounts["voided_amount"]),
            "net_amount": _money_text(amounts["net_amount"]),
        },
        "payments": _payment_rows(line_qs),
        "owners": _owner_rows(line_qs),
        "products": _product_rows(line_qs, top_n=parsed["top_n"]),
        "cashiers": _cashier_rows(line_qs),
    }
