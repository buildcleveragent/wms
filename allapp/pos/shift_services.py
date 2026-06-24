from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import (
    PosAuditLog,
    PosPayment,
    PosPaymentLine,
    PosPrintLog,
    PosRefund,
    PosReturn,
    PosSale,
    PosSaleLine,
    PosShift,
    PosShiftPaymentSummary,
)

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.000")
MONEY = Decimal("0.01")
QTY = Decimal("0.001")


def _money(value):
    if value in (None, ""):
        value = ZERO_MONEY
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _qty(value):
    if value in (None, ""):
        value = ZERO_QTY
    return Decimal(str(value)).quantize(QTY, rounding=ROUND_HALF_UP)


def _money_text(value):
    return format(_money(value), ".2f")


def _qty_text(value):
    return format(_qty(value), ".3f")


def _sum_money(field, *, filter_expr=None):
    return Coalesce(
        Sum(field, filter=filter_expr),
        Value(ZERO_MONEY),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )


def _sum_qty(field, *, filter_expr=None):
    return Coalesce(
        Sum(field, filter=filter_expr),
        Value(ZERO_QTY),
        output_field=DecimalField(max_digits=18, decimal_places=3),
    )


def _warehouse_id_or_error(user):
    warehouse_id = getattr(user, "warehouse_id", None)
    if not warehouse_id:
        raise ValidationError("当前用户未绑定仓库(warehouse)，无法操作 POS 班次。")
    return warehouse_id


def _make_shift_no(now=None):
    now = now or timezone.now()
    for _ in range(8):
        shift_no = f"SHIFT{now:%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"
        if not PosShift.objects.filter(shift_no=shift_no).exists():
            return shift_no
    raise ValidationError("无法生成 POS 班次号，请重试。")


def current_shift_for_user(user, *, for_update=False):
    warehouse_id = _warehouse_id_or_error(user)
    queryset = PosShift.objects.filter(
        warehouse_id=warehouse_id,
        cashier=user,
        status__in=[PosShift.Status.OPEN, PosShift.Status.REOPENED],
    ).order_by("-opened_at", "-id")
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.first()


@transaction.atomic
def open_pos_shift(*, user, opening_cash_amount=ZERO_MONEY, remark=""):
    warehouse_id = _warehouse_id_or_error(user)
    if current_shift_for_user(user, for_update=True):
        raise ValidationError("当前收银员已有进行中的 POS 班次，请先交班。")

    now = timezone.now()
    shift = PosShift.objects.create(
        shift_no=_make_shift_no(now),
        warehouse_id=warehouse_id,
        cashier=user,
        opened_by=user if user and user.is_authenticated else None,
        opened_at=now,
        opening_cash_amount=_money(opening_cash_amount),
        expected_cash_amount=_money(opening_cash_amount),
        actual_cash_amount=_money(opening_cash_amount),
        remark=(remark or "").strip(),
    )
    PosAuditLog.objects.create(
        action=PosAuditLog.Action.SHIFT_OPEN,
        shift=shift,
        actor=user if user and user.is_authenticated else None,
        metadata={"opening_cash_amount": _money_text(opening_cash_amount)},
    )
    return shift


def _payment_summary_rows_for_shift(shift):
    sale_rows = (
        PosPaymentLine.objects.filter(
            sale__shift=shift,
            sale__status=PosSale.Status.COMPLETED,
            status=PosPayment.Status.PAID,
        )
        .values("method")
        .annotate(
            sale_count=Count("sale_id", distinct=True),
            sale_amount=_sum_money("amount"),
            amount_received=_sum_money("amount_received"),
            change_amount=_sum_money("change_amount"),
        )
        .order_by("method")
    )
    result = {
        row["method"]: {
            "method": row["method"],
            "method_label": dict(PosPayment.Method.choices).get(row["method"], ""),
            "sale_count": row["sale_count"] or 0,
            "refund_count": 0,
            "sale_amount": row["sale_amount"] or ZERO_MONEY,
            "refund_amount": ZERO_MONEY,
            "expected_amount": row["sale_amount"] or ZERO_MONEY,
            "amount_received": row["amount_received"] or ZERO_MONEY,
            "change_amount": row["change_amount"] or ZERO_MONEY,
        }
        for row in sale_rows
    }
    refund_rows = (
        PosRefund.objects.filter(
            shift=shift,
            return_order__status=PosReturn.Status.COMPLETED,
            status=PosRefund.Status.REFUNDED,
        )
        .values("method")
        .annotate(
            refund_count=Count("return_order_id", distinct=True),
            refund_amount=_sum_money("amount"),
        )
        .order_by("method")
    )
    for row in refund_rows:
        method = row["method"]
        current = result.setdefault(
            method,
            {
                "method": method,
                "method_label": dict(PosPayment.Method.choices).get(method, ""),
                "sale_count": 0,
                "refund_count": 0,
                "sale_amount": ZERO_MONEY,
                "refund_amount": ZERO_MONEY,
                "expected_amount": ZERO_MONEY,
                "amount_received": ZERO_MONEY,
                "change_amount": ZERO_MONEY,
            },
        )
        current["refund_count"] = row["refund_count"] or 0
        current["refund_amount"] = row["refund_amount"] or ZERO_MONEY
        current["expected_amount"] = _money(
            current["sale_amount"] - current["refund_amount"]
        )
    return result


