# allapp/inventory/services.py
# -*- coding: utf-8 -*-
"""
Scan-Only + 批内聚合 的统一过账服务（业内最佳实践版本）

为什么是“Scan-Only + 聚合”？
--------------------------------
1) 留痕层（TaskScanLog）：逐条记录每次扫码（可复核、可回放、可审计），绝不聚合、绝不丢。
2) 交易层（InventoryTransaction）：对“本次过账动作（posting_batch）”按业务维度聚合后再入账：
   - 收货/发货/盘点：同一批（posting_batch）内，(owner,wh,product,location,lot,mfg,exp,serial,tx_type,task_id) 维度聚合。
   - 上架/移库：对 (from → to) 路径成对聚合，写一对 MOVE_OUT/MOVE_IN，用 pair_id 关联。
   这样交易表规模适中，语义贴近“这次过账到底收了/发了/移了多少”。

幂等策略（两层保险）：
--------------------------------
A) 任务级 PostingJournal：对一笔“任务过账动作”加行锁（src_model="WmsTask", src_id=task.id, tx_type="POST"）。
   - 状态已 POSTED → 直接返回，避免重复过账。
B) 扫描打点：仅处理 status=OK & posted_at IS NULL 的扫描；写完统一回写 posted_at/posting_batch。
   - 即便外层重试，也不会重复处理同一批扫描。

锁顺序与并发安全：
--------------------------------
- 本服务内部只锁任务（WmsTask）与任务级 PJ（PostingJournal）。
- 你的 DefaultPostingHandler 里已经采用 “WmsTask -> WmsTaskLine -> TaskScanLog(order_by id)” 的加锁顺序；
  在高并发下，建议仍通过 handler 入口调用本服务，保证锁顺序一致，避免死锁。
- 本服务的 select_for_update 锁住 WmsTask 行，确保同一个任务不会被两个并发事务同时过账。

数量精度：
--------------------------------
- 你模型对数量有“四位小数”的校验（之前出现过 qty_delta 小数位 >4 的错误）。
- 所有数量一律通过 _q4() 量化为 4 位小数后再参与聚合/入账。

库位/商品兜底策略：
--------------------------------
- 商品：优先取 scan.product，其次取 scan.task_line.product（避免设备端漏传）。
- 库位：
  * RECEIVE：scan.location → 行.to_location → 行.from_location → settings.TASKING_DEFAULT_RECEIVE_LOCATION_ID
  * PICK/DISPATCH：scan.location → 行.from_location → 行.to_location
  * COUNT：必须有 scan.location 或可兜底行.from/to（按 COUNT 业务习惯建议必须显式传）
  * PUTAWAY/RELOC：必须成对 from/to，有一方缺失则抛错

聚合键（默认不含 task_line_id）：
--------------------------------
- 收/发/盘： posting_batch + task_id + owner_id, warehouse_id, product_id, location_id, batch_no, production_date, expiry_date, serial_no, tx_type
- 上架/移库：对 (from → to) 成对聚合，最终落账时仍分别以 location_id=from / location_id=to 写 OUT/IN 两条交易，共用 pair_id。
- 如需“按行结算/回滚”，可在未来把 task_line_id 纳入聚合键或另建 TransactionAttribution 归属表，不影响当前实现。

"""
from __future__ import annotations
import logging
from django.db.models import F
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from uuid import uuid4
from django.db.models import F, Value
from django.db.models.functions import Least

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from allapp.inventory.models import (InvTxType,
    InventoryDetail,
    InventoryTransaction,
    InventorySummary,
    PostingJournal,
)
from allapp.locations.models import Location
from allapp.tasking.models import WmsTask, WmsTaskLine, TaskScanLog
from decimal import Decimal
from django.core.exceptions import ValidationError
logger = logging.getLogger(__name__)
# ======================
# 小工具：统一数量精度/安全
# ======================

def _q4(x) -> Decimal:
    """
    统一把数量量化为 4 位小数（ROUND_HALF_UP），确保不触发你模型上的小数位校验。
    传入 None 返回 None；传入非 Decimal 会先转换为 Decimal。
    """
    if x is None:
        return None
    if not isinstance(x, Decimal):
        x = Decimal(str(x))
    return x.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _lock_task(task_id: int) -> WmsTask:
    """
    对任务行加 select_for_update（与 handler 的锁顺序配合，避免并发过账的竞态条件）。
    """
    return WmsTask.objects.select_for_update().get(pk=task_id)


