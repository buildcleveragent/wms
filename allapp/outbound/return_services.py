"""Atomic, scoped PDA return posting and restoration of the original inventory."""

import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.inbound.models import Lot
from allapp.inventory.fifo import consume_fifo
from allapp.inventory.locking import (
    lock_warehouses_for_inventory_write,
    run_inventory_write_with_retry,
)
from allapp.inventory.models import (
    InventoryDetail,
    InventoryLayerMovement,
    InventoryLayerPosition,
    InventoryTransaction,
)
from allapp.inventory.services import _refresh_summaries
from allapp.locations.models import Warehouse

from .return_models import ReturnEvent, ReturnLine, ReturnOrder

ZERO = Decimal("0")
DIMENSIONS = (
    "owner_id",
    "warehouse_id",
    "product_id",
    "location_id",
    "subwarehouse_id",
    "zone_type",
    "batch_no",
    "production_date",
    "expiry_date",
    "serial_no",
    "container_id",
    "lot_id",
)


class ReturnConflict(APIException):
    status_code = 409
    default_detail = "单据或库存已变化，请刷新后重试。"


def return_scope(user):
    scope = AccessScope.for_user(user)
    if not (
        scope.is_valid
        and scope.single_warehouse_id
        and UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles
        and user.has_perm("outbound.process_pda_return")
    ):
        raise PermissionDenied("需要绑定单个仓库的操作员及退货出库权限。")
    if not Warehouse.objects.filter(pk=scope.single_warehouse_id, is_active=True).exists():
        raise PermissionDenied("绑定仓库不可用。")
    return scope


def can_process_return(user):
    try:
        return_scope(user)
        return True
    except PermissionDenied:
        return False


def allowed_owners(warehouse_id):
    return OwnerWarehouseBinding.objects.filter(
        warehouse_id=warehouse_id, is_active=True, owner__is_active=True, owner__is_deleted=False
    ).values_list("owner_id", flat=True)


def stock_queryset(user):
    warehouse_id = return_scope(user).single_warehouse_id
    return (
        InventoryDetail.objects.filter(
            warehouse_id=warehouse_id,
            owner_id__in=allowed_owners(warehouse_id),
            is_active=True,
            product__is_active=True,
            product__is_deleted=False,
            location__is_deleted=False,
            location__is_active=True,
        )
        .filter(
            Q(container__isnull=True) | Q(container__is_active=True, container__is_deleted=False)
        )
        .select_related("product__base_uom", "owner", "location", "lot__supplier", "container")
    )


def orders_for(user):
    warehouse_id = return_scope(user).single_warehouse_id
    return ReturnOrder.objects.filter(
        warehouse_id=warehouse_id, owner_id__in=allowed_owners(warehouse_id)
    )


def dimensions(detail):
    snapshot = {
        name: (value.isoformat() if hasattr(value, "isoformat") else value)
        for name in DIMENSIONS
        for value in [getattr(detail, name)]
    }
    snapshot["container_no"] = detail.container.container_no if detail.container_id else ""
    return snapshot


def source_supplier(detail):
    """Only a unique, matching lot can establish supplier provenance."""
    if not detail.batch_no:
        return None
    lots = Lot.objects.filter(
        owner_id=detail.owner_id,
        product_code=detail.product.code,
        lot_no_norm=detail.batch_no.strip().upper(),
        is_active=True,
    )
    if detail.lot_id:
        lots = lots.filter(pk=detail.lot_id)
    rows = list(lots.select_related("supplier")[:2])
    if len(rows) != 1:
        return None
    lot = rows[0]
    if (
        lot.product_id not in (None, detail.product_id)
        or lot.mfg_date != detail.production_date
        or lot.exp_date != detail.expiry_date
    ):
        return None
    supplier = lot.supplier
    if (
        not supplier
        or supplier.is_deleted
        or not supplier.is_active
        or supplier.owner_id != detail.owner_id
    ):
        return None
    return supplier


