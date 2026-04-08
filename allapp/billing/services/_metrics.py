# allapp/billing/services/_metrics.py
"""
计费指标构建模块 — 四种核心计量类型的计算、存储与管理。

本模块负责仓储计费系统中四类日度指标的全生命周期处理：

1. PALLET（托盘/库位占用数）
   计算当日库存中 onhand_qty > 0 的 **不重复库位数量**，用于按库位收取仓储费。
   数据来源：历史日期读快照表；当天读实时 InventoryDetail。

2. CBM（立方米体积）
   对所有在库 SKU 执行 Sum(onhand_qty × unit_volume_m3)，用于按体积收费。
   当天：从 product.volume 或 ProductPackage 推算单位体积；
   历史：直接读快照中预存的 unit_volume_m3_snapshot 字段。

3. AREA_M2（占用面积，平方米）
   对当日所有有货库位求面积之和（Sum(distinct location_area_m2)）。
   若无面积主数据且 allow_area_fallback=True，则降级使用占用库位数代替。
   支持通过 settings.BILLING_AREA_M2_METRIC_RESOLVER 注入自定义实现。

4. ORDER_AMT（出库订单金额）
   对指定 biz_date 内已提交（SUBMITTED）且非取消的出库订单行求和：
     优先使用 final_line_amount；若为 0 则回退 base_qty × base_price。

模块职责划分：
  - _load_metric_resolver / _normalize_metric_payload：插件机制与数据标准化
  - _inventory_metric_rows 及相关帮助函数：库存数据源路由（实时 vs 快照）
  - _build_*_metric：四种指标的具体计算逻辑
  - _default_metric_payload / _auto_metric_types：调度入口与类型过滤
  - _store_generated_metric：指标持久化，含竞态恢复与手工值保护
"""
import importlib
from decimal import Decimal
from typing import Dict, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import (
    Case, DecimalField, ExpressionWrapper, F, Prefetch, Sum, When,
)
from django.db.utils import IntegrityError
from django.utils import timezone

from allapp.billing.enums import MetricType
from allapp.billing.models import BillingMetricDaily
from allapp.outbound.models import OutboundOrderLine

from ._common import AUTO_METRIC_SOURCE_PREFIX, _q, logger


# ======================== 指标解析器插件机制 ======================== #
# 允许业务方通过 Django settings 注入自定义的指标计算函数，
# 而无需修改本模块代码，符合开闭原则（对扩展开放、对修改关闭）。

def _load_metric_resolver(metric_type: str):
    """
    从 Django settings 动态加载自定义指标解析器（可选插件）。

    settings 键名格式：BILLING_{METRIC_TYPE}_METRIC_RESOLVER
    值格式：          "myapp.billing.resolvers:custom_cbm_resolver"
                      （Python 模块路径 + 冒号 + 函数名）

    设计决策：
    - 使用 importlib 延迟导入，避免循环依赖和启动时加载过多模块。
    - 若 settings 中未配置对应键，则直接返回 None，调用方负责降级处理。
    - 解析器函数签名应与内置 _build_*_metric 保持一致：
        fn(owner_id, warehouse_id, service_date, **kwargs) -> dict | None

    参数：
        metric_type: MetricType 枚举值字符串，如 "PALLET"、"CBM" 等。

    返回：
        可调用对象（自定义解析器），或 None（未配置时）。
    """
    path = getattr(settings, f"BILLING_{metric_type}_METRIC_RESOLVER", None)
    if not path:
        return None
    mod, func = path.split(":")
    return getattr(importlib.import_module(mod), func)


# ======================== 指标载荷规范化 ======================== #
# 自定义解析器可能返回 dict、tuple 或裸数值等多种格式，
# 本函数将其统一转换为内部标准结构，降低后续处理的复杂度。