def _lock_journal(src_model: str, src_id: int, tx_type: str) -> PostingJournal:
    """
    任务级过账的幂等锚点：
    - 第一次创建为 PENDING，并加行锁（select_for_update）。
    - 若已 POSTED，则说明某次过账已成功，直接视为幂等返回。
    """
    j, _ = PostingJournal.objects.get_or_create(
        src_model=src_model,
        src_id=src_id,
        tx_type=tx_type,
        defaults=dict(status="PENDING", attempt_count=0, message=""),
    )
    return PostingJournal.objects.select_for_update().get(pk=j.pk)


def _ensure_same_wh(*, task: WmsTask, location_id: int):
    """
    校验库位与任务在同一仓库，避免跨仓误过账。
    """
    if not location_id:
        raise ValidationError("缺少库位")
    if getattr(task, "warehouse_id", None) is None:
        raise ValidationError("任务缺少仓库")
    loc = Location.objects.only("warehouse_id").get(pk=location_id)
    if loc.warehouse_id != task.warehouse_id:
        raise ValidationError("库位所属仓与任务仓库不一致")


def _get_line_from_to_ids(line: Optional[WmsTaskLine]) -> Tuple[Optional[int], Optional[int]]:
    """
    从任务行获得 to/from 库位的 id（都可能为 None，用作兜底）。
    """
    if not line:
        return None, None
    to_id = getattr(line, "to_location_id", None) or getattr(getattr(line, "to_location", None), "id", None)
    from_id = getattr(line, "from_location_id", None) or getattr(getattr(line, "from_location", None), "id", None)
    return to_id, from_id


def _scan_loc_id(s: TaskScanLog) -> Optional[int]:
    """
    扫描库位优先从 scan.location 读；如果是外键对象取其 id，若直接存 id 直接返回。
    """
    loc = getattr(s, "location_id", None) or getattr(s, "location", None)
    return getattr(loc, "id", loc) or None


def _scan_product_id(s: TaskScanLog, line: Optional[WmsTaskLine]) -> Optional[int]:
    """
    扫描商品优先从 scan.product 读；兜底到行.product。
    """
    pid = getattr(s, "product_id", None) or getattr(getattr(s, "product", None), "id", None)
    if pid:
        return pid
    if line:
        pid = getattr(line, "product_id", None) or getattr(getattr(line, "product", None), "id", None)
    return pid


def _upsert_detail(
    *,
    owner_id: int,
    warehouse_id: int,
    product_id: int,
    location_id: int,
    qty_delta: Decimal,
    batch_no: Optional[str] = "",
    production_date=None,
    expiry_date=None,
    serial_no: Optional[str] = "",
    task_type: Optional[str] = None,  # 增加任务类型参数
) -> InventoryDetail:
    """
    Upsert 库存明细，并把 onhand_qty += qty_delta。
    说明：
    - available_qty 的更新由模型层规则保证（通常是 onhand - allocated - locked - damaged）。
    - batch_no/serial_no 统一大写与空值归一（None 而非 ""），以免同一维度被拆成两条。
    """
    print("173 所有参数: localaaa", locals())
    # serial_no=""
    # batch_no=""
    det, created = InventoryDetail.objects.get_or_create(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        location_id=location_id,
        batch_no=(batch_no or "").strip().upper(),  # 字符串→空串+大写
        production_date=production_date or None,  # 日期→None 表示无
        expiry_date=expiry_date or None,
        serial_no=(serial_no or "").strip().upper(),

        defaults=dict(onhand_qty=Decimal("0"), allocated_qty=Decimal("0"), locked_qty=Decimal("0"), damaged_qty=Decimal("0")),
        # batch_no=(batch_no or "").upper() or None,
        # production_date=production_date or None,
        # expiry_date=expiry_date or None,
        # serial_no=(serial_no or "").upper() or None,
    )

    if created:
         print("created det111", det)

    if not task_type:
        raise ValidationError(f"未知任务类型: {task_type}")

    base_onhand = det.onhand_qty or Decimal("0")
    base_alloc = det.allocated_qty or Decimal("0")

    if task_type in ["PICK", "SHIP", "DISPATCH", "LOAD"]:
        # 对拣货/发运任务：qty_delta 一般为负数（ISSUE）
        det.onhand_qty = base_onhand + qty_delta

        if qty_delta < 0:
            # 本次实际出库数量（正数）
            used = min(base_alloc, -qty_delta)  # 最多只能释放当前已冻结的这部分
            det.allocated_qty = base_alloc - used
        else:
            # 理论上不会走到这里（除非将来支持“反冲销”），先保持不动
            det.allocated_qty = base_alloc
    else:
        # RECEIVE / ADJ_GAIN / ADJ_LOSS / COUNT 等，沿用原来的 onhand 逻辑
        det.onhand_qty = base_onhand + qty_delta

    #
    #
    # # 根据任务类型更新库存
    # if task_type in ["PICK","SHIP","DISPATCH"]:  # 收货，增加库存
    #     det.onhand_qty = (det.onhand_qty or Decimal("0")) + qty_delta
    #     det.allocated_qty = (det.allocated_qty or Decimal("0")) + qty_delta
    # else:
    #     det.onhand_qty = (det.onhand_qty or Decimal("0")) + qty_delta

    # 防御性校验，避免 onhand 被减成负数时直接 500
    if det.onhand_qty < 0:
        raise ValidationError("库存不足：出库数量超出当前账面库存。")

    det.available_qty = det.onhand_qty - det.allocated_qty - det.locked_qty - det.damaged_qty

    print("bbb 190 所有参数:det.product_id,det.location_id,qty_delta,det.onhand_qty det.available_qty",det.product_id,det.location_id,qty_delta,det.onhand_qty,det.available_qty )
    print("det  所有参数:",det)
    det.save()
    print("ccc 192 所有参数:")
    return det


