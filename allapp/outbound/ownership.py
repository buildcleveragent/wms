"""One physical pick, one atomic source -> reseller -> customer posting."""

from collections import defaultdict
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from allapp.baseinfo.models import Owner
from allapp.core.models import DocSequence
from allapp.inventory.models import InventoryDetail, InventoryTransaction, InvTxType
from allapp.inventory.services import _refresh_summaries
from allapp.products.identifier_lookup import effective_product_barcodes
from allapp.products.identifier_services import add_product_barcode
from allapp.products.models import Product, ProductPackage
from allapp.tasking.models import TaskStatusLog, WmsTask, WmsTaskLine

from .transfer_models import (
    AssistedOwnershipTransfer,
    AssistedOwnershipTransferLine,
    AssistedTransferReservation,
    OwnerProductMapping,
)

PRODUCT_FIELDS = (
    "name",
    "spec",
    "category_id",
    "brand_id",
    "temperature_zone_id",
    "description",
    "base_uom_id",
    "volume",
    "weight",
    "net_content",
    "pack_requirement",
    "pack_note",
    "serial_control",
    "batch_control",
    "expiry_control",
    "expiry_basis",
    "shelf_life_days",
    "inbound_valid_days",
    "expiry_warning_days",
    "fefo_required",
    "mix_lot_allowed",
    "mix_expiry_allowed",
    "pick_policy",
    "break_box_allowed",
    "min_pick_multiple",
    "origin_country",
    "material_quality",
    "vender",
)
COMPATIBLE_FIELDS = (
    "base_uom_id",
    "serial_control",
    "batch_control",
    "expiry_control",
    "expiry_basis",
    "shelf_life_days",
    "fefo_required",
    "mix_lot_allowed",
    "mix_expiry_allowed",
)
SNAPSHOT_FIELDS = (
    "owner_id",
    "product_id",
    "warehouse_id",
    "location_id",
    "container_id",
    "subwarehouse_id",
    "zone_type",
    "batch_no",
    "production_date",
    "expiry_date",
    "serial_no",
)


def transfer_for_order(order):
    return getattr(order, "assisted_transfer", None)


def transfer_summary(order):
    transfer = transfer_for_order(order)
    source = transfer.source_owner if transfer else order.owner
    return {
        "source_owner_id": source.pk,
        "sales_owner_id": order.owner_id,
        "new_owner_id": order.owner_id if transfer else None,
        "new_owner": (
            {"id": order.owner_id, "name": order.owner.name, "code": order.owner.code}
            if transfer
            else None
        ),
        "source_owner": {"id": source.pk, "name": source.name, "code": source.code},
        "transfer_id": transfer.pk if transfer else None,
        "transfer_no": transfer.transfer_no if transfer else "",
        "transfer_status": transfer.status if transfer else None,
    }


def transfer_for_task(task):
    if (
        task.task_type != WmsTask.TaskType.PICK
        or (task.source_model or "").lower() != "outboundorder"
    ):
        return None
    try:
        order_id = int(task.source_pk)
    except (TypeError, ValueError):
        return None
    transfer = AssistedOwnershipTransfer.objects.filter(sales_order_id=order_id).first()
    if transfer and (
        transfer.target_owner_id != task.owner_id or transfer.warehouse_id != task.warehouse_id
    ):
        raise ValidationError("转货任务与销售订单的货主或仓库不一致。")
    return transfer


