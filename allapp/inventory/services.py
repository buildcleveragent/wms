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
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from uuid import uuid4
from django.db.models import F, Q, Value
from django.db.models.functions import Least

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from allapp.core.utils.log_context import build_log_payload
from allapp.inventory.models import (InvTxType,
    InventoryDetail,
    InventoryTransaction,
    InventorySummary,
    PostingJournal,
)
from allapp.locations.models import Location, Warehouse
from allapp.tasking.models import WmsTask, WmsTaskLine, TaskScanLog
logger = logging.getLogger(__name__)


_POSTING_FAMILY_BY_TASK_TYPE = {
    "RECEIVE": "RECEIVE",
    "PUTAWAY": "MOVE",
    "RELOC": "MOVE",
    "REPLEN": "MOVE",
    "PICK": "ISSUE",
    "LOAD": "ISSUE",
    "DISPATCH": "ISSUE",
    "COUNT": "COUNT",
    "ADJUST": "ADJUST",
}


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


def _lock_and_validate_scans(
    *, task: WmsTask, scans: Optional[Iterable[TaskScanLog]]
) -> List[TaskScanLog]:
    """Re-read and lock the exact scan set accepted for this posting attempt."""
    supplied = [] if scans is None else list(scans)
    if not supplied:
        raise ValidationError("无可过账扫描（必须显式提供至少一条扫描）。")

    scan_ids = []
    for scan in supplied:
        scan_id = getattr(scan, "pk", None)
        if scan_id is None:
            raise ValidationError("过账扫描必须是已持久化的 TaskScanLog。")
        scan_ids.append(scan_id)

    if len(scan_ids) != len(set(scan_ids)):
        raise ValidationError("过账扫描包含重复记录。")

    # 与标准处理器保持相同锁序：task -> journal -> task lines -> scans。
    valid_line_ids = set(
        WmsTaskLine.objects.select_for_update()
        .filter(task_id=task.id)
        .order_by("id")
        .values_list("id", flat=True)
    )
    locked_scans = list(
        TaskScanLog.objects.select_for_update()
        .filter(pk__in=scan_ids, task_id=task.id)
        .order_by("id")
    )
    if len(locked_scans) != len(scan_ids):
        raise ValidationError("部分过账扫描不存在或不属于当前任务。")

    status_ok = getattr(TaskScanLog.ScanStatus, "OK", "OK")
    rejected = getattr(TaskScanLog.ReviewStatus, "REJECTED", "REJECTED")
    for scan in locked_scans:
        if scan.task_id != task.id:
            raise ValidationError(f"扫描 {scan.id} 不属于任务 {task.id}。")
        if scan.owner_id != task.owner_id or scan.warehouse_id != task.warehouse_id:
            raise ValidationError(f"扫描 {scan.id} 的货主或仓库与任务不一致。")
        if scan.task_line_id and scan.task_line_id not in valid_line_ids:
            raise ValidationError(f"扫描 {scan.id} 的任务行不属于任务 {task.id}。")
        if scan.status != status_ok:
            raise ValidationError(f"扫描 {scan.id} 状态不是 OK，不能过账。")
        if scan.review_status == rejected:
            raise ValidationError(f"扫描 {scan.id} 已被拒绝，不能过账。")
        if scan.posted_at is not None:
            raise ValidationError(f"扫描 {scan.id} 已过账，不能重复处理。")
        if scan.posting_journal_id is not None or scan.posting_batch is not None:
            raise ValidationError(f"扫描 {scan.id} 已存在过账标记，拒绝覆盖。")

    return locked_scans


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
    task: Optional[WmsTask] = None,
    detail: Optional[InventoryDetail] = None,
) -> InventoryDetail:
    """
    Upsert 库存明细，并把 onhand_qty += qty_delta。
    说明：
    - available_qty 的更新由模型层规则保证（通常是 onhand - allocated - locked - damaged）。
    - batch_no/serial_no 统一大写与空值归一（None 而非 ""），以免同一维度被拆成两条。
    """
    posting_family = _POSTING_FAMILY_BY_TASK_TYPE.get(task_type or "")
    if posting_family is None:
        raise ValidationError(f"不支持的库存过账任务类型：{task_type or '<空>'}")

    from allapp.tasking.counting import assert_inventory_not_count_locked

    assert_inventory_not_count_locked(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        location_id=location_id,
        batch_no=batch_no or "",
        task=task,
    )

    # 正常过账路径会在进入本函数前按固定顺序锁定所有候选明细。
    # 保留 detail=None 的兼容入口，但也必须重取行锁，不允许无锁读改写。
    created = False
    if detail is None:
        lookup = _detail_dimension_values(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            location_id=location_id,
            batch_no=batch_no,
            production_date=production_date,
            expiry_date=expiry_date,
            serial_no=serial_no,
        )
        detail, created = InventoryDetail.objects.get_or_create(
            **lookup,
            defaults=_empty_detail_quantities(),
        )
        detail = InventoryDetail.objects.select_for_update().get(pk=detail.pk)
    det = detail

    if created:
        ctx, ctx_text = build_log_payload(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        logger.info(
            "inventory.detail.created %s product_id=%s location_id=%s detail_id=%s batch_no=%s serial_no=%s",
            ctx_text,
            product_id,
            location_id,
            det.id,
            (batch_no or "").strip().upper() or "-",
            (serial_no or "").strip().upper() or "-",
            extra=ctx,
        )

    base_onhand = det.onhand_qty or Decimal("0")
    base_alloc = det.allocated_qty or Decimal("0")
    det.zone_type = Location.objects.only("zone_type").get(pk=location_id).zone_type

    if posting_family == "ISSUE":
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
        # RECEIVE / MOVE / COUNT / ADJUST 只调整 onhand，不释放出库预占。
        det.onhand_qty = base_onhand + qty_delta

    # 防御性校验，避免 onhand 被减成负数时直接 500
    if det.onhand_qty < 0:
        raise ValidationError("库存不足：出库数量超出当前账面库存。")

    det.available_qty = det.onhand_qty - det.allocated_qty - det.locked_qty - det.damaged_qty
    det.save()
    ctx, ctx_text = build_log_payload(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    logger.info(
        "inventory.detail.upserted %s product_id=%s location_id=%s qty_delta=%s onhand_qty=%s allocated_qty=%s available_qty=%s detail_id=%s task_type=%s",
        ctx_text,
        det.product_id,
        det.location_id,
        qty_delta,
        det.onhand_qty,
        det.allocated_qty,
        det.available_qty,
        det.id,
        task_type,
        extra=ctx,
    )

    logger.info(
        "inventory.detail.upserted %s product_id=%s location_id=%s batch_no=%s production_date=%s expiry_date=%s qty_delta=%s onhand_qty=%s allocated_qty=%s available_qty=%s detail_id=%s task_type=%s",
        ctx_text,
        det.product_id,
        det.location_id,
        det.batch_no or "-",
        det.production_date or "-",
        det.expiry_date or "-",
        qty_delta,
        det.onhand_qty,
        det.allocated_qty,
        det.available_qty,
        det.id,
        task_type,
        extra=ctx,
    )

    return det


def _empty_detail_quantities() -> Dict[str, Decimal]:
    return {
        "onhand_qty": Decimal("0"),
        "allocated_qty": Decimal("0"),
        "locked_qty": Decimal("0"),
        "damaged_qty": Decimal("0"),
    }


def _detail_dimension_values(
    *,
    owner_id: int,
    warehouse_id: int,
    product_id: int,
    location_id: int,
    batch_no: Optional[str] = "",
    production_date=None,
    expiry_date=None,
    serial_no: Optional[str] = "",
) -> Dict[str, Any]:
    """Return the normalized unique inventory dimension used for locking/upsert."""
    return {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "location_id": location_id,
        "batch_no": (batch_no or "").strip().upper(),
        "production_date": production_date or None,
        "expiry_date": expiry_date or None,
        "serial_no": (serial_no or "").strip().upper(),
        "is_active": True,
    }


def _detail_dimension_key(values: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        values["owner_id"],
        values["product_id"],
        values["warehouse_id"],
        values["location_id"],
        values["batch_no"],
        values["production_date"],
        values["expiry_date"],
        values["serial_no"],
    )


def _detail_key_from_instance(detail: InventoryDetail) -> Tuple[Any, ...]:
    return _detail_dimension_key(
        _detail_dimension_values(
            owner_id=detail.owner_id,
            warehouse_id=detail.warehouse_id,
            product_id=detail.product_id,
            location_id=detail.location_id,
            batch_no=detail.batch_no,
            production_date=detail.production_date,
            expiry_date=detail.expiry_date,
            serial_no=detail.serial_no,
        )
    )


def _dimension_sort_key(key: Tuple[Any, ...]) -> Tuple[Any, ...]:
    owner_id, product_id, warehouse_id, location_id, batch, production, expiry, serial = key
    return (
        owner_id,
        product_id,
        warehouse_id,
        location_id,
        batch or "",
        production.isoformat() if production else "",
        expiry.isoformat() if expiry else "",
        serial or "",
    )


def _lock_or_create_summary(owner_id: int, product_id: int) -> InventorySummary:
    summary, _ = InventorySummary.objects.get_or_create(
        owner_id=owner_id,
        product_id=product_id,
        is_active=True,
        defaults=_empty_detail_quantities(),
    )
    return InventorySummary.objects.select_for_update().get(pk=summary.pk)


def lock_active_inventory_details_for_update(
    pairs: Iterable[Tuple[int, int]],
) -> List[InventoryDetail]:
    """Lock every active detail in each owner/product scope in a stable pair order."""
    locked: List[InventoryDetail] = []
    normalized_pairs = sorted(
        {(int(owner_id), int(product_id)) for owner_id, product_id in pairs}
    )
    for owner_id, product_id in normalized_pairs:
        locked.extend(
            InventoryDetail.objects.select_for_update()
            .filter(
                owner_id=owner_id,
                product_id=product_id,
                is_active=True,
            )
            .order_by("warehouse_id", "location_id", "id")
        )
    return locked


def _lock_inventory_dimensions(
    dimensions: Iterable[Dict[str, Any]],
) -> Tuple[Dict[Tuple[Any, ...], InventoryDetail], Dict[Tuple[int, int], InventorySummary]]:
    """
    Lock all inventory rows touched by one posting in a stable order.

    Existing rows are locked for the complete owner/product scope before any mutation.  This
    both serializes two tasks that touch different dimensions of the same SKU and keeps the
    subsequent full summary recalculation consistent.  If the scope has no detail yet, its
    summary row is used as the creation mutex.
    """
    dimensions_by_key = {
        _detail_dimension_key(values): values
        for values in dimensions
    }
    if not dimensions_by_key:
        return {}, {}

    pairs = sorted({(key[0], key[1]) for key in dimensions_by_key})
    existing = lock_active_inventory_details_for_update(pairs)
    details_by_key = {_detail_key_from_instance(detail): detail for detail in existing}
    existing_pairs = {(detail.owner_id, detail.product_id) for detail in existing}

    # With no detail row there is nothing else to lock, so the summary is the pair-level mutex
    # that prevents concurrent first receipts from creating disjoint, stale summary snapshots.
    summaries: Dict[Tuple[int, int], InventorySummary] = {}
    for owner_id, product_id in pairs:
        if (owner_id, product_id) not in existing_pairs:
            summaries[(owner_id, product_id)] = _lock_or_create_summary(owner_id, product_id)

    for key in sorted(dimensions_by_key, key=_dimension_sort_key):
        if key in details_by_key:
            continue
        detail, _ = InventoryDetail.objects.get_or_create(
            **dimensions_by_key[key],
            defaults=_empty_detail_quantities(),
        )
        details_by_key[key] = InventoryDetail.objects.select_for_update().get(pk=detail.pk)

    # Match the established detail -> summary lock order used by other inventory writers.
    for owner_id, product_id in pairs:
        if (owner_id, product_id) not in summaries:
            summaries[(owner_id, product_id)] = _lock_or_create_summary(owner_id, product_id)

    return details_by_key, summaries


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
    subwarehouse_id: Optional[int] = None,
    zone_type: Optional[int] = None,
    src_no: Optional[str] = None,
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
    # 交易幂等由任务级 PostingJournal + 扫描打点保证，这里不做静默去重。

    optional_fields = {}
    if subwarehouse_id is not None:
        optional_fields["subwarehouse_id"] = subwarehouse_id
    if zone_type is not None:
        optional_fields["zone_type"] = zone_type
    if src_no is not None:
        optional_fields["src_no"] = (src_no or "")[:64]

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
        **optional_fields,
    )

def _refresh_summaries(
    pairs: Iterable[Tuple[int, int]],
    *,
    locked_summaries: Optional[Dict[Tuple[int, int], InventorySummary]] = None,
    locked_details: Optional[Dict[Tuple[Any, ...], InventoryDetail]] = None,
):
    """Recalculate summaries from the current in-memory state of locked detail rows."""

    unique_pairs: Set[Tuple[int, int]] = {p for p in pairs if p and all(p)}
    if not unique_pairs:
        return

    if locked_details is None:
        pair_filter = Q()
        for owner_id, product_id in sorted(unique_pairs):
            pair_filter |= Q(owner_id=owner_id, product_id=product_id)
        current_details = list(
            InventoryDetail.objects.select_for_update()
            .filter(pair_filter, is_active=True)
            .order_by("owner_id", "product_id", "warehouse_id", "location_id", "id")
        )
    else:
        # _lock_inventory_dimensions() returns every active row in each touched owner/product
        # scope, not only the requested dimensions. Updated rows are the same Python objects.
        current_details = list(locked_details.values())

    for owner_id, product_id in unique_pairs:
        pair_details = [
            detail
            for detail in current_details
            if detail.owner_id == owner_id and detail.product_id == product_id
        ]
        onhand = sum((detail.onhand_qty or Decimal("0") for detail in pair_details), Decimal("0"))
        allocated = sum((detail.allocated_qty or Decimal("0") for detail in pair_details), Decimal("0"))
        locked = sum((detail.locked_qty or Decimal("0") for detail in pair_details), Decimal("0"))
        damaged = sum((detail.damaged_qty or Decimal("0") for detail in pair_details), Decimal("0"))

        summary = (locked_summaries or {}).get((owner_id, product_id))
        if summary is None:
            summary = _lock_or_create_summary(owner_id, product_id)

        summary.onhand_qty = _q4(onhand)
        summary.allocated_qty = _q4(allocated)
        summary.locked_qty = _q4(locked)
        summary.damaged_qty = _q4(damaged)
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
    t = task_type or ""

    if t in ("RECEIVE", "PUTAWAY", "RELOC", "REPLEN", "PICK", "DISPATCH", "LOAD", "ADJUST"):
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

    raise ValidationError(f"不支持的库存过账任务类型：{t or '<空>'}")



def _qty_for_type(task_type: str, scan: TaskScanLog) -> Decimal:
    """
    方向归一规则：
    - RECEIVE / PUTAWAY / RELOC / REPLEN：>0
    - PICK / DISPATCH / LOAD：<0（若取到正数则转为负）
    - COUNT：可正可负（0=无差异，不入账）
    - ADJUST：可正可负，但不可为 0
    """
    t = task_type or ""
    q = _scan_qty_for_type(t, scan)  # 严格来源

    if t in ("RECEIVE", "PUTAWAY", "RELOC", "REPLEN"):
        if q <= 0:
            raise ValidationError(f"{task_type} 需要 qty_base_delta > 0")
        return _q4(q)

    if t in ("PICK", "DISPATCH", "LOAD"):
        if q == 0:
            raise ValidationError(f"{task_type} 需要非零 qty_base_delta")
        if q > 0:
            q = -q
        return _q4(q)

    if t == "COUNT":
        # COUNT：保留正负，0 代表无差异
        return _q4(q)

    if t == "ADJUST":
        if q == 0:
            raise ValidationError("ADJUST 需要非零 qty_base_delta")
        return _q4(q)

    raise ValidationError(f"不支持的库存过账任务类型：{t or '<空>'}")


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
        "location_id", "batch_no", "production_date", "expiry_date", "serial_no", "tx_type",
        "task_line_id", "source_detail_id",
    )

    def __init__(self, posting_batch, task_id, owner_id, warehouse_id, product_id,
                 location_id, batch_no, production_date, expiry_date, serial_no, tx_type,
                 task_line_id=None, source_detail_id=None):
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
        self.task_line_id = task_line_id
        self.source_detail_id = source_detail_id

    def as_tuple(self):
        return (
            self.posting_batch, self.task_id, self.owner_id, self.warehouse_id, self.product_id,
            self.location_id, self.batch_no, self.production_date, self.expiry_date, self.serial_no, self.tx_type,
            self.task_line_id, self.source_detail_id,
        )

    def __hash__(self):
        return hash(self.as_tuple())

    def __eq__(self, other):
        return isinstance(other, _AggKey) and self.as_tuple() == other.as_tuple()


