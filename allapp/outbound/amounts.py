"""Sales amounts: round each original line half-even to cents before summing."""

from decimal import ROUND_HALF_EVEN, Decimal


def line_amount(quantity, price):
    return (Decimal(str(quantity or 0)) * Decimal(str(price or 0))).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_EVEN,
    )


def order_amount(lines):
    return sum(
        (line_amount(line.base_qty, line.base_price) for line in lines if not line.is_deleted),
        Decimal("0.00"),
    )
