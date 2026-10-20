"""Server-owned pricing applicability for owner sales and saved order lines."""

from decimal import Decimal, InvalidOperation

from allapp.products.pricing import InvalidSalePriceRule, minimum_sale_price

ZERO = Decimal("0")


def has_reference_price(product):
    """Zero and missing reference prices mean unpriced, never free."""
    try:
        price = None if product.price is None else Decimal(str(product.price))
    except (InvalidOperation, ValueError):
        raise InvalidSalePriceRule("商品参考价必须是非负有限数字。") from None
    if price is not None and (not price.is_finite() or price < ZERO):
        raise InvalidSalePriceRule("商品参考价必须是非负有限数字。")
    return price is not None and price > ZERO


def pricing_applies(product, saved_line=None):
    """Preserve submitted snapshots and valid historical negotiated prices."""
    if saved_line is not None:
        if not saved_line.is_priced:
            return False
        if saved_line.base_price > ZERO:
            return True
    return has_reference_price(product)


def sale_minimum(product):
    """An unconfigured reference price has no applicable sales-price guards."""
    if not has_reference_price(product):
        return None
    return minimum_sale_price(
        base_price=product.price, min_price=product.min_price, max_discount=product.max_discount
    )


def saved_pricing_lines(order):
    """Resolve draft line identities only from the authorized server-side order."""
    return {
        f"edit-{line.pk}": line
        for line in order.lines.filter(is_deleted=False).select_related("product")
    }


def match_saved_line(item, saved_lines):
    line = (saved_lines or {}).get(item.get("line_key"))
    return line if line and line.product_id == item["product_id"] else None