def build_shift_summary(shift):
    sales = PosSale.objects.filter(shift=shift)
    sale_counts = sales.aggregate(
        sale_count=Count("id"),
        completed_count=Count("id", filter=Q(status=PosSale.Status.COMPLETED)),
        voided_count=Count("id", filter=Q(status=PosSale.Status.VOIDED)),
        net_amount=_sum_money(
            "total_amount", filter_expr=Q(status=PosSale.Status.COMPLETED)
        ),
        voided_amount=_sum_money(
            "total_amount", filter_expr=Q(status=PosSale.Status.VOIDED)
        ),
    )
    return_totals = PosReturn.objects.filter(shift=shift).aggregate(
        return_count=Count("id", filter=Q(status=PosReturn.Status.COMPLETED)),
        return_amount=_sum_money(
            "total_amount", filter_expr=Q(status=PosReturn.Status.COMPLETED)
        ),
    )
    line_totals = PosSaleLine.objects.filter(sale__shift=shift).aggregate(
        line_count=Count("id"),
        completed_line_count=Count(
            "id", filter=Q(sale__status=PosSale.Status.COMPLETED)
        ),
        completed_qty=_sum_qty(
            "qty", filter_expr=Q(sale__status=PosSale.Status.COMPLETED)
        ),
        completed_amount=_sum_money(
            "amount", filter_expr=Q(sale__status=PosSale.Status.COMPLETED)
        ),
        voided_qty=_sum_qty("qty", filter_expr=Q(sale__status=PosSale.Status.VOIDED)),
        voided_amount=_sum_money(
            "amount", filter_expr=Q(sale__status=PosSale.Status.VOIDED)
        ),
    )
    net_amount = _money(
        (sale_counts["net_amount"] or ZERO_MONEY)
        - (return_totals["return_amount"] or ZERO_MONEY)
    )

    payment_rows = _payment_summary_rows_for_shift(shift)
    payment_summaries = []
    for method, label in PosPayment.Method.choices:
        row = payment_rows.get(method)
        if not row:
            continue
        expected = _money(row["expected_amount"])
        payment_summaries.append(
            {
                "method": method,
                "method_label": label,
                "sale_count": row["sale_count"],
                "refund_count": row["refund_count"],
                "sale_amount": _money_text(row["sale_amount"]),
                "refund_amount": _money_text(row["refund_amount"]),
                "expected_amount": _money_text(expected),
                "actual_amount": _money_text(expected),
                "difference": _money_text(ZERO_MONEY),
                "amount_received": _money_text(row["amount_received"]),
                "change_amount": _money_text(row["change_amount"]),
            }
        )

    cash_expected_sales = _money(
        payment_rows.get(PosPayment.Method.CASH, {}).get("expected_amount", ZERO_MONEY)
    )
    expected_cash_total = _money(shift.opening_cash_amount + cash_expected_sales)

    return {
        "sale_count": sale_counts["sale_count"] or 0,
        "completed_count": sale_counts["completed_count"] or 0,
        "voided_count": sale_counts["voided_count"] or 0,
        "line_count": line_totals["line_count"] or 0,
        "completed_line_count": line_totals["completed_line_count"] or 0,
        "completed_qty": _qty_text(line_totals["completed_qty"]),
        "voided_qty": _qty_text(line_totals["voided_qty"]),
        "gross_sales_amount": _money_text(sale_counts["net_amount"]),
        "return_count": return_totals["return_count"] or 0,
        "return_amount": _money_text(return_totals["return_amount"]),
        "net_amount": _money_text(net_amount),
        "completed_line_amount": _money_text(line_totals["completed_amount"]),
        "voided_amount": _money_text(sale_counts["voided_amount"]),
        "voided_line_amount": _money_text(line_totals["voided_amount"]),
        "opening_cash_amount": _money_text(shift.opening_cash_amount),
        "expected_cash_amount": _money_text(expected_cash_total),
        "cash_sales_amount": _money_text(cash_expected_sales),
        "payments": payment_summaries,
    }


