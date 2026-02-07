# allapp/tasking/plugins/defaults.py
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from django.utils import timezone

@dataclass
class BarcodeResolveResult:
    product_id: Optional[int] = None
    code_type: str = ""              # "SKU" / "LOC" / "LPN" / "LOT" ...
    label_key: str = ""              # 原始码
    uom_code: Optional[str] = None
    pack_qty: Decimal = Decimal("1")
    lot_no: Optional[str] = None
    mfg_date = None
    exp_date = None

def resolve_barcode(owner_id: int, barcode: str) -> BarcodeResolveResult:
    # MVP：先朴素实现（只认 SKU / LOC），真实项目可接入更复杂的编码/前缀/校验位规则
    if barcode.startswith("LOC-"):
        return BarcodeResolveResult(code_type="LOC", label_key=barcode[4:])
    return BarcodeResolveResult(code_type="SKU", label_key=barcode)

class DefaultPostingHandler:
    """
    MVP 过账处理器：按 Task.type 分发到 post_<type>，
    未实现的类型统一走 handle_task_posting（可 no-op）。
    """
    def handle_task_posting(self, task, ctx: dict) -> dict:
        return {"ok": True, "msg": "noop"}

    def post_receive(self, task, ctx: dict) -> dict:
        # 示例：收货后直接写一条 IN 事务，数量来自 ctx['qty']；真实实现请用你 inventory.services 的接口
        return {"ok": True, "msg": "received"}

    def post_putaway(self, task, ctx: dict) -> dict:
        return {"ok": True}

    def post_pick(self, task, ctx: dict) -> dict:
        return {"ok": True}

    def post_pack(self, task, ctx: dict) -> dict:
        return {"ok": True}

    def post_dispatch(self, task, ctx: dict) -> dict:
        return {"ok": True}
