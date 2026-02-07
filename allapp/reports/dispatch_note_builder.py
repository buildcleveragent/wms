## 4) 数据聚合：构建配送单上下文
# **文件：`allapp/reports/dispatch_note_builder.py`**

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Optional
from django.utils import timezone
from django.db.models import Prefetch

from allapp.tasking.models import WmsTask, WmsTaskLine

CN_NUM = "零壹贰叁肆伍陆柒捌玖"
CN_UNIT = ["", "拾", "佰", "仟"]
CN_GROUP = ["", "万", "亿", "兆"]

def amount_to_cny_upper(amount: Decimal) -> str:
    amt = (amount or Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    integer = int(amt)
    fraction = int((amt * 100) % 100)
    def _four(n):
        s = ""; u = CN_UNIT
        for i in range(4):
            d = n % 10; n //= 10
            s = (CN_NUM[d] + u[i] + s) if d else (CN_NUM[0] + s)
        return re.sub(f"{CN_NUM[0]}+", CN_NUM[0], s).rstrip(CN_NUM[0]) or CN_NUM[0]
    parts = []
    g = 0; n = integer
    while n > 0:
        four = n % 10000; n //= 10000
        if four:
            parts.insert(0, _four(four) + CN_GROUP[g])
        elif parts and not parts[0].startswith(CN_NUM[0]):
            parts.insert(0, CN_NUM[0])
        g += 1
    head = "".join(parts) or CN_NUM[0]
    jiao = fraction // 10; fen = fraction % 10
    tail = (CN_NUM[jiao] + "角" if jiao else "") + (CN_NUM[fen] + "分" if fen else "")
    return head + "元" + (tail or "整")

_spec_digits = re.compile(r"(\d+)")

def parse_spec_inner_qty(spec_text: str | None) -> Optional[int]:
    if not spec_text:
        return None
    nums = [int(x) for x in _spec_digits.findall(spec_text)]
    if not nums:
        return None
    total = 1
    for n in nums:
        total *= n
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

@dataclass
class DispatchNote:
    header: NoteHeader
    items: list[NoteItem]
    total_amount: Decimal
    total_amount_upper: str


def build_dispatch_note(task_id: int) -> DispatchNote:
    task = (WmsTask.objects
            .select_related("owner", "warehouse")
            .prefetch_related(Prefetch("lines", queryset=WmsTaskLine.objects.select_related("product")))
            .get(pk=task_id))

    owner_name = getattr(task.owner, "name", "") or getattr(task.owner, "owner_name", "")
    hotline = getattr(task.owner, "service_hotline", None)

    dte = getattr(task, "dispatchtaskextra", None)
    manifest_no = getattr(dte, "manifest_no", "") if dte else ""
    note_no = manifest_no or getattr(task, "task_no", str(task.id))

    customer_name = ""; customer_addr = ""; contact = None; business_user = None
    remark = getattr(task, "memo", None)

    # 尝试从来源出库单推断客户信息（按你冻结模型调整字段名）
    src_obj = None
    for ln in task.lines.all():
        if ln.src_model and ln.src_id:
            src_obj = (ln.src_model, ln.src_id); break
    if src_obj:
        try:
            from allapp.outbound.models import OutboundOrder
            oo = OutboundOrder.objects.select_related("customer").get(pk=src_obj[1])
            customer_name = getattr(oo, "customer_name", "") or getattr(oo.customer, "name", "")
            customer_addr = getattr(oo, "ship_to_address", "") or getattr(oo.customer, "address", "")
            contact = getattr(oo, "contact_name", None)
            business_user = getattr(oo, "sales_user_name", None)
            remark = remark or getattr(oo, "remark", None)
        except Exception:
            pass

    header = NoteHeader(
        title="配送单",
        owner_name=owner_name,
        hotline=hotline,
        note_no=note_no,
        date=date.today().strftime("%Y-%m-%d"),
        customer_name=customer_name,
        customer_addr=customer_addr,
        contact=contact,
        business_user=business_user,
        remark=remark,
    )

    items: list[NoteItem] = []
    total_amount = Decimal("0")

    for i, ln in enumerate(task.lines.all(), start=1):
        prod = ln.product
        sku_code = getattr(prod, "sku", None) or getattr(prod, "barcode", None) or str(prod.id)
        sku_name = getattr(prod, "name", "")
        spec_text = getattr(prod, "sales_pack_spec", None) or ""
        try:
            dle = ln.dispatchlineextra
            spec_text = spec_text or getattr(dle, "pack_spec", None)
        except Exception:
            pass

        qty = ln.qty_done or ln.qty_plan or Decimal("0")
        uom = getattr(prod, "base_uom_name", None) or getattr(prod, "uom", "件")

        price = Decimal("0"); amount = Decimal("0")
        try:
            from allapp.outbound.models import OutboundOrderLine
            if ln.src_model == "OutboundOrderLine" and ln.src_id:
                ol = OutboundOrderLine.objects.get(pk=ln.src_id)
                price = getattr(ol, "price", Decimal("0")) or Decimal("0")
        except Exception:
            pass
        amount = (price * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount += amount

        inner = parse_spec_inner_qty(spec_text)
        piece_qty = None
        if inner and inner > 0:
            piece_qty = (qty / Decimal(inner)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        items.append(NoteItem(i, str(sku_code), sku_name, spec_text, qty, uom, price, amount, piece_qty))

    return DispatchNote(header, items, total_amount, amount_to_cny_upper(total_amount))
