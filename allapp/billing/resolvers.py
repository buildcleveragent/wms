from typing import Dict, Optional, Set, Tuple

from django.contrib.contenttypes.models import ContentType

try:
    from allapp.outbound.models import OutboundOrderLine, OrderLineSourceLink
except Exception:
    OutboundOrderLine = None
    OrderLineSourceLink = None

try:
    from allapp.tasking.models import WmsTaskLine
except Exception:
    WmsTaskLine = None


def _normalize_model_name(value) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _collect_order_line(order_line, *, order_ids: Set[int], order_line_ids: Set[Tuple[int, int]]) -> None:
    if not order_line or not getattr(order_line, "id", None) or not getattr(order_line, "order_id", None):
        return
    order_ids.add(order_line.order_id)
    order_line_ids.add((order_line.order_id, order_line.id))


def _collect_links_from_source_link(
    task_line,
    *,
    order_ids: Set[int],
    order_line_ids: Set[Tuple[int, int]],
) -> None:
    if OrderLineSourceLink is None:
        return

    ct = ContentType.objects.get_for_model(task_line.__class__)
    links = (
        OrderLineSourceLink.objects
        .filter(src_ct=ct, src_id=task_line.id)
        .select_related("order_line")
    )
    for link in links:
        _collect_order_line(link.order_line, order_ids=order_ids, order_line_ids=order_line_ids)


def _collect_links_from_direct_source(
    task_line,
    *,
    order_ids: Set[int],
    order_line_ids: Set[Tuple[int, int]],
    visited_task_line_ids: Set[int],
) -> None:
    if OutboundOrderLine is None:
        return

    src_id = getattr(task_line, "src_id", None)
    normalized_src_model = _normalize_model_name(getattr(task_line, "src_model", ""))
    if not src_id or not normalized_src_model:
        return

    if "outboundorderline" in normalized_src_model:
        order_line = (
            OutboundOrderLine.objects
            .select_related("order")
            .filter(pk=src_id)
            .first()
        )
        _collect_order_line(order_line, order_ids=order_ids, order_line_ids=order_line_ids)
        return

    if "wmstaskline" in normalized_src_model or normalized_src_model == "taskline":
        if WmsTaskLine is None:
            return
        parent_task_line = WmsTaskLine.objects.filter(pk=src_id).first()
        if parent_task_line is None:
            return
        _collect_taskline_mapping(
            parent_task_line,
            order_ids=order_ids,
            order_line_ids=order_line_ids,
            visited_task_line_ids=visited_task_line_ids,
        )
        return

    if "outboundorder" in normalized_src_model:
        order_ids.add(int(src_id))

        product_id = getattr(task_line, "product_id", None)
        if not product_id:
            return

        matched_lines = list(
            OutboundOrderLine.objects
            .filter(order_id=src_id, product_id=product_id)
            .only("id", "order_id")[:2]
        )
        if len(matched_lines) == 1:
            _collect_order_line(
                matched_lines[0],
                order_ids=order_ids,
                order_line_ids=order_line_ids,
            )


def _collect_taskline_mapping(
    task_line,
    *,
    order_ids: Set[int],
    order_line_ids: Set[Tuple[int, int]],
    visited_task_line_ids: Set[int],
) -> None:
    task_line_id = getattr(task_line, "id", None)
    if not task_line_id or task_line_id in visited_task_line_ids:
        return

    visited_task_line_ids.add(task_line_id)
    _collect_links_from_source_link(task_line, order_ids=order_ids, order_line_ids=order_line_ids)
    _collect_links_from_direct_source(
        task_line,
        order_ids=order_ids,
        order_line_ids=order_line_ids,
        visited_task_line_ids=visited_task_line_ids,
    )


def taskline_to_order_mapping(task_line) -> Optional[Dict]:
    if task_line is None or OutboundOrderLine is None:
        return None

    order_ids: Set[int] = set()
    order_line_ids: Set[Tuple[int, int]] = set()
    _collect_taskline_mapping(
        task_line,
        order_ids=order_ids,
        order_line_ids=order_line_ids,
        visited_task_line_ids=set(),
    )
    return {"order_ids": order_ids, "order_line_ids": order_line_ids}
