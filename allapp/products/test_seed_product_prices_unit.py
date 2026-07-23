from decimal import Decimal
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from allapp.products.management.commands.seed_test_product_prices import (
    PRICE_BANDS,
    Command,
    generate_test_price,
)


class SeedTestProductPricesUnitTests(SimpleTestCase):
    def test_generated_price_is_stable(self):
        first = generate_test_price(
            owner_id=2,
            product_code="BYNY-0001",
            root_category_code="06",
        )
        second = generate_test_price(
            owner_id=2,
            product_code="BYNY-0001",
            root_category_code="06",
        )
        self.assertEqual(first, second)

    def test_generated_prices_stay_inside_each_category_band(self):
        for root_code, (lower, upper, step) in PRICE_BANDS.items():
            for index in range(30):
                price = generate_test_price(
                    owner_id=index + 1,
                    product_code=f"TEST-{index}",
                    root_category_code=root_code,
                )
                with self.subTest(root_code=root_code, index=index):
                    self.assertGreaterEqual(price, lower)
                    self.assertLessEqual(price, upper)
                    self.assertEqual((price - lower) % step, Decimal("0.00"))

    def test_unknown_category_uses_positive_default_band(self):
        price = generate_test_price(
            owner_id=1,
            product_code="UNKNOWN",
            root_category_code="UNKNOWN",
        )
        self.assertGreaterEqual(price, Decimal("5.00"))
        self.assertLessEqual(price, Decimal("60.00"))

    def test_database_guard_rejects_other_database_names(self):
        command = Command()
        with patch(
            "allapp.products.management.commands.seed_test_product_prices."
            "connection.settings_dict",
            {"NAME": "other_db"},
        ):
            with self.assertRaisesMessage(CommandError, "只允许写入 wms_db"):
                command._validate_database()