# ======================
# 聚合：收/发/盘（不含 from→to 的简单型）
# ======================

def _normalized_model_name(value: Optional[str]) -> str:
    return (value or "").replace("_", "").replace(".", "").lower()


def _is_pos_pick(task: WmsTask, tx_type: str) -> bool:
    return (
        tx_type == InvTxType.ISSUE
        and task.task_type == WmsTask.TaskType.PICK
        and (task.source_app or "").lower() == "pos"
    )


def _required_pos_meta_int(line: WmsTaskLine, key: str) -> int:
    try:
        value = int((line.plan_meta or {})[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"POS PICK 任务行 {line.id} 缺少有效的 {key}") from exc
    if value <= 0:
        raise ValidationError(f"POS PICK 任务行 {line.id} 的 {key} 无效")
    return value


def _pos_scan_source_details(
    task: WmsTask, scans: List[TaskScanLog]
) -> Dict[int, InventoryDetail]:
    """Resolve each POS scan to its explicit source layer without guessing by primary key."""
    if _normalized_model_name(task.source_model) != "outboundorder":
        raise ValidationError(f"POS PICK 任务 {task.id} 来源必须为 OutboundOrder")

    detail_ids_by_scan: Dict[int, int] = {}
    for scan in scans:
        line = getattr(scan, "task_line", None)
        if not line or line.task_id != task.id:
            raise ValidationError(f"POS PICK 扫描 {scan.id} 缺少所属任务行")
        if line.status != WmsTaskLine.Status.COMPLETED:
            raise ValidationError(f"POS PICK 任务行 {line.id} 尚未完成")
        if _normalized_model_name(line.src_model) != "outboundorderline":
            raise ValidationError(f"POS PICK 任务行 {line.id} 来源必须为 OutboundOrderLine")

        detail_id = _required_pos_meta_int(line, "source_inventory_detail_id")
        _required_pos_meta_int(line, "pos_sale_id")
        _required_pos_meta_int(line, "pos_sale_line_id")
        order_id = _required_pos_meta_int(line, "outbound_order_id")
        order_line_id = _required_pos_meta_int(line, "outbound_order_line_id")
        if line.src_id != order_line_id:
            raise ValidationError(f"POS PICK 任务行 {line.id} 的出库单行来源不一致")
        try:
            task_order_id = int(task.source_pk)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"POS PICK 任务 {task.id} 缺少有效的出库单来源") from exc
        if task_order_id != order_id:
            raise ValidationError(f"POS PICK 任务行 {line.id} 的出库单来源不一致")
        if (line.plan_meta or {}).get("sale_no") != task.ref_no:
            raise ValidationError(f"POS PICK 任务行 {line.id} 的销售单号不一致")
        detail_ids_by_scan[scan.id] = detail_id

    details = InventoryDetail.objects.in_bulk(set(detail_ids_by_scan.values()))
    missing = sorted(set(detail_ids_by_scan.values()) - set(details))
    if missing:
        raise ValidationError(f"POS PICK 原库存层不存在或已软删除: {missing}")
    return {
        scan_id: details[detail_id]
        for scan_id, detail_id in detail_ids_by_scan.items()
    }


