from decimal import Decimal
from django.db.models import Q, Min
from ..models import (
    PriceList, PriceItem, CustomerSpecialPrice, PromotionSpecialPrice, PriceMemory,
    Channel, Promotion
)

def _find_special_price(owner, customer, product, order_date, channel=None):
    # 一店一价
    sp = CustomerSpecialPrice.objects.filter(
        owner=owner, customer=customer, product=product,
        effective_from__lte=order_date
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=order_date)).order_by("-effective_from").first()
    if sp:
        return sp.special_price

    # 促销特价（客户/渠道定向）
    qs = PromotionSpecialPrice.objects.filter(owner=owner, product=product,
                                              promotion__effective_from__lte=order_date).filter(
        Q(promotion__effective_to__isnull=True) | Q(promotion__effective_to__gte=order_date)
    )
    if channel:
        qs = qs.filter(Q(promotion__channel=channel) | Q(promotion__channel__isnull=True))
    qs = qs.order_by("-promotion__effective_from")
    special = qs.first()
    if special:
        return special.special_price
    return None

def _find_pricelist_price(owner, product, order_date, channel=None):
    qs = PriceList.objects.filter(owner=owner, effective_from__lte=order_date).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=order_date)
    )
    if channel:
        qs = qs.filter(Q(channel=channel) | Q(channel__isnull=True))
    qs = qs.order_by("-is_default", "-effective_from")
    for pl in qs:
        pi = pl.items.filter(product=product).first()
        if pi:
            return pi.price
    return None

def compute_price_for_line(owner, customer, product, order_date, channel=None):
    """
    价格优先级：
    1) 一店一价
    2) 促销特价（匹配客户/渠道/时间）
    3) 价目表（渠道专属 > 通用）
    4) 价格记忆（最近成交价）
    """
    price = _find_special_price(owner, customer, product, order_date, channel)
    if price is not None:
        return Decimal(price)

    pl_price = _find_pricelist_price(owner, product, order_date, channel)
    if pl_price is not None:
        return Decimal(pl_price)

    mem = PriceMemory.objects.filter(owner=owner, customer=customer, product=product).first()
    if mem:
        return Decimal(mem.last_price)

    return Decimal("0.00")
