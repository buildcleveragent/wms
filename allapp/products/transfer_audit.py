"""Read-only reports and explicit, revalidated repairs of legacy reseller masters."""

import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from allapp.accounts.audit import record_audit_event
from allapp.core.product_identity import SNAPSHOT_MODELS, require_product_snapshots
from allapp.inventory.models import InventoryCostLayer, InventoryDetail, InventoryTransaction
from allapp.outbound.models import OutboundOrderLine
from allapp.outbound.transfer_models import (
    AssistedOwnershipTransferLine,
    AssistedTransferReservation,
)
from allapp.tasking.models import WmsTaskLine

from .models import ProductBarcode, ProductPackage, TransferProductBinding
from .transfer_protection import (
    MEASUREMENT_FIELDS,
    PRODUCT_FIELDS,
    _system_write,
    differences,
    lock_family,
)

PRICE_FIELDS = ("price", "purchase_price", "min_price")


def _master(product):
    return {
        **{field: getattr(product, field) for field in (*PRODUCT_FIELDS, *PRICE_FIELDS)},
        "id": product.pk,
        "owner_id": product.owner_id,
        "updated_at": str(product.updated_at),
        "packages": list(
            ProductPackage.all_objects.filter(product=product)
            .order_by("pk")
            .values("id", "uom_id", "qty_in_base", "is_active", "is_deleted")
        ),
    }


def report_binding(binding, *, price_basis=None):
    source, target = binding.source_product, binding.target_product
    blockers = []
    if (
        TransferProductBinding.objects.filter(target_product=target)
        .exclude(source_product=source)
        .exists()
    ):
        blockers.append("接收商品有多个来源")
    if TransferProductBinding.objects.filter(source_product=target).exists():
        blockers.append("接收商品还向其他货主转货，需另行审核整个来源链")
    if (
        TransferProductBinding.objects.filter(
            source_product=source, target_product__owner_id=target.owner_id
        )
        .exclude(target_product=target)
        .exists()
    ):
        blockers.append("同一来源在接收货主下有多个档案，需核查历史对应")
    if source.owner_id == target.owner_id:
        blockers.append("来源和接收货主相同")
    stock = list(
        InventoryDetail.all_objects.filter(product=target)
        .order_by("pk")
        .values("id", "onhand_qty", "allocated_qty", "locked_qty", "damaged_qty")
    )
    if any(
        any(row[field] for field in ("onhand_qty", "allocated_qty", "locked_qty", "damaged_qty"))
        for row in stock
    ):
        blockers.append("接收商品仍有库存或预留/锁定/残损数量")
    lines = OutboundOrderLine.all_objects.filter(product=target)
    if lines.filter(order__is_closed=False).exclude(order__approval_status="CANCELLED").exists():
        blockers.append("接收商品仍有未完成销售出库单")
    pending_reservations = list(
        AssistedTransferReservation.all_objects.filter(
            transfer_line__sales_line__product=target, status="RESERVED"
        )
        .order_by("pk")
        .values("id", "source_inventory_id", "quantity")
    )
    if pending_reservations:
        blockers.append("转货仍有未释放或未过账的来源库存预留")
    if (
        WmsTaskLine.all_objects.filter(product=target)
        .exclude(task__status__in=("COMPLETED", "CANCELLED"))
        .exists()
    ):
        blockers.append("接收商品仍有未完成仓库任务")
    if lines.exclude(base_uom_id=source.base_uom_id).exists():
        blockers.append("历史销售出库基本单位与来源不一致，不能修改历史记录来迁就修复")
    # Other business documents require separate review; do not infer their old unit semantics.
    from django.apps import apps

    for app, model in SNAPSHOT_MODELS:
        if (app, model) != ("outbound", "OutboundOrderLine"):
            if apps.get_model(app, model)._base_manager.filter(product_id=target.pk).exists():
                blockers.append(f"存在其他业务单据 {app}.{model}，需单独核查")
    layers = InventoryCostLayer.objects.filter(product=target)
    if (
        layers.exclude(base_uom_id=source.base_uom_id).exists()
        or layers.filter(positions__remaining_qty__gt=0).exists()
    ):
        blockers.append("历史成本层单位不一致或仍有成本库存")
    allowed_tx = InventoryTransaction.objects.filter(product=target).exclude(
        tx_type__in=("OWNER_XFER_IN", "ISSUE")
    )
    if allowed_tx.exists():
        blockers.append("存在转入/销售出库以外的库存流水，需单独核查")
    # Every historical transaction must belong to this provenance chain.
    known_tasks = AssistedOwnershipTransferLine.all_objects.filter(
        source_product=source, sales_line__product=target
    ).values_list("reservations__task_line__task_id", flat=True)
    if (
        InventoryTransaction.objects.filter(product=target)
        .exclude(src_model="WmsTask", src_id__in=known_tasks)
        .exists()
    ):
        blockers.append("存在不属于此来源链的库存流水")
    for field in MEASUREMENT_FIELDS:
        if field != "base_uom_id" and getattr(source, field) != getattr(target, field):
            blockers.append(f"追踪规则 {field} 不一致，不能自动修复")
    source_packages = {p.uom_id: p for p in source.packages.filter(is_active=True)}
    target_packages = list(ProductPackage.all_objects.filter(product=target))
    if any(
        p.uom_id not in source_packages or p.is_deleted or not p.is_active for p in target_packages
    ):
        blockers.append("接收方有来源未包含或已停用的包装")
    for line in lines.select_related("aux_uom"):
        if line.aux_uom_id:
            package = source_packages.get(line.aux_uom.uom_id)
            if not package or not line.ratio or line.ratio != package.qty_in_base:
                blockers.append(f"历史销售行 {line.pk} 的包装比例无法与来源核对")
    try:
        require_product_snapshots([target.pk])
    except ValidationError as exc:
        blockers.extend(exc.messages)
    proposed_prices = {field: getattr(target, field) for field in PRICE_FIELDS}
    price_note = "基本单位未变，保持接收方当前价格"
    if source.base_uom_id != target.base_uom_id:
        if ProductBarcode.all_objects.filter(product=target).exists():
            blockers.append("接收商品存在条码换算快照，恢复基本单位前需单独核查")
        price_note = "需明确当前主档价格按当前单位还是已按来源基本单位；历史成交价不调整"
        if price_basis not in {"current", "source"}:
            blockers.append("恢复基本单位前必须明确 --price-basis current 或 source")
        elif price_basis == "current":
            package = source_packages.get(target.base_uom_id)
            if not package:
                blockers.append("当前基本单位无法按来源包装折算价格")
            else:
                for field, value in proposed_prices.items():
                    if value is not None:
                        converted = value / package.qty_in_base
                        if converted != converted.quantize(Decimal("0.01")):
                            blockers.append(f"{field} 换算超出现有主档价格精度")
                        proposed_prices[field] = converted
    result = {
        "source": _master(source),
        "target": _master(target),
        "differences": differences(source, target),
        "stock": stock,
        "pending_transfer_reservations": pending_reservations,
        "history": {
            "sales_lines": lines.count(),
            "inventory_transactions": InventoryTransaction.objects.filter(product=target).count(),
            "cost_layers": layers.count(),
        },
        "price_basis": price_basis,
        "price_note": price_note,
        "proposed_prices": proposed_prices,
        "blockers": sorted(set(blockers)),
    }
    result["signature"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, default=str).encode()
    ).hexdigest()
    return result


