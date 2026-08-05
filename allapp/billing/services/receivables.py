"""Immutable receipt posting, allocation and reversal services."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Case, DecimalField, F, Sum, When
from django.utils import timezone

from allapp.billing.enums import (
    BillDocumentStatus,
    BillPaymentStatus,
    BillStatus,
    PaymentReceiptStatus,
)
from allapp.billing.models import Bill, PaymentAllocation, PaymentReceipt, qmoney


def _effective_paid_amount(bill_id, *, cutoff=None):
    qs = PaymentAllocation.objects.filter(
        bill_id=bill_id,
        receipt__status__in=[
            PaymentReceiptStatus.POSTED,
            PaymentReceiptStatus.REVERSED,
        ],
    )
    if cutoff is not None:
        qs = qs.filter(receipt__receipt_date__lte=cutoff)
    value = qs.aggregate(
        value=Sum(
            Case(
                When(is_reversal=True, then=-F("amount")),
                default=F("amount"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        )
    )["value"]
    return qmoney(value or Decimal("0.00"))


def _sync_bill_payment_status(bill):
    paid = _effective_paid_amount(bill.pk)
    if paid <= 0:
        payment_status = BillPaymentStatus.UNPAID
        legacy_status = BillStatus.ISSUED
    elif paid < bill.total:
        payment_status = BillPaymentStatus.PARTIAL
        legacy_status = BillStatus.ISSUED
    else:
        payment_status = BillPaymentStatus.PAID
        legacy_status = BillStatus.PAID
    Bill.objects.filter(pk=bill.pk).update(
        payment_status=payment_status,
        status=legacy_status,
    )
    bill.payment_status = payment_status
    bill.status = legacy_status


@transaction.atomic
def post_receipt(receipt_id, *, by_user=None):
    receipt = PaymentReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.status != PaymentReceiptStatus.DRAFT:
        raise ValueError("Only DRAFT receipts can be posted.")
    allocations = list(
        PaymentAllocation.objects.select_for_update()
        .select_related("bill")
        .filter(receipt=receipt, is_reversal=False)
        .order_by("bill_id", "id")
    )
    if not allocations:
        raise ValueError("At least one allocation is required before posting.")
    allocated_total = qmoney(sum((row.amount for row in allocations), Decimal("0.00")))
    if allocated_total > receipt.amount:
        raise ValueError("Allocated amount exceeds receipt amount.")

    bills = {
        bill.pk: bill
        for bill in Bill.objects.select_for_update().filter(
            pk__in={row.bill_id for row in allocations}
        )
    }
    for allocation in allocations:
        bill = bills[allocation.bill_id]
        if (
            bill.document_status != BillDocumentStatus.ISSUED
            or bill.status == BillStatus.VOID
        ):
            raise ValueError("Only issued, non-void bills can be allocated.")
        if (
            bill.owner_id != receipt.owner_id
            or bill.warehouse_id != receipt.warehouse_id
            or bill.currency != receipt.currency
        ):
            raise ValueError("Receipt and bill scope/currency must match.")
        already_paid = _effective_paid_amount(bill.pk)
        if already_paid + allocation.amount > bill.total:
            raise ValueError(
                f"Allocation exceeds outstanding amount for bill {bill.pk}."
            )

    receipt.status = PaymentReceiptStatus.POSTED
    receipt.posted_at = timezone.now()
    receipt.posted_by = by_user
    receipt.save(update_fields=["status", "posted_at", "posted_by"])
    for bill in bills.values():
        _sync_bill_payment_status(bill)
    return receipt


@transaction.atomic
def reverse_receipt(
    receipt_id, *, receipt_no, reversal_date=None, by_user=None, memo=""
):
    original = PaymentReceipt.objects.select_for_update().get(pk=receipt_id)
    if original.status != PaymentReceiptStatus.POSTED:
        raise ValueError("Only POSTED receipts can be reversed.")
    original_allocations = list(
        PaymentAllocation.objects.select_for_update()
        .filter(receipt=original, is_reversal=False)
        .order_by("id")
    )
    bills = list(
        Bill.objects.select_for_update().filter(
            pk__in={row.bill_id for row in original_allocations}
        )
    )
    current = timezone.now()
    today = (
        timezone.localtime(current).date()
        if timezone.is_aware(current)
        else current.date()
    )
    reversal = PaymentReceipt.objects.create(
        owner_id=original.owner_id,
        warehouse_id=original.warehouse_id,
        currency=original.currency,
        receipt_no=receipt_no,
        receipt_date=reversal_date or today,
        amount=original.amount,
        channel="REVERSAL",
        bank_reference=original.bank_reference,
        memo=memo or f"Reverse {original.receipt_no}",
        status=PaymentReceiptStatus.POSTED,
        posted_at=timezone.now(),
        posted_by=by_user,
        reversal_of=original,
        created_by=by_user,
    )
    PaymentAllocation.objects.bulk_create(
        [
            PaymentAllocation(
                receipt=reversal,
                bill_id=row.bill_id,
                amount=row.amount,
                allocated_at=timezone.now(),
                is_reversal=True,
                reversal_of=row,
                created_by=by_user,
            )
            for row in original_allocations
        ]
    )
    original.status = PaymentReceiptStatus.REVERSED
    original.reversed_at = timezone.now()
    original.reversed_by = by_user
    original.save(update_fields=["status", "reversed_at", "reversed_by"])
    for bill in bills:
        _sync_bill_payment_status(bill)
    return reversal


def receipt_unapplied_amount(receipt):
    allocated = receipt.allocations.filter(is_reversal=False).aggregate(
        value=Sum("amount")
    )["value"] or Decimal("0.00")
    return qmoney(max(Decimal("0.00"), receipt.amount - allocated))
