from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TransactionTestCase

from . import test_checkout_concurrency as fixtures
from .models import PosCustomer, PosCustomerRepayment, PosSale
from .services import (
    create_customer_repayment,
    create_pos_sale,
    customer_pos_debt_balance,
    void_pos_sale,
)


class RepaymentConcurrencyTests(TransactionTestCase):
    setUp = fixtures.PosCheckoutConcurrencyTests.setUp
    _create_product = fixtures.PosCheckoutConcurrencyTests._create_product
    _run_actions = fixtures.PosCheckoutConcurrencyTests._run_actions
    concurrent_action_timeout_seconds = 120

    def _credit(self):
        self.customer = PosCustomer.objects.create(
            warehouse=self.warehouse, code="REPAY-CUSTOMER", name="Repayment customer"
        )
        return create_pos_sale(
            user=self.users[0],
            customer_id=self.customer.pk,
            src_bill_no="CREDIT-CONCURRENT",
            items=[{"product_id": self.products[0].pk, "qty": "1.000", "price": "10.0000"}],
            payment={"method": "CREDIT", "amount_received": "0.00"},
        )["sale"]

    def test_two_cashiers_cannot_collect_more_than_outstanding_debt(self):
        self._credit()
        results, errors = self._run_actions(
            [
                lambda index=index: create_customer_repayment(
                    user=self.users[index],
                    customer_id=self.customer.pk,
                    amount="7.00",
                    method="CASH",
                    idempotency_key=f"cashier-request-{index}",
                )
                for index in range(2)
            ]
        )
        self.assertEqual(sum(result is not None for result in results), 1, errors)
        self.assertEqual(sum(isinstance(error, ValidationError) for error in errors), 1, errors)
        self.assertEqual(PosCustomerRepayment.objects.count(), 1)
        self.assertEqual(
            customer_pos_debt_balance(
                customer_id=self.customer.pk,
                warehouse_id=self.warehouse.pk,
            ),
            Decimal("3.00"),
        )

    def test_repayment_and_void_have_one_consistent_settlement_order(self):
        sale = self._credit()
        results, errors = self._run_actions(
            [
                lambda: create_customer_repayment(
                    user=self.users[0],
                    customer_id=self.customer.pk,
                    amount="4.00",
                    method="CASH",
                    idempotency_key="repay-versus-void",
                ),
                lambda: void_pos_sale(sale_id=sale.pk, user=self.users[1], reason="acceptance"),
            ]
        )
        self.assertEqual(sum(result is not None for result in results), 1, errors)
        self.assertEqual(sum(isinstance(error, ValidationError) for error in errors), 1, errors)
        sale.refresh_from_db()
        debt = customer_pos_debt_balance(
            customer_id=self.customer.pk, warehouse_id=self.warehouse.pk
        )
        if sale.status == PosSale.Status.VOIDED:
            self.assertEqual(debt, Decimal("0.00"))
            self.assertEqual(PosCustomerRepayment.objects.count(), 0)
        else:
            self.assertEqual(debt, Decimal("6.00"))
            self.assertEqual(PosCustomerRepayment.objects.count(), 1)
