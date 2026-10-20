"""Resolve manual pick facts from an explicitly allocated inventory layer."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from allapp.inventory.models import InventoryDetail


def manual_pick_sources(task, lines):
    """Batch resolve sources; never infer a layer from product or location alone."""
    from allapp.outbound.ownership import transfer_for_task
    from allapp.outbound.transfer_models import AssistedTransferReservation

    lines = {line.pk: line for line in lines}
    transfer = transfer_for_task(task)
    if transfer:
        # Ownership transfers have their own explicit reservation and posting service.
        reservations = {
            row.task_line_id: row
            for row in AssistedTransferReservation.objects.filter(
                transfer_line__transfer=transfer,
                task_line_id__in=lines,
                status="RESERVED",
            ).select_related("source_inventory__product__base_uom", "transfer_line__sales_line")
        }
        sources = {}
        for line in lines.values():
            row = reservations.get(line.pk)
            detail = row.source_inventory if row else None
            if (
                detail is None
                or not detail.is_active
                or detail.is_deleted
                or detail.owner_id != transfer.source_owner_id
                or detail.warehouse_id != task.warehouse_id
                or detail.location_id != line.from_location_id
                or row.transfer_line.sales_line.product_id != line.product_id
                or detail.product_id != row.transfer_line.source_product_id
                or detail.base_unit != detail.product.base_uom.code
                or (line.plan_meta or {}).get("source_inventory_detail_id") != detail.pk
            ):
                raise ValidationError("库存来源待核对：转货预留库存与任务不一致。")
            sources[line.pk] = detail
        return sources
    ids = {}
    for line in lines.values():
        try:
            raw_id = (line.plan_meta or {})["inventory_detail_id"]
            if not str(raw_id).isdigit() or int(raw_id) <= 0:
                raise ValueError("invalid inventory ID")
            ids[line.pk] = int(raw_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("库存来源待核对：任务行缺少明确的分配库存。") from exc
    details = InventoryDetail.objects.select_related("product__base_uom").in_bulk(ids.values())
    result = {}
    for line in lines.values():
        detail = details.get(ids[line.pk])
        if (
            detail is None
            or not detail.is_active
            or line.task_id != task.pk
            or detail.owner_id != task.owner_id
            or detail.warehouse_id != task.warehouse_id
            or detail.product_id != line.product_id
            or detail.location_id != line.from_location_id
            or detail.product.owner_id != task.owner_id
            or detail.base_unit != detail.product.base_uom.code
        ):
            raise ValidationError(
                "库存来源待核对：分配库存与任务的货主、仓库、商品、库位或单位不一致。"
            )
        result[line.pk] = detail
    return result


def scan_dimensions(scan, detail):
    """Permit missing historical dimensions only; conflicting facts fail closed."""
    recorded = (scan.lot_no or "", scan.mfg_date, scan.exp_date, scan.serial_no or "")
    expected = (
        detail.batch_no or "",
        detail.production_date,
        detail.expiry_date,
        detail.serial_no or "",
    )
    if any(old and old != new for old, new in zip(recorded, expected)):
        raise ValidationError("库存来源待核对：扫描记录与分配库存的批次、效期或序列号冲突。")
    return expected, recorded != expected


def validate_manual_pick_scans(task, scans):
    """Validate the signed history before posting, including reversals and totals."""
    line_ids = {s.task_line_id for s in scans if s.method == "MANUAL"}
    if not line_ids:
        return {}, []
    lines = {s.task_line_id: s.task_line for s in scans if s.task_line_id in line_ids}
    if None in lines or any(line is None for line in lines.values()):
        raise ValidationError("库存来源待核对：手工记录缺少任务行。")
    sources = manual_pick_sources(task, lines.values())
    totals = {pk: Decimal("0") for pk in lines}
    recovered = []
    for scan in sorted(scans, key=lambda s: s.pk):
        if scan.task_line_id not in lines:
            continue
        detail = sources[scan.task_line_id]
        if (
            scan.owner_id != task.owner_id
            or scan.warehouse_id != task.warehouse_id
            or scan.product_id != lines[scan.task_line_id].product_id
            or scan.location_id != detail.location_id
        ):
            raise ValidationError("库存来源待核对：手工记录的归属与分配库存不一致。")
        _, missing = scan_dimensions(scan, detail)
        if missing and scan.method != "MANUAL":
            raise ValidationError("库存来源待核对：原扫码记录没有完整库存维度，不能手工冲销。")
        if missing:
            recovered.append({"scan_id": scan.pk, "inventory_detail_id": detail.pk})
        qty = Decimal(scan.qty_base_delta)
        # Legacy barcode scans may store outbound negatives; manual deltas are signed.
        totals[scan.task_line_id] += qty if scan.method == "MANUAL" else abs(qty)
        if totals[scan.task_line_id] < 0:
            raise ValidationError("手工冲销不能超过已登记拣货数量。")
    for pk, total in totals.items():
        if total != lines[pk].qty_done or total > lines[pk].qty_plan:
            raise ValidationError("库存来源待核对：登记数量与任务完成数量或计划数量不一致。")
        if sources[pk].serial_no and total not in (0, 1):
            raise ValidationError("序列号商品的拣货数量必须为 0 或 1。")
    return sources, recovered
