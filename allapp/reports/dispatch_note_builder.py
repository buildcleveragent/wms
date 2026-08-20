"""Fail-closed dispatch note aggregation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.utils import timezone

from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.tasking.models import WmsTask, WmsTaskLine

CN_NUM = "零壹贰叁肆伍陆柒捌玖"
CN_UNIT = ["", "拾", "佰", "仟"]
CN_GROUP = ["", "万", "亿", "兆"]


class DispatchNoteDataError(ValueError):
    """The task cannot be mapped to one authoritative outbound document."""


def amount_to_cny_upper(amount: Decimal) -> str:
    amt = (amount or Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integer = int(amt)
    fraction = int((amt * 100) % 100)

    def _four(number):
        rendered = ""
        for index in range(4):
            digit = number % 10
            number //= 10
            rendered = CN_NUM[digit] + CN_UNIT[index] + rendered if digit else CN_NUM[0] + rendered
        return re.sub(f"{CN_NUM[0]}+", CN_NUM[0], rendered).rstrip(CN_NUM[0]) or CN_NUM[0]

    parts = []
    group = 0
    remaining = integer
    while remaining > 0:
        four = remaining % 10000
        remaining //= 10000
        if four:
            parts.insert(0, _four(four) + CN_GROUP[group])
        elif parts and not parts[0].startswith(CN_NUM[0]):
            parts.insert(0, CN_NUM[0])
        group += 1
    head = "".join(parts) or CN_NUM[0]
    jiao = fraction // 10
    fen = fraction % 10
    tail = (CN_NUM[jiao] + "角" if jiao else "") + (CN_NUM[fen] + "分" if fen else "")
    return head + "元" + (tail or "整")


_SPEC_DIGITS = re.compile(r"(\d+)")


def parse_spec_inner_qty(spec_text: str | None) -> Optional[int]:
    if not spec_text:
        return None
    numbers = [int(value) for value in _SPEC_DIGITS.findall(spec_text)]
    if not numbers:
        return None
    total = 1
    for number in numbers:
        total *= number
    return total


@dataclass
class NoteItem:
    idx: int
    sku_code: str
    sku_name: str
    spec: str
    qty: Decimal
    uom: str
    price: Decimal
    amount: Decimal
    piece_qty: Optional[Decimal]


@dataclass
class NoteHeader:
    title: str
    owner_name: str
    hotline: str | None
    note_no: str
    date: str
    customer_name: str
    customer_addr: str
    contact: str | None
    business_user: str | None
    remark: str | None
    is_preview: bool


@dataclass
class DispatchNote:
    header: NoteHeader
    items: list[NoteItem]
    total_amount: Decimal
    total_amount_upper: str
    is_preview: bool


def _display_name(user) -> str | None:
    if not user:
        return None
    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    return full_name or getattr(user, "username", None) or str(user)


def _resolve_order(task: WmsTask) -> OutboundOrder:
    source_model = (task.source_model or "").lower()
    if not source_model.endswith("outboundorder"):
        raise DispatchNoteDataError("配送任务缺少有效出库单来源。")
    try:
        order_id = int(task.source_pk)
    except (TypeError, ValueError) as exc:
        raise DispatchNoteDataError("配送任务的出库单来源无效。") from exc
    order = (
        OutboundOrder.objects.select_related(
            "customer",
            "customer__salesperson",
            "created_by",
        )
        .filter(
            pk=order_id,
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
        )
        .first()
    )
    if order is None:
        raise DispatchNoteDataError("配送任务与出库单的货主或仓库不一致。")
    return order


def build_dispatch_note(task_id: int) -> DispatchNote:
    task = (
        WmsTask.objects.select_related("owner", "warehouse")
        .prefetch_related(
            "lines__product__base_uom",
        )
        .get(pk=task_id)
    )
    if task.task_type != WmsTask.TaskType.DISPATCH:
        raise DispatchNoteDataError("仅配送任务可生成配送单。")

    order = _resolve_order(task)
    lines = list(task.lines.exclude(status=WmsTaskLine.Status.CANCELLED).order_by("id"))
    if not lines:
        raise DispatchNoteDataError("配送任务没有有效明细。")
    if any(
        (line.src_model or "").lower() != "outboundorderline" or not line.src_id for line in lines
    ):
        raise DispatchNoteDataError("配送任务明细缺少有效出库单行来源。")

    order_lines = {
        line.pk: line
        for line in OutboundOrderLine.objects.filter(
            pk__in=[line.src_id for line in lines],
            order=order,
            is_deleted=False,
        ).select_related("product", "base_uom")
    }
    if len(order_lines) != len({line.src_id for line in lines}):
        raise DispatchNoteDataError("配送任务引用了非当前订单的明细。")

    is_preview = task.status != WmsTask.Status.COMPLETED
    dte = getattr(task, "dispatchtaskextra", None)
    note_no = getattr(dte, "manifest_no", "") or task.task_no or str(task.pk)
    salesperson = getattr(order.customer, "salesperson", None) if order.customer else None
    note_date = task.finished_at or task.updated_at or timezone.now()
    if timezone.is_aware(note_date):
        note_date = timezone.localtime(note_date)
    header = NoteHeader(
        title="配送单（预览）" if is_preview else "配送单",
        owner_name=task.owner.name,
        hotline=getattr(task.owner, "service_hotline", None),
        note_no=note_no,
        date=note_date.strftime("%Y-%m-%d") if note_date else date.today().isoformat(),
        customer_name=getattr(order.customer, "name", "") or "",
        customer_addr=order.ship_to or getattr(order.customer, "address", "") or "",
        contact=order.contact or getattr(order.customer, "contact_person", None),
        business_user=_display_name(salesperson) or _display_name(order.created_by),
        remark=order.memo or None,
        is_preview=is_preview,
    )

    items: list[NoteItem] = []
    total_amount = Decimal("0.00")
    for index, task_line in enumerate(lines, start=1):
        order_line = order_lines[task_line.src_id]
        if order_line.product_id != task_line.product_id:
            raise DispatchNoteDataError("配送任务明细与出库单行商品不一致。")
        quantity = Decimal(task_line.qty_plan if is_preview else task_line.qty_done or 0)
        price = Decimal(order_line.base_price)
        amount = (quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount += amount
        product = task_line.product
        spec_text = (
            getattr(product, "spec", None) or getattr(product, "sales_pack_spec", None) or ""
        )
        inner_quantity = parse_spec_inner_qty(spec_text)
        piece_quantity = (
            (quantity / Decimal(inner_quantity)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if inner_quantity
            else None
        )
        base_uom = product.base_uom
        items.append(
            NoteItem(
                idx=index,
                sku_code=product.sku or product.code or str(product.pk),
                sku_name=product.name,
                spec=spec_text,
                qty=quantity,
                uom=base_uom.name or base_uom.code,
                price=price,
                amount=amount,
                piece_qty=piece_quantity,
            )
        )

    total_amount = total_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return DispatchNote(
        header=header,
        items=items,
        total_amount=total_amount,
        total_amount_upper=amount_to_cny_upper(total_amount),
        is_preview=is_preview,
    )