def inventory_snapshot(detail):
    result = {}
    for field in SNAPSHOT_FIELDS:
        value = getattr(detail, field)
        result[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


def _check_product_pair(source, target, target_owner_id):
    if (
        not source.is_active
        or source.is_deleted
        or not target.is_active
        or target.is_deleted
        or target.owner_id != target_owner_id
        or source.owner_id == target_owner_id
        or any(getattr(source, field) != getattr(target, field) for field in COMPATIBLE_FIELDS)
        or (source.spec or "").strip() != (target.spec or "").strip()
    ):
        raise ValidationError(
            f"商品 {source.name} 的新货主商品对应无效或规格、单位、追踪规则不一致。"
        )


def _map_package(source_package, target, *, actor=None, create=False, copy_defaults=False):
    if source_package is None:
        return None
    package = ProductPackage.all_objects.filter(
        product=target, uom_id=source_package.uom_id
    ).first()
    if package:
        if (
            package.is_deleted
            or not package.is_active
            or package.qty_in_base != source_package.qty_in_base
        ):
            raise ValidationError(f"商品 {target.name} 的对应包装换算不一致。")
        return package
    if not create:
        raise ValidationError(f"商品 {target.name} 缺少对应包装，请先维护商品对应。")
    return ProductPackage.objects.create(
        product=target,
        uom_id=source_package.uom_id,
        qty_in_base=source_package.qty_in_base,
        length_cm=source_package.length_cm,
        width_cm=source_package.width_cm,
        height_cm=source_package.height_cm,
        gross_weight_kg=source_package.gross_weight_kg,
        volume_m3=source_package.volume_m3,
        volume_auto=source_package.volume_auto,
        is_pickable=source_package.is_pickable,
        is_purchase_default=source_package.is_purchase_default if copy_defaults else None,
        is_sales_default=source_package.is_sales_default if copy_defaults else None,
        sort_order=source_package.sort_order,
        created_by=actor,
        updated_by=actor,
    )


def map_product(source, target_owner, *, actor=None):
    """Reuse an explicit/exact mapping, or clone only master-data attributes."""
    mapping = (
        OwnerProductMapping.all_objects.select_for_update()
        .filter(source_product=source, target_owner=target_owner)
        .select_related("target_product")
        .first()
    )
    if mapping:
        if not mapping.is_active or mapping.is_deleted:
            raise ValidationError(f"商品 {source.name} 的货主对应已停用。")
        _check_product_pair(source, mapping.target_product, target_owner.pk)
        return mapping.target_product

    barcodes = list(effective_product_barcodes().filter(product=source).select_related("package"))
    values = {barcode.normalized_value for barcode in barcodes}
    matches = set(
        effective_product_barcodes()
        .filter(owner=target_owner, normalized_value__in=values)
        .values_list("product_id", flat=True)
    )
    if len(matches) > 1:
        raise ValidationError(f"商品 {source.name} 的条码在新货主下匹配多个商品，请先维护对应。")
    if matches:
        target = Product.objects.get(pk=next(iter(matches)))
        _check_product_pair(source, target, target_owner.pk)
        target_barcodes = effective_product_barcodes().filter(
            product=target, normalized_value__in=values
        )
        for barcode in target_barcodes:
            expected = {
                b.qty_in_base for b in barcodes if b.normalized_value == barcode.normalized_value
            }
            if expected != {barcode.qty_in_base}:
                raise ValidationError(f"商品 {source.name} 的条码包装换算冲突。")
    else:
        target = Product(
            owner=target_owner, **{field: getattr(source, field) for field in PRODUCT_FIELDS}
        )
        target._derive_code_from_owner_id_on_create = True
        target.created_by = target.updated_by = actor
        target.save()
        packages = {
            package.pk: _map_package(package, target, actor=actor, create=True, copy_defaults=True)
            for package in source.packages.filter(is_active=True, uom__is_active=True)
        }
        for barcode in barcodes:
            add_product_barcode(
                product=target,
                barcode=barcode.barcode,
                barcode_type=barcode.barcode_type,
                package=packages.get(barcode.package_id),
                is_primary=barcode.is_primary,
                valid_from=barcode.valid_from,
                valid_to=barcode.valid_to,
                actor=actor,
            )
    OwnerProductMapping.objects.create(
        source_product=source,
        target_owner=target_owner,
        target_product=target,
        created_by=actor,
        updated_by=actor,
    )
    return target


def prepare_transfer_items(items, source_owner, target_owner, *, actor):
    # Product creation locks owner sequences; acquire both owners in stable order.
    list(
        Owner.objects.select_for_update()
        .filter(pk__in=[source_owner.pk, target_owner.pk])
        .order_by("pk")
    )
    result = []
    for item in items:
        source = item["product"]
        target = map_product(source, target_owner, actor=actor)
        # Validate every usable package before publishing a task: the picker may
        # scan a source carton barcode even when the sale was entered in base units.
        for source_package in source.packages.filter(is_active=True, uom__is_active=True):
            _map_package(source_package, target, actor=actor, create=True)
        package = _map_package(item.get("package"), target, actor=actor, create=True)
        result.append(
            {
                **item,
                "source_product": source,
                "source_package": item.get("package"),
                "product": target,
                "package": package,
            }
        )
    return result


def create_transfer(order, source_owner, *, actor):
    return AssistedOwnershipTransfer.objects.create(
        transfer_no=DocSequence.next_code(
            doc_type="HZ", warehouse=order.warehouse, owner=source_owner, biz_date=order.biz_date
        ),
        sales_order=order,
        source_owner=source_owner,
        target_owner=order.owner,
        warehouse=order.warehouse,
        created_by=actor,
        updated_by=actor,
    )


def create_transfer_line(transfer, sales_line, item, *, actor):
    return AssistedOwnershipTransferLine.objects.create(
        transfer=transfer,
        sales_line=sales_line,
        source_product=item["source_product"],
        source_package=item.get("source_package"),
        package_qty=item.get("package_qty"),
        quantity=item["qty"],
        transfer_price=item["transfer_price"],
        created_by=actor,
        updated_by=actor,
    )


def reserve_transfer_inventory(order, task, transfer, *, actor):
    from allapp.tasking.counting import assert_inventory_not_count_locked

    from .services import _fefo_details_qs

    if transfer.status != transfer.Status.RESERVED:
        raise ValidationError("转货单状态不允许预留库存。")
    pairs = set()
    for line in transfer.lines.select_related("sales_line").order_by("id"):
        pairs.add((transfer.source_owner_id, line.source_product_id))
        existing = sum(
            (r.quantity for r in line.reservations.filter(status="RESERVED")), Decimal(0)
        )
        remaining = line.quantity - existing
        if remaining < 0:
            raise ValidationError("转货预留量超过需求。")
        details = (
            _fefo_details_qs(transfer.source_owner_id, order.warehouse_id, line.source_product_id)
            .defer(None)
            .select_related("location", "product")
        )
        for detail in details:
            if remaining <= 0:
                break
            location = detail.location
            if location.is_disabled or location.is_frozen:
                continue
            assert_inventory_not_count_locked(
                owner_id=detail.owner_id,
                warehouse_id=detail.warehouse_id,
                product_id=detail.product_id,
                location_id=detail.location_id,
                batch_no=detail.batch_no,
                task=task,
            )
            # Tasks and orders carry three decimals; never round a four-decimal
            # inventory remainder up into an unfulfillable reservation.
            available = detail.available_qty.quantize(Decimal("0.001"), rounding=ROUND_DOWN)
            qty = min(available, remaining)
            if qty <= 0:
                continue
            detail.allocated_qty += qty
            detail.save()
            task_line = WmsTaskLine.objects.create(
                task=task,
                product_id=line.sales_line.product_id,
                from_location_id=detail.location_id,
                qty_plan=qty,
                src_model="OutboundOrderLine",
                src_id=line.sales_line_id,
                rule_key="OWNER_TRANSFER_FEFO",
                status="RESERVED",
                plan_meta={
                    "source_inventory_detail_id": detail.pk,
                    "source_owner_id": detail.owner_id,
                    "source_product_id": detail.product_id,
                },
            )
            AssistedTransferReservation.objects.create(
                transfer_line=line,
                task_line=task_line,
                source_inventory=detail,
                quantity=qty,
                source_snapshot=inventory_snapshot(detail),
                created_by=actor,
            )
            remaining -= qty
        if remaining > 0:
            raise ValidationError(
                f"库存不足，原货主商品 {line.source_product_id} 缺口 {remaining}。"
            )
    _refresh_summaries(pairs)
    return task


def release_transfer_inventory(order, transfer, *, actor):
    from .services import _task_source_q

    if transfer.status == transfer.Status.POSTED:
        raise ValidationError("已过账转货不能取消。")
    released = Decimal(0)
    pairs = set()
    for reservation in (
        AssistedTransferReservation.objects.select_for_update()
        .filter(transfer_line__transfer=transfer, status="RESERVED")
        .order_by("source_inventory_id", "id")
    ):
        detail = InventoryDetail.objects.select_for_update().get(pk=reservation.source_inventory_id)
        if (
            inventory_snapshot(detail) != reservation.source_snapshot
            or detail.allocated_qty < reservation.quantity
        ):
            raise ValidationError("转货来源库存或预留量已变化，取消已回滚。")
        detail.allocated_qty -= reservation.quantity
        detail.save()
        reservation.status = "RELEASED"
        reservation.updated_by = actor
        reservation.save(update_fields=["status", "updated_by", "updated_at"])
        released += reservation.quantity
        pairs.add((detail.owner_id, detail.product_id))
    for task in (
        WmsTask.objects.select_for_update()
        .filter(_task_source_q(order), task_type="PICK")
        .exclude(status="CANCELLED")
    ):
        old_status = task.status
        task.lines.update(status="CANCELLED")
        task.status = "CANCELLED"
        task.finished_at = timezone.now()
        task.updated_by = actor
        task.save(update_fields=["status", "finished_at", "updated_by", "updated_at"])
        TaskStatusLog.objects.create(
            task=task,
            old_status=old_status,
            new_status="CANCELLED",
            changed_by=actor,
            note="取消转货出库，释放原货主库存",
        )
    transfer.status = transfer.Status.CANCELLED
    transfer.updated_by = actor
    transfer.save(update_fields=["status", "updated_by", "updated_at"])
    _refresh_summaries(pairs)
    return released


def map_transfer_scan(task, resolved, transfer):
    """Translate source-owner identifiers, without relaxing task ownership checks."""
    source_id = resolved.get("product_id")
    reservations = AssistedTransferReservation.objects.filter(
        transfer_line__transfer=transfer,
        task_line__task=task,
        transfer_line__source_product_id=source_id,
        status="RESERVED",
    ).select_related("task_line__product")
    for field, key in (
        ("batch_no", "lot_no"),
        ("production_date", "mfg_date"),
        ("expiry_date", "exp_date"),
        ("serial_no", "serial_no"),
    ):
        value = resolved.get(key)
        if key == "serial_no" and not value and resolved.get("code_type") in {"SN", "SERIAL"}:
            value = resolved.get("label_key")
        if value not in (None, ""):
            value = value.isoformat() if hasattr(value, "isoformat") else str(value)
            reservations = reservations.filter(**{f"source_inventory__{field}": value})
    rows = list(reservations)
    if not rows:
        raise ValidationError("条码商品不属于本次转货的原货主预留商品。")
    target = rows[0].task_line.product
    mapped = {**resolved, "product_id": target.pk}
    if resolved.get("product_package_id"):
        source_package = ProductPackage.objects.get(pk=resolved["product_package_id"])
        mapped["product_package_id"] = _map_package(source_package, target).pk
    return mapped, [row.task_line_id for row in rows]


def validate_transfer_scan(line, *, lot_no, mfg_date, exp_date, serial_no):
    reservation = AssistedTransferReservation.objects.filter(task_line=line).first()
    if not reservation:
        return lot_no, mfg_date, exp_date, serial_no
    snapshot = reservation.source_snapshot
    values = (lot_no, mfg_date, exp_date, serial_no)
    names = ("batch_no", "production_date", "expiry_date", "serial_no")
    for name, value in zip(names, values):
        normalized = value.isoformat() if hasattr(value, "isoformat") else value
        if (
            normalized not in (None, "")
            and str(normalized).upper() != str(snapshot[name] or "").upper()
        ):
            raise ValidationError("扫码批次、效期或序列号与转货预留库存不一致。")
    return tuple(snapshot[name] for name in names)


def post_transfer_inventory(*, task, transfer, scans, now, batch_no, actor=None):
    """Post all three legs within post_task's existing atomic journal boundary."""
    from allapp.baseinfo.customer_access import customer_binding
    from allapp.tasking.counting import assert_inventory_not_count_locked

    from .services import require_assisted_owner_warehouse

    transfer = (
        AssistedOwnershipTransfer.objects.select_for_update()
        .select_related("source_owner", "target_owner", "sales_order")
        .get(pk=transfer.pk)
    )
    if transfer.status != transfer.Status.RESERVED:
        raise ValidationError("转货状态与任务过账状态不一致。")
    require_assisted_owner_warehouse(transfer.source_owner, task.warehouse_id)
    require_assisted_owner_warehouse(transfer.target_owner, task.warehouse_id)
    if not customer_binding(transfer.sales_order.customer_id, transfer.target_owner_id):
        raise ValidationError("客户与新货主的关联已停用，不能过账。")
    reservations = list(
        AssistedTransferReservation.objects.select_for_update()
        .filter(transfer_line__transfer=transfer, task_line__task=task)
        .select_related(
            "task_line", "transfer_line__source_product", "transfer_line__sales_line__product"
        )
        .order_by("id")
    )
    by_line = {r.task_line_id: r for r in reservations}
    active_ids = set(task.lines.exclude(status="CANCELLED").values_list("id", flat=True))
    if not reservations or set(by_line) != active_ids:
        raise ValidationError("转货任务缺少完整的来源库存预留。")
    totals = defaultdict(lambda: Decimal(0))
    for scan in scans:
        reservation = by_line.get(scan.task_line_id)
        if (
            not reservation
            or scan.product_id != reservation.task_line.product_id
            or scan.location_id != reservation.task_line.from_location_id
        ):
            raise ValidationError("转货扫描与预留任务行不一致。")
        validate_transfer_scan(
            reservation.task_line,
            lot_no=scan.lot_no,
            mfg_date=scan.mfg_date,
            exp_date=scan.exp_date,
            serial_no=scan.serial_no,
        )
        if scan.qty_base_delta is None:
            raise ValidationError("转货扫描缺少数量。")
        totals[scan.task_line_id] += scan.qty_base_delta
    pairs = set()
    for reservation in reservations:
        line = reservation.task_line
        qty = reservation.quantity
        if (
            reservation.status != "RESERVED"
            or totals[line.pk] != qty
            or line.qty_done != qty
            or line.qty_plan != qty
            or line.product_id != reservation.transfer_line.sales_line.product_id
            or line.src_id != reservation.transfer_line.sales_line_id
            or reservation.transfer_line.sales_line.order_id != transfer.sales_order_id
        ):
            raise ValidationError("转货必须全量拣货、复核后一次过账。")
        source = (
            InventoryDetail.objects.select_for_update()
            .select_related("product", "location")
            .get(pk=reservation.source_inventory_id)
        )
        if (
            not source.is_active
            or inventory_snapshot(source) != reservation.source_snapshot
            or source.owner_id != transfer.source_owner_id
            or source.product_id != reservation.transfer_line.source_product_id
            or source.warehouse_id != task.warehouse_id
            or source.location_id != line.from_location_id
            or source.onhand_qty < qty
            or source.allocated_qty < qty
            or source.location.is_disabled
            or source.location.is_frozen
        ):
            raise ValidationError("原货主库存、库位或预留量已变化，转货过账已回滚。")
        target_product = reservation.transfer_line.sales_line.product
        _check_product_pair(source.product, target_product, transfer.target_owner_id)
        for owner_id, product_id in [
            (source.owner_id, source.product_id),
            (transfer.target_owner_id, target_product.pk),
        ]:
            assert_inventory_not_count_locked(
                owner_id=owner_id,
                warehouse_id=task.warehouse_id,
                product_id=product_id,
                location_id=source.location_id,
                batch_no=source.batch_no,
                task=task,
            )
            pairs.add((owner_id, product_id))
        dimensions = {
            field: getattr(source, field)
            for field in (
                "warehouse_id",
                "location_id",
                "batch_no",
                "production_date",
                "expiry_date",
                "serial_no",
            )
        }
        target, _ = InventoryDetail.objects.get_or_create(
            **dimensions,
            owner_id=transfer.target_owner_id,
            product=target_product,
            container=None,
            is_active=True,
            defaults={
                "onhand_qty": 0,
                "allocated_qty": 0,
                "locked_qty": 0,
                "damaged_qty": 0,
                "subwarehouse_id": source.subwarehouse_id,
                "zone_type": source.zone_type,
            },
        )
        target = InventoryDetail.objects.select_for_update().get(pk=target.pk)
        if target_product.serial_control and target.onhand_qty:
            raise ValidationError("新货主已存在相同序列号库存，不能转入。")
        source.onhand_qty -= qty
        source.allocated_qty -= qty
        source.save()
        target.onhand_qty += qty
        target.save()
        pair_id = uuid4()
        common = {
            **dimensions,
            "src_model": "WmsTask",
            "src_id": task.pk,
            "src_line_id": line.pk,
            "src_no": transfer.transfer_no,
            "posted_at": now,
            "posting_batch": batch_no,
            "subwarehouse_id": source.subwarehouse_id,
            "zone_type": source.zone_type,
            "created_by": actor,
        }
        outgoing = InventoryTransaction.objects.create(
            **common,
            tx_type=InvTxType.OWNER_XFER_OUT,
            owner_id=source.owner_id,
            product_id=source.product_id,
            container_id=source.container_id,
            qty_delta=-qty,
            pair_id=pair_id,
            memo="原货主转出",
        )
        incoming = InventoryTransaction.objects.create(
            **common,
            tx_type=InvTxType.OWNER_XFER_IN,
            owner_id=transfer.target_owner_id,
            product=target_product,
            qty_delta=qty,
            pair_id=pair_id,
            memo="新货主接收",
        )
        # Only the just-received quantity leaves; existing target reservations stay intact.
        target.onhand_qty -= qty
        target.save()
        issued = InventoryTransaction.objects.create(
            **{**common, "src_no": transfer.sales_order.order_no},
            tx_type=InvTxType.ISSUE,
            owner_id=transfer.target_owner_id,
            product=target_product,
            qty_delta=-qty,
            memo="新货主销售出库",
        )
        if getattr(settings, "INVENTORY_FIFO_ENABLED", False):
            from allapp.inventory.fifo import consume_fifo, create_receipt_layer
            from allapp.inventory.models import InventoryCostLayer

            source_layer_ids = InventoryCostLayer.objects.filter(
                owner_id=source.owner_id,
                warehouse_id=source.warehouse_id,
                product_id=source.product_id,
                batch_no=source.batch_no,
                serial_no=source.serial_no,
                expiry_date=source.expiry_date,
                positions__location_id=source.location_id,
                positions__container_id=source.container_id,
            ).values_list("id", flat=True)
            consume_fifo(
                owner_id=source.owner_id,
                warehouse_id=source.warehouse_id,
                product_id=source.product_id,
                location_id=source.location_id,
                quantity=qty,
                batch_no=source.batch_no,
                serial_no=source.serial_no,
                container_id=source.container_id,
                exact_container=True,
                layer_ids=list(source_layer_ids),
                inventory_transaction_id=outgoing.pk,
                occurred_at=now,
                by_user=actor,
            )
            layer, _ = create_receipt_layer(
                owner_id=transfer.target_owner_id,
                warehouse_id=task.warehouse_id,
                product_id=target_product.pk,
                location_id=source.location_id,
                quantity=qty,
                source_type="InventoryTransaction",
                source_id=incoming.pk,
                received_date=(timezone.localtime(now) if timezone.is_aware(now) else now).date(),
                batch_no=source.batch_no,
                serial_no=source.serial_no,
                expiry_date=source.expiry_date,
                unit_cost=reservation.transfer_line.transfer_price,
                cost_currency="CNY",
                inventory_transaction_id=incoming.pk,
                by_user=actor,
            )
            consume_fifo(
                owner_id=transfer.target_owner_id,
                warehouse_id=task.warehouse_id,
                product_id=target_product.pk,
                location_id=source.location_id,
                quantity=qty,
                batch_no=source.batch_no,
                serial_no=source.serial_no,
                layer_ids=[layer.pk],
                inventory_transaction_id=issued.pk,
                occurred_at=now,
                by_user=actor,
                exact_container=True,
            )
        reservation.status = "POSTED"
        reservation.updated_by = actor
        reservation.save(update_fields=["status", "updated_by", "updated_at"])
    _refresh_summaries(pairs)
    transfer.status = transfer.Status.POSTED
    transfer.posted_at = now
    transfer.posting_batch = batch_no
    transfer.updated_by = actor
    transfer.save(
        update_fields=["status", "posted_at", "posting_batch", "updated_by", "updated_at"]
    )
    from .services import close_assisted_order_for_posted_task

    close_assisted_order_for_posted_task(task)
    return len(reservations) * 3