def _normalize_metric_payload(metric_type: str, payload, default_source: str, default_note: str = ""):
    """
    将自定义解析器返回的多种格式统一规范化为内部标准字典。

    支持的输入格式：
    - None           → 返回 None（表示该指标无有效数据，跳过存储）
    - dict           → 读取 "value"、"source"（可选）、"note"（可选）键
    - tuple(3)       → (value, source, note)
    - tuple(2)       → (value, source)，note 使用默认值
    - tuple(1)       → (value,)，source 和 note 均使用默认值
    - 其他标量       → 直接作为 value，source/note 使用默认值

    规范化后的输出结构（若 value 非 None）：
    {
        "metric_type": str,     # 指标类型标识
        "value":       Decimal, # 精度统一为 0.0001（4位小数）
        "source":      str,     # 数据来源标记
        "note":        str,     # 备注说明
    }

    参数：
        metric_type:    指标类型标识字符串。
        payload:        解析器返回的原始载荷。
        default_source: value 有效但 payload 未提供 source 时的默认来源。
        default_note:   value 有效但 payload 未提供 note 时的默认备注。

    返回：
        标准化后的 dict，或 None（payload 为 None 或 value 为 None 时）。

    异常：
        ValueError：tuple 长度为 0 时抛出，视为不合法载荷。
    """
    if payload is None:
        return None

    if isinstance(payload, dict):
        value = payload.get("value")
        source = payload.get("source", default_source)
        note = payload.get("note", default_note)
    elif isinstance(payload, tuple):
        if len(payload) == 3:
            value, source, note = payload
        elif len(payload) == 2:
            value, source = payload
            note = default_note
        elif len(payload) == 1:
            value = payload[0]
            source = default_source
            note = default_note
        else:
            raise ValueError(f"Unsupported metric payload tuple for {metric_type}: {payload!r}")
    else:
        # 裸标量（int、float、Decimal 等），直接使用默认的 source 和 note
        value = payload
        source = default_source
        note = default_note

    if value is None:
        return None

    return {
        "metric_type": metric_type,
        "value": _q(Decimal(value), "0.0001"),  # 统一精度到 4 位小数
        "source": source,
        "note": note or "",
    }


# ======================== 库存数据源路由帮助函数 ======================== #
#
# 核心设计原则：
#   - 当天（today）：使用实时库存表（InventoryDetail），可反映最新状态。
#   - 历史日期（< today）：使用日度快照表（InventorySnapshotDaily）。
#     若快照不存在，则自动触发快照生成，然后再次查询。
#
# 这样可以保证：历史数据幂等可重算；当天数据反映实时库存。

def _current_inventory_metric_rows(owner_id, warehouse_id):
    """
    从实时库存明细表（InventoryDetail）查询当前有货库存行。

    查询条件：
    - is_active=True：仅包含有效库存记录。
    - onhand_qty__gt=0：排除零库存行，避免干扰指标计算。

    预加载策略（select_related + prefetch_related）：
    - select_related("product", "location")：通过 JOIN 减少 N+1 查询。
    - prefetch_related("product__packages")：关联包装规格，用于 CBM 计算时的
      体积回退逻辑。packages 按 is_sales_default DESC, is_pickable DESC,
      sort_order ASC, id ASC 排序，确保回退时优先选取最合适的包装规格。

    参数：
        owner_id:      货主 ID。
        warehouse_id:  仓库 ID。

    返回：
        InventoryDetail 实例列表（已在内存中评估的 QuerySet）。
    """
    from allapp.inventory.models import InventoryDetail
    from allapp.products.models import ProductPackage

    return list(
        InventoryDetail.objects
        .filter(owner_id=owner_id, warehouse_id=warehouse_id, is_active=True, onhand_qty__gt=0)
        .select_related("product", "location")
        .prefetch_related(
            Prefetch(
                "product__packages",
                queryset=ProductPackage.objects.order_by("-is_sales_default", "-is_pickable", "sort_order", "id"),
            )
        )
    )


def _snapshot_inventory_metric_rows(owner_id, warehouse_id, service_date):
    """
    从库存日度快照表（InventorySnapshotDaily）查询指定日期的历史库存行。

    快照表的优势：
    - 字段 unit_volume_m3_snapshot 已记录快照时刻的商品单位体积，
      CBM 计算无需再查询商品主数据，避免商品维度改变导致历史数据失真。
    - 字段 location_area_m2_snapshot 已记录快照时刻的库位面积，
      AREA_M2 计算同理，保证历史数据的可重现性。

    查询条件：
    - snapshot_date=service_date：精确匹配日期。
    - onhand_qty__gt=0：只取有货行。

    参数：
        owner_id:      货主 ID。
        warehouse_id:  仓库 ID。
        service_date:  服务日期（date 类型）。

    返回：
        InventorySnapshotDaily 实例列表。若该日期快照不存在，返回空列表。
    """
    from allapp.inventory.models import InventorySnapshotDaily

    return list(
        InventorySnapshotDaily.objects
        .filter(
            snapshot_date=service_date,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            onhand_qty__gt=0,
        )
        .select_related("product", "location")
    )


def _ensure_inventory_snapshot_for_date(owner_id, warehouse_id, service_date):
    """
    确保指定日期的库存快照存在；若不存在则触发自动生成。

    调用时机：_inventory_metric_rows 发现历史日期的快照为空时，
    调用本函数生成快照，随后再次查询，保证历史指标可以补算。

    设计决策：
    - 快照生成操作委托给 inventory.snapshot_services，隔离职责。
    - 若对应日期已有快照，generate_inventory_snapshot_for_date 内部应具备
      幂等性（不重复生成），避免数据重复。

    参数：
        owner_id:      货主 ID。
        warehouse_id:  仓库 ID。
        service_date:  需要确保快照的日期（date 类型）。

    返回：
        由 generate_inventory_snapshot_for_date 决定（通常为快照记录数或状态）。
    """
    from allapp.inventory.snapshot_services import generate_inventory_snapshot_for_date

    return generate_inventory_snapshot_for_date(
        service_date,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )


