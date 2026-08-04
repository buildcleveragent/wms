"""Canonical financial-ledger semantics for billing accruals.

Billing currently also uses ``is_reversal`` for some positive, superseded
accruals.  Financial reporting must therefore identify a real additive
reversal by both its lifecycle fields and its signed monetary values.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from allapp.billing.enums import AccrualStatus
from allapp.billing.models import BillingAccrual


def financial_ledger_q() -> Q:
    """Return the canonical ORM predicate for reportable accrual entries."""

    normal_entry = ~Q(status=AccrualStatus.VOID) & Q(is_reversal=False)
    reversal_entry = Q(
        status=AccrualStatus.VOID,
        is_reversal=True,
        reversal_of__isnull=False,
        amount__lt=0,
        unit_price__lte=0,
        tax_amount__lte=0,
    )
    return normal_entry | reversal_entry


def financial_ledger_accruals(
    queryset: QuerySet[BillingAccrual] | None = None,
) -> QuerySet[BillingAccrual]:
    """Select accruals that contribute signed amounts to financial reports."""

    source = BillingAccrual.objects.all() if queryset is None else queryset
    return source.filter(financial_ledger_q())


def is_financial_ledger_accrual(accrual: BillingAccrual) -> bool:
    """Return whether one in-memory accrual is a reportable ledger entry."""

    if accrual.status != AccrualStatus.VOID:
        return not accrual.is_reversal

    return bool(
        accrual.is_reversal
        and accrual.reversal_of_id is not None
        and accrual.amount < 0
        and accrual.unit_price <= 0
        and accrual.tax_amount <= 0
    )
