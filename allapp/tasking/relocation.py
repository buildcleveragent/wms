from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from allapp.baseinfo.models import OwnerWarehouseBinding
from allapp.core.choices import InvTxType
from allapp.core.models import DocSequence
from allapp.inventory.locking import lock_warehouses_for_inventory_write
from allapp.inventory.models import (
    InventoryDetail,
    InventoryTransaction,
)
from allapp.locations.models import Container, Location
from allapp.tasking.models import (
    ContainerUsage,
    RelocationRequest,
    RelocationRequestLine,
    RelocationReservation,
    RelocLineExtra,
    RelocTaskExtra,
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)

QTY_QUANT = Decimal("0.0001")


class RelocationIdempotencyConflict(Exception):
    pass


def q3(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def _snapshot(detail: InventoryDetail) -> dict:
    return {
        "inventory_detail_id": detail.pk,
        "product_id": detail.product_id,
        "location_id": detail.location_id,
        "container_id": detail.container_id,
        "batch_no": detail.batch_no or "",
        "production_date": (detail.production_date.isoformat() if detail.production_date else None),
        "expiry_date": detail.expiry_date.isoformat() if detail.expiry_date else None,
        "serial_no": detail.serial_no or "",
        "zone_type": detail.zone_type,
    }


def container_tree_ids(root: Container, *, lock: bool = False) -> list[int]:
    """Return a stable, cycle-safe pre-order list for one container subtree."""
    result: list[int] = []
    pending = [root.pk]
    seen: set[int] = set()
    while pending:
        batch = sorted(set(pending) - seen)
        pending = []
        if not batch:
            break
        qs = Container.objects.filter(pk__in=batch).order_by("id")
        if lock:
            qs = qs.select_for_update()
        current = list(qs)
        if len(current) != len(batch):
            raise ValidationError("容器树中存在已删除或不可用的容器。")
        for item in current:
            if item.pk in seen:
                raise ValidationError("容器树存在循环引用。")
            seen.add(item.pk)
            result.append(item.pk)
        child_qs = Container.objects.filter(parent_id__in=batch).order_by("id")
        pending.extend(child_qs.values_list("pk", flat=True))
    return result


def _validate_location(location: Location, warehouse_id: int, *, label: str) -> None:
    if location.warehouse_id != warehouse_id:
        raise ValidationError(f"{label}不属于申请仓库。")
    if not location.is_active or location.is_disabled or location.is_frozen:
        raise ValidationError(f"{label}已停用或冻结。")


def _validate_container_scope(
    container: Container | None, *, owner_id: int, warehouse_id: int
) -> None:
    if container is None:
        return
    if not container.is_active:
        raise ValidationError("容器已停用。")
    if container.warehouse_id != warehouse_id:
        raise ValidationError("容器不属于申请仓库。")
    if container.scope == Container.Scope.PRIVATE and container.owner_id != owner_id:
        raise ValidationError("私有容器不属于申请货主。")


def _validate_target_capacity(lines: list[RelocationRequestLine]) -> None:
    """Validate configured location/category/container limits for the full request."""
    if not lines:
        raise ValidationError("移库申请没有有效明细。")

    location_deltas = defaultdict(lambda: {"volume": Decimal("0"), "weight": Decimal("0")})
    container_deltas = defaultdict(lambda: Decimal("0"))
    target_container_ids: set[int] = set()
    products_by_location: dict[int, set[int]] = defaultdict(set)
    target_locations: dict[int, Location] = {}

    for req_line in lines:
        detail = req_line.inventory_detail
        qty = Decimal(req_line.requested_qty)
        target = req_line.to_location
        target_locations[target.pk] = target
        products_by_location[target.pk].add(detail.product_id)
        if detail.location_id != target.pk:
            volume = Decimal(detail.product.volume or 0) * qty
            weight = Decimal(detail.product.weight or 0) * qty
            location_deltas[target.pk]["volume"] += volume
            location_deltas[target.pk]["weight"] += weight
            location_deltas[detail.location_id]["volume"] -= volume
            location_deltas[detail.location_id]["weight"] -= weight
        if req_line.to_container_id and req_line.to_container_id != detail.container_id:
            weight = Decimal(detail.product.weight or 0) * qty
            target_container_ids.add(req_line.to_container_id)
            container_deltas[req_line.to_container_id] += weight
            if detail.container_id:
                container_deltas[detail.container_id] -= weight

    for location_id, target in target_locations.items():
        configured_categories = set(target.product_categories.values_list("pk", flat=True))
        if configured_categories:
            invalid = (
                InventoryDetail.objects.filter(product_id__in=products_by_location[location_id])
                .exclude(product__category_id__in=configured_categories)
                .exists()
            )
            if invalid:
                raise ValidationError(f"目标库位 {target.code} 不允许存放申请中的商品分类。")

        current = list(
            InventoryDetail.objects.filter(location_id=location_id, is_active=True).select_related(
                "product"
            )
        )
        current_volume = sum(
            (Decimal(row.product.volume or 0) * Decimal(row.onhand_qty or 0) for row in current),
            Decimal("0"),
        )
        current_weight = sum(
            (Decimal(row.product.weight or 0) * Decimal(row.onhand_qty or 0) for row in current),
            Decimal("0"),
        )
        delta = location_deltas[location_id]
        if (
            target.max_volume_m3 is not None
            and current_volume + delta["volume"] > target.max_volume_m3
        ):
            raise ValidationError(f"目标库位 {target.code} 超出最大体积。")
        if (
            target.max_weight_kg is not None
            and current_weight + delta["weight"] > target.max_weight_kg
        ):
            raise ValidationError(f"目标库位 {target.code} 超出最大承重。")

    for container_id in target_container_ids:
        container = Container.objects.get(pk=container_id)
        if container.max_gross_kg is None:
            continue
        tree_ids = container_tree_ids(container)
        incoming_weight = sum((container_deltas[item_id] for item_id in tree_ids), Decimal("0"))
        current_weight = sum(
            (
                Decimal(row.product.weight or 0) * Decimal(row.onhand_qty or 0)
                for row in InventoryDetail.objects.filter(
                    container_id__in=tree_ids, is_active=True
                ).select_related("product")
            ),
            Decimal("0"),
        )
        tare = sum(
            (
                Decimal(value or 0)
                for value in Container.objects.filter(pk__in=tree_ids).values_list(
                    "tare_kg", flat=True
                )
            ),
            Decimal("0"),
        )
        gross = tare + current_weight + incoming_weight
        if gross > container.max_gross_kg:
            raise ValidationError(f"目标容器 {container.container_no} 超出最大毛重。")


def _validate_parent_container_capacity(
    parent: Container | None, *, moving_tree_ids: list[int]
) -> None:
    if parent is None or parent.max_gross_kg is None:
        return
    parent_tree_ids = container_tree_ids(parent)
    current_inventory_weight = sum(
        (
            Decimal(row.product.weight or 0) * Decimal(row.onhand_qty or 0)
            for row in InventoryDetail.objects.filter(
                container_id__in=parent_tree_ids,
                is_active=True,
            ).select_related("product")
        ),
        Decimal("0"),
    )
    current_tare = sum(
        (
            Decimal(value or 0)
            for value in Container.objects.filter(pk__in=parent_tree_ids).values_list(
                "tare_kg", flat=True
            )
        ),
        Decimal("0"),
    )
    incoming_inventory_weight = sum(
        (
            Decimal(row.product.weight or 0) * Decimal(row.onhand_qty or 0)
            for row in InventoryDetail.objects.filter(
                container_id__in=moving_tree_ids,
                is_active=True,
            ).select_related("product")
        ),
        Decimal("0"),
    )
    incoming_tare = sum(
        (
            Decimal(value or 0)
            for value in Container.objects.filter(pk__in=moving_tree_ids).values_list(
                "tare_kg", flat=True
            )
        ),
        Decimal("0"),
    )
    if (
        current_inventory_weight + current_tare + incoming_inventory_weight + incoming_tare
        > Decimal(parent.max_gross_kg)
    ):
        raise ValidationError(f"目标父容器 {parent.container_no} 超出最大毛重。")


def _new_request(
    *,
    owner,
    warehouse,
    mode: str,
    trigger: str,
    reason: str,
    by_user,
    source_container=None,
    to_location=None,
    target_parent_container=None,
) -> RelocationRequest:
    if not OwnerWarehouseBinding.objects.filter(
        owner=owner, warehouse=warehouse, is_active=True
    ).exists():
        raise ValidationError("货主未绑定该仓库。")
    return RelocationRequest.objects.create(
        owner=owner,
        warehouse=warehouse,
        mode=mode,
        trigger=trigger,
        reason=(reason or "").strip()[:200],
        source_container=source_container,
        to_location=to_location,
        target_parent_container=target_parent_container,
        created_by=by_user,
        updated_by=by_user,
    )


@transaction.atomic
def create_layer_request(
    *, owner, warehouse, lines: list[dict], reason: str, by_user, trigger="REQUEST"
):
    if not lines:
        raise ValidationError("至少需要一条移库明细。")
    request = _new_request(
        owner=owner,
        warehouse=warehouse,
        mode=RelocationRequest.Mode.LAYER,
        trigger=trigger,
        reason=reason,
        by_user=by_user,
    )
    seen: set[int] = set()
    for raw in lines:
        detail_id = int(raw["inventory_detail_id"])
        if detail_id in seen:
            raise ValidationError("同一来源库存层不能在一个申请中重复提交。")
        seen.add(detail_id)
        detail = (
            InventoryDetail.objects.select_for_update()
            .select_related("product", "location", "container")
            .get(pk=detail_id, is_active=True)
        )
        qty = q3(raw["qty"])
        target = Location.objects.get(pk=int(raw["to_location_id"]))
        target_container = None
        if raw.get("to_container_id"):
            target_container = Container.objects.get(pk=int(raw["to_container_id"]))
        if detail.owner_id != owner.pk or detail.warehouse_id != warehouse.pk:
            raise ValidationError("来源库存超出申请货主或仓库范围。")
        _validate_location(detail.location, warehouse.pk, label="来源库位")
        _validate_location(target, warehouse.pk, label="目标库位")
        _validate_container_scope(detail.container, owner_id=owner.pk, warehouse_id=warehouse.pk)
        _validate_container_scope(target_container, owner_id=owner.pk, warehouse_id=warehouse.pk)
        if target_container and target_container.location_id != target.pk:
            raise ValidationError("目标容器不在目标库位。")
        if detail.container_id and detail.container.children.exists():
            raise ValidationError("非叶子容器不能通过库存层模式拆分。")
        if any(
            Decimal(value or 0) != 0
            for value in (detail.allocated_qty, detail.locked_qty, detail.damaged_qty)
        ):
            raise ValidationError(f"来源库存 {detail.pk} 已分配、锁定或损坏，不能移库。")
        if qty <= 0 or qty > q3(detail.available_qty):
            raise ValidationError(
                f"来源库存 {detail.pk} 可移数量不足；当前最多 {q3(detail.available_qty)}。"
            )
        if detail.product.serial_control and qty != Decimal("1.000"):
            raise ValidationError("序列号商品必须逐件移库，数量必须为1。")
        if detail.location_id == target.pk and detail.container_id == getattr(
            target_container, "pk", None
        ):
            raise ValidationError("来源与目标库位、容器完全相同。")
        RelocationRequestLine.objects.create(
            request=request,
            inventory_detail=detail,
            requested_qty=qty,
            to_location=target,
            to_container=target_container,
            source_snapshot=_snapshot(detail),
            created_by=by_user,
            updated_by=by_user,
        )
    _validate_target_capacity(
        list(
            request.lines.select_related("inventory_detail__product", "to_location", "to_container")
        )
    )
    return request


@transaction.atomic
def create_container_request(
    *,
    owner,
    warehouse,
    source_container,
    to_location,
    reason: str,
    by_user,
    target_parent_container=None,
    trigger="REQUEST",
):
    root = Container.objects.select_for_update().get(pk=source_container.pk)
    target = Location.objects.get(pk=to_location.pk)
    _validate_container_scope(root, owner_id=owner.pk, warehouse_id=warehouse.pk)
    _validate_location(target, warehouse.pk, label="目标库位")
    if root.location_id is None:
        raise ValidationError("来源容器没有当前位置。")
    _validate_location(root.location, warehouse.pk, label="来源库位")
    tree_ids = container_tree_ids(root, lock=True)
    if Container.objects.filter(pk__in=tree_ids, is_active=False).exists():
        raise ValidationError("来源容器树中存在已停用容器。")
    if Container.objects.filter(pk__in=tree_ids).exclude(location_id=root.location_id).exists():
        raise ValidationError("容器树中存在与根容器位置不一致的容器。")
    if target_parent_container:
        parent = Container.objects.select_for_update().get(pk=target_parent_container.pk)
        _validate_container_scope(parent, owner_id=owner.pk, warehouse_id=warehouse.pk)
        if parent.pk in tree_ids:
            raise ValidationError("目标父容器不能属于待移动容器树。")
        if parent.location_id != target.pk:
            raise ValidationError("目标父容器不在目标库位。")
        _validate_parent_container_capacity(parent, moving_tree_ids=tree_ids)
    if root.location_id == target.pk:
        raise ValidationError("整容器移库的来源与目标库位不能相同。")

    request = _new_request(
        owner=owner,
        warehouse=warehouse,
        mode=RelocationRequest.Mode.CONTAINER,
        trigger=trigger,
        reason=reason,
        by_user=by_user,
        source_container=root,
        to_location=target,
        target_parent_container=target_parent_container,
    )
    details = list(
        InventoryDetail.objects.select_for_update()
        .filter(container_id__in=tree_ids, is_active=True, onhand_qty__gt=0)
        .select_related("product", "location", "container")
        .order_by("id")
    )
    if not details:
        raise ValidationError("来源容器树没有可移动库存。")
    for detail in details:
        if detail.owner_id != owner.pk or detail.warehouse_id != warehouse.pk:
            raise ValidationError("容器树中存在不同货主或仓库的库存。")
        if (
            detail.location_id != root.location_id
            or detail.container.location_id != root.location_id
        ):
            raise ValidationError("容器树库存与容器位置不一致。")
        if any(
            Decimal(value or 0) != 0
            for value in (detail.allocated_qty, detail.locked_qty, detail.damaged_qty)
        ):
            raise ValidationError(f"库存层 {detail.pk} 已分配、锁定或损坏，不能整容器移动。")
        RelocationRequestLine.objects.create(
            request=request,
            inventory_detail=detail,
            requested_qty=q3(detail.onhand_qty),
            to_location=target,
            to_container=detail.container,
            source_snapshot=_snapshot(detail),
            created_by=by_user,
            updated_by=by_user,
        )
    _validate_target_capacity(
        list(
            request.lines.select_related("inventory_detail__product", "to_location", "to_container")
        )
    )
    return request


def _release_task(task: WmsTask, *, by_user) -> None:
    now = timezone.now()
    old = task.status
    task.status = WmsTask.Status.RELEASED
    task.released_at = now
    task.updated_by = by_user
    task.save(update_fields=["status", "released_at", "updated_by", "updated_at"])
    task.lines.update(status=WmsTaskLine.Status.RELEASED, updated_by=by_user, updated_at=now)
    TaskStatusLog.objects.create(
        task=task,
        old_status=old,
        new_status=WmsTask.Status.RELEASED,
        changed_by=by_user,
        note="移库申请审核并发布",
    )


@transaction.atomic
def approve_request(request_id: int, *, by_user, note: str = "") -> WmsTask:
    warehouse_id = (
        RelocationRequest.objects.filter(pk=request_id).values_list("warehouse_id", flat=True).get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    request = (
        RelocationRequest.objects.select_for_update()
        .select_related(
            "owner",
            "warehouse",
            "source_container",
            "to_location",
            "target_parent_container",
        )
        .get(pk=request_id)
    )
    if request.status == RelocationRequest.Status.APPROVED and request.generated_task_id:
        return request.generated_task
    if request.status != RelocationRequest.Status.PENDING:
        raise ValidationError("只有待审核的移库申请可以批准。")
    if request.warehouse_id != warehouse_id:
        raise ValidationError("移库申请仓库在审核期间发生变化，请重试。")
    lines = list(
        RelocationRequestLine.objects.select_for_update()
        .filter(request=request)
        .select_related(
            "inventory_detail__product",
            "inventory_detail__location",
            "inventory_detail__container",
            "to_location",
            "to_container",
        )
        .order_by("inventory_detail_id", "id")
    )
    if not lines:
        raise ValidationError("移库申请没有明细。")
    if request.mode == RelocationRequest.Mode.CONTAINER:
        current_tree = set(container_tree_ids(request.source_container, lock=True))
        if Container.objects.filter(pk__in=current_tree, is_active=False).exists():
            raise ValidationError("来源容器树中存在已停用容器。")
        if (
            Container.objects.filter(pk__in=current_tree)
            .exclude(location_id=request.source_container.location_id)
            .exists()
        ):
            raise ValidationError("来源容器树位置已变化，请重新提交。")
        if any(line.inventory_detail.container_id not in current_tree for line in lines):
            raise ValidationError("来源容器树结构已变化，请重新提交。")
        if request.target_parent_container_id:
            parent = Container.objects.select_for_update().get(
                pk=request.target_parent_container_id
            )
            _validate_container_scope(
                parent, owner_id=request.owner_id, warehouse_id=request.warehouse_id
            )
            if parent.pk in current_tree:
                raise ValidationError("目标父容器不能属于待移动容器树。")
            if parent.location_id != request.to_location_id:
                raise ValidationError("目标父容器位置已变化，请重新提交。")
        _validate_parent_container_capacity(
            request.target_parent_container, moving_tree_ids=sorted(current_tree)
        )
    source_ids = [line.inventory_detail_id for line in lines]
    locked_sources = {
        row.pk: row
        for row in InventoryDetail.objects.select_for_update()
        .filter(pk__in=source_ids)
        .select_related("product", "location", "container")
        .order_by("id")
    }
    if len(locked_sources) != len(set(source_ids)):
        raise ValidationError("部分来源库存已不存在。")

    for req_line in lines:
        detail = locked_sources[req_line.inventory_detail_id]
        req_line.inventory_detail = detail
        qty = q3(req_line.requested_qty)
        _validate_location(detail.location, request.warehouse_id, label="来源库位")
        _validate_location(req_line.to_location, request.warehouse_id, label="目标库位")
        if detail.owner_id != request.owner_id or detail.warehouse_id != request.warehouse_id:
            raise ValidationError("来源库存超出申请范围。")
        if qty > q3(detail.available_qty):
            raise ValidationError(
                f"来源库存 {detail.pk} 不足；申请 {qty}，当前最多可移 {q3(detail.available_qty)}。"
            )
        if any(
            Decimal(value or 0) != 0
            for value in (detail.allocated_qty, detail.locked_qty, detail.damaged_qty)
        ):
            raise ValidationError(f"来源库存 {detail.pk} 已分配、锁定或损坏，不能移库。")
        if request.mode == RelocationRequest.Mode.CONTAINER and qty != q3(detail.onhand_qty):
            raise ValidationError("整容器申请的库存数量已变化，请重新提交。")
        if req_line.source_snapshot != _snapshot(detail):
            raise ValidationError(f"来源库存 {detail.pk} 的批次、位置或容器已变化，请重新提交。")
        _validate_container_scope(
            req_line.to_container,
            owner_id=request.owner_id,
            warehouse_id=request.warehouse_id,
        )
        if req_line.to_container and req_line.to_container.location_id != req_line.to_location_id:
            if request.mode != RelocationRequest.Mode.CONTAINER:
                raise ValidationError("目标容器不在目标库位。")

        from allapp.tasking.counting import assert_inventory_not_count_locked

        for location_id in (detail.location_id, req_line.to_location_id):
            assert_inventory_not_count_locked(
                owner_id=request.owner_id,
                warehouse_id=request.warehouse_id,
                product_id=detail.product_id,
                location_id=location_id,
                batch_no=detail.batch_no or "",
            )
    _validate_target_capacity(lines)

    task = WmsTask.objects.create(
        task_no=DocSequence.next_code(
            doc_type="RLC", warehouse=request.warehouse, owner=request.owner
        ),
        task_type=WmsTask.TaskType.RELOC,
        status=WmsTask.Status.DRAFT,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
        owner=request.owner,
        warehouse=request.warehouse,
        ref_no=f"RLC-REQ-{request.pk}",
        source_app="tasking",
        source_model="RelocationRequest",
        source_pk=str(request.pk),
        remark=request.reason,
        created_by=by_user,
        updated_by=by_user,
    )
    RelocTaskExtra.objects.create(
        task=task,
        request=request,
        trigger=request.trigger,
        reason=request.reason,
        reason_code="MANUAL",
        execution_state="READY",
        root_container=request.source_container,
        target_parent_container=request.target_parent_container,
    )
    for req_line in lines:
        detail = locked_sources[req_line.inventory_detail_id]
        qty = q3(req_line.requested_qty)
        task_line = WmsTaskLine.objects.create(
            task=task,
            product=detail.product,
            from_location=detail.location,
            to_location=req_line.to_location,
            qty_plan=qty,
            qty_done=0,
            status=WmsTaskLine.Status.DRAFT,
            src_model="InventoryDetail",
            src_id=detail.pk,
            rule_key=request.mode,
            plan_meta={**_snapshot(detail), "request_line_id": req_line.pk},
            created_by=by_user,
            updated_by=by_user,
        )
        RelocLineExtra.objects.create(
            line=task_line,
            from_location=detail.location,
            to_location=req_line.to_location,
            from_container=detail.container,
            to_container=req_line.to_container,
            from_lpn=detail.container.container_no if detail.container else "",
            to_lpn=req_line.to_container.container_no if req_line.to_container else "",
            qty_move=0,
            reason_code="MANUAL",
        )
        detail.locked_qty = Decimal(detail.locked_qty or 0) + qty
        detail.save(update_fields=["locked_qty", "available_qty", "updated_at"])
        RelocationReservation.objects.create(
            task_line=task_line,
            inventory_detail=detail,
            qty=qty,
            created_by=by_user,
            updated_by=by_user,
        )

    container_ids = {
        container_id
        for pair in RelocLineExtra.objects.filter(line__task=task).values_list(
            "from_container_id", "to_container_id"
        )
        for container_id in pair
        if container_id
    }
    if request.mode == RelocationRequest.Mode.CONTAINER:
        container_ids.update(container_tree_ids(request.source_container))
        if request.target_parent_container_id:
            container_ids.add(request.target_parent_container_id)
    busy_usage = (
        ContainerUsage.objects.select_for_update()
        .filter(container_id__in=container_ids, purpose="MOVE", status="OPEN")
        .select_related("container", "task")
        .order_by("container_id", "id")
        .first()
    )
    if busy_usage:
        raise ValidationError(
            f"容器 {busy_usage.container.container_no} 正在被任务 {busy_usage.task.task_no} 使用。"
        )
    for container_id in sorted(container_ids):
        ContainerUsage.objects.create(
            task=task,
            container_id=container_id,
            purpose="MOVE",
            created_by=by_user,
            updated_by=by_user,
        )

    _release_task(task, by_user=by_user)
    request.status = RelocationRequest.Status.APPROVED
    request.reviewed_by = by_user
    request.reviewed_at = timezone.now()
    request.review_note = (note or "")[:200]
    request.generated_task = task
    request.updated_by = by_user
    request.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "generated_task",
            "updated_by",
            "updated_at",
        ]
    )
    return task