def _group_receive_like(
    task: WmsTask,
    scans: List[TaskScanLog],
    *,
    now,
    batch_no: str,
    tx_type: str,
    qty_task_type: str,
) -> Dict[_AggKey, Decimal]:
    """
    针对 RECEIVE/ISSUE（PICK/DISPATCH 等）/COUNT 的聚合过程：
    - 核心是把每条扫描映射到聚合键，然后把数量累加到该键上。
    - 兜底策略详见注释。
    """
    agg: Dict[_AggKey, Decimal] = defaultdict(lambda: Decimal("0"))
    is_pos_pick = _is_pos_pick(task, tx_type)
    source_details = _pos_scan_source_details(task, scans) if is_pos_pick else {}

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

        # 3) 数量方向归一。数量口径由调用方显式指定，不从交易类型猜测。
        qty = _qty_for_type(qty_task_type, scan=s)


        # 4) POS uses the exact reserved layer as the authoritative dimension.
        source_detail = source_details.get(s.id)
        if source_detail is not None:
            meta = line.plan_meta or {}
            if s.owner_id != task.owner_id or s.warehouse_id != task.warehouse_id:
                raise ValidationError(f"POS PICK 扫描 {s.id} 的货主或仓库归属不一致")
            if s.qty_base_delta is None or _q4(s.qty_base_delta) <= 0:
                raise ValidationError(f"POS PICK 扫描 {s.id} 必须记录正数增量")
            if s.scan_snapshot_rev != line.scan_snapshot_rev:
                raise ValidationError(f"POS PICK 扫描 {s.id} 的任务行快照版本不一致")
            if not source_detail.is_active:
                raise ValidationError(f"POS PICK 原库存层 {source_detail.id} 已停用")
            if (
                source_detail.owner_id != task.owner_id
                or source_detail.warehouse_id != task.warehouse_id
                or source_detail.product_id != pid
                or source_detail.location_id != loc_id
                or line.product_id != pid
                or line.from_location_id != loc_id
            ):
                raise ValidationError(f"POS PICK 任务行 {line.id} 与原库存层归属不一致")
            if (
                (source_detail.batch_no or "").upper() != (s.lot_no or "").upper()
                or source_detail.production_date != s.mfg_date
                or source_detail.expiry_date != s.exp_date
                or (source_detail.serial_no or "").upper() != (s.barcode or "").upper()
            ):
                raise ValidationError(f"POS PICK 任务行 {line.id} 的跟踪属性与原库存层不一致")

            meta_subwarehouse_id = meta.get("subwarehouse_id")
            if meta_subwarehouse_id is not None:
                try:
                    meta_subwarehouse_id = int(meta_subwarehouse_id)
                except (TypeError, ValueError) as exc:
                    raise ValidationError(f"POS PICK 任务行 {line.id} 的子仓快照无效") from exc
            try:
                meta_zone_type = int(meta.get("zone_type"))
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"POS PICK 任务行 {line.id} 的区域快照无效") from exc
            if (
                source_detail.subwarehouse_id != meta_subwarehouse_id
                or source_detail.zone_type != meta_zone_type
                or (source_detail.serial_no or "").upper()
                != (meta.get("serial_no") or "").upper()
            ):
                raise ValidationError(f"POS PICK 任务行 {line.id} 的库存层快照不一致")

            qty_abs = abs(qty)
            try:
                reserved_qty = _q4(meta["reserved_qty"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(f"POS PICK 任务行 {line.id} 的预占数量无效") from exc
            if (
                reserved_qty != qty_abs
                or _q4(line.qty_plan) != qty_abs
                or _q4(line.qty_done) != qty_abs
            ):
                raise ValidationError(f"POS PICK 任务行 {line.id} 的扫描、完成和预占数量不一致")

            loc_id = source_detail.location_id
            batch_value = source_detail.batch_no
            production_date = source_detail.production_date
            expiry_date = source_detail.expiry_date
            serial_no = source_detail.serial_no
            task_line_id = line.id
            source_detail_id = source_detail.id
        else:
            batch_value = getattr(s, "lot_no", "")
            production_date = getattr(s, "mfg_date", None)
            expiry_date = getattr(s, "exp_date", None)
            serial_no = getattr(s, "serial_no", "")
            task_line_id = None
            source_detail_id = None

        # 5) 组装聚合键
        key = _AggKey(
            posting_batch=batch_no,
            task_id=task.id,
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            product_id=pid,
            location_id=loc_id,
            batch_no=batch_value,
            production_date=production_date,
            expiry_date=expiry_date,
            serial_no=serial_no,
            tx_type=tx_type,
            task_line_id=task_line_id,
            source_detail_id=source_detail_id,
        )

        agg[key] += qty
        ctx, ctx_text = build_log_payload(task=task, posting_batch=batch_no)
        logger.info(
            "inventory.receive_like.scan_grouped %s tx_type=%s scan_id=%s product_id=%s location_id=%s batch_no=%s production_date=%s expiry_date=%s qty=%s grouped_qty=%s",
            ctx_text,
            tx_type,
            getattr(s, "id", None),
            pid,
            loc_id,
            key.batch_no or "-",
            key.production_date or "-",
            key.expiry_date or "-",
            qty,
            agg[key],
            extra=ctx,
          )

    return agg


# ======================
# 聚合：PUTAWAY/RELOC（需要 from→to 成对的复杂型）
# ======================

def _group_putaway(
    task: WmsTask,
    scans: List[TaskScanLog],
    *,
    now,
    batch_no: str,
    qty_task_type: str,
) -> Dict[Tuple[_AggKey, _AggKey], Decimal]:
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

        qty_pos = _qty_for_type(qty_task_type, s)  # >0
        # pair = uuid4().hex[:16]                # 一对交易的关联 id
        # pair = uuid4() 每条扫描都产出一对 MOVE（语义还说自己是“聚合”😅）

        is_replenishment = task.task_type == WmsTask.TaskType.REPLEN
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
            task_line_id=getattr(line, "id", None),
            source_detail_id=(
                getattr(line, "src_id", None)
                if is_replenishment
                and _normalized_model_name(getattr(line, "src_model", ""))
                == "inventorydetail"
                else None
            ),
        )

        key_in = _AggKey(
            location_id=s_to,
            tx_type=(InvTxType.MOVE_IN if is_replenishment else InvTxType.RECEIVE),
            **common,
        )
        key_out = _AggKey(
            location_id=s_from,
            tx_type=(InvTxType.MOVE_OUT if is_replenishment else InvTxType.ISSUE),
            **common,
        )
        agg[(key_out, key_in)] += qty_pos
        ctx, ctx_text = build_log_payload(task=task, posting_batch=batch_no)
        logger.info(
            "inventory.putaway.scan_grouped %s from_location_id=%s to_location_id=%s product_id=%s qty=%s grouped_qty=%s",
            ctx_text,
            s_from,
            s_to,
            pid,
            qty_pos,
            agg[(key_out, key_in)],
            extra=ctx,
        )

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
    pending_groups = [
        (key, _q4(qty))
        for key, qty in groups.items()
        if _q4(qty) != 0
    ]
    dimensions = [
        _detail_dimension_values(
            owner_id=key.owner_id,
            warehouse_id=key.warehouse_id,
            product_id=key.product_id,
            location_id=key.location_id,
            batch_no=key.batch_no,
            production_date=key.production_date,
            expiry_date=key.expiry_date,
            serial_no=key.serial_no,
        )
        for key, _qty in pending_groups
    ]
    details_by_key, locked_summaries = _lock_inventory_dimensions(dimensions)
    touched_pairs: Set[Tuple[int, int]] = {
        (key.owner_id, key.product_id) for key, _qty in pending_groups
    }

    def group_sort(item):
        key, _qty = item
        values = _detail_dimension_values(
            owner_id=key.owner_id,
            warehouse_id=key.warehouse_id,
            product_id=key.product_id,
            location_id=key.location_id,
            batch_no=key.batch_no,
            production_date=key.production_date,
            expiry_date=key.expiry_date,
            serial_no=key.serial_no,
        )
        return (
            _dimension_sort_key(_detail_dimension_key(values)),
            key.task_line_id or 0,
            key.source_detail_id or 0,
        )

    for key, qty in sorted(pending_groups, key=group_sort):
        dimension = _detail_dimension_values(
            owner_id=key.owner_id,
            warehouse_id=key.warehouse_id,
            product_id=key.product_id,
            location_id=key.location_id,
            batch_no=key.batch_no,
            production_date=key.production_date,
            expiry_date=key.expiry_date,
            serial_no=key.serial_no,
        )

        locked_detail = details_by_key[_detail_dimension_key(dimension)]
        is_pos_group = key.task_line_id is not None or key.source_detail_id is not None
        if is_pos_group:
            if key.task_line_id is None or key.source_detail_id is None:
                raise ValidationError("POS PICK 聚合缺少任务行或原库存层")
            if locked_detail.id != key.source_detail_id:
                raise ValidationError(
                    f"POS PICK 原库存层 {key.source_detail_id} 已变化或不再可用"
                )
            if not locked_detail.is_active:
                raise ValidationError(f"POS PICK 原库存层 {locked_detail.id} 已停用")
            qty_abs = abs(qty)
            if locked_detail.onhand_qty < qty_abs:
                raise ValidationError(
                    f"POS PICK 原库存层 {locked_detail.id} 账面库存不足"
                )
            if locked_detail.allocated_qty < qty_abs:
                raise ValidationError(
                    f"POS PICK 原库存层 {locked_detail.id} 预占数量不足"
                )

        # 明细增量（出库为负数，进库为正数；COUNT 的 ADJ_* 同理）
        posted_detail = _upsert_detail(
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
            task=task,
            detail=locked_detail,
        )
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
            src_line_id=key.task_line_id if is_pos_group else None,
            memo="POS_SALE" if is_pos_group else key.tx_type,
            pair_id=None,
            posted_at=now,
            posting_batch=batch_no,
            subwarehouse_id=(posted_detail.subwarehouse_id if is_pos_group else None),
            zone_type=(posted_detail.zone_type if is_pos_group else None),
            src_no=(task.ref_no if is_pos_group else None),
        )
        ctx, ctx_text = build_log_payload(task=task, posting_batch=batch_no)
        logger.info(
            "inventory.receive_like.applied %s tx_type=%s product_id=%s location_id=%s qty=%s",
            ctx_text,
            key.tx_type,
            key.product_id,
            key.location_id,
            qty,
            extra=ctx,
        )
        created += 1
    _refresh_summaries(
        touched_pairs,
        locked_summaries=locked_summaries,
        locked_details=details_by_key,
    )
    return created


