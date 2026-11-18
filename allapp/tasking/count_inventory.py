"""Exact inventory-layer snapshots shared by counting and inventory posting."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from allapp.inventory.models import InventoryDetail
from allapp.tasking.models import CountLineExtra

COUNT_MAX_QTY = Decimal("99999999999999.9999")
COUNT_QUANT = Decimal("0.0001")
SNAPSHOT_KEY = "count_inventory"


def count_quantity(value, *, field="qty_counted"):
    """Validate without rounding away unsupported precision or integer capacity."""
    try:
        quantity = Decimal(str(value))
        if (
            not quantity.is_finite()
            or quantity < 0
            or quantity > COUNT_MAX_QTY
            or quantity.as_tuple().exponent < -4
        ):
            raise ValueError
        return quantity.quantize(COUNT_QUANT)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(
            {field: "数量须在 0～99999999999999.9999 之间，最多四位小数。"}
        ) from exc


def inventory_snapshot(detail):
    """Snapshot identity only; book quantity lives in CountLineExtra."""
    return {
        "id": detail.pk,
        "owner_id": detail.owner_id,
        "warehouse_id": detail.warehouse_id,
        "product_id": detail.product_id,
        "location_id": detail.location_id,
        "subwarehouse_id": detail.subwarehouse_id,
        "zone_type": detail.zone_type,
        "batch_no": detail.batch_no or "",
        "production_date": detail.production_date.isoformat() if detail.production_date else None,
        "expiry_date": detail.expiry_date.isoformat() if detail.expiry_date else None,
        "serial_no": detail.serial_no or "",
        "product_serial_control": detail.product_serial_control,
        "container_id": detail.container_id,
    }


def validate_count_sources(task, *, extras=None, scans=None):
    """Lock and validate every original layer, including zero-difference lines.

    Legacy tasks may lack plan_meta; their original PK, ownership, batch, expiry
    and book quantity must still match. Never resolve a replacement by dimension.
    The caller holds the task lock (and warehouse lock when posting/submitting).
    """
    if extras is None:
        extras = list(
            CountLineExtra.objects.filter(line__task=task)
            .select_related("line")
            .order_by("line_id")
        )
    source_ids = [extra.line.src_id for extra in extras]
    if not extras or None in source_ids or len(set(source_ids)) != len(source_ids):
        raise ValidationError("盘点原库存层引用缺失或重复。")
    details = {
        detail.pk: detail
        for detail in InventoryDetail.objects.select_for_update()
        .filter(pk__in=source_ids, is_active=True)
        .select_related("product", "location", "container")
        .order_by("pk")
    }
    sources = {}
    by_line = {}
    for extra in extras:
        line = extra.line
        detail = details.get(line.src_id)
        snapshot = (line.plan_meta or {}).get(SNAPSHOT_KEY)
        if (
            line.task_id != task.pk
            or line.src_model != "InventoryDetail"
            or detail is None
            or detail.owner_id != task.owner_id
            or detail.warehouse_id != task.warehouse_id
            or detail.product_id != line.product_id
            or detail.product.owner_id != task.owner_id
            or detail.product_serial_control != detail.product.serial_control
            or detail.location_id != line.from_location_id
            or detail.location.warehouse_id != task.warehouse_id
            or (detail.batch_no or "").strip().upper() != extra.lot_no
            or detail.expiry_date != extra.exp_date
            or detail.onhand_qty != extra.qty_book
            or (snapshot is not None and snapshot != inventory_snapshot(detail))
        ):
            raise ValidationError(
                f"盘点行 {line.pk} 的原库存层或账面库存已变化，请取消后重新发布。"
            )
        sources[line.pk] = detail
        by_line[line.pk] = extra
    if scans is not None:
        if len(scans) != len(extras) or {s.task_line_id for s in scans} != set(sources):
            raise ValidationError("盘点有效快照数量与任务行不一致。")
        for scan in scans:
            extra = by_line[scan.task_line_id]
            detail = sources[scan.task_line_id]
            done_matches = extra.line.qty_done == extra.qty_counted
            if SNAPSHOT_KEY not in (extra.line.plan_meta or {}):
                # Before the capacity migration, task lines stored only three places.
                # The count extra and scan still contain the authoritative four places.
                done_matches = done_matches or extra.line.qty_done == extra.qty_counted.quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                )
            if (
                extra.count_status != "COUNTED"
                or scan.product_id != detail.product_id
                or scan.location_id != detail.location_id
                or scan.qty_base != extra.qty_diff
                or extra.qty_diff != extra.qty_counted - extra.qty_book
                or not done_matches
                or extra.line.scan_snapshot_rev != scan.scan_snapshot_rev
            ):
                raise ValidationError(f"盘点行 {extra.line_id} 的实盘数量与有效扫描不一致。")
    return sources