def _insert_tx(
    *,
    tx_type: str,
    owner_id: int,
    warehouse_id: int,
    location_id: int,
    product_id: int,
    qty_delta: Decimal,
    batch_no: Optional[str],
    production_date,
    expiry_date,
    serial_no: Optional[str],
    src_model: str,
    src_id: int,
    src_line_id: Optional[int],
    memo: str,
    pair_id: Optional[str],
    posted_at,
    posting_batch: Optional[str],
) -> InventoryTransaction:
    """
    写一条 InventoryTransaction。
    这里不做 get_or_create，以免“聚合后重复写同一条”的逻辑被隐藏错误吞掉；
    若外层重试（例如事务回滚重跑），由任务级 PJ + 扫描打点来保证幂等。
    """
    qty_delta = _q4(qty_delta)
    if qty_delta == 0:
        # 按照常见 WMS 规则，0 数量不应入账；若你要保留，也建议在上层就过滤掉。
        raise ValidationError("qty_delta 不能为 0")
    # print("_insert_tx")

    return InventoryTransaction.objects.create(
        tx_type=tx_type,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        location_id=location_id,
        product_id=product_id,
        batch_no=(batch_no or "").upper() or None,
        production_date=production_date,
        expiry_date=expiry_date,
        serial_no=(serial_no or "").upper() or None,
        qty_delta=qty_delta,
        pair_id=pair_id,
        src_model=src_model,
        src_id=src_id,
        src_line_id=src_line_id,
        memo=(memo or "")[:255],
        posted_at=posted_at,                                  # 过账打点（若模型有该字段）
        posting_batch=(posting_batch or None)[:40] if posting_batch else None,  # 批次号（若模型有该字段）
    )

def _refresh_summaries(pairs: Iterable[Tuple[int, int]]):
    """Recalculate InventorySummary totals for touched (owner, product) pairs."""

    unique_pairs: Set[Tuple[int, int]] = {p for p in pairs if p and all(p)}
    if not unique_pairs:
        return

    for owner_id, product_id in unique_pairs:
        aggregates = InventoryDetail.objects.filter(
            owner_id=owner_id,
            product_id=product_id,
            is_active=True,
        ).aggregate(
            onhand=Sum("onhand_qty"),
            allocated=Sum("allocated_qty"),
            locked=Sum("locked_qty"),
            damaged=Sum("damaged_qty"),
        )

        summary, _ = InventorySummary.objects.get_or_create(
            owner_id=owner_id,
            product_id=product_id,
            defaults=dict(
                onhand_qty=Decimal("0"),
                allocated_qty=Decimal("0"),
                locked_qty=Decimal("0"),
                damaged_qty=Decimal("0"),
            ),
        )

        summary.onhand_qty = _q4(aggregates["onhand"] or Decimal("0"))
        summary.allocated_qty = _q4(aggregates["allocated"] or Decimal("0"))
        summary.locked_qty = _q4(aggregates["locked"] or Decimal("0"))
        summary.damaged_qty = _q4(aggregates["damaged"] or Decimal("0"))
        summary.save()