def _apply_putaway(task: WmsTask, groups: Dict[Tuple[_AggKey, _AggKey], Decimal], *, now, batch_no: str) -> int:
    """
    对“上架/移库”的聚合结果逐条入账：
    - 每个分组（同一条路径 from→to） → 两条交易：MOVE_OUT(-qty) + MOVE_IN(+qty)，用 pair_id 关联。
    - 同时更新两个库位的库存明细。
    """
    created = 0
    task_type = task.task_type
    pending_groups = [
        ((key_out, key_in), _q4(qty_pos))
        for (key_out, key_in), qty_pos in groups.items()
        if _q4(qty_pos) != 0
    ]

    location_ids = sorted(
        {
            key.location_id
            for (key_out, key_in), _qty in pending_groups
            for key in (key_out, key_in)
        }
    )
    locations = {
        location.id: location
        for location in Location.objects.select_for_update()
        .filter(id__in=location_ids)
        .order_by("id")
    }
    if len(locations) != len(location_ids):
        raise ValidationError("上架来源或目标库位不存在。")
    for location in locations.values():
        if location.warehouse_id != task.warehouse_id:
            raise ValidationError("上架库位必须属于任务仓库。")
        if location.is_disabled or location.is_frozen:
            raise ValidationError(f"库位 {location.code} 已停用或冻结，不能过账。")

    serial_routes: Dict[Tuple[int, int, str], Tuple[int, int, Decimal]] = {}
    for (key_out, key_in), qty in pending_groups:
        serial = (key_out.serial_no or "").strip().upper()
        if not serial:
            continue
        serial_key = (key_out.owner_id, key_out.product_id, serial)
        route = (key_out.location_id, key_in.location_id, _q4(qty))
        if serial_key in serial_routes:
            raise ValidationError(f"序列号 {serial} 在同一任务中存在重复上架路径。")
        if route[0] == route[1]:
            raise ValidationError("序列号上架的来源和目标库位不能相同。")
        if route[2] != Decimal("1.0000"):
            raise ValidationError("序列号商品必须整件一次性上架，数量必须为1。")
        serial_routes[serial_key] = route

    dimensions = []
    for (key_out, key_in), _qty in pending_groups:
        keys = (key_out,) if key_out.serial_no else (key_out, key_in)
        for key in keys:
            dimensions.append(
                _detail_dimension_values(
                    owner_id=key.owner_id,
                    warehouse_id=key.warehouse_id,
                    product_id=key.product_id,
                    location_id=key.location_id,
                    batch_no=key.batch_no,
                    production_date=key.production_date,
                    expiry_date=key.expiry_date,
                    serial_no=key.serial_no,
                )
            )
    details_by_key, locked_summaries = _lock_inventory_dimensions(dimensions)
    touched_pairs: Set[Tuple[int, int]] = {
        (key_out.owner_id, key_out.product_id)
        for (key_out, _key_in), _qty in pending_groups
    }

    def move_sort(item):
        (key_out, key_in), _qty = item
        out_values = _detail_dimension_values(
            owner_id=key_out.owner_id,
            warehouse_id=key_out.warehouse_id,
            product_id=key_out.product_id,
            location_id=key_out.location_id,
            batch_no=key_out.batch_no,
            production_date=key_out.production_date,
            expiry_date=key_out.expiry_date,
            serial_no=key_out.serial_no,
        )
        in_values = _detail_dimension_values(
            owner_id=key_in.owner_id,
            warehouse_id=key_in.warehouse_id,
            product_id=key_in.product_id,
            location_id=key_in.location_id,
            batch_no=key_in.batch_no,
            production_date=key_in.production_date,
            expiry_date=key_in.expiry_date,
            serial_no=key_in.serial_no,
        )
        return (
            _dimension_sort_key(_detail_dimension_key(out_values)),
            _dimension_sort_key(_detail_dimension_key(in_values)),
        )

    for (key_out, key_in), qty_pos in sorted(pending_groups, key=move_sort):

        # 先 OUT（发出库位 onhand -= qty_pos）
        # 这里生成本对 OUT/IN 的 pair_id（字符串，满足 _insert_tx 的类型注解）
        pair = str(uuid4())

        if key_out.serial_no:
            from allapp.tasking.counting import assert_inventory_not_count_locked

            assert_inventory_not_count_locked(
                owner_id=key_out.owner_id,
                warehouse_id=key_out.warehouse_id,
                product_id=key_out.product_id,
                location_id=key_out.location_id,
                batch_no=key_out.batch_no,
                task=task,
            )
            assert_inventory_not_count_locked(
                owner_id=key_in.owner_id,
                warehouse_id=key_in.warehouse_id,
                product_id=key_in.product_id,
                location_id=key_in.location_id,
                batch_no=key_in.batch_no,
                task=task,
            )
            source_key = _detail_dimension_key(
                _detail_dimension_values(
                    owner_id=key_out.owner_id,
                    warehouse_id=key_out.warehouse_id,
                    product_id=key_out.product_id,
                    location_id=key_out.location_id,
                    batch_no=key_out.batch_no,
                    production_date=key_out.production_date,
                    expiry_date=key_out.expiry_date,
                    serial_no=key_out.serial_no,
                )
            )
            source_detail = details_by_key[source_key]
            if task_type == WmsTask.TaskType.REPLEN and (
                not key_out.source_detail_id
                or source_detail.id != key_out.source_detail_id
            ):
                raise ValidationError("补货来源库存层已发生变化，请重新规划任务。")
            if not source_detail.product_serial_control:
                raise ValidationError("带序列号的上架记录必须对应序列号管理商品。")
            if (source_detail.serial_no or "").upper() != (
                key_out.serial_no or ""
            ).upper():
                raise ValidationError("来源库存序列号与上架记录不一致。")
            if source_detail.location_id != key_out.location_id:
                raise ValidationError("序列号来源库存位置已发生变化，请重新执行任务。")
            if _q4(source_detail.onhand_qty) != Decimal("1.0000"):
                raise ValidationError("序列号来源库存数量必须为1。")
            if any(
                _q4(value) != 0
                for value in (
                    source_detail.allocated_qty,
                    source_detail.locked_qty,
                    source_detail.damaged_qty,
                )
            ):
                raise ValidationError("序列号库存已分配、锁定或损坏，不能上架。")

            destination = locations[key_in.location_id]
            source_detail.location = destination
            source_detail.warehouse_id = destination.warehouse_id
            source_detail.subwarehouse_id = destination.subwarehouse_id
            source_detail.zone_type = destination.zone_type
            source_detail.save(
                update_fields=[
                    "location",
                    "warehouse",
                    "subwarehouse",
                    "zone_type",
                    "base_unit",
                    "product_serial_control",
                    "serial_no",
                    "serial_no_norm",
                    "available_qty",
                    "updated_at",
                ]
            )
            _insert_tx(
                tx_type=key_out.tx_type,
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
                src_line_id=key_out.task_line_id,
                memo=task_type,
                pair_id=pair,
                posted_at=now,
                posting_batch=batch_no,
            )
            _insert_tx(
                tx_type=key_in.tx_type,
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
                src_line_id=key_in.task_line_id,
                memo=task_type,
                pair_id=pair,
                posted_at=now,
                posting_batch=batch_no,
            )
            created += 2
            continue

        source_values = _detail_dimension_values(
            owner_id=key_out.owner_id,
            warehouse_id=key_out.warehouse_id,
            product_id=key_out.product_id,
            location_id=key_out.location_id,
            batch_no=key_out.batch_no,
            production_date=key_out.production_date,
            expiry_date=key_out.expiry_date,
            serial_no=key_out.serial_no,
        )
        source_detail = details_by_key[_detail_dimension_key(source_values)]
        if task_type == WmsTask.TaskType.REPLEN:
            if not key_out.source_detail_id or source_detail.id != key_out.source_detail_id:
                raise ValidationError("补货来源库存层已发生变化，请重新规划任务。")
            if any(
                _q4(value) != 0
                for value in (
                    source_detail.allocated_qty,
                    source_detail.locked_qty,
                    source_detail.damaged_qty,
                )
            ):
                raise ValidationError("补货来源库存已分配、锁定或损坏，请重新规划任务。")
            if _q4(source_detail.available_qty) < qty_pos:
                raise ValidationError("补货来源可用库存已发生变化，请重新规划任务。")

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
            task_type=task_type,
            task=task,
            detail=source_detail,
        )
        _insert_tx(
            tx_type=key_out.tx_type,
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
            src_line_id=key_out.task_line_id,
            memo=task_type,
            pair_id=pair,
            posted_at=now,
            posting_batch=batch_no,
        )
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
            task_type=task_type,
            task=task,
            detail=details_by_key[
                _detail_dimension_key(
                    _detail_dimension_values(
                        owner_id=key_in.owner_id,
                        warehouse_id=key_in.warehouse_id,
                        product_id=key_in.product_id,
                        location_id=key_in.location_id,
                        batch_no=key_in.batch_no,
                        production_date=key_in.production_date,
                        expiry_date=key_in.expiry_date,
                        serial_no=key_in.serial_no,
                    )
                )
            ],
        )
        _insert_tx(
            tx_type=key_in.tx_type,
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
            src_line_id=key_in.task_line_id,
            memo=task_type,
            pair_id=pair,
            posted_at=now,
            posting_batch=batch_no,
        )
        ctx, ctx_text = build_log_payload(task=task, posting_batch=batch_no)
        logger.info(
            "inventory.putaway.applied_pair %s from_location_id=%s to_location_id=%s product_id=%s qty=%s pair_id=%s",
            ctx_text,
            key_out.location_id,
            key_in.location_id,
            key_out.product_id,
            qty_pos,
            pair,
            extra=ctx,
        )
        created += 2
    _refresh_summaries(
        touched_pairs,
        locked_summaries=locked_summaries,
        locked_details=details_by_key,
    )
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
    1) 依次锁任务、任务级 PostingJournal，并在锁内校验幂等状态。
    2) 解析显式任务类型策略；未支持类型直接失败。
    3) 按传入 ID 重取并锁定扫描，严格校验归属与可过账状态。
    4) 聚合并写 InventoryDetail / InventoryTransaction。
    5) 精确打点扫描；更新数量不一致则回滚。
    6) 回填任务 posting_status=POSTED；PJ 置 POSTED 并记录批号。
    """
    # 1) 任务 + PJ：统一锁序并在锁内判定幂等/状态一致性
    task = _lock_task(task.id)
    pj = _lock_journal("WmsTask", task.id, "POST")
    ctx, ctx_text = build_log_payload(task=task, user=user, journal=pj)
    logger.info("inventory.post_task.begin %s", ctx_text, extra=ctx)

    posted_status = getattr(
        getattr(WmsTask, "PostingStatus", None), "POSTED", "POSTED"
    )
    task_is_posted = task.posting_status == posted_status
    journal_is_posted = pj.status == "POSTED"
    if task_is_posted and journal_is_posted:
        logger.info("inventory.post_task.already_posted %s", ctx_text, extra=ctx)
        return {
            "ok": True,
            "affected_tx_count": 0,
            "batch_no": pj.message or "",
            "message": "already POSTED",
        }
    if task.posting_status != pj.status:
        logger.warning(
            "inventory.post_task.inconsistent_posting_state %s task_status=%s journal_status=%s",
            ctx_text,
            task.posting_status,
            pj.status,
            extra=ctx,
        )
        raise ValidationError("任务与过账日记账状态不一致，拒绝继续过账。")

    retryable_statuses = {
        getattr(WmsTask.PostingStatus, "PENDING", "PENDING"),
        getattr(WmsTask.PostingStatus, "FAILED", "FAILED"),
    }
    if task.posting_status not in retryable_statuses:
        raise ValidationError(
            f"过账状态 {task.posting_status} 不允许执行库存过账。"
        )

    # 与盘点发布使用同一仓库互斥锁，保证“刷新快照并落范围锁”与库存变更串行。
    Warehouse.objects.select_for_update().get(pk=task.warehouse_id)

    ok, why = _can_post(task)
    if not ok:
        raise ValidationError(why)

    # 2) 任务类型必须命中显式库存策略
    task_type = getattr(task, "task_type", "") or ""
    posting_family = _POSTING_FAMILY_BY_TASK_TYPE.get(task_type)
    if posting_family is None:
        logger.warning(
            "inventory.post_task.unsupported_task_type %s task_type=%s",
            ctx_text,
            task_type or "<empty>",
            extra=ctx,
        )
        raise ValidationError(
            f"不支持的库存过账任务类型：{task_type or '<空>'}"
        )

    # 3) 重取并锁定调用方明确提供的扫描
    now_ts = now or timezone.now()
    batch = batch_no or now_ts.strftime("%Y%m%d-%H%M%S")
    pj_ctx, pj_text = build_log_payload(task=task, user=user, journal=pj, posting_batch=batch)
    try:
        scans = _lock_and_validate_scans(task=task, scans=scans)
    except ValidationError:
        logger.warning("inventory.post_task.invalid_scans %s", pj_text, extra=pj_ctx)
        raise
    logger.info(
        "inventory.post_task.scans_loaded %s scan_count=%s",
        pj_text,
        len(scans),
        extra=pj_ctx,
    )

    # 4) 聚合 + 入账
    affected = 0
    if posting_family == "RECEIVE":
        groups = _group_receive_like(
            task,
            scans,
            now=now_ts,
            batch_no=batch,
            tx_type=InvTxType.RECEIVE,
            qty_task_type=task_type,
        )
        affected = _apply_receive_like(task, groups, now=now_ts, batch_no=batch)

    elif posting_family == "MOVE":
        groups = _group_putaway(
            task,
            scans,
            now=now_ts,
            batch_no=batch,
            qty_task_type=task_type,
        )
        affected = _apply_putaway(task, groups, now=now_ts, batch_no=batch)

    elif posting_family == "ISSUE":
        groups = _group_receive_like(
            task,
            scans,
            now=now_ts,
            batch_no=batch,
            tx_type=InvTxType.ISSUE,
            qty_task_type=task_type,
        )
        affected = _apply_receive_like(task, groups, now=now_ts, batch_no=batch)

    elif posting_family in ("COUNT", "ADJUST"):
        # COUNT 读取 qty_base；ADJUST 读取 qty_base_delta。两者均按符号分录。
        pos_scans = []
        neg_scans = []
        for s in scans:
            q = _qty_for_type(task_type, s)
            if q > 0:
                pos_scans.append(s)
            elif q < 0:
                neg_scans.append(s)
            # 仅 COUNT 允许 q == 0，表示无差异。

        if pos_scans:
            groups_pos = _group_receive_like(
                task,
                pos_scans,
                now=now_ts,
                batch_no=batch,
                tx_type=InvTxType.ADJ_GAIN,
                qty_task_type=task_type,
            )
            affected += _apply_receive_like(
                task, groups_pos, now=now_ts, batch_no=batch
            )
        if neg_scans:
            groups_neg = _group_receive_like(
                task,
                neg_scans,
                now=now_ts,
                batch_no=batch,
                tx_type=InvTxType.ADJ_LOSS,
                qty_task_type=task_type,
            )
            affected += _apply_receive_like(
                task, groups_neg, now=now_ts, batch_no=batch
            )

    if affected <= 0 and posting_family != "COUNT":
        raise ValidationError("库存过账未生成任何交易，拒绝提交成功状态。")

    # 5) 扫描精确打点；任何并发状态变化都必须使整笔过账回滚
    scan_ids = [scan.id for scan in scans]
    status_ok = getattr(TaskScanLog.ScanStatus, "OK", "OK")
    rejected = getattr(TaskScanLog.ReviewStatus, "REJECTED", "REJECTED")
    updated_scan_count = (
        TaskScanLog.objects.filter(
            pk__in=scan_ids,
            task_id=task.id,
            status=status_ok,
            posted_at__isnull=True,
            posting_journal_id__isnull=True,
            posting_batch__isnull=True,
        )
        .exclude(review_status=rejected)
        .update(
            posted_at=now_ts,
            posting_batch=batch,
            posting_journal_id=pj.id,
        )
    )
    if updated_scan_count != len(scan_ids):
        raise ValidationError("扫描打点数量与锁定候选数量不一致，过账已回滚。")

    # 6) 回填任务状态 & 提交 PJ
    try:
        task.posting_status = posted_status
        task.save(update_fields=["posting_status"])
    except Exception:
        logger.exception("inventory.post_task.task_status_update_failed %s", pj_text, extra=pj_ctx)
        raise  # 重新抛出异常，让事务回滚

    pj.status = "POSTED"
    pj.message = f"{batch}"
    pj.attempt_count = (pj.attempt_count or 0) + 1
    pj.save(update_fields=["status", "message", "attempt_count"])
    logger.info("inventory.post_task.completed %s affected_tx_count=%s", pj_text, affected, extra=pj_ctx)

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