def stock_payload(detail):
    supplier = source_supplier(detail)
    return {
        "id": detail.id,
        "product_id": detail.product_id,
        "product_name": detail.product.name,
        "product_code": detail.product.code,
        "spec": detail.product.spec or "",
        "owner_id": detail.owner_id,
        "owner_name": detail.owner.name,
        "location_name": detail.location.code,
        "batch_no": detail.batch_no,
        "expiry_date": detail.expiry_date,
        "serial_no": detail.serial_no,
        "container_no": (
            getattr(detail.container, "container_no", "") if detail.container_id else ""
        ),
        "base_unit": detail.base_unit,
        "available_qty": detail.available_qty,
        "damaged_qty": detail.damaged_qty,
        "allocated_qty": detail.allocated_qty,
        "locked_qty": detail.locked_qty,
        "supplier_id": supplier.pk if supplier else None,
        "supplier_name": supplier.name if supplier else "",
    }


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(data, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _line_values(detail, item):
    supplier = source_supplier(detail)
    return {
        "inventory": detail,
        "product": detail.product,
        "product_name": detail.product.name,
        "product_code": detail.product.code,
        "spec": detail.product.spec or "",
        "base_unit": detail.base_unit,
        "location_name": detail.location.code,
        "dimensions": dimensions(detail),
        "source_supplier_id": supplier.pk if supplier else None,
        "source_supplier_name": supplier.name if supplier else "",
        **{key: item[key] for key in ("available_qty", "damaged_qty", "new_damage_qty")},
    }


def validate_lines(user, data):
    ids = [row["inventory_id"] for row in data["lines"]]
    if len(set(ids)) != len(ids):
        raise ValidationError("同一库存记录不能重复添加。")
    details = {row.pk: row for row in stock_queryset(user).filter(pk__in=ids).order_by("id")}
    if set(ids) != set(details):
        raise ValidationError("部分库存不存在、已停用或超出权限范围。")
    values = []
    for item in data["lines"]:
        detail = details[item["inventory_id"]]
        if detail.owner_id != data["owner_id"]:
            raise ValidationError("每张退货单只能包含所选货主的库存。")
        a, d, n = (item[key] for key in ("available_qty", "damaged_qty", "new_damage_qty"))
        if a + d + n <= 0:
            raise ValidationError("每行退货数量必须大于零。")
        if detail.product_serial_control and a + d + n != 1:
            raise ValidationError("序列号商品每行只能退一件。")
        if a + n > detail.available_qty or d > detail.damaged_qty:
            raise ValidationError("可退库存不足：正常及现场破损合计不能超过可用库存。")
        row = _line_values(detail, item)
        if data["recipient_type"] == "SUPPLIER" and (
            not row["source_supplier_id"] or row["source_supplier_id"] != data.get("supplier_id")
        ):
            raise ValidationError("退供应商必须是所有所选批次记录的同一原供应商。")
        values.append(row)
    return values


def write_draft(user, data, order_id=None):
    def operation():
        with transaction.atomic():
            warehouse_id = return_scope(user).single_warehouse_id
            lock_warehouses_for_inventory_write(warehouse_id)
            if order_id:
                order = orders_for(user).select_for_update().filter(pk=order_id).first()
                _editable(order, user, data["version"])
            else:
                fp = fingerprint(data)
                existing = ReturnOrder.all_objects.filter(
                    warehouse_id=warehouse_id, operator=user, request_id=data["request_id"]
                ).first()
                if existing:
                    if existing.is_deleted or existing.fingerprint != fp:
                        raise ReturnConflict("同一请求标识已用于其他内容或已删除单据。")
                    if not orders_for(user).filter(pk=existing.pk).exists():
                        raise PermissionDenied("单据已超出当前权限范围。")
                    return existing
            values = validate_lines(user, data)
            recipient_name = (
                values[0]["inventory"].owner.name
                if data["recipient_type"] == "OWNER"
                else values[0]["source_supplier_name"]
            )
            fields = {
                key: data.get(key)
                for key in ("owner_id", "recipient_type", "supplier_id", "reason")
            }
            fields["recipient_name"] = recipient_name
            if order_id:
                for key, value in fields.items():
                    setattr(order, key, value)
                order.version += 1
                order.save()
                order.lines.all().delete()
            else:
                order = ReturnOrder.objects.create(
                    return_no=f"RT{timezone.now():%Y%m%d}{uuid4().hex[:16].upper()}",
                    warehouse_id=warehouse_id,
                    operator=user,
                    request_id=data["request_id"],
                    fingerprint=fp,
                    **fields,
                )
            ReturnLine.objects.bulk_create([ReturnLine(order=order, **row) for row in values])
            return order

    return run_inventory_write_with_retry(operation, operation_name="pda_return.draft")


def _editable(order, user, version):
    if not order or order.operator_id != user.pk:
        raise PermissionDenied("只能操作本人单据。")
    if order.status != "DRAFT" or order.version != version:
        raise ReturnConflict()


def delete_draft(user, order_id, version):
    with transaction.atomic():
        warehouse_id = return_scope(user).single_warehouse_id
        lock_warehouses_for_inventory_write(warehouse_id)
        order = (
            ReturnOrder.all_objects.select_for_update()
            .filter(
                pk=order_id,
                warehouse_id=warehouse_id,
                owner_id__in=allowed_owners(warehouse_id),
            )
            .first()
        )
        if order is None:
            raise NotFound()
        _editable(order, user, version)
        # A response may be lost after soft deletion. The same scoped request is safe to replay.
        if order.is_deleted:
            return
        order.is_deleted = True
        order.deleted_at = timezone.now()
        order.deleted_by = user
        order.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_at"])


def _inventory_tx(order, line, detail, qty, reverse, now):
    return InventoryTransaction.objects.create(
        **{key: getattr(detail, key) for key in DIMENSIONS if key != "lot_id"},
        tx_type="RECEIVE" if reverse else "ISSUE",
        qty_delta=qty,
        src_model="ReturnOrder",
        src_id=order.pk,
        src_line_id=line.pk,
        src_no=order.return_no,
        memo="PDA_RETURN_REVERSE" if reverse else "PDA_RETURN",
        posted_at=now,
        posting_batch=f"{order.return_no}{'-R' if reverse else ''}",
    )


def _restore_fifo(line, detail, tx, user, now):
    for entry in line.fifo_consumed:
        position = (
            InventoryLayerPosition.objects.select_for_update()
            .filter(
                pk=entry["position_id"],
                layer_id=entry["layer_id"],
                location_id=detail.location_id,
                container_id=detail.container_id,
            )
            .first()
        )
        if position is None:
            raise ValidationError("原成本层位置已失效，无法冲销。")
        qty = Decimal(entry["quantity"])
        position.remaining_qty += qty
        position.save(update_fields=["remaining_qty", "updated_at"])
        InventoryLayerMovement.objects.create(
            layer_id=entry["layer_id"],
            inventory_transaction=tx,
            movement_type="RECEIVE",
            to_location_id=detail.location_id,
            quantity=qty,
            occurred_at=now,
            created_by=user,
        )


def post_return(user, order_id, data, *, reverse=False):
    def operation():
        with transaction.atomic():
            warehouse_id = return_scope(user).single_warehouse_id
            lock_warehouses_for_inventory_write(warehouse_id)
            order = orders_for(user).select_for_update().filter(pk=order_id).first()
            if not order or order.operator_id != user.pk:
                raise PermissionDenied("只能操作本人单据。")
            key_field = "reverse_key" if reverse else "confirm_key"
            if getattr(order, key_field) == data["request_id"]:
                if reverse and order.reversal_reason != data["reason"]:
                    raise ReturnConflict("冲销请求内容与原请求不一致。")
                return order
            expected = "COMPLETED" if reverse else "DRAFT"
            if order.status != expected or order.version != data["version"]:
                raise ReturnConflict()
            lines = list(order.lines.select_related("product").order_by("inventory_id"))
            if not lines:
                raise ValidationError("退货明细不能为空。")
            details = {
                row.pk: row
                for row in stock_queryset(user)
                .select_for_update()
                .filter(pk__in=[line.inventory_id for line in lines])
                .order_by("id")
            }
            now = timezone.now()
            for line in lines:
                detail = details.get(line.inventory_id)
                if (
                    not detail
                    or dimensions(detail) != line.dimensions
                    or detail.base_unit != line.base_unit
                    or detail.product.base_uom.code != line.base_unit
                ):
                    raise ReturnConflict("原库存维度已失效或变化，请核对后重试。")
                a, d, n = line.available_qty, line.damaged_qty, line.new_damage_qty
                total = a + d + n
                if not reverse:
                    supplier = source_supplier(detail)
                    if order.recipient_type == "SUPPLIER" and (
                        not supplier or supplier.pk != order.supplier_id
                    ):
                        raise ReturnConflict("批次供应商来源已变化。")
                    if a + n > detail.available_qty or d > detail.damaged_qty:
                        raise ReturnConflict("可退库存不足，请刷新库存后修改草稿。")
                    if n:
                        ReturnEvent.objects.create(
                            line=line,
                            kind="DAMAGE",
                            actor=user,
                            occurred_at=now,
                            reason=order.reason,
                            damaged_delta=n,
                        )
                    detail.onhand_qty -= total
                    detail.damaged_qty -= d
                else:
                    detail.onhand_qty += total
                    detail.damaged_qty += d + n
                detail.save()
                tx = _inventory_tx(order, line, detail, total if reverse else -total, reverse, now)
                if reverse and settings.INVENTORY_FIFO_ENABLED and not line.fifo_used:
                    raise ValidationError(
                        "原退货未记录成本层，当前已启用 FIFO，请核对成本层后处理。"
                    )
                if reverse and line.fifo_used:
                    _restore_fifo(line, detail, tx, user, now)
                elif not reverse and settings.INVENTORY_FIFO_ENABLED:
                    # Restrict FIFO to this inventory's exact lot/expiry/container dimensions.
                    positions = InventoryLayerPosition.objects.filter(
                        layer__owner_id=detail.owner_id,
                        layer__warehouse_id=detail.warehouse_id,
                        layer__product_id=detail.product_id,
                        layer__batch_no=detail.batch_no,
                        layer__serial_no=detail.serial_no,
                        layer__expiry_date=detail.expiry_date,
                        location_id=detail.location_id,
                        container_id=detail.container_id,
                    )
                    if detail.lot_id:
                        positions = positions.filter(layer__lot_id=detail.lot_id)
                    position_map = {p.layer_id: p.pk for p in positions}
                    consumed = consume_fifo(
                        owner_id=detail.owner_id,
                        warehouse_id=detail.warehouse_id,
                        product_id=detail.product_id,
                        location_id=detail.location_id,
                        quantity=total,
                        batch_no=detail.batch_no,
                        serial_no=detail.serial_no,
                        container_id=detail.container_id,
                        exact_container=True,
                        layer_ids=list(position_map),
                        inventory_transaction_id=tx.pk,
                        occurred_at=now,
                        by_user=user,
                    )
                    line.fifo_used = True
                    line.fifo_consumed = [
                        {
                            **item,
                            "quantity": str(item["quantity"]),
                            "position_id": position_map[item["layer_id"]],
                        }
                        for item in consumed
                    ]
                    line.save(update_fields=["fifo_used", "fifo_consumed"])
                ReturnEvent.objects.create(
                    line=line,
                    kind="REVERSE" if reverse else "CONFIRM",
                    actor=user,
                    occurred_at=now,
                    reason=data["reason"] if reverse else order.reason,
                    damaged_delta=d + n if reverse else -(d + n),
                    inventory_transaction=tx,
                )
            _refresh_summaries({(order.owner_id, line.product_id) for line in lines})
            setattr(order, key_field, data["request_id"])
            order.status = "REVERSED" if reverse else "COMPLETED"
            order.version += 1
            if reverse:
                order.reversed_at = now
                order.reversal_reason = data["reason"]
            else:
                order.confirmed_at = now
            order.save()
            return order

    try:
        return run_inventory_write_with_retry(
            operation, operation_name="pda_return.reverse" if reverse else "pda_return.confirm"
        )
    except (DjangoValidationError, ValueError) as exc:
        raise ValidationError(getattr(exc, "messages", None) or str(exc)) from exc