def _inventory_rows_use_snapshot(service_date, rows=None) -> bool:
    """
    判断给定服务日期是否应当使用快照数据。

    判断逻辑：service_date < today → 历史日期 → 使用快照。

    参数：
        service_date: 服务日期（date 类型）。
        rows:         当前已取到的库存行（此参数目前未使用，
                      保留以便将来扩展，例如基于行内容动态判断数据来源）。
                      注意：del rows 用于明确抑制 IDE 的"未使用参数"警告。

    返回：
        True  → 应使用快照数据（历史日期）。
        False → 应使用实时数据（当天）。
    """
    del rows  # 参数预留，当前逻辑仅依赖日期判断，显式丢弃以消除 lint 警告
    return service_date < timezone.now().date()


def _inventory_metric_rows(owner_id, warehouse_id, service_date):
    """
    根据服务日期自动路由，返回用于指标计算的库存行列表。

    路由策略（核心数据源切换逻辑）：

    1. service_date == today（当天）：
       → 直接读实时库存（InventoryDetail），无快照操作。

    2. service_date < today（历史日期）：
       → 优先读快照（InventorySnapshotDaily）。
       → 若快照为空（可能是历史补算或快照任务失败）：
           a. 调用 _ensure_inventory_snapshot_for_date 自动生成快照。
           b. 再次查询快照表并返回结果。

    这种"读 → 缺失则生成 → 再读"的模式保证了历史补算的可靠性，
    但要求 generate_inventory_snapshot_for_date 具备幂等性。

    参数：
        owner_id:      货主 ID。
        warehouse_id:  仓库 ID。
        service_date:  服务日期（date 类型）。

    返回：
        InventoryDetail 或 InventorySnapshotDaily 实例列表，
        具体类型取决于日期路由结果。
    """
    today = timezone.now().date()
    if service_date < today:
        # 历史日期：优先使用快照，避免历史计费数据受当前库存变动影响
        rows = _snapshot_inventory_metric_rows(owner_id, warehouse_id, service_date)
        if rows:
            return rows
        # 快照缺失：触发自动生成后重新查询（可能因快照任务未运行或手动补算触发）
        _ensure_inventory_snapshot_for_date(owner_id, warehouse_id, service_date)
        return _snapshot_inventory_metric_rows(owner_id, warehouse_id, service_date)

    # 当天：使用实时库存，反映最新状态
    return _current_inventory_metric_rows(owner_id, warehouse_id)


def _resolve_product_unit_volume(product, cache: Dict[int, Optional[Decimal]]) -> Optional[Decimal]:
    """
    解析单个商品的单位体积（m³/基本单位），并写入内存缓存。

    体积解析优先级（两级回退）：
    1. product.volume：商品主档直接记录的体积字段（最高优先级）。
    2. ProductPackage 回退：遍历 product.packages（已按优先级排序），
       取第一个同时具备 volume_m3 和 qty_in_base 字段的包装规格，
       计算 volume_m3 / qty_in_base 作为单位体积近似值。

    缓存设计：
    - 使用 Dict[product_id → Optional[Decimal]] 在单次指标计算调用内
      避免对同一商品重复解析。
    - 缓存 None 值：若商品无任何体积信息，也会写入 None，
      后续调用直接返回 None 而非再次遍历包装规格。

    参数：
        product: 已 select_related/prefetch_related 的商品对象。
        cache:   调用方持有的 {product_id: unit_volume} 字典。

    返回：
        Decimal（单位体积），或 None（无体积数据时）。
    """
    if product.id in cache:
        return cache[product.id]

    # 优先使用商品主档中的 volume 字段
    unit_volume = Decimal(product.volume) if getattr(product, "volume", None) is not None else None
    if unit_volume is None:
        # 回退：从已排序的包装规格中取第一条有效记录推算单位体积
        for pkg in product.packages.all():
            pkg_volume = getattr(pkg, "volume_m3", None)
            qty_in_base = getattr(pkg, "qty_in_base", None)
            if pkg_volume is None or not qty_in_base:
                continue
            unit_volume = Decimal(pkg_volume) / Decimal(qty_in_base)
            break

    cache[product.id] = unit_volume
    return unit_volume


# ======================== 四种指标计算函数 ======================== #

