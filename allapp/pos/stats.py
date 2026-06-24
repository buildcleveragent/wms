from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    PosPayment,
    PosPaymentLine,
    PosRefund,
    PosReturn,
    PosReturnLine,
    PosSale,
    PosSaleLine,
)

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.000")
MAX_TOP_N = 50


def _money_sum(field="amount", *, filter_expr=None):
    return Coalesce(
        Sum(field, filter=filter_expr),
        Value(ZERO_MONEY),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )


def _qty_sum(field="qty", *, filter_expr=None):
    return Coalesce(
        Sum(field, filter=filter_expr),
        Value(ZERO_QTY),
        output_field=DecimalField(max_digits=18, decimal_places=3),
    )


def _money(value):
    return (value or ZERO_MONEY).quantize(Decimal("0.01"))


def _qty(value):
    return (value or ZERO_QTY).quantize(Decimal("0.001"))


def _money_text(value):
    return format(_money(value), ".2f")


def _qty_text(value):
    return format(_qty(value), ".3f")


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


def _allocated_amount(amount, scoped_amount, total_amount):
    amount = Decimal(amount or ZERO_MONEY)
    scoped_amount = Decimal(scoped_amount or ZERO_MONEY)
    total_amount = Decimal(total_amount or ZERO_MONEY)
    if amount == 0 or scoped_amount == 0 or total_amount <= 0:
        return ZERO_MONEY
    if scoped_amount == total_amount:
        return amount
    return amount * scoped_amount / total_amount


def _base_querysets(*, warehouse_id, start_at, end_at, owner_id=None, cashier_id=None):
    line_qs = PosSaleLine.objects.filter(
        sale__warehouse_id=warehouse_id,
        sale__created_at__gte=start_at,
        sale__created_at__lt=end_at,
    )
    return_line_qs = PosReturnLine.objects.filter(
        return_order__warehouse_id=warehouse_id,
        return_order__created_at__gte=start_at,
        return_order__created_at__lt=end_at,
        return_order__status=PosReturn.Status.COMPLETED,
    )
    if owner_id:
        line_qs = line_qs.filter(owner_id=owner_id)
        return_line_qs = return_line_qs.filter(owner_id=owner_id)
    if cashier_id:
        line_qs = line_qs.filter(sale__cashier_id=cashier_id)
        return_line_qs = return_line_qs.filter(return_order__cashier_id=cashier_id)
    return line_qs, return_line_qs


def _payment_rows(line_qs, return_line_qs):
    labels = dict(PosPayment.Method.choices)
    rows = defaultdict(
        lambda: {
            "sale_count": set(),
            "refund_count": set(),
            "sale_amount": ZERO_MONEY,
            "refund_amount": ZERO_MONEY,
        }
    )

    sale_scope = {
        row["sale_id"]: row["amount"] or ZERO_MONEY
        for row in line_qs.filter(sale__status=PosSale.Status.COMPLETED)
        .values("sale_id")
        .annotate(amount=_money_sum("amount"))
    }
    sale_totals = dict(
        PosSale.objects.filter(id__in=sale_scope.keys()).values_list(
            "id", "total_amount"
        )
    )
    payment_lines = (
        PosPaymentLine.objects.filter(
            sale_id__in=sale_scope.keys(),
            sale__status=PosSale.Status.COMPLETED,
            status=PosPayment.Status.PAID,
        )
        .values("sale_id", "method")
        .annotate(amount=_money_sum("amount"))
    )
    for line in payment_lines:
        sale_id = line["sale_id"]
        method = line["method"] or ""
        allocated = _allocated_amount(
            line["amount"], sale_scope.get(sale_id), sale_totals.get(sale_id)
        )
        if allocated == 0:
            continue
        rows[method]["sale_amount"] += allocated
        rows[method]["sale_count"].add(sale_id)

    return_scope = {
        row["return_order_id"]: row["amount"] or ZERO_MONEY
        for row in return_line_qs.values("return_order_id").annotate(
            amount=_money_sum("amount")
        )
    }
    return_totals = dict(
        PosReturn.objects.filter(id__in=return_scope.keys()).values_list(
            "id", "total_amount"
        )
    )
    refunds = (
        PosRefund.objects.filter(
            return_order_id__in=return_scope.keys(),
            return_order__status=PosReturn.Status.COMPLETED,
            status=PosRefund.Status.REFUNDED,
        )
        .values("return_order_id", "method")
        .annotate(amount=_money_sum("amount"))
    )
    for refund in refunds:
        return_id = refund["return_order_id"]
        method = refund["method"] or ""
        allocated = _allocated_amount(
            refund["amount"], return_scope.get(return_id), return_totals.get(return_id)
        )
        if allocated == 0:
            continue
        rows[method]["refund_amount"] += allocated
        rows[method]["refund_count"].add(return_id)

    payload = []
    for method, row in rows.items():
        sale_amount = _money(row["sale_amount"])
        refund_amount = _money(row["refund_amount"])
        net_amount = _money(sale_amount - refund_amount)
        payload.append(
            {
                "method": method,
                "method_label": labels.get(method, "未收款"),
                "sale_count": len(row["sale_count"]),
                "refund_count": len(row["refund_count"]),
                "sale_amount": _money_text(sale_amount),
                "refund_amount": _money_text(refund_amount),
                "net_amount": _money_text(net_amount),
                "amount": _money_text(net_amount),
            }
        )
    return sorted(
        payload, key=lambda item: (-Decimal(item["net_amount"]), item["method"])
    )