def _can_post(task: WmsTask) -> Tuple[bool, str]:
    """
    简化的可过账判断：
    - 任务必须审核通过（review_status == APPROVED）
    - posting_status != POSTED（避免重复过账）
    - 若想限制 task.status 的取值范围，可在此追加白名单。
    """
    if getattr(task, "review_status", "") != "APPROVED":
        return False, "未审核(APPROVED)不可过账"
    posting_status = getattr(task, "posting_status", "")
    if posting_status in ("POSTED",):
        return False, f"过账状态为 {posting_status}，不可重复过账"
    return True, ""


# ======================
# 按任务类型规范化数量符号
# ======================
# 严格按任务类型取数量；不做兜底、不混用字段
def _scan_qty_for_type(task_type: str, s: TaskScanLog) -> Decimal:
    t = (task_type or "").upper()

    if t in ("RECEIVE", "PUTAWAY", "RELOC", "PICK", "DISPATCH", "LOAD", "SHIP"):
        # 这些任务只允许用 qty_base_delta
        if getattr(s, "qty_base_delta", None) is None:
            raise ValidationError(f"{task_type} 需要 qty_base_delta（缺失）")
        q = Decimal(str(s.qty_base_delta))
        return _q4(q)

    elif t in ("COUNT",):
        # COUNT 只允许用 qty_base（把它解释为“差异量 delta”）
        if getattr(s, "qty_base", None) is None:
            raise ValidationError("COUNT 需要 qty_base（缺失）")
        q = Decimal(str(s.qty_base))
        return _q4(q)

    # 未知类型：保底按 delta 口径（也可改成 raise 更严格）
    if getattr(s, "qty_base_delta", None) is None:
        raise ValidationError(f"{task_type} 需要 qty_base_delta（缺失）")
    return _q4(Decimal(str(s.qty_base_delta)))



def _qty_for_type(task_type: str, scan: TaskScanLog) -> Decimal:
    """
    方向归一规则：
    - RECEIVE / PUTAWAY / RELOC：>0
    - PICK / DISPATCH / LOAD / SHIP：<0（若取到正数则转为负）
    - COUNT：可正可负（0=无差异，不入账）
    """
    t = (task_type or "").upper()
    q = _scan_qty_for_type(t, scan)  # 严格来源

    if t in ("RECEIVE", "PUTAWAY", "RELOC"):
        if q <= 0:
            raise ValidationError(f"{task_type} 需要 qty_base_delta > 0")
        return _q4(q)

    if t in ("PICK", "DISPATCH", "LOAD", "SHIP"):
        if q == 0:
            raise ValidationError(f"{task_type} 需要非零 qty_base_delta")
        if q > 0:
            q = -q
        return _q4(q)

    # COUNT：保留正负，0 代表无差异
    return _q4(q)


# ======================
# 批内聚合键（默认不含 task_line_id）
# ======================

class _AggKey:
    """
    收/发/盘的聚合键：
    - posting_batch（本次过账批），task_id（同一任务内聚合）
    - owner_id, warehouse_id, product_id, location_id
    - batch_no(LOT), production_date, expiry_date, serial_no
    - tx_type（RECEIVE/ISSUE/ADJ_GAIN/ADJ_LOSS）
    """
    __slots__ = (
        "posting_batch", "task_id", "owner_id", "warehouse_id", "product_id",
        "location_id", "batch_no", "production_date", "expiry_date", "serial_no", "tx_type"
    )

    def __init__(self, posting_batch, task_id, owner_id, warehouse_id, product_id,
                 location_id, batch_no, production_date, expiry_date, serial_no, tx_type):
        self.posting_batch = posting_batch
        self.task_id = task_id
        self.owner_id = owner_id
        self.warehouse_id = warehouse_id
        self.product_id = product_id
        self.location_id = location_id
        self.batch_no = (batch_no or "").upper() or None
        self.production_date = production_date
        self.expiry_date = expiry_date
        self.serial_no = (serial_no or "").upper() or None
        self.tx_type = tx_type

    def as_tuple(self):
        return (
            self.posting_batch, self.task_id, self.owner_id, self.warehouse_id, self.product_id,
            self.location_id, self.batch_no, self.production_date, self.expiry_date, self.serial_no, self.tx_type
        )

    def __hash__(self):
        return hash(self.as_tuple())

    def __eq__(self, other):
        return isinstance(other, _AggKey) and self.as_tuple() == other.as_tuple()


# ======================
# 聚合：收/发/盘（不含 from→to 的简单型）
# ======================