@transaction.atomic
def reject_request(request_id: int, *, by_user, note: str):
    note = (note or "").strip()
    if not note:
        raise ValidationError("请填写驳回原因。")
    obj = RelocationRequest.objects.select_for_update().get(pk=request_id)
    if obj.status != RelocationRequest.Status.PENDING:
        raise ValidationError("只有待审核的移库申请可以驳回。")
    obj.status = RelocationRequest.Status.REJECTED
    obj.reviewed_by = by_user
    obj.reviewed_at = timezone.now()
    obj.review_note = note[:200]
    obj.updated_by = by_user
    obj.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "updated_by",
            "updated_at",
        ]
    )
    return obj


@transaction.atomic
def cancel_request(request_id: int, *, by_user):
    obj = RelocationRequest.objects.select_for_update().get(pk=request_id)
    if obj.status != RelocationRequest.Status.PENDING:
        raise ValidationError("只有待审核的移库申请可以取消。")
    if obj.trigger == RelocationRequest.Trigger.REQUEST and obj.created_by_id != by_user.pk:
        raise PermissionDenied("只能取消本人提交的移库申请。")
    obj.status = RelocationRequest.Status.CANCELLED
    obj.updated_by = by_user
    obj.save(update_fields=["status", "updated_by", "updated_at"])
    return obj