def _owner_rows(line_qs, return_line_qs):
    result = {}
    sales = (
        line_qs.filter(sale__status=PosSale.Status.COMPLETED)
        .values("owner_id", "owner__code", "owner__name")
        .annotate(
            sale_count=Count("sale_id", distinct=True),
            line_count=Count("id"),
            sale_qty=_qty_sum(),
            sale_amount=_money_sum(),
        )
    )
    for row in sales:
        result[row["owner_id"]] = {
            "owner_id": row["owner_id"],
            "owner_code": row["owner__code"] or "",
            "owner_name": row["owner__name"] or "",
            "sale_count": row["sale_count"] or 0,
            "return_count": 0,
            "line_count": row["line_count"] or 0,
            "sale_qty": row["sale_qty"] or ZERO_QTY,
            "return_qty": ZERO_QTY,
            "sale_amount": row["sale_amount"] or ZERO_MONEY,
            "return_amount": ZERO_MONEY,
        }

    returns = (
        return_line_qs.values("owner_id", "owner__code", "owner__name")
        .annotate(
            return_count=Count("return_order_id", distinct=True),
            return_qty=_qty_sum(),
            return_amount=_money_sum(),
        )
        .order_by()
    )
    for row in returns:
        current = result.setdefault(
            row["owner_id"],
            {
                "owner_id": row["owner_id"],
                "owner_code": row["owner__code"] or "",
                "owner_name": row["owner__name"] or "",
                "sale_count": 0,
                "return_count": 0,
                "line_count": 0,
                "sale_qty": ZERO_QTY,
                "return_qty": ZERO_QTY,
                "sale_amount": ZERO_MONEY,
                "return_amount": ZERO_MONEY,
            },
        )
        current["return_count"] = row["return_count"] or 0
        current["return_qty"] = row["return_qty"] or ZERO_QTY
        current["return_amount"] = row["return_amount"] or ZERO_MONEY

    rows = []
    for row in result.values():
        net_qty = _qty(row["sale_qty"] - row["return_qty"])
        net_amount = _money(row["sale_amount"] - row["return_amount"])
        rows.append(
            {
                "owner_id": row["owner_id"],
                "owner_code": row["owner_code"],
                "owner_name": row["owner_name"],
                "sale_count": row["sale_count"],
                "return_count": row["return_count"],
                "line_count": row["line_count"],
                "sale_qty": _qty_text(row["sale_qty"]),
                "return_qty": _qty_text(row["return_qty"]),
                "net_qty": _qty_text(net_qty),
                "qty": _qty_text(net_qty),
                "sale_amount": _money_text(row["sale_amount"]),
                "return_amount": _money_text(row["return_amount"]),
                "net_amount": _money_text(net_amount),
                "amount": _money_text(net_amount),
            }
        )
    return sorted(
        rows, key=lambda item: (-Decimal(item["net_amount"]), item["owner_id"])
    )