def _build_pallet_metric(owner_id, warehouse_id, service_date, *, inventory_rows=None, **kwargs):
    """
    计算 PALLET 指标：当日占用的不重复库位数。

    业务含义：
        仓库通常按"占用库位数"收取仓储费，每个有货的库位计一个计费单元。
        本指标统计当日 onhand_qty > 0 的所有库存行中涉及的不重复 location_id 数量。

    计算步骤：
    1. 获取库存行（优先使用调用方传入的 inventory_rows，避免重复查询）。
    2. 对所有 onhand_qty > 0 的行提取 location_id，使用 set 去重。
    3. 返回去重后的库位集合大小。

    数据来源标记（source 字段）：
    - 历史日期：AUTO_METRIC_SOURCE_PREFIX + "SNAPSHOT_PALLET_LOC"
    - 当天实时：AUTO_METRIC_SOURCE_PREFIX + "PALLET_OCCUPIED_LOCATION_COUNT"

    参数：
        owner_id:        货主 ID。
        warehouse_id:    仓库 ID。
        service_date:    服务日期（date 类型）。
        inventory_rows:  可选，已查询好的库存行列表（可由调用方跨指标共用）。
        **kwargs:        兼容其他指标函数签名，此处不使用。

    返回：
        dict，包含 value（Decimal）、source（str）、note（str）。
    """
    rows = inventory_rows if inventory_rows is not None else _inventory_metric_rows(owner_id, warehouse_id, service_date)
    occupied_locations = {row.location_id for row in rows if Decimal(row.onhand_qty or 0) > 0}
    use_snapshot = _inventory_rows_use_snapshot(service_date, rows)
    return {
        "value": Decimal(len(occupied_locations)),
        "source": (
            f"{AUTO_METRIC_SOURCE_PREFIX}SNAPSHOT_PALLET_LOC"
            if use_snapshot
            else f"{AUTO_METRIC_SOURCE_PREFIX}PALLET_OCCUPIED_LOCATION_COUNT"
        ),
        "note": "Distinct occupied inventory locations with onhand_qty > 0.",
    }


def _build_cbm_metric(owner_id, warehouse_id, service_date, *, inventory_rows=None, **kwargs):
    """
    计算 CBM 指标：当日库存总体积（立方米）。

    公式：Sum(onhand_qty × unit_volume_m3)

    两种计算路径（根据数据来源自动选择）：

    路径 A — 历史日期（使用快照）：
        读取快照行的 unit_volume_m3_snapshot 字段，该字段已在生成快照时
        固化商品体积，避免商品维度变更导致历史数据失真。
        若某行 unit_volume_m3_snapshot 为 None，则跳过并记录缺失商品数。

    路径 B — 当天（使用实时库存）：
        调用 _resolve_product_unit_volume 动态解析单位体积（含包装规格回退）。
        使用 volume_cache 避免同一商品重复计算。
        统计 package_fallback_hits（回退到包装规格计算的行数）用于 note 记录。
        无法解析体积的商品被跳过，并在 note 中报告缺失数量。

    透明度设计：
        note 字段会如实记录跳过的商品数和回退命中数，便于计费审计时
        快速定位数据质量问题（哪些 SKU 缺体积配置）。

    参数：
        owner_id:        货主 ID。
        warehouse_id:    仓库 ID。
        service_date:    服务日期（date 类型）。
        inventory_rows:  可选，已查询好的库存行列表（跨指标共用）。
        **kwargs:        兼容签名，不使用。

    返回：
        dict，包含 value（Decimal，总立方米）、source（str）、note（str）。
    """
    rows = inventory_rows if inventory_rows is not None else _inventory_metric_rows(owner_id, warehouse_id, service_date)
    if _inventory_rows_use_snapshot(service_date, rows):
        # 路径 A：历史快照路径，直接使用快照固化的单位体积字段
        total = Decimal("0.0000")
        missing_product_ids = set()

        for row in rows:
            unit_volume = row.unit_volume_m3_snapshot
            if unit_volume is None:
                # 快照记录缺失体积时跳过，并记录商品 ID 便于后续排查
                missing_product_ids.add(row.product_id)
                continue
            total += Decimal(row.onhand_qty or 0) * Decimal(unit_volume)

        notes = ["Sum(onhand_qty * unit_volume_m3_snapshot)."]
        if missing_product_ids:
            notes.append(f"{len(missing_product_ids)} snapshot products missing volume and were skipped.")
        return {
            "value": total,
            "source": f"{AUTO_METRIC_SOURCE_PREFIX}INVENTORY_SNAPSHOT_ONHAND_VOLUME",
            "note": " ".join(notes),
        }

    # 路径 B：实时库存路径，动态解析体积（含两级回退）
    volume_cache: Dict[int, Optional[Decimal]] = {}
    total = Decimal("0.0000")
    missing_product_ids = set()
    package_fallback_hits = 0  # 统计通过包装规格回退计算体积的行数

    for row in rows:
        unit_volume = _resolve_product_unit_volume(row.product, volume_cache)
        if unit_volume is None:
            missing_product_ids.add(row.product_id)
            continue
        if getattr(row.product, "volume", None) is None:
            # 商品主档无 volume，说明该行使用了包装规格回退
            package_fallback_hits += 1
        total += Decimal(row.onhand_qty or 0) * unit_volume

    notes = ["Sum(onhand_qty * unit_volume_m3)."]
    if package_fallback_hits:
        notes.append(f"{package_fallback_hits} detail rows used package-level volume fallback.")
    if missing_product_ids:
        notes.append(f"{len(missing_product_ids)} products missing volume and were skipped.")

    return {
        "value": total,
        "source": f"{AUTO_METRIC_SOURCE_PREFIX}INVENTORY_ONHAND_VOLUME",
        "note": " ".join(notes),
    }