def _canonical_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@transaction.atomic
def record_relocation(
    *,
    task_id: int,
    line_id: int,
    request_id: str,
    from_location_code: str,
    to_location_code: str,
    product_code: str,
    qty,
    by_user,
    serial_no: str = "",
    from_container_code: str = "",
    to_container_code: str = "",
):
    task = WmsTask.objects.select_for_update().get(pk=task_id, task_type=WmsTask.TaskType.RELOC)
    line = (
        WmsTaskLine.objects.select_for_update()
        .select_related("product", "from_location", "to_location", "reloclineextra")
        .get(pk=line_id, task=task)
    )
    extra = line.reloclineextra
    task_extra = RelocTaskExtra.objects.select_for_update().get(task=task)

    from_code = (from_location_code or "").strip().upper()
    to_code = (to_location_code or "").strip().upper()
    scanned = (product_code or "").strip().upper()
    from_container_scan = (from_container_code or "").strip().upper()
    to_container_scan = (to_container_code or "").strip().upper()
    move_qty = q3(qty)
    serial = (serial_no or "").strip().upper()
    payload = {
        "line_id": line.pk,
        "from": from_code,
        "to": to_code,
        "product": scanned,
        "qty": str(move_qty),
        "serial_no": serial,
        "from_container": from_container_scan,
        "to_container": to_container_scan,
    }
    payload_hash = hashlib.sha256(_canonical_payload(payload).encode("utf-8")).hexdigest()
    fp = hashlib.sha256(f"reloc:{task.pk}:{by_user.pk}:{request_id}".encode("utf-8")).hexdigest()
    expected_remark = f"IDEMPOTENCY:{payload_hash}"
    existing = TaskScanLog.objects.filter(fp=fp).first()
    if existing:
        if existing.remark != expected_remark:
            raise RelocationIdempotencyConflict("同一请求编号不能用于不同的移库内容。")
        return {"idempotent": True, "task": task, "posting_required": False}

    if task_extra.execution_state == "EXCEPTION":
        raise ValidationError("移库任务处于异常暂停状态。")
    if task.status != WmsTask.Status.IN_PROGRESS:
        raise ValidationError("移库任务必须先领取并开始。")
    if not TaskAssignment.objects.filter(
        task=task, assignee=by_user, finished_at__isnull=True
    ).exists():
        raise PermissionDenied("只能执行自己领取的移库任务。")

    if from_code != (line.from_location.code or "").strip().upper():
        raise ValidationError("扫描的来源库位与任务不一致。")
    if to_code != (line.to_location.code or "").strip().upper():
        raise ValidationError("扫描的目标库位与任务不一致。")
    expected_from_container = (
        (extra.from_container.container_no if extra.from_container_id else "").strip().upper()
    )
    expected_to_container = (
        (extra.to_container.container_no if extra.to_container_id else "").strip().upper()
    )
    if from_container_scan != expected_from_container:
        raise ValidationError("扫描的来源容器与任务不一致。")
    if to_container_scan != expected_to_container:
        raise ValidationError("扫描的目标容器与任务不一致。")
    product_codes = {
        line.product.code,
        line.product.sku,
        line.product.unit_barcode,
        line.product.carton_barcode,
    }
    product_codes.update(
        line.product.packages.exclude(barcode__isnull=True).values_list("barcode", flat=True)
    )
    valid_codes = {str(value).strip().upper() for value in product_codes if value}
    valid_codes.update(value for value in (expected_from_container, expected_to_container) if value)
    if scanned not in valid_codes:
        raise ValidationError("扫描的商品或容器与任务行不一致。")
    if move_qty <= 0:
        raise ValidationError("移库数量必须大于零。")
    expected_serial = ((line.plan_meta or {}).get("serial_no") or "").upper()
    if line.product.serial_control:
        if move_qty != Decimal("1.000") or serial != expected_serial:
            raise ValidationError("序列号商品必须逐件扫描且序列号与来源一致。")
    elif serial:
        raise ValidationError("非序列号商品不能提交序列号。")

    if line.status == WmsTaskLine.Status.COMPLETED:
        raise ValidationError("该移库任务行已完成。")
    pending = q3(Decimal(line.qty_plan or 0) - Decimal(line.qty_done or 0))
    if move_qty > pending:
        raise ValidationError("本次数量不能超过任务剩余数量。")

    line.scan_snapshot_rev = (line.scan_snapshot_rev or 0) + 1
    meta = line.plan_meta or {}
    TaskScanLog.objects.create(
        owner=task.owner,
        warehouse=task.warehouse,
        task=task,
        task_line=line,
        product=line.product,
        location=line.to_location,
        barcode=scanned,
        label_key=serial or None,
        code_type=(
            "SERIAL"
            if serial
            else (
                "CONTAINER"
                if scanned in {expected_from_container, expected_to_container}
                else "ITEM"
            )
        ),
        by_user=by_user,
        method=TaskScanLog.Method.SCAN,
        source="PDA",
        qty_base_delta=move_qty,
        lot_no=meta.get("batch_no") or None,
        mfg_date=meta.get("production_date") or None,
        exp_date=meta.get("expiry_date") or None,
        serial_no=serial or None,
        container_no=expected_to_container or expected_from_container or None,
        fp=fp,
        scan_snapshot_rev=line.scan_snapshot_rev,
        remark=expected_remark,
    )
    line.qty_done = q3(Decimal(line.qty_done or 0) + move_qty)
    line.status = WmsTaskLine.Status.IN_PROGRESS
    line.updated_by = by_user
    fields = ["qty_done", "status", "scan_snapshot_rev", "updated_by", "updated_at"]
    if line.qty_done == q3(line.qty_plan):
        line.status = WmsTaskLine.Status.COMPLETED
        line.finished_at = timezone.now()
        line.finished_by = by_user
        fields.extend(["finished_at", "finished_by"])
    line.save(update_fields=fields)
    RelocLineExtra.objects.filter(line=line).update(qty_move=line.qty_done)

    posting_required = not task.lines.exclude(
        status__in=[WmsTaskLine.Status.COMPLETED, WmsTaskLine.Status.CANCELLED]
    ).exists()
    if posting_required:
        old = task.status
        now = timezone.now()
        task.status = WmsTask.Status.COMPLETED
        task.review_status = WmsTask.ReviewStatus.APPROVED
        task.posting_status = WmsTask.PostingStatus.PENDING
        task.approved_by = by_user
        task.approved_at = now
        task.finished_at = now
        task.updated_by = by_user
        task.save(
            update_fields=[
                "status",
                "review_status",
                "posting_status",
                "approved_by",
                "approved_at",
                "finished_at",
                "updated_by",
                "updated_at",
            ]
        )
        TaskAssignment.objects.filter(task=task, finished_at__isnull=True).update(finished_at=now)
        TaskStatusLog.objects.create(
            task=task,
            old_status=old,
            new_status=WmsTask.Status.COMPLETED,
            changed_by=by_user,
            note="移库作业完成，等待库存过账",
        )
    else:
        task_extra.execution_state = "WORKING"
        task_extra.save(update_fields=["execution_state"])
    return {"idempotent": False, "task": task, "posting_required": posting_required}