def _product_rows(line_qs, return_line_qs, *, top_n):
    result = {}
    sales = (
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
            sale_qty=_qty_sum(),
            sale_amount=_money_sum(),
        )
    )
    for row in sales:
        result[(row["product_id"], row["owner_id"])] = {
            "product_id": row["product_id"],
            "product_code": row["product__code"] or "",
            "product_sku": row["product__sku"] or "",
            "product_name": row["product__name"] or "",
            "owner_id": row["owner_id"],
            "owner_name": row["owner__name"] or "",
            "sale_count": row["sale_count"] or 0,
            "return_count": 0,
            "line_count": row["line_count"] or 0,
            "sale_qty": row["sale_qty"] or ZERO_QTY,
            "return_qty": ZERO_QTY,
            "sale_amount": row["sale_amount"] or ZERO_MONEY,
            "return_amount": ZERO_MONEY,
        }

    returns = (
        return_line_qs.values(
            "product_id",
            "product__code",
            "product__sku",
            "product__name",
            "owner_id",
            "owner__name",
        )
        .annotate(
            return_count=Count("return_order_id", distinct=True),
            return_qty=_qty_sum(),
            return_amount=_money_sum(),
        )
        .order_by()
    )
    for row in returns:
        key = (row["product_id"], row["owner_id"])
        current = result.setdefault(
            key,
            {
                "product_id": row["product_id"],
                "product_code": row["product__code"] or "",
                "product_sku": row["product__sku"] or "",
                "product_name": row["product__name"] or "",
                "owner_id": row["owner_id"],
                "owner_name": row["owner__name"] or "",
                "sale_count": 0,
                "return_count": 0,
                "line_count": 0,
                "sale_qty": ZERO_QTY,
                "return_qty": ZERO_QTY,
                "sale_amount": ZERO_MONEY,
                "return_amount": ZERO_MONEY,
            },
        )
        current["return_count"] = row["return_count"] or 0
        current["return_qty"] = row["return_qty"] or ZERO_QTY
        current["return_amount"] = row["return_amount"] or ZERO_MONEY

    rows = []
    for row in result.values():
        net_qty = _qty(row["sale_qty"] - row["return_qty"])
        net_amount = _money(row["sale_amount"] - row["return_amount"])
        rows.append(
            {
                "product_id": row["product_id"],
                "product_code": row["product_code"],
                "product_sku": row["product_sku"],
                "product_name": row["product_name"],
                "owner_id": row["owner_id"],
                "owner_name": row["owner_name"],
                "sale_count": row["sale_count"],
                "return_count": row["return_count"],
                "line_count": row["line_count"],
                "sale_qty": _qty_text(row["sale_qty"]),
                "return_qty": _qty_text(row["return_qty"]),
                "net_qty": _qty_text(net_qty),
                "qty": _qty_text(net_qty),
                "sale_amount": _money_text(row["sale_amount"]),
                "return_amount": _money_text(row["return_amount"]),
                "net_amount": _money_text(net_amount),
                "amount": _money_text(net_amount),
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            -Decimal(item["net_amount"]),
            -Decimal(item["net_qty"]),
            item["product_id"],
        ),
    )[:top_n]


def _cashier_rows(line_qs, return_line_qs):
    result = {}
    sales = (
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
        .order_by()
    )
    for row in sales:
        cashier_id = row["sale__cashier_id"]
        result[cashier_id] = {
            "cashier_id": cashier_id,
            "cashier_username": row["sale__cashier__username"] or "",
            "sale_count": row["sale_count"] or 0,
            "completed_count": row["completed_count"] or 0,
            "voided_count": row["voided_count"] or 0,
            "return_count": 0,
            "completed_amount": row["completed_amount"] or ZERO_MONEY,
            "voided_amount": row["voided_amount"] or ZERO_MONEY,
            "return_amount": ZERO_MONEY,
        }

    returns = (
        return_line_qs.values(
            "return_order__cashier_id", "return_order__cashier__username"
        )
        .annotate(
            return_count=Count("return_order_id", distinct=True),
            return_amount=_money_sum(),
        )
        .order_by()
    )
    for row in returns:
        cashier_id = row["return_order__cashier_id"]
        current = result.setdefault(
            cashier_id,
            {
                "cashier_id": cashier_id,
                "cashier_username": row["return_order__cashier__username"] or "",
                "sale_count": 0,
                "completed_count": 0,
                "voided_count": 0,
                "return_count": 0,
                "completed_amount": ZERO_MONEY,
                "voided_amount": ZERO_MONEY,
                "return_amount": ZERO_MONEY,
            },
        )
        current["return_count"] = row["return_count"] or 0
        current["return_amount"] = row["return_amount"] or ZERO_MONEY

    rows = []
    for row in result.values():
        net_amount = _money(row["completed_amount"] - row["return_amount"])
        rows.append(
            {
                "cashier_id": row["cashier_id"],
                "cashier_username": row["cashier_username"],
                "sale_count": row["sale_count"],
                "completed_count": row["completed_count"],
                "voided_count": row["voided_count"],
                "return_count": row["return_count"],
                "completed_amount": _money_text(row["completed_amount"]),
                "voided_amount": _money_text(row["voided_amount"]),
                "return_amount": _money_text(row["return_amount"]),
                "net_amount": _money_text(net_amount),
            }
        )
    return sorted(
        rows, key=lambda item: (-Decimal(item["net_amount"]), item["cashier_id"] or 0)
    )


