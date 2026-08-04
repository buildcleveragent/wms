"""Shared catalog primitives for the active sale-mini API.

This module deliberately contains no DRF views, routers, or URL registrations.
The retired ``/api/sales/mobile/*`` transport previously owned these helpers,
which made the active mall API depend on an unsafe legacy endpoint module.
"""

from decimal import ROUND_HALF_UP, Decimal

from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import ChannelProductPolicy, CustomerChannel, CustomerProductPolicy


MONEY_QUANT = Decimal("0.01")
PRICE_QUANT = Decimal("0.0001")
QTY_QUANT = Decimal("0.001")


def _money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _price(value):
    return Decimal(value or 0).quantize(PRICE_QUANT, rounding=ROUND_HALF_UP)


def _qty(value):
    return Decimal(value or 0).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def _str(value, quant):
    return str(Decimal(value or 0).quantize(quant, rounding=ROUND_HALF_UP))


def _error_message(error):
    detail = getattr(error, "detail", error)
    if isinstance(detail, dict):
        for value in detail.values():
            return _error_message(value)
    if isinstance(detail, (list, tuple)):
        return _error_message(detail[0]) if detail else ""
    return str(detail)


def _owner_for_user(user):
    owner = getattr(user, "owner", None)
    if owner:
        return owner
    raise PermissionDenied("当前用户未绑定货主，不能使用销售小程序。")


def _channel_for_customer(owner, customer):
    relation = (
        CustomerChannel.objects.filter(
            owner=owner,
            customer=customer,
            is_active=True,
        )
        .select_related("channel")
        .first()
    )
    return relation.channel if relation else None


def _image_url(request, product):
    image = getattr(product, "product_image", None)
    if not image:
        return ""
    try:
        return request.build_absolute_uri(image.url)
    except ValueError:
        return ""


def _unit_price_from_base(base_unit_price, qty_in_base):
    return _price(Decimal(base_unit_price or 0) * Decimal(qty_in_base or 1))


def _uom_options(product, base_unit_price=None):
    options = []
    seen = set()

    def add(code, name, qty_in_base, source, is_default=False):
        key = (code or name or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        ratio = Decimal(qty_in_base or 1)
        option = {
            "code": code or name,
            "name": name or code,
            "qty_in_base": _str(ratio, QTY_QUANT),
            "source": source,
            "is_default": bool(is_default),
        }
        if base_unit_price is not None:
            option["unit_price"] = _str(
                _unit_price_from_base(base_unit_price, ratio),
                PRICE_QUANT,
            )
        options.append(option)

    base_uom = getattr(product, "base_uom", None)
    add(
        getattr(base_uom, "code", "") or "EA",
        getattr(base_uom, "name", "") or getattr(base_uom, "code", "") or "基本单位",
        Decimal("1"),
        "base",
        True,
    )
    for package in product.packages.all():
        uom = getattr(package, "uom", None)
        add(
            getattr(uom, "code", ""),
            getattr(uom, "name", ""),
            package.qty_in_base,
            "package",
            package.is_sales_default,
        )
    return options


def _policy_for(owner, customer, product, channel):
    if customer:
        customer_policy = CustomerProductPolicy.objects.filter(
            owner=owner,
            customer=customer,
            product=product,
            is_active=True,
        ).first()
        if customer_policy:
            return {
                "source": "customer",
                "order_uom": customer_policy.order_uom,
                "min_order_qty": customer_policy.min_order_qty,
                "multiple_qty": customer_policy.multiple_qty,
            }

    if channel:
        channel_policy = ChannelProductPolicy.objects.filter(
            owner=owner,
            channel=channel,
            product=product,
            is_active=True,
        ).first()
        if channel_policy:
            return {
                "source": "channel",
                "order_uom": channel_policy.order_uom,
                "min_order_qty": channel_policy.min_order_qty,
                "multiple_qty": Decimal("0"),
            }

    return {
        "source": "",
        "order_uom": "",
        "min_order_qty": Decimal("0"),
        "multiple_qty": Decimal("0"),
    }


def _default_order_uom(product, policy):
    if policy.get("order_uom"):
        return policy["order_uom"]
    options = _uom_options(product)
    for option in options:
        if option["is_default"] and option["source"] == "package":
            return option["code"]
    return options[0]["code"] if options else "EA"


def _qty_in_base_for_uom(product, order_uom):
    order_uom = (order_uom or "").strip()
    base_uom = getattr(product, "base_uom", None)
    base_values = {
        (getattr(base_uom, "code", "") or "").strip(),
        (getattr(base_uom, "name", "") or "").strip(),
    }
    if order_uom in base_values:
        return Decimal("1")

    for package in product.packages.all():
        uom = getattr(package, "uom", None)
        package_values = {
            (getattr(uom, "code", "") or "").strip(),
            (getattr(uom, "name", "") or "").strip(),
        }
        if order_uom in package_values:
            return Decimal(package.qty_in_base or 1)
    return None


def _validate_order_uom(product, order_uom):
    valid_values = set()
    for option in _uom_options(product):
        valid_values.add((option.get("code") or "").strip())
        valid_values.add((option.get("name") or "").strip())
    valid_values.discard("")
    if order_uom not in valid_values:
        raise ValidationError(
            {"order_uom": f"商品 {product.code} 未配置订货单位 {order_uom}。"}
        )