@transaction.atomic
def report_exception(task_id: int, *, by_user, code: str, note: str):
    task = WmsTask.objects.select_for_update().get(pk=task_id, task_type=WmsTask.TaskType.RELOC)
    if task.status != WmsTask.Status.IN_PROGRESS:
        raise ValidationError("只有执行中的移库任务可以报告异常。")
    if not TaskAssignment.objects.filter(
        task=task, assignee=by_user, finished_at__isnull=True
    ).exists():
        raise PermissionDenied("只能为自己领取的移库任务报告异常。")
    extra = RelocTaskExtra.objects.select_for_update().get(task=task)
    extra.execution_state = "EXCEPTION"
    extra.exception_code = (code or "OPERATION_EXCEPTION")[:30]
    extra.exception_note = (note or "")[:200]
    extra.exception_by = by_user
    extra.save(
        update_fields=[
            "execution_state",
            "exception_code",
            "exception_note",
            "exception_by",
        ]
    )
    return task


@transaction.atomic
def resume_task(task_id: int, *, by_user):
    task = WmsTask.objects.select_for_update().get(pk=task_id, task_type=WmsTask.TaskType.RELOC)
    extra = RelocTaskExtra.objects.select_for_update().get(task=task)
    if task.status != WmsTask.Status.IN_PROGRESS or extra.execution_state != "EXCEPTION":
        raise ValidationError("当前任务不处于可恢复的异常状态。")
    extra.execution_state = "WORKING"
    extra.exception_code = ""
    extra.exception_note = ""
    extra.exception_by = by_user
    extra.save(
        update_fields=[
            "execution_state",
            "exception_code",
            "exception_note",
            "exception_by",
        ]
    )
    if not task.lines.exclude(status=WmsTaskLine.Status.COMPLETED).exists():
        task.status = WmsTask.Status.COMPLETED
        task.review_status = WmsTask.ReviewStatus.APPROVED
        task.updated_by = by_user
        task.save(update_fields=["status", "review_status", "updated_by", "updated_at"])
    return task