def _group_receive_like(task: WmsTask, scans: List[TaskScanLog], *, now, batch_no: str, tx_type: str) -> Dict[_AggKey, Decimal]:
    """
    针对 RECEIVE/ISSUE（PICK/DISPATCH 等）/COUNT 的聚合过程：
    - 核心是把每条扫描映射到聚合键，然后把数量累加到该键上。
    - 兜底策略详见注释。
    """
    agg: Dict[_AggKey, Decimal] = defaultdict(lambda: Decimal("0"))

    for s in scans:
        # 1) 商品：scan.product → line.product
        line = getattr(s, "task_line", None)
        pid = _scan_product_id(s, line)
        if not pid:
            raise ValidationError(f"{tx_type} 缺少商品")

        # 2) 库位：
        #    - RECEIVE：scan.location → 行.to → 行.from → SETTINGS 默认收货位
        #    - 其他：  scan.location → 行.from → 行.to
        loc_id = _scan_loc_id(s)
        if not loc_id:
            to_id, from_id = _get_line_from_to_ids(line)
            if tx_type == InvTxType.RECEIVE:
                loc_id = to_id or from_id or getattr(settings, "TASKING_DEFAULT_RECEIVE_LOCATION_ID", None)
            else:
                loc_id = from_id or to_id
        if not loc_id:
            raise ValidationError(f"{tx_type} 缺少库位")
        _ensure_same_wh(task=task, location_id=loc_id)

        # 3) 数量方向归一
        # 把tx_type = ADJ_GAIN / ADJ_LOSS # 误当成“任务类型”传给取量函数了，导致落入“增量口径”而去要求qty_base_delta
        # qty = _qty_for_type(task_type=tx_type if tx_type != InvTxType.ISSUE else "PICK", scan=s)
        # 修正：明确映射交易类型 -> 取量口径的“任务类型”
        if tx_type in (InvTxType.ADJ_GAIN, InvTxType.ADJ_LOSS):
            task_type_for_qty = "COUNT"
        elif tx_type == InvTxType.ISSUE:
            task_type_for_qty = "PICK"
        else:  # InvTxType.RECEIVE
            task_type_for_qty = "RECEIVE"

        qty = _qty_for_type(task_type_for_qty, scan=s)


        # 4) 组装聚合键
        key = _AggKey(
            posting_batch=batch_no,
            task_id=task.id,
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            product_id=pid,
            location_id=loc_id,
            batch_no=getattr(s, "lot_no", ""),
            production_date=getattr(s, "mfg_date", None),
            expiry_date=getattr(s, "exp_date", None),
            serial_no=getattr(s, "serial_no", ""),
            tx_type=tx_type,
        )

        agg[key] += qty
        print("459 _group_receive_like key qty  agg[key]=", key.as_tuple(), qty, agg[key])

    return agg


# ======================
# 聚合：PUTAWAY/RELOC（需要 from→to 成对的复杂型）
# ======================

def _group_putaway(task: WmsTask, scans: List[TaskScanLog], *, now, batch_no: str) -> Dict[Tuple[_AggKey, _AggKey], Decimal]:
    """
    上架/移库的聚合：
    - 需要成对 from→to，所以返回结构是 { (key_out, key_in, pair_id) : qty_sum }
    - 其中 key_out/location_id=from，key_in/location_id=to，同一对使用同一 pair_id。
    """
    agg: Dict[Tuple[_AggKey, _AggKey], Decimal] = defaultdict(lambda: Decimal("0"))

    for s in scans:
        line = getattr(s, "task_line", None)
        pid = _scan_product_id(s, line)
        if not pid:
            raise ValidationError("PUTAWAY 缺少商品")

        # from/to 必须齐全：scan.* → line.*
        to_id, from_id = _get_line_from_to_ids(line)
        s_to = getattr(s, "to_location_id", None) or getattr(getattr(s, "to_location", None), "id", None) or to_id
        s_from = getattr(s, "from_location_id", None) or getattr(getattr(s, "from_location", None), "id", None) or from_id
        if not s_from or not s_to:
            raise ValidationError("PUTAWAY 需要 from/to 库位")

        _ensure_same_wh(task=task, location_id=s_from)
        _ensure_same_wh(task=task, location_id=s_to)

        qty_pos = _qty_for_type("PUTAWAY", s)  # >0
        # pair = uuid4().hex[:16]                # 一对交易的关联 id
        # pair = uuid4() 每条扫描都产出一对 MOVE（语义还说自己是“聚合”😅）

        common = dict(
            posting_batch=batch_no,
            task_id=task.id,
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            product_id=pid,
            batch_no=getattr(s, "lot_no", ""),
            production_date=getattr(s, "mfg_date", None),
            expiry_date=getattr(s, "exp_date", None),
            serial_no=getattr(s, "serial_no", ""),
        )
        print("2 MOVE_OUT pair" )

        key_in  = _AggKey(location_id=s_to,  tx_type=InvTxType.RECEIVE, **common)
        key_out = _AggKey(location_id=s_from, tx_type=InvTxType.ISSUE, **common)
        agg[(key_out, key_in)] += qty_pos
        print("MOVE_OUT key_out key_out.as_tuple()",key_out.as_tuple())

    return agg


