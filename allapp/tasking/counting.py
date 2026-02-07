# allapp/tasking/services/counting.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple, List

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location, Warehouse
from allapp.baseinfo.models import Owner
from allapp.tasking.models import WmsTask, WmsTaskLine


@dataclass
class CountScopeParams:
    warehouse_id: int
    subwarehouse_id: int
    owner_id: int
    # 细化筛选（均为可选）
    # zone_id: Optional[int] = None
    zone_type: Optional[int] = None  # Optional[int] 表示该字段为可选，存储 ZoneType 的枚举值
    location_id: Optional[int] = None
    location_prefix: Optional[str] = None  # 例如 “A-01-” 前缀
    product_id: Optional[int] = None
    batch_no: Optional[str] = None  # 若你的 InventoryDetail 有 batch 字段
    lpn: Optional[str] = None       # 若你的明细或容器存在 LPN / container_no
    exclude_zero_onhand: bool = True
    max_lines: int = 100000


def _has_field(model, name: str) -> bool:
    return any(getattr(f, "attname", getattr(f, "name", None)) == name or getattr(f, "name", None) == name
               for f in model._meta.get_fields())


def _apply_optional_filters(qs, p: CountScopeParams, notes: List[str]):
    """对 InventoryDetail 的 QuerySet 施加可选过滤；不存在的字段自动忽略并记录 notes。"""
    # —— owner/warehouse 基本范围
    qs = qs.filter(warehouse_id=p.warehouse_id, owner_id=p.owner_id)

    # —— zone 过滤
    if p.zone_tpye:
        if _has_field(InventoryDetail, "zone_tpye"):
            qs = qs.filter(zone_tpye=p.zone_tpye)


    if p.subwarehouse_id:
        if _has_field(InventoryDetail, "subwarehouse_id"):
            qs = qs.filter(subwarehouse_id=p.subwarehouse_id)

    # —— location 精确选择
    if p.location_id:
        if _has_field(InventoryDetail, "location_id"):
            qs = qs.filter(location_id=p.location_id)
        else:
            notes.append("InventoryDetail 无 location_id，忽略“指定库位”过滤。")

    # —— location 前缀（优先匹配 Location.code，其次 name）
    if p.location_prefix:
        if _has_field(InventoryDetail, "location_id"):
            q_code = Q(location__code__istartswith=p.location_prefix)
            q_name = Q(location__name__istartswith=p.location_prefix)
            qs = qs.filter(q_code | q_name)
        else:
            notes.append("InventoryDetail 无 location_id，忽略“库位前缀”过滤。")

    # —— SKU 过滤
    if p.product_id:
        if _has_field(InventoryDetail, "product_id"):
            qs = qs.filter(product_id=p.product_id)
        else:
            notes.append("InventoryDetail 无 product_id，忽略 SKU 过滤。")

    # —— 批次过滤（按你库里的字段：优先 batch_no，其次 lot、lot.batch_no）
    if p.batch_no:
        normalized = (p.batch_no or "").strip().upper()
        applied = False
        for path in ["batch_no__iexact", "batch__iexact", "lot__batch_no__iexact", "lot__code__iexact", "lot__name__iexact"]:
            try:
                qs = qs.filter(**{path: normalized})
                applied = True
                break
            except Exception:
                continue
        if not applied:
            notes.append("未找到适配的批次字段（batch_no/lot.batch_no 等），已忽略批次过滤。")

    # —— LPN / 容器过滤（尝试若干常见字段）
    if p.lpn:
        applied = False
        candidate_fields = [
            "lpn__iexact",
            "lpn_no__iexact",
            "container_no__iexact",
            "container_code__iexact",
            "container__code__iexact",
            "container__no__iexact",
            "container__lpn__iexact",
        ]
        for path in candidate_fields:
            try:
                qs = qs.filter(**{path: p.lpn})
                applied = True
                break
            except Exception:
                continue
        if not applied:
            notes.append("未找到适配的 LPN/容器字段（例如 container_no/lpn），已忽略 LPN 过滤。")

    # —— 过滤 onhand = 0
    if p.exclude_zero_onhand and _has_field(InventoryDetail, "onhand_qty"):
        qs = qs.filter(onhand_qty__gt=0)

    return qs


@transaction.atomic
def create_lines_from_scope(
    *,
    created_by,
    owner_id: int,
    warehouse_id: int,
    zone_type: Optional[int] = None,
    location_id: Optional[int] = None,
    location_prefix: Optional[str] = None,
    product_id: Optional[int] = None,
    batch_no: Optional[str] = None,
    lpn: Optional[str] = None,
    exclude_zero_onhand: bool = True,
    max_lines: int = 1000,
    task_remark: Optional[str] = None,
) -> Tuple[Optional[WmsTask], int, bool, List[str]]:
    """
    根据筛选条件：创建一个 COUNT 任务（DRAFT）并批量生成盘点行。
    返回：(task, created_count, truncated, notes)

    - 若未匹配到任何明细：不创建任务头，返回 (None, 0, False, notes)
    - truncated=True 表示行数被 max_lines 截断
    """
    # 1) 预取校验（尽量避免跨 owner/warehouse 错误）
    Warehouse.objects.only("id").get(id=warehouse_id)
    Owner.objects.only("id").get(id=owner_id)

    # 2) 构建查询
    params = CountScopeParams(
        warehouse_id=warehouse_id,
        subwarehouse_id=subwarehouse_id,
        owner_id=owner_id,
        zone_type=zone_type,
        location_id=location_id,
        location_prefix=location_prefix,
        product_id=product_id,
        batch_no=batch_no,
        lpn=lpn,
        exclude_zero_onhand=exclude_zero_onhand,
        max_lines=max_lines,
    )
    notes: List[str] = []

    qs = InventoryDetail.objects.all()
    qs = _apply_optional_filters(qs, params, notes)

    # 截断保护
    details = list(qs.select_related("location", "product").order_by("id")[: max_lines + 1])
    if not details:
        return None, 0, False, notes

    truncated = len(details) > max_lines
    if truncated:
        details = details[:max_lines]

    # 3) 创建任务头（DRAFT, COUNT）
    tt = getattr(WmsTask.TaskType, "COUNT", "COUNT")
    st = getattr(WmsTask.Status, "DRAFT", "DRAFT")
    task = WmsTask(
        task_type=tt,
        status=st,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        created_by=created_by,
        remark=(task_remark or "盘点向导自动生成"),
    )
    # 若你的模型有 biz_date 字段，这里可安全地回填今天
    if hasattr(task, "biz_date") and not getattr(task, "biz_date"):
        task.biz_date = date.today()
    task.save()  # 触发 task_no 生成逻辑（若在 save()/signals 中实现）

    # 4) 批量生成行
    lines: List[WmsTaskLine] = []
    for d in details:
        lines.append(WmsTaskLine(
            task=task,
            product_id=getattr(d, "product_id", None),
            from_location_id=getattr(d, "location_id", None),  # COUNT 用 from_location 作为被盘库位
            qty_plan=getattr(d, "onhand_qty", 0),
            qty_done=0,
            src_model="InventoryDetail",
            src_id=d.id,
        ))
    WmsTaskLine.objects.bulk_create(lines, batch_size=500)

    return task, len(lines), truncated, notes
