#resolvers.py
from typing import Dict, Set, Tuple, Optional
from django.contrib.contenttypes.models import ContentType

try:
    from allapp.outbound.models import OutboundOrderLine, OrderLineSourceLink
except Exception:
    OutboundOrderLine = None
    OrderLineSourceLink = None

def taskline_to_order_mapping(task_line) -> Optional[Dict]:
    if OutboundOrderLine is None or OrderLineSourceLink is None:
        return None
    ct = ContentType.objects.get_for_model(task_line.__class__)
    links = (OrderLineSourceLink.objects.filter(src_ct=ct, src_id=task_line.id).select_related("order_line"))
    order_ids: Set[int] = set()
    order_line_ids: Set[Tuple[int,int]] = set()
    for lk in links:
        if not lk.order_line_id: continue
        order_ids.add(lk.order_line.order_id)
        order_line_ids.add((lk.order_line.order_id, lk.order_line_id))
    # 如需统计包裹数/订单金额，可在此延伸
    return {"order_ids": order_ids, "order_line_ids": order_line_ids}