# ======================
# 执行：把聚合结果一次性写入明细与交易
# ======================

def _apply_receive_like(task: WmsTask, groups: Dict[_AggKey, Decimal], *, now, batch_no: str) -> int:
    """
    对“收/发/盘”的聚合结果逐条入账：
    - 每个分组 → 1 条交易（RECEIVE / ISSUE / ADJ_*）
    - 库存明细先增量，然后写交易
    """
    created = 0
    task_type = task.task_type
    touched_pairs: Set[Tuple[int, int]] = set()
    print("529 groups.items()=",groups.items())
    for key, qty in groups.items():
        qty = _q4(qty)
        if qty == 0:
            # 聚合后恰好抵消为 0 的分组不入账
            continue

        # 明细增量（出库为负数，进库为正数；COUNT 的 ADJ_* 同理）
        _upsert_detail(
            owner_id=key.owner_id,
            warehouse_id=key.warehouse_id,
            product_id=key.product_id,
            location_id=key.location_id,
            qty_delta=qty,
            batch_no=key.batch_no,
            production_date=key.production_date,
            expiry_date=key.expiry_date,
            serial_no=key.serial_no,
            task_type=task_type,
        )
        touched_pairs.add((key.owner_id, key.product_id))
        print("529 touched_pairs ", touched_pairs)
        # 交易
        _insert_tx(
            tx_type=key.tx_type,
            owner_id=key.owner_id,
            warehouse_id=key.warehouse_id,
            product_id=key.product_id,
            location_id=key.location_id,
            qty_delta=qty,
            batch_no=key.batch_no,
            production_date=key.production_date,
            expiry_date=key.expiry_date,
            serial_no=key.serial_no,
            src_model="WmsTask",
            src_id=task.id,
            src_line_id=None,     # 默认不按行聚合；若将来要“按行对账”，可改为 line_id
            memo=key.tx_type,
            pair_id=None,
            posted_at=now,
            posting_batch=batch_no,
        )
        created += 1
    _refresh_summaries(touched_pairs)
    return created


def _apply_putaway(task: WmsTask, groups: Dict[Tuple[_AggKey, _AggKey], Decimal], *, now, batch_no: str) -> int:
    """
    对“上架/移库”的聚合结果逐条入账：
    - 每个分组（同一条路径 from→to） → 两条交易：MOVE_OUT(-qty) + MOVE_IN(+qty)，用 pair_id 关联。
    - 同时更新两个库位的库存明细。
    """
    print("1 MOVE_OUT pair")
    created = 0
    task_type = task.task_type
    touched_pairs: Set[Tuple[int, int]] = set()
    for (key_out, key_in), qty_pos in groups.items():
        qty_pos = _q4(qty_pos)
        if qty_pos == 0:
            continue

        print("_upsert_detail 570 out qty_pos= key_out.location_id,",qty_pos,key_out.location_id,)
        # 先 OUT（发出库位 onhand -= qty_pos）
        # 这里生成本对 OUT/IN 的 pair_id（字符串，满足 _insert_tx 的类型注解）
        pair = str(uuid4())

        _upsert_detail(
            owner_id=key_out.owner_id,
            warehouse_id=key_out.warehouse_id,
            product_id=key_out.product_id,
            location_id=key_out.location_id,
            qty_delta=-qty_pos,
            batch_no=key_out.batch_no,
            production_date=key_out.production_date,
            expiry_date=key_out.expiry_date,
            serial_no=key_out.serial_no,
            task_type=task_type
        )
        _insert_tx(
            tx_type=InvTxType.ISSUE,
            owner_id=key_out.owner_id,
            warehouse_id=key_out.warehouse_id,
            product_id=key_out.product_id,
            location_id=key_out.location_id,
            qty_delta=-qty_pos,
            batch_no=key_out.batch_no,
            production_date=key_out.production_date,
            expiry_date=key_out.expiry_date,
            serial_no=key_out.serial_no,
            src_model="WmsTask",
            src_id=task.id,
            src_line_id=None,
            memo="PUTAWAY",
            pair_id=pair,
            posted_at=now,
            posting_batch=batch_no,
        )
        print("_upsert_detail 601 in, ")
        # 再 IN（目标库位 onhand += qty_pos）
        _upsert_detail(
            owner_id=key_in.owner_id,
            warehouse_id=key_in.warehouse_id,
            product_id=key_in.product_id,
            location_id=key_in.location_id,
            qty_delta=qty_pos,
            batch_no=key_in.batch_no,
            production_date=key_in.production_date,
            expiry_date=key_in.expiry_date,
            serial_no=key_in.serial_no,
            task_type=task_type
        )
        touched_pairs.add((key_out.owner_id, key_out.product_id))
        _insert_tx(
            tx_type=InvTxType.RECEIVE,
            owner_id=key_in.owner_id,
            warehouse_id=key_in.warehouse_id,
            product_id=key_in.product_id,
            location_id=key_in.location_id,
            qty_delta=qty_pos,
            batch_no=key_in.batch_no,
            production_date=key_in.production_date,
            expiry_date=key_in.expiry_date,
            serial_no=key_in.serial_no,
            src_model="WmsTask",
            src_id=task.id,
            src_line_id=None,
            memo="PUTAWAY",
            pair_id=pair,
            posted_at=now,
            posting_batch=batch_no,
        )
        created += 2
    _refresh_summaries(touched_pairs)
    return created


