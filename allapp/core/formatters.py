from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


def format_decimal(value, places=0, strip=True):
    """
    数量显示格式化：
    - places=0：10.0000 -> 10
    - places=3：10.1000 -> 10.1，10.1250 -> 10.125
    - strip=True：去掉多余尾零
    """
    if value is None:
        return ""

    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)

    places = int(places or 0)

    if places <= 0:
        q = Decimal("1")
    else:
        q = Decimal("1").scaleb(-places)

    d = d.quantize(q, rounding=ROUND_HALF_UP)

    if strip:
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"

    return format(d, f".{places}f")


def product_qty_places(product):
    """
    从商品基本单位取显示精度。
    默认 0，因为大多数仓库商品是个/件/箱。
    """
    uom = getattr(product, "base_uom", None)
    return int(getattr(uom, "decimal_places", 0) or 0)


def format_product_qty(value, product):
    return format_decimal(value, product_qty_places(product))