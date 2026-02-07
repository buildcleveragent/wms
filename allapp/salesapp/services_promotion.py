from decimal import Decimal
from django.db import transaction
from ..models import Promotion, PromotionGiftItem, PromotionDiscountStep, SalesOrder, SalesOrderLine
from .pricing import compute_price_for_line

def _calc_base_amount(order: SalesOrder):
    amount = Decimal("0.00")
    for ln in order.lines.all():
        amount += (Decimal(ln.unit_price) * Decimal(ln.qty) - Decimal(ln.discount_amount))
    return amount

@transaction.atomic
def apply_promotions_for_order(order: SalesOrder):
    """
    计算每行单价（若为0则按规则回填），套用促销：
    - special_price 已在 pricing 中优先处理
    - discount_step: 按门槛满减
    - gift: 满赠生成赠品行（单价0）
    """
    # 先补价
    for ln in order.lines.all():
        if not ln.unit_price or Decimal(ln.unit_price) <= 0:
            ln.unit_price = compute_price_for_line(order.owner, order.customer, ln.product, order.order_date)
            ln.save(update_fields=["unit_price", "updated_at"])

    base_amount = _calc_base_amount(order)

    # 满减（取“最优”一级）
    discount_steps = PromotionDiscountStep.objects.filter(
        owner=order.owner, promotion__effective_from__lte=order.order_date
    ).filter(promotion__effective_to__isnull=True) | PromotionDiscountStep.objects.filter(
        owner=order.owner, promotion__effective_to__gte=order.order_date
    )
    best_discount = Decimal("0.00")
    for step in discount_steps:
        if base_amount >= step.threshold_amount and step.discount_amount > best_discount:
            best_discount = step.discount_amount

    # 应用满减：按订单级别抵扣
    if best_discount > 0:
        # 对首行打折（简化实现，你也可以拆分成订单级 discount 表）
        first_line = order.lines.first()
        if first_line:
            first_line.discount_amount = Decimal(first_line.discount_amount) + best_discount
            first_line.save(update_fields=["discount_amount", "updated_at"])

    # 满赠：生成赠品行
    gifts = PromotionGiftItem.objects.filter(owner=order.owner, promotion__effective_from__lte=order.order_date)
    for g in gifts:
        # 真实业务需校验门槛；这里演示：当 base_amount > 0 给赠品
        if base_amount > 0:
            SalesOrderLine.objects.create(
                owner=order.owner, order=order, product=g.product,
                order_uom="EA", qty=g.gift_qty, unit_price=Decimal("0.00"),
                discount_amount=Decimal("0.00"), line_amount=Decimal("0.00"),
                created_by=order.created_by
            )

    # 重新汇总金额
    total = Decimal("0.00")
    for ln in order.lines.all():
        line_amount = Decimal(ln.unit_price) * Decimal(ln.qty) - Decimal(ln.discount_amount)
        if ln.line_amount != line_amount:
            ln.line_amount = line_amount
            ln.save(update_fields=["line_amount", "updated_at"])
        total += line_amount

    order.total_amount = total
    order.save(update_fields=["total_amount", "updated_at"])
