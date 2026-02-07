# allapp/tasking/rcv_scan.py
from dataclasses import dataclass
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from allapp.tasking.models import WmsTask, WmsTaskLine, TaskStatus, TaskScanLog, TaskType
from allapp.tasking.utils import bind_triplet_from
import hashlib

# —— 可配置容差（基于你项目 settings 或策略中心读取） —— #
OVER_ABS = Decimal("0")      # 绝对容差（件）
OVER_PCT = Decimal("0.00")   # 百分比容差（如 0.02 表示 2%）
OVER_MODE = "block"          # 'block' | 'allow' | 'need_approval'

@dataclass
class ScanResult:
    product_id: int
    code_type: str    # 'UNIT' | 'CARTON' | 'LPN' | 'SSCC' | ...
    uom_code: str     # 'EA'/'CS' 等
    pack_qty: Decimal # 1 for EA；箱转件因子（如 12）
    lot_no: str | None = None
    exp_date: str | None = None

# —— 你已有产品/包装模型，按你的字段实现以下三函数 —— #
def classify_barcode(barcode: str) -> str:
    """简单示例：先按长度/前缀/正则判断类型，再到条码表确认"""
    # TODO: 用你项目里 ProductUom / ProductBarcode 实表校验
    if barcode.startswith("LPN"):
        return "LPN"
    return "CARTON" if len(barcode) in (12,14) else "UNIT"

def resolve_product_pack(owner_id: int, barcode: str, code_type: str) -> tuple[int, str, Decimal]:
    """
    返回: (product_id, uom_code, pack_qty_to_base)
    - 单件码: (prod, 'EA', 1)
    - 箱码:   (prod, 'CS', 件/箱换算)
    - LPN:    (prod, 'LPN', 托内总件数) —— 如 LPN 里混多SKU则在上层拆分
    """
    # TODO: 查询你项目里的产品条码/包装表，举例：
    # ProductBarcode(owner=..., barcode=...).select_related('product','uom').first()
    raise NotImplementedError

def fefo_or_default_allocation(task: WmsTask, product_id: int) -> WmsTaskLine | None:
    """为该 SKU 选择/创建一个任务行：优先选还未完成的行；无单可新建（手工收货）"""
    tl = (task.lines
          .select_for_update()
          .filter(product_id=product_id)
          .order_by("id")  # 收货不需要 FEFO；如你要“按托/LPN拆行”，可在创建时就按策略拆好
          .first())
    if tl:
        return tl
    # 无行：允许手工收货时自动建一条“未绑定”的行
    return WmsTaskLine.objects.create(
        task=task, product_id=product_id, qty_plan=Decimal("0"), qty_done=Decimal("0")
    )

def _fingerprint(task_id: int, code: str, qty: Decimal, location_id: int | None, user_id: int | None) -> str:
    """构造幂等指纹（按你的口径可加入时间桶/设备号）"""
    raw = f"RCV|{task_id}|{code}|{qty}|{location_id or 0}|{user_id or 0}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

@transaction.atomic
def rcv_scan(*, task_id: int, barcode: str, qty: Decimal = Decimal("1"),
             location_id: int | None = None, user=None) -> dict:
    """
    扫码闭环：校验任务→解析条码→换算数量→选择/建行→幂等去重→写行/记审计
    返回：本次增量与行完成情况（便于前端刷新）
    """
    task = WmsTask.objects.select_for_update().get(pk=task_id)
    if task.task_type != TaskType.RCV:
        raise ValidationError("仅收货任务允许调用此接口")
    if task.status not in (TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS, TaskStatus.RELEASED):
        raise ValidationError("当前状态不允许扫码")

    # 1) 条码分类与解析
    code_type = classify_barcode(barcode)
    prod_id, uom_code, pack_qty = resolve_product_pack(task.owner_id, barcode, code_type)
    inc_qty = (qty * pack_qty).quantize(Decimal("0.000"))  # 统一成“基本单位数量”

    # 2) 选择/生成任务行（按你策略：通常取“仍未完成”的行；无则新建）
    tline = fefo_or_default_allocation(task, prod_id)

    # 3) 容差校验（计划为 0 视为无单/手工，不做上限校验）
    if tline.qty_plan and OVER_MODE != "allow":
        planned = tline.qty_plan
        done_after = tline.qty_done + inc_qty
        over = done_after - planned
        allow_abs = OVER_ABS
        allow_pct = (planned * OVER_PCT) if OVER_PCT else Decimal("0")
        if over > max(allow_abs, allow_pct):
            if OVER_MODE == "block":
                raise ValidationError(f"超收：计划 {planned}，完成后 {done_after}，超出 {over}")
            # 'need_approval' 模式：可在此打标，等待主管审批后再过账
            # TODO: 标记 tline/scan 为待审批
    # 4) 幂等去重（相同指纹不重复记账）
    fp = _fingerprint(task.id, barcode, inc_qty, location_id, getattr(user, "id", None))
    if TaskScanLog.objects.filter(fp=fp).exists():
        # 已处理过，直接返回当前行状态
        return {"line_id": tline.id, "qty_done": str(tline.qty_done), "inc_qty": "0"}

    # 5) 回写数量 + 扫码日志
    tline.qty_done = (tline.qty_done or 0) + inc_qty
    tline.save(update_fields=["qty_done", "updated_at"])

    TaskScanLog.objects.create(
        task=task, task_line=tline, code=barcode, qty=inc_qty,
        code_type=code_type, uom_code=uom_code, pack_qty=pack_qty,
        location_id=location_id, by_user=user, fp=fp,
    )

    # 6) 自动进入 IN_PROGRESS（若仍为 RELEASED/CLAIMED）
    if task.status in (TaskStatus.RELEASED, TaskStatus.CLAIMED):
        from .services import start_task
        start_task(task.id, user)

    return {"line_id": tline.id, "qty_done": str(tline.qty_done), "inc_qty": str(inc_qty)}
