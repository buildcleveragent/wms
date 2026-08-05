import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from allapp.baseinfo.models import Owner
from allapp.billing.enums import (
    BillDocumentStatus,
    BillPaymentStatus,
    BillStatus,
    PaymentReceiptStatus,
    PeriodStatus,
)
from allapp.billing.models import (
    Bill,
    BillingPeriod,
    PaymentAllocation,
    PaymentReceipt,
)
from allapp.billing.services.receivables import post_receipt, reverse_receipt
from allapp.locations.models import Warehouse


class PaymentReceiptServiceTests(TestCase):
    def setUp(self):
        self.owner = Owner.objects.create(code="P1-OWNER", name="P1 Owner")
        self.warehouse = Warehouse.objects.create(code="P1-WH", name="P1 Warehouse")
        self.user = get_user_model().objects.create_user(username="p1-finance")
        self.period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-07",
            start_date=datetime.date(2026, 7, 1),
            end_date=datetime.date(2026, 7, 31),
            status=PeriodStatus.INVOICED,
            currency="CNY",
        )

    def bill(self, invoice_no, total):
        return Bill.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            period=self.period,
            invoice_no=invoice_no,
            issue_date=datetime.date(2026, 8, 1),
            due_date=datetime.date(2026, 8, 31),
            currency="CNY",
            subtotal=total,
            tax_total=Decimal("0.00"),
            total=total,
            status=BillStatus.ISSUED,
            document_status=BillDocumentStatus.ISSUED,
        )

    def test_partial_and_multi_bill_allocation_then_reversal(self):
        first = self.bill("P1-INV-1", Decimal("100.00"))
        # The one-active-bill-per-period invariant is correct in production;
        # use a second period to exercise one receipt allocating many bills.
        second_period = BillingPeriod.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            label="2026-08",
            start_date=datetime.date(2026, 8, 1),
            end_date=datetime.date(2026, 8, 31),
            status=PeriodStatus.INVOICED,
            currency="CNY",
        )
        self.period = second_period
        second = self.bill("P1-INV-2", Decimal("80.00"))
        receipt = PaymentReceipt.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            currency="CNY",
            receipt_no="P1-RCP-1",
            receipt_date=datetime.date(2026, 8, 10),
            amount=Decimal("120.00"),
            created_by=self.user,
        )
        PaymentAllocation.objects.create(
            receipt=receipt, bill=first, amount=Decimal("70.00")
        )
        PaymentAllocation.objects.create(
            receipt=receipt, bill=second, amount=Decimal("50.00")
        )

        post_receipt(receipt.pk, by_user=self.user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.payment_status, BillPaymentStatus.PARTIAL)
        self.assertEqual(second.payment_status, BillPaymentStatus.PARTIAL)
        self.assertEqual(first.outstanding_amount, Decimal("30.00"))
        self.assertEqual(second.outstanding_amount, Decimal("30.00"))

        reversal = reverse_receipt(
            receipt.pk,
            receipt_no="P1-RCP-1-R",
            reversal_date=datetime.date(2026, 8, 11),
            by_user=self.user,
        )
        receipt.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(receipt.status, PaymentReceiptStatus.REVERSED)
        self.assertEqual(reversal.status, PaymentReceiptStatus.POSTED)
        self.assertEqual(first.payment_status, BillPaymentStatus.UNPAID)
        self.assertEqual(second.payment_status, BillPaymentStatus.UNPAID)
        self.assertEqual(first.outstanding_amount, Decimal("100.00"))

    def test_post_rejects_over_allocation(self):
        bill = self.bill("P1-INV-OVER", Decimal("100.00"))
        receipt = PaymentReceipt.objects.create(
            owner=self.owner,
            warehouse=self.warehouse,
            currency="CNY",
            receipt_no="P1-RCP-OVER",
            receipt_date=datetime.date(2026, 8, 10),
            amount=Decimal("90.00"),
        )
        PaymentAllocation.objects.create(
            receipt=receipt, bill=bill, amount=Decimal("100.00")
        )
        with self.assertRaisesMessage(ValueError, "exceeds receipt amount"):
            post_receipt(receipt.pk, by_user=self.user)