def _build_area_metric(owner_id, warehouse_id, service_date, *, inventory_rows=None, allow_area_fallback=False, **kwargs):
    """
    计算 AREA_M2 指标：当日占用库位的总面积（平方米）。

    公式：Sum(distinct occupied location_area_m2)

    本指标的数据复杂度最高，有三条计算路径：

    路径 A — 历史快照（有面积数据）：
        读取快照行的 location_area_m2_snapshot，对所有 onhand_qty > 0
        的不重复库位求面积之和。
        注意：同一库位可能出现多行快照（多个 SKU），用 dict.setdefault 去重，
        只记录同一 location_id 的第一次面积值。

    路径 A-fallback — 历史快照（无面积数据 + allow_area_fallback=True）：
        若快照行中没有任何库位面积信息（全部为 None），且调用方允许回退，
        则以占用库位数代替面积返回（source 标记为 AREA_FALLBACK_LOC_COUNT）。
        若 allow_area_fallback=False 则直接返回 None，表示跳过该指标。

    路径 B — 实时库存（自定义解析器）：
        优先检查 settings.BILLING_AREA_M2_METRIC_RESOLVER 是否配置了自定义解析器。
        若配置，则调用自定义函数计算面积（适用于有面积主数据的仓库）。
        若未配置：
          - allow_area_fallback=True：降级为占用库位数代替面积。
          - allow_area_fallback=False：返回 None，跳过存储。

    设计决策：
        面积数据来源复杂（库位主档、WMS 规划图、人工录入等），
        内置逻辑无法覆盖所有场景，因此提供插件机制供各仓库定制。
        allow_area_fallback 参数使调用方可以控制数据缺失时的降级策略。

    参数：
        owner_id:            货主 ID。
        warehouse_id:        仓库 ID。
        service_date:        服务日期（date 类型）。
        inventory_rows:      可选，已查询好的库存行列表（跨指标共用）。
        allow_area_fallback: 是否允许在无面积数据时回退为库位数代替。默认 False。
        **kwargs:            兼容签名，透传给自定义解析器。

    返回：
        dict（含 value、source、note），或 None（无数据且不允许回退时）。
    """
    rows = inventory_rows if inventory_rows is not None else _inventory_metric_rows(owner_id, warehouse_id, service_date)
    if _inventory_rows_use_snapshot(service_date, rows):
        # 路径 A：历史快照路径
        occupied_locations = {row.location_id for row in rows if Decimal(row.onhand_qty or 0) > 0}
        location_areas = {}       # {location_id: Decimal(area_m2)} 去重存储
        missing_area_locations = set()  # 缺失面积快照的库位集合

        for row in rows:
            if Decimal(row.onhand_qty or 0) <= 0:
                continue
            if row.location_area_m2_snapshot is None:
                missing_area_locations.add(row.location_id)
                continue
            # setdefault 保证同一库位面积只被记录一次，避免重复累加
            location_areas.setdefault(row.location_id, Decimal(row.location_area_m2_snapshot))

        if location_areas:
            # 有有效面积数据，求和返回
            notes = ["Sum(distinct occupied location_area_m2_snapshot)."]
            if missing_area_locations:
                notes.append(f"{len(missing_area_locations)} occupied locations missing area snapshot.")
            return {
                "value": sum(location_areas.values(), Decimal("0.0000")),
                "source": f"{AUTO_METRIC_SOURCE_PREFIX}SNAPSHOT_AREA_M2",
                "note": " ".join(notes),
            }

        # 快照行全部缺失面积，决定是否允许回退
        if not allow_area_fallback:
            return None  # 调用方会跳过本指标的存储

        # 路径 A-fallback：以占用库位数代替面积（数据降级，source 标记说明）
        return {
            "value": Decimal(len(occupied_locations)),
            "source": f"{AUTO_METRIC_SOURCE_PREFIX}AREA_FALLBACK_LOC_COUNT",
            "note": "No snapshot location area data found; using occupied location count as area proxy.",
        }

    # 路径 B：实时库存路径，优先使用自定义解析器
    resolver = _load_metric_resolver(MetricType.AREA_M2)
    if resolver:
        return resolver(owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date, inventory_rows=rows)

    # 无自定义解析器时的处理
    if not allow_area_fallback:
        return None  # 无面积主数据，不回退，跳过本指标

    # 路径 B-fallback：以占用库位数代替面积（适用于尚未配置面积主数据的仓库）
    occupied_locations = {row.location_id for row in rows if Decimal(row.onhand_qty or 0) > 0}
    return {
        "value": Decimal(len(occupied_locations)),
        "source": f"{AUTO_METRIC_SOURCE_PREFIX}AREA_FALLBACK_LOC_COUNT",
        "note": "No explicit area master data found; using occupied location count as area proxy.",
    }