@transaction.atomic
def repair_binding(binding_id, *, signature, price_basis, actor):
    """Apply one reviewed master repair, with all preconditions rechecked under locks."""
    binding = TransferProductBinding.objects.select_related("source_product", "target_product").get(
        pk=binding_id
    )
    from allapp.inventory.models import InventoryDetail
    from allapp.locations.models import Warehouse

    # Match posting order: warehouses, owners, products, then inventory rows.
    warehouses = InventoryDetail.all_objects.filter(
        product_id__in=[binding.source_product_id, binding.target_product_id]
    ).values_list("warehouse_id", flat=True)
    list(Warehouse.all_objects.select_for_update().filter(pk__in=warehouses).order_by("pk"))
    lock_family([binding.source_product_id, binding.target_product_id])
    binding = TransferProductBinding.objects.select_related("source_product", "target_product").get(
        pk=binding_id
    )
    before = report_binding(binding, price_basis=price_basis)
    if before["signature"] != signature:
        raise ValidationError("检查结果已变化，请重新生成并审核修复报告。")
    if before["blockers"]:
        raise ValidationError(before["blockers"])
    if not before["differences"]:
        return before
    source, target = binding.source_product, binding.target_product
    with _system_write([target.pk]):
        for field in PRODUCT_FIELDS:
            setattr(target, field, getattr(source, field))
        for field, value in before["proposed_prices"].items():
            setattr(target, field, value)
        target.updated_by = actor
        target.save()
        for package in source.packages.filter(is_active=True):
            target_package = ProductPackage.all_objects.filter(
                product=target, uom_id=package.uom_id
            ).first()
            if target_package:
                target_package.qty_in_base = package.qty_in_base
                target_package.updated_by = actor
                target_package.save()
            else:
                ProductPackage.objects.create(
                    product=target,
                    uom_id=package.uom_id,
                    qty_in_base=package.qty_in_base,
                    created_by=actor,
                )
    after = report_binding(binding, price_basis=price_basis)
    record_audit_event(
        action="products.transfer.repair",
        module="products",
        user=actor,
        obj=target,
        before=before,
        after=after,
    )
    return after
