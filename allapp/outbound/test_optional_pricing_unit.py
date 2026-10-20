from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from allapp.outbound.amounts import pricing_summary
from allapp.outbound.pricing import has_reference_price, pricing_applies, sale_minimum
from allapp.products.pricing import InvalidSalePriceRule


class OptionalPricingPolicyTests(SimpleTestCase):
    def test_missing_reference_disables_guards_only_for_unpriced_goods(self):
        for price in (None, 0, Decimal("0.00")):
            product = SimpleNamespace(price=price, min_price=10, max_discount=101)
            self.assertFalse(has_reference_price(product))
            self.assertIsNone(sale_minimum(product))
        with self.assertRaises(InvalidSalePriceRule):
            sale_minimum(SimpleNamespace(price=20, min_price=10, max_discount=101))
        for price in (-1, Decimal("NaN"), Decimal("Infinity")):
            with self.assertRaises(InvalidSalePriceRule):
                has_reference_price(SimpleNamespace(price=price))

    def test_saved_unpriced_and_positive_historical_prices_are_stable(self):
        self.assertFalse(
            pricing_applies(
                SimpleNamespace(price=20), SimpleNamespace(is_priced=False, base_price=0)
            )
        )
        self.assertTrue(
            pricing_applies(
                SimpleNamespace(price=None), SimpleNamespace(is_priced=True, base_price=10)
            )
        )
        self.assertFalse(
            pricing_applies(
                SimpleNamespace(price=None), SimpleNamespace(is_priced=True, base_price=0)
            )
        )

    def test_subtotal_excludes_unpriced_and_deleted_lines_and_keeps_rounding(self):
        lines = [
            SimpleNamespace(is_deleted=False, is_priced=False, base_qty=2, base_price=999),
            SimpleNamespace(is_deleted=False, is_priced=True, base_qty=1, base_price="1.005"),
            SimpleNamespace(is_deleted=False, is_priced=True, base_qty=1, base_price="1.015"),
            SimpleNamespace(is_deleted=True, is_priced=True, base_qty=1, base_price=100),
        ]
        self.assertEqual(
            pricing_summary(lines),
            {
                "amount_status": "PARTIAL",
                "priced_amount": Decimal("2.02"),
                "unpriced_line_count": 1,
            },
        )
        self.assertEqual(pricing_summary(lines[:1])["amount_status"], "UNPRICED")