# ======================
# 对外入口：统一过账（仅扫描 + 批内聚合）
# ======================

@transaction.atomic
def post_task(
    *,
    task: WmsTask,
    user=None,
    scans: Optional[List[TaskScanLog]] = None,
    note: str = "",
    now=None,
    batch_no: Optional[str] = None,
) -> Dict[str, Any]:
    """
    统一任务过账入口（Scan-Only + 批内聚合）

    流程概览：
    1) 锁任务（WmsTask）→ 校验可过账（审核状态、posting_status）。
    2) 锁/建 任务级 PostingJournal(src="WmsTask", id=task.id, tx="POST")，若已 POSTED → 直接返回。
    3) 过滤扫描：只处理 status=OK & posted_at IS NULL（避免重复）。
    4) 按任务类型，把扫描聚合为分组（RECEIVE/ISSUE/ADJ_*；PUTAWAY 为 OUT+IN 成对）。
    5) 逐分组入账：先更新 InventoryDetail，再写 InventoryTransaction。
    6) 批量回写扫描打点（posted_at / posting_batch / posting_journal）。
    7) 回填任务 posting_status=POSTED；PJ 置 POSTED 并记录 message=批号。
    """
    # 1) 任务 + 可过账
    task = _lock_task(task.id)
    ok, why = _can_post(task)
    if not ok:
        raise ValidationError(why)

    # 2) PJ 幂等锚点
    pj = _lock_journal("WmsTask", task.id, "POST")
    if pj.status == "POSTED":
        # 说明之前一次已完成；直接返回幂等成功
        return {"ok": True, "affected_tx_count": 0, "batch_no": pj.message or "", "message": "already POSTED"}

    # 3) 过滤扫描
    now_ts = now or timezone.now()
    batch = batch_no or now_ts.strftime("%Y%m%d-%H%M%S")
    status_ok = getattr(TaskScanLog.ScanStatus, "OK", "OK")
    scans = [s for s in (scans or []) if getattr(s, "status", None) == status_ok and getattr(s, "posted_at", None) is None]

    # 4) 任务类型映射
    t = (getattr(task, "task_type", "") or "").upper()
    if hasattr(WmsTask, "TaskType"):
        try:
            t_enum = WmsTask.TaskType
            t_map = {
                "RECEIVE": getattr(t_enum, "RECEIVE", "RECEIVE"),
                "PUTAWAY": getattr(t_enum, "PUTAWAY", "PUTAWAY"),
                "RELOC": "RELOC",
                "PICK": getattr(t_enum, "PICK", "PICK"),
                "LOAD": getattr(t_enum, "LOAD", "LOAD"),
                "DISPATCH": getattr(t_enum, "DISPATCH", "DISPATCH"),
                "COUNT": getattr(t_enum, "COUNT", "COUNT"),
            }
        except Exception:
            t_map = {}
    else:
        t_map = {}

    # 5) 聚合 + 入账
    affected = 0
    if t in ("RECEIVE", t_map.get("RECEIVE", "RECEIVE")):
        groups = _group_receive_like(task, scans, now=now_ts, batch_no=batch, tx_type=InvTxType.RECEIVE)
        affected = _apply_receive_like(task, groups, now=now_ts, batch_no=batch)

    elif t in ("PUTAWAY", t_map.get("PUTAWAY", "PUTAWAY"), "RELOC"):
        groups = _group_putaway(task, scans, now=now_ts, batch_no=batch)
        affected = _apply_putaway(task, groups, now=now_ts, batch_no=batch)

    elif t in ("PICK", t_map.get("PICK", "PICK")):
        groups = _group_receive_like(task, scans, now=now_ts, batch_no=batch, tx_type=InvTxType.ISSUE)
        affected = _apply_receive_like(task, groups, now=now_ts, batch_no=batch)

    elif t in ("DISPATCH", "SHIP", "LOAD", t_map.get("DISPATCH", "DISPATCH"), t_map.get("LOAD", "LOAD")):
        groups = _group_receive_like(task, scans, now=now_ts, batch_no=batch, tx_type=InvTxType.ISSUE)
        affected = _apply_receive_like(task, groups, now=now_ts, batch_no=batch)

    elif t in ("COUNT", t_map.get("COUNT", "COUNT")):
        # COUNT：只读 qty_base；正→ADJ_GAIN，负→ADJ_LOSS，0 不入账
        pos_scans = []
        neg_scans = []
        for s in scans:
            q = _qty_for_type("COUNT", s)  # 严格：缺少 qty_base 会直接抛 ValidationError
            if q > 0:
                pos_scans.append(s)
            elif q < 0:
                neg_scans.append(s)
            # q == 0 则忽略（无差异）

        if pos_scans:
            groups_pos = _group_receive_like(task, pos_scans, now=now_ts, batch_no=batch, tx_type=InvTxType.ADJ_GAIN)
            affected += _apply_receive_like(task, groups_pos, now=now_ts, batch_no=batch)
        if neg_scans:
            groups_neg = _group_receive_like(task, neg_scans, now=now_ts, batch_no=batch, tx_type=InvTxType.ADJ_LOSS)
            affected += _apply_receive_like(task, groups_neg, now=now_ts, batch_no=batch)

    else:
        # 未知类型：保底按 RECEIVE 规则处理，或改为 raise ValidationError 更严格
        groups = _group_receive_like(task, scans, now=now_ts, batch_no=batch, tx_type=InvTxType.RECEIVE)
        affected = _apply_receive_like(task, groups, now=now_ts, batch_no=batch)

    # 6) 扫描批量打点（posted_at / posting_batch / posting_journal）
    ids = [s.id for s in scans if getattr(s, "id", None)]
    if ids:
        TaskScanLog.objects.filter(pk__in=ids, posted_at__isnull=True).update(
            posted_at=now_ts,
            posting_batch=batch,
            posting_journal_id=pj.id if hasattr(TaskScanLog, "posting_journal") else None,
        )

    # 7) 回填任务状态 & 提交 PJ
    posted = getattr(getattr(WmsTask, "PostingStatus", None), "POSTED", "POSTED")
    try:
        task.posting_status = posted
        task.save(update_fields=["posting_status"])
    except Exception as e:
        logger.error("[SERVICES] 任务状态更新失败: task=%s, 错误=%s", task.id, e)
        raise  # 重新抛出异常，让事务回滚

    pj.status = "POSTED"
    pj.message = f"{batch}"                         # 把批号记入 message，便于追查
    pj.attempt_count = (pj.attempt_count or 0) + 1
    pj.save(update_fields=["status", "message", "attempt_count"])

    return {"ok": True, "affected_tx_count": int(affected), "batch_no": batch, "message": "OK"}

# allapp/inventory/services.py （在处理 ISSUE 成功写分录并更新 onhand 后，追加 ↓）
def _release_allocated_after_issue(owner_id, warehouse_id, product_id, location_id, qty_abs):
    """
    ISSUE 后释放 allocated：allocated = max(allocated - qty_abs, 0)
    """
    # 以条件更新防止负数；如果需要精确对应“分配来源明细”，可以带上批次/效期/序列维度字段一起过滤
    # InventoryDetail.objects.filter(
    #     owner_id=owner_id,
    #     warehouse_id=warehouse_id,
    #     product_id=product_id,
    #     location_id=location_id,
    #     allocated_qty__gt=0,
    # ).update(allocated_qty=F("allocated_qty") - qty_abs)


    qs = InventoryDetail.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        location_id=location_id,
        allocated_qty__gt=0,
    )

    used = Least(F("allocated_qty"), Value(qty_abs))
    qs.update(
        allocated_qty=F("allocated_qty") - used,
        available_qty=F("available_qty") + used,
    )