def _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs):
    """
    计算 ORDER_AMT 指标：指定日期出库订单的货物总金额。

    业务含义：
        部分仓储合同按出库货物金额的一定比例收取服务费，
        本指标为该收费模型提供计费基数。

    查询范围（OutboundOrderLine）：
    - order__biz_date=service_date：业务日期精确匹配（非创建时间）。
    - order__submit_status="SUBMITTED"：只统计已提交的订单（草稿状态排除）。
    - order__is_deleted=False / is_deleted=False：排除已软删除的主单和行。
    - exclude(approval_status="CANCELLED")：排除已审核取消的订单。

    金额计算逻辑（CASE WHEN）：
        当 final_line_amount > 0 时，优先使用 final_line_amount（已确认的行金额）；
        否则回退到 base_qty × base_price（预估金额）。
        这样可以覆盖"已开价但未结算"以及"仅有基础单价"两种场景。

    若没有符合条件的订单行，返回 Decimal("0.00") 而非 None，
    确保调用方拿到的始终是有效数值（0 会被存储层进一步处理）。

    参数：
        owner_id:      货主 ID。
        warehouse_id:  仓库 ID。
        service_date:  服务日期（对应 order.biz_date）。
        **kwargs:      兼容签名，不使用。

    返回：
        dict，包含 value（Decimal，总金额）、source（str）、note（str）。
    """
    # 构建 CASE WHEN 表达式：优先取 final_line_amount，否则取 base_qty * base_price
    line_amount_expr = Case(
        When(
            final_line_amount__gt=0,
            then=F("final_line_amount"),
        ),
        default=ExpressionWrapper(
            F("base_qty") * F("base_price"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    total = (
        OutboundOrderLine.objects
        .filter(
            order__owner_id=owner_id,
            order__warehouse_id=warehouse_id,
            order__biz_date=service_date,
            order__submit_status="SUBMITTED",
            order__is_deleted=False,
            is_deleted=False,
        )
        .exclude(order__approval_status="CANCELLED")
        .aggregate(total=Sum(line_amount_expr))["total"]
        or Decimal("0.00")  # aggregate 返回 None（无匹配行）时归零
    )

    return {
        "value": total,
        "source": f"{AUTO_METRIC_SOURCE_PREFIX}OUTBOUND_ORDER_AMOUNT",
        "note": "Sum(final_line_amount) with fallback to base_qty * base_price for submitted outbound lines, excluding cancelled orders.",
    }


# ======================== 调度入口与类型过滤 ======================== #

def _default_metric_payload(metric_type: str, owner_id, warehouse_id, service_date, **kwargs):
    """
    统一调度入口：根据 metric_type 调用对应的指标构建函数。

    优先级：
    1. 尝试加载自定义解析器（settings.BILLING_{METRIC_TYPE}_METRIC_RESOLVER）。
       若配置，则完全由自定义解析器负责计算，内置逻辑跳过。
    2. 根据 metric_type 分发到内置的 _build_*_metric 函数。
    3. 未知的 metric_type 返回 None（由调用方处理）。

    设计决策：
        将分发逻辑集中在此函数，外部调用方无需知道各指标的具体实现，
        实现了指标构建的单入口原则，便于日志、异常追踪等横切逻辑的插入。

    参数：
        metric_type:   MetricType 枚举值字符串。
        owner_id:      货主 ID。
        warehouse_id:  仓库 ID。
        service_date:  服务日期（date 类型）。
        **kwargs:      透传给各指标构建函数（如 inventory_rows、allow_area_fallback 等）。

    返回：
        dict（包含 value、source、note），或 None（无数据或未知类型时）。
    """
    # 优先检查是否配置了整体类型级别的自定义解析器
    resolver = _load_metric_resolver(metric_type)
    if resolver:
        return resolver(owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date, **kwargs)

    # 内置指标分发
    if metric_type == MetricType.PALLET:
        return _build_pallet_metric(owner_id, warehouse_id, service_date, **kwargs)
    if metric_type == MetricType.CBM:
        return _build_cbm_metric(owner_id, warehouse_id, service_date, **kwargs)
    if metric_type == MetricType.AREA_M2:
        return _build_area_metric(owner_id, warehouse_id, service_date, **kwargs)
    if metric_type == MetricType.ORDER_AMT:
        return _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs)
    return None  # 未识别的指标类型，由调用方决定如何处理


def _auto_metric_types(metric_types=None):
    """
    返回需要自动计算的指标类型列表。

    当 metric_types=None 时，返回全部四种内置类型（PALLET、CBM、AREA_M2、ORDER_AMT）。
    当 metric_types 非 None 时，返回其与内置类型的交集，并保留调用方指定的顺序。
    交集过滤的目的：防止调用方传入不支持的自定义类型，导致后续分发失败。

    参数：
        metric_types: 可选的指标类型列表；None 表示计算全部内置类型。

    返回：
        有效指标类型字符串的列表。
    """
    base_types = [MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2, MetricType.ORDER_AMT]
    if metric_types is None:
        return base_types
    allowed = set(base_types)
    # 保留调用方传入顺序，同时过滤掉不在内置列表中的类型
    return [metric_type for metric_type in metric_types if metric_type in allowed]


def _is_auto_metric_row(metric: BillingMetricDaily) -> bool:
    """
    判断一条 BillingMetricDaily 记录是否为系统自动生成的指标。

    判断依据：source 字段是否以 AUTO_METRIC_SOURCE_PREFIX 开头。
    所有通过本模块内置 _build_*_metric 函数生成的指标，其 source 均带有此前缀。
    人工录入或外部导入的指标则不带该前缀。

    该函数在 _store_generated_metric 中用于区分手工值与自动值：
    - 手工值：默认受保护，自动计算不会覆盖。
    - 自动值：可被系统重算覆盖（或在值为 0 时自动删除）。

    参数：
        metric: BillingMetricDaily 实例。

    返回：
        True → 系统自动生成；False → 人工录入或来源未知。
    """
    return (metric.source or "").startswith(AUTO_METRIC_SOURCE_PREFIX)


# ======================== 指标持久化（含竞态恢复） ======================== #

def _recover_existing_metric_after_create_race(metric_filter):
    """
    竞态恢复：在 CREATE 操作因唯一约束冲突后，查询已存在的记录。

    调用时机：
        _store_generated_metric 在执行 BillingMetricDaily.objects.create 时，
        若抛出 IntegrityError（说明同一时刻另一进程/线程已插入相同记录），
        则调用本函数尝试取回已存在的记录，以便后续决定是更新还是跳过。

    竞态场景分析：
        多个计费调度任务（如按货主并发执行）可能同时尝试为同一
        (owner_id, warehouse_id, service_date, metric_type) 插入指标，
        第二个插入会触发 IntegrityError。
        此时不应直接抛出异常（导致任务失败），而应取回已有记录并继续决策。

    参数：
        metric_filter: 包含 owner_id、warehouse_id、service_date、metric_type 的过滤字典。

    返回：
        BillingMetricDaily 实例，或 None（若记录仍不存在，说明 IntegrityError 另有原因）。
        调用方在返回 None 时应重新抛出原始异常。
    """
    return BillingMetricDaily.objects.filter(**metric_filter).first()


def _store_generated_metric(
    *,
    owner_id,
    warehouse_id,
    service_date,
    metric_payload,
    overwrite: bool = False,
):
    """
    将计算好的指标载荷持久化到 BillingMetricDaily 表。

    本函数实现了完整的"检查 → 保护手工值 → 创建/更新/删除/跳过"流程，
    并内置了竞态恢复机制。

    核心流程（决策树）：

    1. 加锁查询已有记录（select_for_update）
       ↓
    2. 若已有记录 且 非自动生成 且 overwrite=False
       → 返回 "skipped_manual"（保护手工录入值，不覆盖）
       ↓
    3. 若计算值 <= 0
       ├─ 已有记录（自动或 overwrite=True）→ 删除记录，返回 "deleted_zero"
       └─ 无记录 / 手工记录不可覆盖 → 返回 "skipped_zero"（零值不插入）
       ↓
    4. 无已有记录 → 尝试 CREATE
       ├─ 成功 → 返回 "created"
       └─ IntegrityError（竞态冲突）→ 调用 _recover_existing_metric_after_create_race
            ├─ 取回已有记录 → 继续走更新/跳过逻辑（步骤 5~6）
            └─ 仍为 None（非唯一冲突异常）→ 重新抛出原始异常
       ↓
    5. 已有记录（竞态后恢复或直接查到）
       ├─ 非自动 且 overwrite=False → 返回 "skipped_manual"
       └─ 值 <= 0 → 删除，返回 "deleted_zero"
       ↓
    6. 比较值/source/note 是否变化
       ├─ 有变化 → UPDATE，返回 "updated"
       └─ 无变化 → 返回 "noop"（幂等，无 DB 写操作）

    为什么先查后写（而非直接 update_or_create）：
        - 需要区分手工值与自动值（source 前缀检查），update_or_create 无法
          优雅地实现"有记录但不覆盖手工值"的逻辑。
        - 零值删除逻辑：值为 0 不应写入，已有的 0 记录应删除，
          这超出了 update_or_create 的表达能力。
        - noop 检测：避免不必要的 UPDATE 写入，减少数据库压力。

    select_for_update 的作用：
        在事务内锁定已有记录，防止并发读到旧值后各自执行 UPDATE 产生覆盖。
        注意：select_for_update 只对"已存在"的记录有效，
        对"不存在"的情况仍需 IntegrityError + 恢复机制来处理竞态。

    参数：
        owner_id:        货主 ID。
        warehouse_id:    仓库 ID。
        service_date:    服务日期（date 类型）。
        metric_payload:  已规范化的指标字典，包含 metric_type、value、source、note。
        overwrite:       是否强制覆盖手工录入值。默认 False（保护手工值）。

    返回：
        dict，包含：
            metric_type: 指标类型。
            action:      操作结果标识，取值范围：
                         "skipped_manual" | "skipped_zero" | "deleted_zero" |
                         "created" | "updated" | "noop"。
            value:       最终生效的值（Decimal）。
            source:      最终生效的来源标记。
            note:        最终生效的备注。
    """
    metric_type = metric_payload["metric_type"]
    value = Decimal(metric_payload["value"])
    source = metric_payload["source"]
    note = metric_payload["note"]
    create_kwargs = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "metric_type": metric_type,
        "value": value,
        "source": source,
        "note": note,
    }
    metric_filter = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "metric_type": metric_type,
    }

    # 步骤 1：加锁查询已有记录，防止并发 UPDATE 覆盖
    existing = BillingMetricDaily.objects.select_for_update().filter(**metric_filter).first()

    # 步骤 2：保护手工录入值（非自动 + 不允许强制覆盖）
    if existing and not overwrite and not _is_auto_metric_row(existing):
        return {
            "metric_type": metric_type,
            "action": "skipped_manual",
            "value": existing.value,
            "source": existing.source,
            "note": existing.note,
        }

    # 步骤 3：零值处理（在创建前检查，避免无意义写入）
    if value <= 0:
        if existing and (_is_auto_metric_row(existing) or overwrite):
            # 已有的自动/可覆盖记录，值降为 0 → 删除（不保留零值记录）
            existing.delete()
            return {
                "metric_type": metric_type,
                "action": "deleted_zero",
                "value": Decimal("0.0000"),
                "source": source,
                "note": note,
            }
        # 无记录或手工记录不可覆盖时，跳过零值插入
        return {
            "metric_type": metric_type,
            "action": "skipped_zero",
            "value": value,
            "source": source,
            "note": note,
        }

    # 步骤 4：无已有记录 → 尝试创建
    if not existing:
        try:
            with transaction.atomic():
                BillingMetricDaily.objects.create(**create_kwargs)
            return {
                "metric_type": metric_type,
                "action": "created",
                "value": value,
                "source": source,
                "note": note,
            }
        except IntegrityError as exc:
            # 竞态恢复：另一进程已抢先插入，取回已有记录继续决策
            existing = _recover_existing_metric_after_create_race(metric_filter)
            if existing is None:
                # 取不到记录说明 IntegrityError 另有原因，重新抛出
                raise exc

    # 步骤 5：竞态恢复后或直接查到已有记录，再次检查手工值保护
    # （竞态场景下取回的记录可能是人工录入值，需再次判断）
    if not overwrite and not _is_auto_metric_row(existing):
        return {
            "metric_type": metric_type,
            "action": "skipped_manual",
            "value": existing.value,
            "source": existing.source,
            "note": existing.note,
        }

    # 步骤 5b：竞态恢复后再次零值检查（极端情况：竞态期间值已为 0）
    if value <= 0:
        if _is_auto_metric_row(existing) or overwrite:
            existing.delete()
            return {
                "metric_type": metric_type,
                "action": "deleted_zero",
                "value": Decimal("0.0000"),
                "source": source,
                "note": note,
            }
        return {
            "metric_type": metric_type,
            "action": "skipped_zero",
            "value": value,
            "source": source,
            "note": note,
        }

    # 步骤 6：比较变化，仅在有实际差异时执行 UPDATE（避免无效写入）
    changed = (
        Decimal(existing.value) != value
        or (existing.source or "") != source
        or (existing.note or "") != note
    )
    if changed:
        existing.value = value
        existing.source = source
        existing.note = note
        existing.save(update_fields=["value", "source", "note"])
        action = "updated"
    else:
        action = "noop"  # 数据完全一致，无需任何数据库操作

    return {
        "metric_type": metric_type,
        "action": action,
        "value": value,
        "source": source,
        "note": note,
    }
