from decimal import Decimal

from django.test import SimpleTestCase

from allapp.products.pricing import InvalidSalePriceRule, minimum_sale_price


class MinimumSalePriceTests(SimpleTestCase):
    def test_twenty_percent_discount_from_one_hundred_is_eighty(self):
        self.assertEqual(
            minimum_sale_price(base_price="100", max_discount="20"),
            Decimal("80.0000"),
        )

    def test_zero_discount_keeps_the_base_price(self):
        self.assertEqual(
            minimum_sale_price(base_price="100", max_discount="0"),
            Decimal("100.0000"),
        )

    def test_missing_discount_uses_only_configured_minimum(self):
        self.assertEqual(
            minimum_sale_price(base_price="100", min_price="63.25"),
            Decimal("63.2500"),
        )

    def test_missing_minimum_uses_only_discount_floor(self):
        self.assertEqual(
            minimum_sale_price(base_price="99.99", max_discount="12.5"),
            Decimal("87.4913"),
        )

    def test_stricter_guard_wins(self):
        self.assertEqual(
            minimum_sale_price(
                base_price="100",
                min_price="85",
                max_discount="20",
            ),
            Decimal("85.0000"),
        )

    def test_no_configured_guard_returns_none(self):
        self.assertIsNone(minimum_sale_price(base_price="100"))

    def test_invalid_discount_fails_closed(self):
        for value in ("-0.01", "100.01"):
            with self.subTest(value=value), self.assertRaises(InvalidSalePriceRule):
                minimum_sale_price(base_price="100", max_discount=value)

    def test_discount_requires_a_base_price(self):
        with self.assertRaises(InvalidSalePriceRule):
            minimum_sale_price(base_price=None, max_discount="20")
