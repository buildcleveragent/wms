# apps/core/utils/uom.py
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Optional, Tuple, Union

from django.db.models import Q

# 建议根据你的项目实际路径修改这三行导入
from allapp.products.models import Product, ProductUom, ProductPackage


UomLike = Union[ProductUom, int, str]  # 实例 / 主键ID / code字符串
NumberLike = Union[int, float, Decimal]


# -------------------------------
# 内部工具：解析 uom 为 uom_id
# -------------------------------
def _resolve_uom_id(product: Product, uom: UomLike) -> Optional[int]:
    """
    将传入的 uom（实例/ID/code）解析为 ProductUom.id。
    - 优先：如果是实例，取 .id
    - 其次：如果是 int，视为 id
    - 最后：如果是 str，视为 code。先从该商品相关的 uom 范围里匹配（base_uom 或 packages 的 uom），
            若没找到，再全局 ProductUom 按 code 查（避免跨商品误匹配，可按需注释掉全局兜底）。
    """
    # 1) 实例
    uom_id = getattr(uom, "id", None)
    if isinstance(uom_id, int):
        return uom_id

    # 2) 直接的 int
    if isinstance(uom, int):
        return uom

    # 3) 代码字符串
    if isinstance(uom, str):
        code = uom
        # 3.1 在该商品上下文范围内找（推荐）
        rel = product.packages.filter(uom__code=code, is_deleted=False).values_list("uom_id", flat=True).first()
        if rel:
            return int(rel)
        if product.base_uom and product.base_uom.code == code:
            return int(product.base_uom_id)

        # 3.2 全局兜底（可选：若你的系统允许跨商品通用 code）
        try:
            return int(ProductUom.objects.only("id").get(code=code).id)
        except ProductUom.DoesNotExist:
            return None

    return None


def _to_decimal(x: NumberLike) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


# -------------------------------
# 换算：任意包装数量 → 基本单位数量
# -------------------------------
def to_base_qty(product: Product, uom: UomLike, qty: NumberLike) -> Decimal:
    """
    将 (uom, qty) 换算为 **基本单位数量**（product.base_uom）。
    规则：
      - 若 uom == product.base_uom → 1:1 直接返回
      - 否则，必须在 ProductPackage 中找到对应 uom 的换算（qty_in_base）；找不到则抛错
    备注：
      - 允许“隐式基础层”（不强制存在 uom==base_uom 的 package 记录）
    """
    if product.base_uom_id is None:
        raise ValueError(f"商品 {product.code} 未设置 base_uom")

    uom_id = _resolve_uom_id(product, uom)
    if uom_id is None:
        raise ValueError(f"无法解析 UOM：{uom!r}")

    q = _to_decimal(qty)

    # 基础单位：1:1
    if uom_id == product.base_uom_id:
        return q

    # 非基础单位：查包装换算
    pkg = (
        product.packages.filter(uom_id=uom_id, is_deleted=False)
        .only("qty_in_base")
        .first()
    )
    if not pkg:
        # 没有定义该包装的换算率 → 明确抛错，避免幽灵库存
        code = None
        if isinstance(uom, ProductUom):
            code = uom.code
        elif isinstance(uom, str):
            code = uom
        raise ValueError(f"未配置包装换算：{product.code} - {code or uom_id}")

    return q * _to_decimal(pkg.qty_in_base)