@transaction.atomic
def void_task(task_id: int, *, by_user, note: str):
    warehouse_id = WmsTask.objects.filter(pk=task_id).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    task = WmsTask.objects.select_for_update().get(pk=task_id, task_type=WmsTask.TaskType.RELOC)
    if task.warehouse_id != warehouse_id:
        raise ValidationError("移库任务仓库在作废期间发生变化，请重试。")
    if task.posting_status == WmsTask.PostingStatus.POSTED:
        raise ValidationError("已过账移库任务不能作废，请创建反向移库任务。")
    reservations = list(
        RelocationReservation.objects.select_for_update()
        .filter(task_line__task=task, status=RelocationReservation.Status.ACTIVE)
        .select_related("inventory_detail")
        .order_by("inventory_detail_id")
    )
    for reservation in reservations:
        detail = InventoryDetail.objects.select_for_update().get(pk=reservation.inventory_detail_id)
        if Decimal(detail.locked_qty or 0) < Decimal(reservation.qty):
            raise ValidationError("来源库存锁定量异常，不能自动作废。")
        detail.locked_qty = Decimal(detail.locked_qty or 0) - Decimal(reservation.qty)
        detail.save(update_fields=["locked_qty", "available_qty", "updated_at"])
        reservation.status = RelocationReservation.Status.RELEASED
        reservation.released_at = timezone.now()
        reservation.updated_by = by_user
        reservation.save(update_fields=["status", "released_at", "updated_by", "updated_at"])
    TaskScanLog.objects.filter(task=task, posted_at__isnull=True).update(
        status=TaskScanLog.ScanStatus.IGNORED,
        void_reason=(note or "移库任务整单作废")[:50],
        updated_at=timezone.now(),
    )
    task.lines.update(
        qty_done=0,
        status=WmsTaskLine.Status.CANCELLED,
        finished_at=timezone.now(),
        finished_by=by_user,
        updated_by=by_user,
        updated_at=timezone.now(),
    )
    RelocLineExtra.objects.filter(line__task=task).update(qty_move=0)
    old = task.status
    task.status = WmsTask.Status.CANCELLED
    task.review_status = WmsTask.ReviewStatus.NOT_READY
    task.posting_status = WmsTask.PostingStatus.NOT_READY
    task.finished_at = timezone.now()
    task.updated_by = by_user
    task.save(
        update_fields=[
            "status",
            "review_status",
            "posting_status",
            "finished_at",
            "updated_by",
            "updated_at",
        ]
    )
    RelocTaskExtra.objects.filter(task=task).update(
        execution_state="EXCEPTION",
        exception_code="VOIDED",
        exception_note=(note or "移库任务整单作废")[:200],
        exception_by=by_user,
    )
    ContainerUsage.objects.filter(task=task, purpose="MOVE", status="OPEN").update(
        status="CLOSED",
        closed_at=timezone.now(),
        updated_by=by_user,
        updated_at=timezone.now(),
    )
    TaskAssignment.objects.filter(task=task, finished_at__isnull=True).update(
        finished_at=timezone.now()
    )
    if old != WmsTask.Status.CANCELLED:
        TaskStatusLog.objects.create(
            task=task,
            old_status=old,
            new_status=WmsTask.Status.CANCELLED,
            changed_by=by_user,
            note=(note or "移库任务整单作废")[:200],
        )
    return task


