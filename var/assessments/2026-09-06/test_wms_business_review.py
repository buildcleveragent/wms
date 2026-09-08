"""Temporary review reproducers; run only through repository's guarded MySQL pytest setup."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from allapp.inventory.models import InventoryDetail, InventorySummary
from allapp.inventory.services import _refresh_summaries
from allapp.outbound import services as outbound_services
from allapp.outbound import tests as outbound_fixtures
from allapp.outbound.models import OutboundOrder
from allapp.pos import tests as pos_fixtures
from allapp.pos.models import PosCustomerRepayment, PosRefund, PosSale


class ReviewInventorySummaryTests(TestCase):
    setUp = outbound_fixtures.OutboundWarehouseScopeTests.setUp

    def _stock_and_order(self):
        detail = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            onhand_qty=Decimal("10"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
            base_unit=self.base_uom.code,
        )
        summary = InventorySummary.objects.create(
            owner=self.owner,
            product=self.product,
            onhand_qty=Decimal("10"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
            base_unit=self.base_uom.code,
        )
        order = OutboundOrder.objects.create(
            owner=self.owner,
            customer=self.customer,
            warehouse=self.warehouse,
            created_by=self.user,
            submit_status="SUBMITTED",
        )
        order.lines.create(
            product=self.product,
            base_qty=Decimal("3"),
            base_price=Decimal("10"),
        )
        return detail, summary, order

    def test_allocation_keeps_owner_summary_consistent(self):
        detail, summary, order = self._stock_and_order()
        order.owner_approve(by_user=self.user, allow_backorder=False)
        detail.refresh_from_db()
        summary.refresh_from_db()
        self.assertEqual(detail.available_qty, Decimal("7"))
        self.assertEqual(
            summary.available_qty,
            detail.available_qty,
            f"owner summary={summary.available_qty}, detail={detail.available_qty}",
        )

    def test_release_keeps_owner_summary_consistent(self):
        detail, summary, order = self._stock_and_order()
        order.owner_approve(by_user=self.user, allow_backorder=False)
        # A separate inventory posting may legitimately refresh the summary
        # while the order remains reserved; establish that valid state here.
        _refresh_summaries([(self.owner.id, self.product.id)])
        summary.refresh_from_db()
        self.assertEqual(summary.available_qty, Decimal("7"))
        outbound_services.cancel_order(order, by_user=self.user, reason="review")
        detail.refresh_from_db()
        summary.refresh_from_db()
        self.assertEqual(detail.available_qty, Decimal("10"))
        self.assertEqual(
            summary.available_qty,
            detail.available_qty,
            f"owner summary={summary.available_qty}, detail={detail.available_qty}",
        )

    def test_standard_outbound_fefo_prioritizes_dated_stock(self):
        undated, _summary, order = self._stock_and_order()
        expiring = InventoryDetail.objects.create(
            owner=self.owner,
            product=self.product,
            warehouse=self.warehouse,
            location=self.location,
            expiry_date=date(2030, 1, 1),
            onhand_qty=Decimal("10"),
            allocated_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            damaged_qty=Decimal("0"),
            base_unit=self.base_uom.code,
        )
        order.owner_approve(by_user=self.user, allow_backorder=False)
        undated.refresh_from_db()
        expiring.refresh_from_db()
        self.assertEqual(
            expiring.allocated_qty,
            Decimal("3"),
            f"dated allocated={expiring.allocated_qty}, undated allocated={undated.allocated_qty}",
        )


class ReviewPosFinancialTests(TestCase):
    setUp = pos_fixtures.PosApiTests.setUp
    open_shift = pos_fixtures.PosApiTests.open_shift

    def _credit_sale(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "customer_id": self.pos_customer.id,
                "src_bill_no": "REVIEW-CREDIT-SALE",
                "payment": {"method": "CREDIT", "amount_received": "0.00"},
                "items": [{"product_id": self.product.id, "qty": "1.000", "price": "10.0000"}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["sale"]["id"]

    def _repay(self):
        return self.client.post(
            "/api/pos/repayments/",
            {
                "customer_id": self.pos_customer.id,
                "method": "CASH",
                "amount": "4.00",
                "reference_no": "REVIEW-ONE-CASH-RECEIPT",
                "remark": "review replay",
            },
            format="json",
        )

    def test_void_after_repayment_preserves_settlement(self):
        sale_id = self._credit_sale()
        repayment = self._repay()
        self.assertEqual(repayment.status_code, 201, repayment.data)
        response = self.client.post(
            f"/api/pos/sales/{sale_id}/void/",
            {"reason": "review"},
            format="json",
        )
        debt = self.client.get(f"/api/pos/customer-debts/{self.pos_customer.id}/")
        self.assertEqual(debt.status_code, 200, debt.data)
        balance = Decimal(debt.data["debt_balance"])
        self.assertGreaterEqual(
            balance,
            Decimal("0"),
            f"void_http={response.status_code}, debt={balance}, "
            f"repayments={PosCustomerRepayment.objects.count()}, "
            f"refunds={PosRefund.objects.count()}",
        )

    def test_repeated_repayment_request_is_not_double_recorded(self):
        self._credit_sale()
        first = self._repay()
        self.assertEqual(first.status_code, 201, first.data)
        second = self._repay()
        debt = self.client.get(f"/api/pos/customer-debts/{self.pos_customer.id}/")
        self.assertEqual(
            PosCustomerRepayment.objects.count(),
            1,
            f"replay_http={second.status_code}, debt={debt.data.get('debt_balance')}",
        )

    def test_zero_value_gift_can_be_returned_without_refund(self):
        response = self.client.post(
            "/api/pos/checkout/",
            {
                "src_bill_no": "REVIEW-ZERO-GIFT",
                "payment": {"method": "WECHAT", "amount_received": "0.00"},
                "items": [
                    {
                        "product_id": self.product.id,
                        "qty": "1.000",
                        "price": "0.0000",
                        "amount": "0.00",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        sale = PosSale.objects.get(pk=response.data["sale"]["id"])
        returned = self.client.post(
            "/api/pos/returns/",
            {
                "sale_id": sale.id,
                "reason": "review gift return",
                "lines": [{"sale_line_id": sale.lines.get().id, "qty": "1.000"}],
                "refunds": [],
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 201, returned.data)