def build_pos_stats_payload(*, user, params):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        raise ValidationError("当前用户未绑定仓库(warehouse)，无法查询 POS 统计。")

    parsed = parse_pos_stats_params(params)
    start_at, end_at = _date_bounds(parsed["start_date"], parsed["end_date"])

    line_qs, return_line_qs = _base_querysets(
        warehouse_id=warehouse_id,
        start_at=start_at,
        end_at=end_at,
        owner_id=parsed["owner_id"],
        cashier_id=parsed["cashier_id"],
    )

    sale_qs = PosSale.objects.filter(id__in=line_qs.values("sale_id").distinct())
    return_qs = PosReturn.objects.filter(
        id__in=return_line_qs.values("return_order_id").distinct()
    )
    counts = sale_qs.aggregate(
        sale_count=Count("id"),
        completed_count=Count("id", filter=Q(status=PosSale.Status.COMPLETED)),
        voided_count=Count("id", filter=Q(status=PosSale.Status.VOIDED)),
    )
    amounts = line_qs.aggregate(
        line_count=Count("id"),
        total_qty=_qty_sum(),
        gross_amount=_money_sum(),
        completed_qty=_qty_sum(filter_expr=Q(sale__status=PosSale.Status.COMPLETED)),
        sales_amount=_money_sum(filter_expr=Q(sale__status=PosSale.Status.COMPLETED)),
        voided_qty=_qty_sum(filter_expr=Q(sale__status=PosSale.Status.VOIDED)),
        voided_amount=_money_sum(filter_expr=Q(sale__status=PosSale.Status.VOIDED)),
    )
    return_amounts = return_line_qs.aggregate(
        return_line_count=Count("id"),
        return_qty=_qty_sum(),
        return_amount=_money_sum(),
    )
    return_counts = return_qs.aggregate(return_count=Count("id"))

    sales_amount = amounts["sales_amount"] or ZERO_MONEY
    return_amount = return_amounts["return_amount"] or ZERO_MONEY
    completed_qty = amounts["completed_qty"] or ZERO_QTY
    return_qty = return_amounts["return_qty"] or ZERO_QTY

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
            "return_count": return_counts["return_count"] or 0,
            "line_count": amounts["line_count"] or 0,
            "return_line_count": return_amounts["return_line_count"] or 0,
            "total_qty": _qty_text(amounts["total_qty"]),
            "completed_qty": _qty_text(completed_qty),
            "voided_qty": _qty_text(amounts["voided_qty"]),
            "return_qty": _qty_text(return_qty),
            "net_qty": _qty_text(completed_qty - return_qty),
            "gross_amount": _money_text(amounts["gross_amount"]),
            "sales_amount": _money_text(sales_amount),
            "voided_amount": _money_text(amounts["voided_amount"]),
            "return_amount": _money_text(return_amount),
            "net_amount": _money_text(sales_amount - return_amount),
        },
        "payments": _payment_rows(line_qs, return_line_qs),
        "owners": _owner_rows(line_qs, return_line_qs),
        "products": _product_rows(line_qs, return_line_qs, top_n=parsed["top_n"]),
        "cashiers": _cashier_rows(line_qs, return_line_qs),
    }
