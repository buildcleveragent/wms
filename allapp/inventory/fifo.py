"""Transactional FIFO layer primitives.

Posting paths can call these functions inside their existing atomic block.  No
caller is allowed to silently manufacture cost: missing cost creates a clearly
marked layer and keeps warehouse operations available.
"""

from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from allapp.products.models import Product

from .models import (
    InventoryCostAdjustment,
    InventoryCostLayer,
    InventoryLayerMovement,
    InventoryLayerPosition,
)


@transaction.atomic
def create_receipt_layer(
    *,
    owner_id,
    warehouse_id,
    product_id,
    location_id,
    quantity,
    source_type,
    source_id,
    source_line_id="",
    received_date=None,
    lot_id=None,
    batch_no="",
    serial_no="",
    expiry_date=None,
    container_id=None,
    unit_cost=None,
    cost_currency="",
    inventory_transaction_id=None,
    movement_type=InventoryLayerMovement.MovementType.RECEIVE,
    by_user=None,
):
    quantity = Decimal(quantity)
    quality = (
        InventoryCostLayer.CostQuality.VERIFIED
        if unit_cost is not None and cost_currency
        else InventoryCostLayer.CostQuality.COST_MISSING
    )
    base_uom_id = Product.objects.only("base_uom_id").get(pk=product_id).base_uom_id
    layer, created = InventoryCostLayer.objects.get_or_create(
        source_type=source_type,
        source_id=str(source_id),
        source_line_id=str(source_line_id or ""),
        product_id=product_id,
        batch_no=batch_no or "",
        serial_no=serial_no or "",
        defaults={
            "owner_id": owner_id,
            "warehouse_id": warehouse_id,
            "base_uom_id": base_uom_id,
            "lot_id": lot_id,
            "expiry_date": expiry_date,
            "received_date": received_date,
            "original_qty": quantity,
            "unit_cost": unit_cost,
            "cost_currency": cost_currency or "",
            "cost_quality": quality,
        },
    )
    if not created:
        return layer, False
    InventoryLayerPosition.objects.create(
        layer=layer,
        location_id=location_id,
        container_id=container_id,
        remaining_qty=quantity,
    )
    InventoryLayerMovement.objects.create(
        layer=layer,
        inventory_transaction_id=inventory_transaction_id,
        movement_type=movement_type,
        to_location_id=location_id,
        quantity=quantity,
        occurred_at=timezone.now(),
        created_by=by_user,
    )
    return layer, True


@transaction.atomic
def consume_fifo(
    *,
    owner_id,
    warehouse_id,
    product_id,
    location_id,
    quantity,
    batch_no="",
    serial_no="",
    container_id=None,
    inventory_transaction_id=None,
    occurred_at=None,
    movement_type=InventoryLayerMovement.MovementType.ISSUE,
    by_user=None,
):
    remaining = Decimal(quantity)
    if remaining <= 0:
        raise ValueError("FIFO issue quantity must be positive.")
    positions = (
        InventoryLayerPosition.objects.select_for_update()
        .select_related("layer")
        .filter(
            layer__owner_id=owner_id,
            layer__warehouse_id=warehouse_id,
            layer__product_id=product_id,
            layer__batch_no=batch_no or "",
            layer__serial_no=serial_no or "",
            location_id=location_id,
            remaining_qty__gt=0,
        )
        .order_by(F("layer__received_date").asc(nulls_last=True), "layer_id", "id")
    )
    if container_id is not None:
        positions = positions.filter(container_id=container_id)
    available = sum((row.remaining_qty for row in positions), Decimal("0"))
    if available < remaining:
        raise ValueError(
            f"FIFO layer balance insufficient: required={remaining}, available={available}."
        )
    consumed = []
    for position in positions:
        if remaining <= 0:
            break
        take = min(position.remaining_qty, remaining)
        position.remaining_qty -= take
        position.save(update_fields=["remaining_qty", "updated_at"])
        InventoryLayerMovement.objects.create(
            layer=position.layer,
            inventory_transaction_id=inventory_transaction_id,
            movement_type=movement_type,
            from_location_id=location_id,
            quantity=take,
            occurred_at=occurred_at or timezone.now(),
            created_by=by_user,
        )
        consumed.append({"layer_id": position.layer_id, "quantity": take})
        remaining -= take
    return consumed


