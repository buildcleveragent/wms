from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


PRICE_QUANTUM = Decimal("0.0001")
PERCENT = Decimal("100")


class InvalidSalePriceRule(ValueError):
    """Raised when a product's configured sales-price guard is unusable."""


def _optional_decimal(value, field_name: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidSalePriceRule(f"{field_name} 不是有效数字。") from exc
    if not number.is_finite():
        raise InvalidSalePriceRule(f"{field_name} 不是有限数字。")
    return number


def minimum_sale_price(
    *,
    base_price,
    min_price=None,
    max_discount=None,
) -> Decimal | None:
    """Return the authoritative minimum sales price at four-decimal precision.

    ``max_discount`` is the maximum percentage that may be taken off the base
    price.  A value of 20 therefore means that an item priced at 100 may be
    sold for no less than 80.  Missing guards are ignored, while malformed
    configured guards fail closed.
    """

    base = _optional_decimal(base_price, "商品原价")
    configured_min = _optional_decimal(min_price, "商品最低价")
    discount = _optional_decimal(max_discount, "最高折扣")

    if base is not None and base < 0:
        raise InvalidSalePriceRule("商品原价不能小于 0。")
    if configured_min is not None and configured_min < 0:
        raise InvalidSalePriceRule("商品最低价不能小于 0。")

    candidates = []
    if configured_min is not None:
        candidates.append(
            configured_min.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
        )

    if discount is not None:
        if discount < 0 or discount > PERCENT:
            raise InvalidSalePriceRule("最高折扣必须在 0 到 100 之间。")
        if base is None:
            raise InvalidSalePriceRule("配置最高折扣前必须先配置商品原价。")
        discounted = (base * (PERCENT - discount) / PERCENT).quantize(
            PRICE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        candidates.append(discounted)

    return max(candidates) if candidates else None