def _actual_payment_amounts(actual_payments):
    if not actual_payments:
        return {}
    if isinstance(actual_payments, dict):
        return {
            str(method).strip().upper(): _money(amount)
            for method, amount in actual_payments.items()
        }

    result = {}
    for item in actual_payments:
        method = str(item.get("method") or "").strip().upper()
        if not method:
            continue
        result[method] = _money(item.get("actual_amount"))
    return result


@transaction.atomic
def close_pos_shift(
    *, shift_id, user, actual_cash_amount=None, actual_payments=None, remark=""
):
    warehouse_id = _warehouse_id_or_error(user)
    shift = (
        PosShift.objects.select_for_update()
        .filter(pk=shift_id, warehouse_id=warehouse_id)
        .first()
    )
    if not shift:
        raise ValidationError("POS 班次不存在或无权操作。")
    if shift.status not in [PosShift.Status.OPEN, PosShift.Status.REOPENED]:
        raise ValidationError("只有进行中的 POS 班次可以交班。")
    if shift.cashier_id != getattr(user, "id", None):
        raise ValidationError("只能交接当前收银员自己的班次。")

    summary = build_shift_summary(shift)
    actual_map = _actual_payment_amounts(actual_payments)
    expected_cash_total = _money(summary["expected_cash_amount"])
    actual_cash_total = (
        _money(actual_cash_amount)
        if actual_cash_amount not in (None, "")
        else expected_cash_total
    )
    actual_cash_sales = _money(actual_cash_total - shift.opening_cash_amount)

    PosShiftPaymentSummary.objects.filter(shift=shift).delete()
    for row in summary["payments"]:
        method = row["method"]
        expected = _money(row["expected_amount"])
        if method == PosPayment.Method.CASH:
            actual = actual_map.get(method, actual_cash_sales)
        else:
            actual = actual_map.get(method, expected)
        PosShiftPaymentSummary.objects.create(
            shift=shift,
            method=method,
            sale_count=row["sale_count"],
            refund_count=row.get("refund_count", 0),
            expected_amount=expected,
            refund_amount=_money(row.get("refund_amount", ZERO_MONEY)),
            actual_amount=actual,
            difference=_money(actual - expected),
        )

    shift.status = PosShift.Status.CLOSED
    shift.closed_at = timezone.now()
    shift.closed_by = user if user and user.is_authenticated else None
    shift.expected_cash_amount = expected_cash_total
    shift.actual_cash_amount = actual_cash_total
    shift.cash_difference = _money(actual_cash_total - expected_cash_total)
    shift.total_sales_amount = _money(summary["net_amount"])
    shift.total_voided_amount = _money(summary["voided_amount"])
    shift.total_return_amount = _money(summary["return_amount"])
    shift.sale_count = summary["sale_count"]
    shift.completed_count = summary["completed_count"]
    shift.voided_count = summary["voided_count"]
    shift.return_count = summary["return_count"]
    shift.remark = (remark or "").strip()
    shift.save(
        update_fields=[
            "status",
            "closed_at",
            "closed_by",
            "expected_cash_amount",
            "actual_cash_amount",
            "cash_difference",
            "total_sales_amount",
            "total_voided_amount",
            "total_return_amount",
            "sale_count",
            "completed_count",
            "voided_count",
            "return_count",
            "remark",
            "updated_at",
        ]
    )
    PosAuditLog.objects.create(
        action=PosAuditLog.Action.SHIFT_CLOSE,
        shift=shift,
        actor=user if user and user.is_authenticated else None,
        reason=(remark or "").strip(),
        metadata={
            "net_amount": summary["net_amount"],
            "return_amount": summary["return_amount"],
            "expected_cash_amount": summary["expected_cash_amount"],
            "actual_cash_amount": _money_text(actual_cash_total),
        },
    )
    return shift


@transaction.atomic
def reopen_pos_shift(*, shift_id, user, reason=""):
    warehouse_id = _warehouse_id_or_error(user)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "重开 POS 班次必须填写原因。"})

    shift = (
        PosShift.objects.select_for_update()
        .filter(pk=shift_id, warehouse_id=warehouse_id)
        .first()
    )
    if not shift:
        raise ValidationError("POS 班次不存在或无权操作。")
    if shift.status != PosShift.Status.CLOSED:
        raise ValidationError("只有已交班的 POS 班次可以重开。")

    existing = (
        PosShift.objects.select_for_update()
        .filter(
            warehouse_id=warehouse_id,
            cashier_id=shift.cashier_id,
            status__in=[PosShift.Status.OPEN, PosShift.Status.REOPENED],
        )
        .exclude(pk=shift.pk)
        .first()
    )
    if existing:
        raise ValidationError("该收银员已有进行中的 POS 班次，请先交班后再重开。")

    now = timezone.now()
    shift.status = PosShift.Status.REOPENED
    shift.reopened_at = now
    shift.reopened_by = user if user and user.is_authenticated else None
    shift.reopen_reason = reason
    shift.reopen_count = (shift.reopen_count or 0) + 1
    shift.save(
        update_fields=[
            "status",
            "reopened_at",
            "reopened_by",
            "reopen_reason",
            "reopen_count",
            "updated_at",
        ]
    )
    PosAuditLog.objects.create(
        action=PosAuditLog.Action.SHIFT_REOPEN,
        shift=shift,
        actor=user if user and user.is_authenticated else None,
        reason=reason,
        metadata={"reopen_count": shift.reopen_count},
    )
    return shift


