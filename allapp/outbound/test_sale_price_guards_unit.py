from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from allapp.outbound.services import validate_standard_order_sale_prices
from allapp.pos.services import _validate_prices_and_shape


class _FakeLines(list):
    def filter(self, **kwargs):
        return self

    def select_related(self, *fields):
        return self


def product(**overrides):
    values = {
        "id": 1,
        "pk": 1,
        "owner_id": 7,
        "code": "UNIT-PRICE-SKU",
        "sku": "UNIT-PRICE-SKU",
        "is_active": True,
        "price": Decimal("100.00"),
        "min_price": Decimal("1.00"),
        "max_discount": Decimal("20.00"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class OutboundSalePriceGuardUnitTests(SimpleTestCase):
    def order(self, line_price, *, processing_mode="STANDARD"):
        current_product = product()
        line = SimpleNamespace(
            line_no=10,
            base_price=Decimal(line_price),
            product=current_product,
        )
        return SimpleNamespace(
            owner_id=current_product.owner_id,
            outbound_type="SALES",
            processing_mode=processing_mode,
            lines=_FakeLines([line]),
        )

    def test_standard_order_rejects_price_below_floor(self):
        with self.assertRaises(ValidationError):
            validate_standard_order_sale_prices(self.order("79.9999"))

    def test_standard_order_accepts_exact_floor(self):
        validate_standard_order_sale_prices(self.order("80.0000"))

    def test_assisted_order_is_not_subject_to_standard_price_guard(self):
        validate_standard_order_sale_prices(
            self.order("0.0000", processing_mode="WAREHOUSE_ASSISTED")
        )


class PosSharedSalePriceRuleTests(SimpleTestCase):
    def test_pos_accepts_the_same_exact_floor(self):
        current_product = product()

        normalized, _ = _validate_prices_and_shape(
            [{"product_id": current_product.id, "qty": "1", "price": "80.0000"}],
            {current_product.id: current_product},
        )

        self.assertEqual(normalized[0]["price"], Decimal("80.0000"))

    def test_pos_rejects_price_below_the_shared_floor(self):
        current_product = product()

        with self.assertRaises(ValidationError):
            _validate_prices_and_shape(
                [
                    {
                        "product_id": current_product.id,
                        "qty": "1",
                        "price": "79.9999",
                    }
                ],
                {current_product.id: current_product},
            )
