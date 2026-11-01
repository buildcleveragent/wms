"""Read-only balances at a reporting date, shared by boss dashboard surfaces."""

from decimal import Decimal

from django.db.models import Case, DecimalField, F, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest

from allapp.billing.enums import BillDocumentStatus
from allapp.billing.models import PaymentAllocation


def receivable_bills_as_of(queryset, date_to):
    """Annotate scoped issued bills with signed receipts effective by date_to."""
    money = DecimalField(max_digits=24, decimal_places=4)
    zero = Value(Decimal("0"), output_field=money)
    allocations = (
        PaymentAllocation.objects.filter(
            bill_id=OuterRef("pk"), receipt__status__in=["POSTED", "REVERSED"]
        )
        .filter(
            Q(receipt__date_quality="VERIFIED", receipt__receipt_date__lte=date_to)
            | Q(receipt__date_quality="UNKNOWN", receipt__created_at__date__lte=date_to)
        )
        .order_by()
        .values("bill_id")
        .annotate(
            net=Sum(
                Case(
                    When(is_reversal=True, then=-F("amount")),
                    default=F("amount"),
                    output_field=money,
                )
            )
        )
        .values("net")
    )
    return (
        queryset.filter(issue_date__lte=date_to, document_status=BillDocumentStatus.ISSUED)
        .annotate(
            report_paid_amount=Greatest(
                zero, Coalesce(Subquery(allocations, output_field=money), zero), output_field=money
            )
        )
        .annotate(
            report_outstanding_amount=Greatest(
                zero, F("total") - F("report_paid_amount"), output_field=money
            )
        )
    )


def outstanding_money_groups(queryset):
    """Group balances by original currency; tax/subtotal retain the bill ratio."""
    buckets = {}
    for bill in queryset:
        currency = str(bill.currency or "").strip().upper() or "UNKNOWN"
        bucket = buckets.setdefault(
            currency,
            dict(
                currency=currency,
                record_count=0,
                subtotal=Decimal(0),
                tax_total=Decimal(0),
                total=Decimal(0),
            ),
        )
        outstanding = bill.report_outstanding_amount
        subtotal = bill.subtotal * outstanding / bill.total if bill.total else Decimal(0)
        bucket["record_count"] += 1
        bucket["total"] += outstanding
        bucket["subtotal"] += subtotal
        bucket["tax_total"] += outstanding - subtotal
    return [buckets[key] for key in sorted(buckets)]