@transaction.atomic
def move_layer_quantity(
    *,
    layer_id,
    from_location_id,
    to_location_id,
    quantity,
    from_container_id=None,
    to_container_id=None,
    inventory_transaction_id=None,
    occurred_at=None,
    by_user=None,
):
    quantity = Decimal(quantity)
    source = InventoryLayerPosition.objects.select_for_update().get(
        layer_id=layer_id, location_id=from_location_id, container_id=from_container_id
    )
    if quantity <= 0 or source.remaining_qty < quantity:
        raise ValueError("Invalid or insufficient FIFO move quantity.")
    source.remaining_qty -= quantity
    source.save(update_fields=["remaining_qty", "updated_at"])
    target, _ = InventoryLayerPosition.objects.select_for_update().get_or_create(
        layer_id=layer_id,
        location_id=to_location_id,
        container_id=to_container_id,
        defaults={"remaining_qty": Decimal("0")},
    )
    target.remaining_qty = F("remaining_qty") + quantity
    target.save(update_fields=["remaining_qty", "updated_at"])
    target.refresh_from_db(fields=["remaining_qty"])
    InventoryLayerMovement.objects.create(
        layer_id=layer_id,
        inventory_transaction_id=inventory_transaction_id,
        movement_type=InventoryLayerMovement.MovementType.MOVE,
        from_location_id=from_location_id,
        to_location_id=to_location_id,
        quantity=quantity,
        occurred_at=occurred_at or timezone.now(),
        created_by=by_user,
    )
    return target


@transaction.atomic
def move_fifo(
    *,
    owner_id,
    warehouse_id,
    product_id,
    from_location_id,
    to_location_id,
    quantity,
    batch_no="",
    serial_no="",
    from_container_id=None,
    to_container_id=None,
    inventory_transaction_id=None,
    occurred_at=None,
    by_user=None,
):
    """Move the oldest matching positions while preserving layer age and cost."""

    remaining = Decimal(quantity)
    if remaining <= 0:
        raise ValueError("FIFO move quantity must be positive.")
    positions = (
        InventoryLayerPosition.objects.select_for_update()
        .select_related("layer")
        .filter(
            layer__owner_id=owner_id,
            layer__warehouse_id=warehouse_id,
            layer__product_id=product_id,
            layer__batch_no=batch_no or "",
            layer__serial_no=serial_no or "",
            location_id=from_location_id,
            remaining_qty__gt=0,
        )
        .order_by(F("layer__received_date").asc(nulls_last=True), "layer_id", "id")
    )
    if from_container_id is not None:
        positions = positions.filter(container_id=from_container_id)
    available = sum((row.remaining_qty for row in positions), Decimal("0"))
    if available < remaining:
        raise ValueError(
            f"FIFO layer balance insufficient: required={remaining}, available={available}."
        )
    moved = []
    for source in positions:
        if remaining <= 0:
            break
        take = min(source.remaining_qty, remaining)
        source.remaining_qty -= take
        source.save(update_fields=["remaining_qty", "updated_at"])
        target = (
            InventoryLayerPosition.objects.select_for_update()
            .filter(
                layer_id=source.layer_id,
                location_id=to_location_id,
                container_scope_id=to_container_id or 0,
            )
            .first()
        )
        if target is None:
            target = InventoryLayerPosition.objects.create(
                layer_id=source.layer_id,
                location_id=to_location_id,
                container_id=to_container_id,
                remaining_qty=take,
            )
        else:
            target.remaining_qty += take
            target.save(update_fields=["remaining_qty", "updated_at"])
        InventoryLayerMovement.objects.create(
            layer=source.layer,
            inventory_transaction_id=inventory_transaction_id,
            movement_type=InventoryLayerMovement.MovementType.MOVE,
            from_location_id=from_location_id,
            to_location_id=to_location_id,
            quantity=take,
            occurred_at=occurred_at or timezone.now(),
            created_by=by_user,
        )
        moved.append({"layer_id": source.layer_id, "quantity": take})
        remaining -= take
    return moved


@transaction.atomic
def adjust_layer_cost(*, layer_id, new_unit_cost, new_currency, reason, by_user):
    layer = InventoryCostLayer.objects.select_for_update().get(pk=layer_id)
    adjustment = InventoryCostAdjustment.objects.create(
        layer=layer,
        old_unit_cost=layer.unit_cost,
        new_unit_cost=new_unit_cost,
        old_currency=layer.cost_currency,
        new_currency=new_currency,
        reason=reason,
        effective_at=timezone.now(),
        created_by=by_user,
    )
    layer.unit_cost = new_unit_cost
    layer.cost_currency = new_currency
    layer.cost_quality = InventoryCostLayer.CostQuality.VERIFIED
    layer.save(update_fields=["unit_cost", "cost_currency", "cost_quality"])
    return adjustment
