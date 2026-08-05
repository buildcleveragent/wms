from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, QuerySet, Sum

from allapp.billing.models import Bill, BillingAccrual
from allapp.billing.serializers import BillListSerializer, BillingAccrualSerializer
from allapp.billing.enums import BillStatus
from allapp.reports.boss_contract import money_groups, normalize_currency

ZERO_MONEY = Decimal("0.00")
ZERO_QTY = Decimal("0.0000")


def _decimal_or_zero(value, default=ZERO_MONEY):
    return default if value is None else value


def build_warehouse_overview_payload(
    *,
    accrual_qs: QuerySet[BillingAccrual],
    ledger_accrual_qs: QuerySet[BillingAccrual],
    bill_qs: QuerySet[Bill],
    recent_limit: int = 10,
):
    operational_summary = accrual_qs.aggregate(
        accrual_count=Count("id"),
        owner_count=Count("owner", distinct=True),
    )
    issued_bill_qs = bill_qs.filter(status__in=[BillStatus.ISSUED, BillStatus.PAID])
    draft_bill_qs = bill_qs.filter(status=BillStatus.DRAFT)

    owner_counts = {
        (row["owner_id"], row["currency"]): row["accrual_count"]
        for row in accrual_qs.values("owner_id", "currency").annotate(
            accrual_count=Count("id")
        )
    }
    by_owner = []
    for row in (
        ledger_accrual_qs.values("owner_id", "owner__name", "currency")
        .annotate(
            subtotal=Sum("amount"),
            tax_total=Sum("tax_amount"),
        )
        .order_by("currency", "-subtotal", "owner_id")
    ):
        subtotal = _decimal_or_zero(row["subtotal"])
        tax_total = _decimal_or_zero(row["tax_total"])
        by_owner.append(
            {
                "owner": row["owner_id"],
                "owner_name": row["owner__name"] or f"Owner #{row['owner_id']}",
                "currency": normalize_currency(row["currency"]),
                "accrual_count": owner_counts.get(
                    (row["owner_id"], row["currency"]), 0
                ),
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    charge_type_counts = {
        (row["charge_type"], row["currency"]): row["accrual_count"]
        for row in accrual_qs.values("charge_type", "currency").annotate(
            accrual_count=Count("id")
        )
    }
    by_charge_type = []
    for row in (
        ledger_accrual_qs.values("charge_type", "currency")
        .annotate(
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
                "currency": normalize_currency(row["currency"]),
                "accrual_count": charge_type_counts.get(
                    (row["charge_type"], row["currency"]), 0
                ),
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    status_counts = {
        (row["status"], row["currency"]): row["accrual_count"]
        for row in accrual_qs.values("status", "currency").annotate(
            accrual_count=Count("id")
        )
    }
    by_status = []
    for row in (
        ledger_accrual_qs.values("status", "currency")
        .annotate(
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
                "currency": normalize_currency(row["currency"]),
                "accrual_count": status_counts.get((row["status"], row["currency"]), 0),
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    service_date_counts = {
        (row["service_date"], row["currency"]): row["accrual_count"]
        for row in accrual_qs.values("service_date", "currency").annotate(
            accrual_count=Count("id")
        )
    }
    by_service_date = []
    for row in (
        ledger_accrual_qs.values("service_date", "currency")
        .annotate(
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
                "currency": normalize_currency(row["currency"]),
                "accrual_count": service_date_counts.get(
                    (row["service_date"], row["currency"]), 0
                ),
                "subtotal": subtotal,
                "tax_total": tax_total,
                "total": subtotal + tax_total,
            }
        )

    return {
        "summary": {
            "owner_count": operational_summary["owner_count"] or 0,
            "accrual_count": operational_summary["accrual_count"] or 0,
            "accruals_by_currency": money_groups(
                ledger_accrual_qs,
                subtotal_field="amount",
                tax_field="tax_amount",
            ),
            "issued_bill_count": issued_bill_qs.count(),
            "issued_bills_by_currency": money_groups(
                issued_bill_qs,
                subtotal_field="subtotal",
                tax_field="tax_total",
                total_field="total",
            ),
            "draft_bill_count": draft_bill_qs.count(),
            "draft_bills_by_currency": money_groups(
                draft_bill_qs,
                subtotal_field="subtotal",
                tax_field="tax_total",
                total_field="total",
            ),
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