# -------------------------------
# 换算：基本单位数量 → 指定包装数量
# -------------------------------
def from_base_qty(
    product: Product,
    uom: UomLike,
    base_qty: NumberLike,
    *,
    rounding: str = "floor",
    return_remainder: bool = False,
) -> Union[Decimal, Tuple[Decimal, Decimal]]:
    """
    将 **基本单位数量** 换算为 (uom, qty)。
    - 若 uom == product.base_uom → 1:1
    - 否则需要从 ProductPackage 读取 qty_in_base（1 uom = N base）
    - rounding:
        - "floor" → 向下取整（默认，用于装箱、配箱）
        - "ceil"  → 向上取整（用于建议采购/补货整包装）
        - "half_up" → 传统四舍五入
    - return_remainder=True 时，返回 (包装数量, 余数_基本单位)

    示例：
      base_qty=25, 1箱=12个 → floor: 2箱，余数=1；ceil: 3箱，余数=-11（或你也可选择不返回余数）
    """
    if product.base_uom_id is None:
        raise ValueError(f"商品 {product.code} 未设置 base_uom")

    uom_id = _resolve_uom_id(product, uom)
    if uom_id is None:
        raise ValueError(f"无法解析 UOM：{uom!r}")

    bq = _to_decimal(base_qty)

    # 基础单位：1:1
    if uom_id == product.base_uom_id:
        if return_remainder:
            return bq, Decimal(0)
        return bq

    pkg = (
        product.packages.filter(uom_id=uom_id, is_deleted=False)
        .only("qty_in_base")
        .first()
    )
    if not pkg:
        code = None
        if isinstance(uom, ProductUom):
            code = uom.code
        elif isinstance(uom, str):
            code = uom
        raise ValueError(f"未配置包装换算：{product.code} - {code or uom_id}")

    factor = _to_decimal(pkg.qty_in_base)
    if factor <= 0:
        raise ValueError(f"无效的换算率：{factor}（应 > 0）")

    raw = bq / factor

    if rounding == "floor":
        qty = raw.to_integral_value(rounding=ROUND_FLOOR)
    elif rounding == "ceil":
        qty = raw.to_integral_value(rounding=ROUND_CEILING)
    elif rounding == "half_up":
        qty = raw.to_integral_value(rounding=ROUND_HALF_UP)
    else:
        raise ValueError("rounding 仅支持 'floor' | 'ceil' | 'half_up'")

    if return_remainder:
        remainder_base = bq - qty * factor
        return qty, remainder_base
    return qty


# -------------------------------
# 校验函数（可在数据导入/任务中使用）
# -------------------------------
def validate_packages_against_base_uom(product: Product) -> None:
    """
    校验与 base_uom 相关的关键规则（不强制存在基础层记录）：
      1) 若存在 uom == base_uom 的 package，则要求 qty_in_base == 1
      2) 同一商品 + 同一 uom 不能重复（通常已由唯一约束保证，这里二次兜底）
    违反规则时抛出 ValueError。
    """
    if product.base_uom_id is None:
        raise ValueError(f"商品 {product.code} 未设置 base_uom")

    # 1) 若定义了基础层记录，必须 1:1
    base_pkg = (
        product.packages.filter(uom_id=product.base_uom_id, is_deleted=False)
        .only("qty_in_base")
        .first()
    )
    if base_pkg and int(base_pkg.qty_in_base) != 1:
        raise ValueError(f"{product.code} 的基础单位层（uom=base_uom）必须 1:1（qty_in_base=1）")

    # 2) 重复 uom 检查（仅兜底，通常由 UniqueConstraint(product,uom,is_deleted) 保障）
    dup = (
        product.packages.filter(is_deleted=False)
        .values("uom_id")
        .order_by()
        .annotate(cnt_id=models.Count("id"))
        .filter(cnt_id__gt=1)
        .first()
    )
    if dup:
        raise ValueError(f"{product.code} 的包装定义中存在重复的 UOM（uom_id={dup['uom_id']}）")


def ensure_can_convert(product: Product, uom: UomLike) -> None:
    """
    预检查是否能在 (product, uom) 与 base_uom 之间进行换算；
    - uom == base_uom → OK
    - 或者存在相应 ProductPackage → OK
    - 否则抛出 ValueError
    """
    uom_id = _resolve_uom_id(product, uom)
    if uom_id is None:
        raise ValueError(f"无法解析 UOM：{uom!r}")

    if uom_id == product.base_uom_id:
        return

    exists = product.packages.filter(uom_id=uom_id, is_deleted=False).exists()
    if not exists:
        code = None
        if isinstance(uom, ProductUom):
            code = uom.code
        elif isinstance(uom, str):
            code = uom
        raise ValueError(f"未配置包装换算：{product.code} - {code or uom_id}")
