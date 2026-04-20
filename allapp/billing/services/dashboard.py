from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, QuerySet, Sum

from allapp.billing.models import Bill, BillingAccrual
from allapp.billing.serializers import BillListSerializer, BillingAccrualSerializer

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.0000")


def _decimal_or_zero(value, default=ZERO_MONEY):
    return default if value is None else value


def build_warehouse_overview_payload(
    *,
    accrual_qs: QuerySet[BillingAccrual],
    bill_qs: QuerySet[Bill],
    recent_limit: int = 10,
):
    accrual_summary = accrual_qs.aggregate(
        accrual_count=Count("id"),
        owner_count=Count("owner", distinct=True),
        quantity_total=Sum("quantity"),
        subtotal=Sum("amount"),
        tax_total=Sum("tax_amount"),
    )
    bill_summary = bill_qs.aggregate(
        bill_count=Count("id"),
        subtotal=Sum("subtotal"),
        tax_total=Sum("tax_total"),
        total=Sum("total"),
    )

    by_owner = []
    for row in (
        accrual_qs.values("owner_id", "owner__name")
        .annotate(
            accrual_count=Count("id"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("-subtotal", "owner_id")
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        by_owner.append(
            {
                "owner": row["owner_id"],
                "owner_name": row["owner__name"] or f"Owner #{row['owner_id']}",
                "accrual_count": row["accrual_count"],
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    by_charge_type = []
    for row in (
        accrual_qs.values("charge_type")
        .annotate(
            accrual_count=Count("id"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("charge_type")
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        by_charge_type.append(
            {
                "charge_type": row["charge_type"],
                "accrual_count": row["accrual_count"],
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    by_status = []
    for row in (
        accrual_qs.values("status")
        .annotate(
            accrual_count=Count("id"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("status")
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        by_status.append(
            {
                "status": row["status"],
                "accrual_count": row["accrual_count"],
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    by_service_date = []
    for row in (
        accrual_qs.values("service_date")
        .annotate(
            accrual_count=Count("id"),
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("service_date")
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        by_service_date.append(
            {
                "service_date": row["service_date"],
                "accrual_count": row["accrual_count"],
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    subtotal = _decimal_or_zero(accrual_summary["subtotal"])
    tax_total = _decimal_or_zero(accrual_summary["tax_total"])
    return {
        "summary": {
            "owner_count": accrual_summary["owner_count"] or 0,
            "accrual_count": accrual_summary["accrual_count"] or 0,
            "quantity_total": _decimal_or_zero(accrual_summary["quantity_total"], ZERO_QTY),
            "subtotal": subtotal,
            "tax_total": tax_total,
            "total": subtotal + tax_total,
            "bill_count": bill_summary["bill_count"] or 0,
            "billed_subtotal": _decimal_or_zero(bill_summary["subtotal"]),
            "billed_tax_total": _decimal_or_zero(bill_summary["tax_total"]),
            "billed_total": _decimal_or_zero(bill_summary["total"]),
        },
        "by_owner": by_owner,
        "by_charge_type": by_charge_type,
        "by_status": by_status,
        "by_service_date": by_service_date,
        "recent_accruals": BillingAccrualSerializer(
            accrual_qs.order_by("-service_date", "-id")[:recent_limit],
            many=True,
        ).data,
        "recent_bills": BillListSerializer(
            bill_qs.order_by("-issue_date", "-id")[:recent_limit],
            many=True,
        ).data,
    }