def _detail_lookup(detail: InventoryDetail, *, location_id: int, container_id: int | None) -> dict:
    return {
        "owner_id": detail.owner_id,
        "warehouse_id": detail.warehouse_id,
        "product_id": detail.product_id,
        "location_id": location_id,
        "container_id": container_id,
        "batch_no": (detail.batch_no or "").upper(),
        "production_date": detail.production_date,
        "expiry_date": detail.expiry_date,
        "serial_no": (detail.serial_no or "").upper(),
        "is_active": True,
    }


def post_relocation_inventory(
    *, task: WmsTask, scans: list[TaskScanLog], now, batch_no: str
) -> int:
    """Apply one RELOC task inside the inventory posting transaction."""
    if task.task_type != WmsTask.TaskType.RELOC:
        raise ValidationError("任务类型不是 RELOC。")
    lines = list(
        WmsTaskLine.objects.select_for_update()
        .filter(task=task)
        .select_related("product", "from_location", "to_location", "reloclineextra")
        .order_by("id")
    )
    scan_totals = {
        row["task_line_id"]: q3(row["total"])
        for row in TaskScanLog.objects.filter(pk__in=[scan.pk for scan in scans])
        .values("task_line_id")
        .annotate(total=Sum("qty_base_delta"))
    }
    if not lines or any(
        line.status != WmsTaskLine.Status.COMPLETED
        or q3(line.qty_done) != q3(line.qty_plan)
        or scan_totals.get(line.pk, Decimal("0.000")) != q3(line.qty_plan)
        for line in lines
    ):
        raise ValidationError("移库任务必须全部按计划数量完成后才能过账。")

    reservations = {
        row.task_line_id: row
        for row in RelocationReservation.objects.select_for_update()
        .filter(task_line__task=task)
        .select_related("inventory_detail")
        .order_by("inventory_detail_id")
    }
    if set(reservations) != {line.pk for line in lines}:
        raise ValidationError("移库任务预留不完整。")
    detail_ids = sorted({row.inventory_detail_id for row in reservations.values()})
    source_details = {
        row.pk: row
        for row in InventoryDetail.objects.select_for_update()
        .filter(pk__in=detail_ids)
        .select_related("product", "location", "container")
        .order_by("id")
    }
    extra = RelocTaskExtra.objects.select_for_update().get(task=task)
    whole_container = bool(extra.root_container_id)
    tree_ids: list[int] = []
    if whole_container:
        root = Container.objects.select_for_update().get(pk=extra.root_container_id)
        tree_ids = container_tree_ids(root, lock=True)
        if Container.objects.filter(pk__in=tree_ids, is_active=False).exists():
            raise ValidationError("来源容器树中存在已停用容器。")
        if root.location_id != lines[0].from_location_id:
            raise ValidationError("来源根容器位置已变化。")
        if Container.objects.filter(pk__in=tree_ids).exclude(location_id=root.location_id).exists():
            raise ValidationError("来源容器树位置已变化。")
        _validate_parent_container_capacity(extra.target_parent_container, moving_tree_ids=tree_ids)
        if extra.target_parent_container_id:
            parent = Container.objects.select_for_update().get(pk=extra.target_parent_container_id)
            _validate_container_scope(
                parent, owner_id=task.owner_id, warehouse_id=task.warehouse_id
            )
            if parent.pk in tree_ids or parent.location_id != lines[0].to_location_id:
                raise ValidationError("目标父容器结构或位置已变化。")
        target_location_id = lines[0].to_location_id
        if any(line.to_location_id != target_location_id for line in lines):
            raise ValidationError("整容器任务的目标库位不一致。")
        Container.objects.filter(pk__in=tree_ids).update(
            location_id=target_location_id, updated_at=now
        )
        for detail in source_details.values():
            if detail.container_id in tree_ids:
                detail.container.location_id = target_location_id
        root.parent_id = extra.target_parent_container_id
        root.save(update_fields=["parent", "updated_at"])

    request_lines = []
    for line in lines:
        reservation = reservations[line.pk]
        detail = source_details.get(reservation.inventory_detail_id)
        if detail is None or reservation.status != RelocationReservation.Status.ACTIVE:
            raise ValidationError("移库来源库存或预留已失效。")
        qty = q3(line.qty_plan)
        line_extra = line.reloclineextra
        if q3(reservation.qty) != qty or line.src_id != detail.pk:
            raise ValidationError("移库任务行与预留不一致。")
        if detail.owner_id != task.owner_id or detail.warehouse_id != task.warehouse_id:
            raise ValidationError("移库来源库存超出任务范围。")
        if (
            detail.location_id != line.from_location_id
            or detail.container_id != line_extra.from_container_id
        ):
            raise ValidationError("移库来源库存位置或容器已变化。")
        if Decimal(detail.locked_qty or 0) < qty:
            raise ValidationError("移库来源库存锁定量不足。")
        if (
            Decimal(detail.onhand_qty or 0)
            - Decimal(detail.allocated_qty or 0)
            - Decimal(detail.damaged_qty or 0)
            < qty
        ):
            raise ValidationError("移库来源库存不足或已被分配/损坏。")
        _validate_location(line.from_location, task.warehouse_id, label="来源库位")
        _validate_location(line.to_location, task.warehouse_id, label="目标库位")
        if (
            line_extra.to_container_id
            and not whole_container
            and line_extra.to_container.location_id != line.to_location_id
        ):
            raise ValidationError("目标容器位置已变化。")
        request_lines.append(
            RelocationRequestLine(
                inventory_detail=detail,
                requested_qty=qty,
                to_location=line.to_location,
                to_container=line_extra.to_container,
            )
        )
    _validate_target_capacity(request_lines)

    # Re-check count locks only after all task/reservation rows are locked.
    from allapp.tasking.counting import assert_inventory_not_count_locked

    for line in lines:
        detail = source_details[reservations[line.pk].inventory_detail_id]
        for location_id in (line.from_location_id, line.to_location_id):
            assert_inventory_not_count_locked(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                product_id=line.product_id,
                location_id=location_id,
                batch_no=detail.batch_no or "",
                task=task,
            )

    touched_pairs: set[tuple[int, int]] = set()
    created = 0
    for line in lines:
        reservation = reservations[line.pk]
        detail = source_details[reservation.inventory_detail_id]
        line_extra = line.reloclineextra
        qty = q3(line.qty_plan)
        touched_pairs.add((detail.owner_id, detail.product_id))

        if whole_container or detail.product_serial_control:
            detail.locked_qty = Decimal(detail.locked_qty or 0) - qty
            detail.location = line.to_location
            detail.subwarehouse_id = line.to_location.subwarehouse_id
            detail.zone_type = line.to_location.zone_type
            detail.container_id = line_extra.to_container_id
            detail.save()
        else:
            detail.locked_qty = Decimal(detail.locked_qty or 0) - qty
            detail.onhand_qty = Decimal(detail.onhand_qty or 0) - qty
            detail.save(
                update_fields=[
                    "locked_qty",
                    "onhand_qty",
                    "available_qty",
                    "updated_at",
                ]
            )
            lookup = _detail_lookup(
                detail,
                location_id=line.to_location_id,
                container_id=line_extra.to_container_id,
            )
            target, _ = InventoryDetail.objects.get_or_create(
                **lookup,
                defaults={
                    "zone_type": line.to_location.zone_type,
                    "subwarehouse_id": line.to_location.subwarehouse_id,
                    "onhand_qty": 0,
                    "allocated_qty": 0,
                    "locked_qty": 0,
                    "damaged_qty": 0,
                    "base_unit": detail.base_unit,
                    "product_serial_control": detail.product_serial_control,
                },
            )
            target = InventoryDetail.objects.select_for_update().get(pk=target.pk)
            target.zone_type = line.to_location.zone_type
            target.subwarehouse_id = line.to_location.subwarehouse_id
            target.onhand_qty = Decimal(target.onhand_qty or 0) + qty
            target.save()

        pair_id = hashlib.md5(
            f"reloc:{task.pk}:{line.pk}".encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        pair_uuid = (
            f"{pair_id[:8]}-{pair_id[8:12]}-{pair_id[12:16]}-" f"{pair_id[16:20]}-{pair_id[20:32]}"
        )
        common = {
            "owner_id": task.owner_id,
            "warehouse_id": task.warehouse_id,
            "product_id": line.product_id,
            "batch_no": detail.batch_no or "",
            "production_date": detail.production_date,
            "expiry_date": detail.expiry_date,
            "serial_no": detail.serial_no or "",
            "src_model": "WmsTask",
            "src_id": task.pk,
            "src_line_id": line.pk,
            "src_no": task.task_no,
            "memo": "RELOC",
            "pair_id": pair_uuid,
            "posted_at": now,
            "posting_batch": batch_no,
        }
        InventoryTransaction.objects.create(
            tx_type=InvTxType.ISSUE,
            location=line.from_location,
            subwarehouse_id=line.from_location.subwarehouse_id,
            zone_type=line.from_location.zone_type,
            container=line_extra.from_container,
            container_no=(
                line_extra.from_container.container_no if line_extra.from_container_id else ""
            ),
            qty_delta=-qty,
            **common,
        )
        move_in_tx = InventoryTransaction.objects.create(
            tx_type=InvTxType.RECEIVE,
            location=line.to_location,
            subwarehouse_id=line.to_location.subwarehouse_id,
            zone_type=line.to_location.zone_type,
            container=line_extra.to_container,
            container_no=(
                line_extra.to_container.container_no if line_extra.to_container_id else ""
            ),
            qty_delta=qty,
            **common,
        )
        if getattr(settings, "INVENTORY_FIFO_ENABLED", False):
            from allapp.inventory.fifo import move_fifo

            move_fifo(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                product_id=line.product_id,
                from_location_id=line.from_location_id,
                to_location_id=line.to_location_id,
                quantity=qty,
                batch_no=detail.batch_no or "",
                serial_no=detail.serial_no or "",
                from_container_id=line_extra.from_container_id,
                to_container_id=line_extra.to_container_id,
                inventory_transaction_id=move_in_tx.id,
                occurred_at=now,
            )
        reservation.status = RelocationReservation.Status.CONSUMED
        reservation.consumed_at = now
        reservation.save(update_fields=["status", "consumed_at", "updated_at"])
        created += 2

    from allapp.inventory.services import _refresh_summaries

    _refresh_summaries(touched_pairs)
    ContainerUsage.objects.filter(task=task, purpose="MOVE", status="OPEN").update(
        status="CLOSED", closed_at=now, updated_at=now
    )
    extra.execution_state = "DONE"
    extra.exception_code = ""
    extra.exception_note = ""
    extra.save(update_fields=["execution_state", "exception_code", "exception_note"])
    return created