def _stored_payment_summaries(shift):
    rows = []
    labels = dict(PosPayment.Method.choices)
    for row in shift.payment_summaries.order_by("method"):
        rows.append(
            {
                "method": row.method,
                "method_label": labels.get(row.method, row.method),
                "sale_count": row.sale_count,
                "refund_count": row.refund_count,
                "refund_amount": _money_text(row.refund_amount),
                "expected_amount": _money_text(row.expected_amount),
                "actual_amount": _money_text(row.actual_amount),
                "difference": _money_text(row.difference),
            }
        )
    return rows


def serialize_shift(shift, *, include_dynamic_summary=True):
    computed = build_shift_summary(shift) if include_dynamic_summary else None
    if shift.status == PosShift.Status.CLOSED:
        payments = _stored_payment_summaries(shift)
        summary = {
            "sale_count": shift.sale_count,
            "completed_count": shift.completed_count,
            "voided_count": shift.voided_count,
            "return_count": shift.return_count,
            "gross_sales_amount": _money_text(
                shift.total_sales_amount + shift.total_return_amount
            ),
            "net_amount": _money_text(shift.total_sales_amount),
            "voided_amount": _money_text(shift.total_voided_amount),
            "return_amount": _money_text(shift.total_return_amount),
            "opening_cash_amount": _money_text(shift.opening_cash_amount),
            "expected_cash_amount": _money_text(shift.expected_cash_amount),
            "actual_cash_amount": _money_text(shift.actual_cash_amount),
            "cash_difference": _money_text(shift.cash_difference),
            "payments": payments,
        }
        if computed:
            summary.update(
                {
                    "line_count": computed["line_count"],
                    "completed_line_count": computed["completed_line_count"],
                    "completed_qty": computed["completed_qty"],
                    "completed_line_amount": computed["completed_line_amount"],
                    "voided_qty": computed["voided_qty"],
                    "voided_line_amount": computed["voided_line_amount"],
                    "cash_sales_amount": computed["cash_sales_amount"],
                }
            )
    else:
        summary = computed or build_shift_summary(shift)
        summary["actual_cash_amount"] = _money_text(shift.actual_cash_amount)
        summary["cash_difference"] = _money_text(shift.cash_difference)

    return {
        "id": shift.id,
        "shift_no": shift.shift_no,
        "warehouse_id": shift.warehouse_id,
        "cashier_id": shift.cashier_id,
        "cashier_username": getattr(shift.cashier, "username", ""),
        "status": shift.status,
        "opened_at": shift.opened_at.isoformat() if shift.opened_at else "",
        "closed_at": shift.closed_at.isoformat() if shift.closed_at else "",
        "reopened_at": shift.reopened_at.isoformat() if shift.reopened_at else "",
        "reopened_by_id": shift.reopened_by_id,
        "reopen_reason": shift.reopen_reason or "",
        "reopen_count": shift.reopen_count or 0,
        "opening_cash_amount": _money_text(shift.opening_cash_amount),
        "remark": shift.remark or "",
        "summary": summary,
    }


def record_print_log(
    *,
    user,
    print_type,
    payload,
    sale=None,
    shift=None,
    source=PosPrintLog.Source.BACKEND_HTML,
    remark="",
):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    existing = PosPrintLog.objects.filter(print_type=print_type)
    if sale:
        existing = existing.filter(sale=sale)
    else:
        existing = existing.filter(sale__isnull=True)
    if shift:
        existing = existing.filter(shift=shift)
    else:
        existing = existing.filter(shift__isnull=True)
    copy_no = existing.count() + 1
    log = PosPrintLog.objects.create(
        sale=sale,
        shift=shift,
        print_type=print_type,
        source=source,
        printed_by=user if user and user.is_authenticated else None,
        copy_no=copy_no,
        payload_hash=payload_hash,
        remark=(remark or "").strip(),
    )
    PosAuditLog.objects.create(
        action=PosAuditLog.Action.PRINT,
        sale=sale,
        shift=shift,
        actor=user if user and user.is_authenticated else None,
        reason=(remark or "").strip(),
        metadata={"print_type": print_type, "source": source, "copy_no": copy_no},
    )
    return log
