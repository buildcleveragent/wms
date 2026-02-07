from decimal import Decimal
from ..models import CustomerProductPolicy, ChannelProductPolicy, CustomerChannel

class OrderRuleError(Exception):
    pass

def validate_order_line_rules(owner, customer_id, product, order_uom, qty):
    """
    规则优先级：客户商品策略 > 渠道商品策略
    校验内容：订货单位、最小订量、倍数
    """
    qty = Decimal(qty or 0)
    if qty <= 0:
        raise OrderRuleError("下单数量必须大于0")

    # 找渠道（客户所属渠道）
    channel_id = CustomerChannel.objects.filter(owner=owner, customer_id=customer_id).values_list("channel_id", flat=True).first()

    # 客户策略
    cp = CustomerProductPolicy.objects.filter(owner=owner, customer_id=customer_id, product=product).first()
    if cp:
        if cp.order_uom and order_uom != cp.order_uom:
            raise OrderRuleError(f"订货单位必须为 {cp.order_uom}")
        if qty < cp.min_order_qty:
            raise OrderRuleError(f"最小订量为 {cp.min_order_qty}")
        if cp.multiple_qty and (qty % cp.multiple_qty != 0):
            raise OrderRuleError(f"下单数量须为 {cp.multiple_qty} 的整数倍")
        return True

    # 渠道策略
    if channel_id:
        chp = ChannelProductPolicy.objects.filter(owner=owner, channel_id=channel_id, product=product).first()
        if chp:
            if chp.order_uom and order_uom != chp.order_uom:
                raise OrderRuleError(f"订货单位必须为 {chp.order_uom}")
            if qty < chp.min_order_qty:
                raise OrderRuleError(f"最小订量为 {chp.min_order_qty}")
    return True
