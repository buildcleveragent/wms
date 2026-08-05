from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from allapp.core.choices import ZoneType
from allapp.core.models import DocSequence
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Warehouse
from allapp.tasking.models import (
    ReplenishLineExtra,
    ReplenishTaskExtra,
    ReplenishmentPolicy,
    ReplenishmentRequest,
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)

logger = logging.getLogger(__name__)

QTY_QUANT = Decimal("0.001")
ACTIVE_TASK_STATUSES = (
    WmsTask.Status.DRAFT,
    WmsTask.Status.READY,
    WmsTask.Status.RELEASED,
    WmsTask.Status.IN_PROGRESS,
)


class ReplenishmentIdempotencyConflict(Exception):
    """A client request id was reused with a different normalized payload."""


def q3(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


def _package_multiple(policy: ReplenishmentPolicy) -> Decimal:
    package = policy.product.packages.filter(
        uom_id=policy.replenish_uom_id, is_active=True
    ).first()
    if package is None:
        raise ValidationError("补货策略的补货单位不在商品包装层级中。")
    return q3(package.qty_in_base)


def _round_up_multiple(qty: Decimal, multiple: Decimal) -> Decimal:
    qty = q3(qty)
    if qty <= 0:
        return Decimal("0.000")
    return q3((qty / multiple).to_integral_value(rounding=ROUND_CEILING) * multiple)


def _round_down_multiple(qty: Decimal, multiple: Decimal) -> Decimal:
    qty = q3(qty)
    return q3((qty / multiple).to_integral_value(rounding=ROUND_FLOOR) * multiple)


def target_available(policy: ReplenishmentPolicy) -> Decimal:
    value = (
        InventoryDetail.objects.filter(
            owner_id=policy.owner_id,
            warehouse_id=policy.warehouse_id,
            product_id=policy.product_id,
            location_id=policy.target_location_id,
            is_active=True,
        ).aggregate(total=Sum("available_qty"))["total"]
        or 0
    )
    return q3(value)


def in_transit_qty(policy: ReplenishmentPolicy) -> Decimal:
    remaining = ExpressionWrapper(
        F("qty_plan") - F("qty_done"),
        output_field=DecimalField(max_digits=18, decimal_places=3),
    )
    value = (
        WmsTaskLine.objects.filter(
            task__task_type=WmsTask.TaskType.REPLEN,
            task__status__in=ACTIVE_TASK_STATUSES,
            product_id=policy.product_id,
            to_location_id=policy.target_location_id,
        )
        .exclude(status=WmsTaskLine.Status.CANCELLED)
        .aggregate(total=Sum(remaining))["total"]
        or 0
    )
    return q3(value)


def _source_details(policy: ReplenishmentPolicy, *, lock: bool = False):
    qs = InventoryDetail.objects.filter(
        owner_id=policy.owner_id,
        warehouse_id=policy.warehouse_id,
        product_id=policy.product_id,
        location__zone_type=policy.source_zone_type,
        location__is_active=True,
        location__is_disabled=False,
        location__is_frozen=False,
        allocated_qty=0,
        locked_qty=0,
        damaged_qty=0,
        container__isnull=True,
        available_qty__gt=0,
        is_active=True,
    ).select_related("location", "product")
    if lock:
        qs = qs.select_for_update()
    return qs.order_by(
        F("expiry_date").asc(nulls_last=True), "batch_no", "location__code", "id"
    )


def _source_candidates(policy: ReplenishmentPolicy, *, lock: bool = False):
    details = list(_source_details(policy, lock=lock))
    if not details:
        return []
    remaining = ExpressionWrapper(
        F("qty_plan") - F("qty_done"),
        output_field=DecimalField(max_digits=18, decimal_places=3),
    )
    commitments = {
        row["src_id"]: q3(row["qty"])
        for row in (
            WmsTaskLine.objects.filter(
                task__task_type=WmsTask.TaskType.REPLEN,
                task__status__in=ACTIVE_TASK_STATUSES,
                src_model="InventoryDetail",
                src_id__in=[detail.pk for detail in details],
            )
            .exclude(status=WmsTaskLine.Status.CANCELLED)
            .values("src_id")
            .annotate(qty=Sum(remaining))
        )
    }
    return [
        (
            detail,
            q3(
                max(
                    Decimal(detail.available_qty or 0) - commitments.get(detail.pk, 0),
                    0,
                )
            ),
        )
        for detail in details
    ]


def source_available(policy: ReplenishmentPolicy) -> Decimal:
    return q3(
        sum(
            (available for _detail, available in _source_candidates(policy)),
            Decimal("0"),
        )
    )


def _release_task(task: WmsTask, *, by_user=None) -> WmsTask:
    old_status = task.status
    task._allow_status_write = True
    task.status = WmsTask.Status.RELEASED
    task.released_at = timezone.now()
    task.updated_by = by_user
    task.save(update_fields=["status", "released_at", "updated_by", "updated_at"])
    task.lines.update(status=WmsTaskLine.Status.RELEASED, updated_by=by_user)
    TaskStatusLog.objects.create(
        task=task,
        old_status=old_status,
        new_status=WmsTask.Status.RELEASED,
        changed_by=by_user,
        note="补货任务发布",
    )
    return task


def _create_task(
    *,
    policy: ReplenishmentPolicy,
    qty: Decimal,
    trigger: str,
    by_user=None,
    request: ReplenishmentRequest | None = None,
    source_model: str = "ReplenishmentPolicy",
    source_pk: str | int | None = None,
    ref_no: str = "",
    auto_release: bool = False,
) -> WmsTask:
    multiple = _package_multiple(policy)
    requested = _round_up_multiple(qty, multiple)
    candidates = _source_candidates(policy, lock=True)
    available = q3(sum((effective for _detail, effective in candidates), Decimal("0")))
    executable = _round_down_multiple(min(requested, available), multiple)
    if policy.product.serial_control:
        executable = min(requested, available)
    if executable <= 0:
        raise ValidationError("存储区没有可用于补货的整包装库存。")
    shortage = q3(requested - executable)
    task_remark = f"{trigger} 补货至 {policy.target_location.code}"
    if shortage > 0:
        task_remark += f"；来源库存缺口 {shortage}"

    task_no = DocSequence.next_code(
        doc_type="RPL", warehouse=policy.warehouse, owner=policy.owner
    )
    task = WmsTask.objects.create(
        task_no=task_no,
        task_type=WmsTask.TaskType.REPLEN,
        status=WmsTask.Status.DRAFT,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
        priority=policy.priority,
        owner=policy.owner,
        warehouse=policy.warehouse,
        ref_no=(ref_no or "")[:60],
        source_app="tasking",
        source_model=source_model,
        source_pk=str(source_pk or policy.pk),
        remark=task_remark,
        created_by=by_user,
        updated_by=by_user,
    )
    ReplenishTaskExtra.objects.create(
        task=task,
        trigger=trigger,
        policy=policy,
        request=request,
        demand_order_ids=(
            [int(source_pk)]
            if trigger == "DEMAND"
            and source_model == "OutboundOrder"
            and source_pk is not None
            else []
        ),
        src_zone=ZoneType(policy.source_zone_type).label,
        dst_zone=ZoneType(ZoneType.PICK).label,
        policy_code=f"RPL-{policy.pk}",
    )

    remaining = executable
    for detail, effective_available in candidates:
        if remaining <= 0:
            break
        take = q3(min(effective_available, remaining))
        if policy.product.serial_control:
            take = min(take, Decimal("1.000"))
        if take <= 0:
            continue
        line = WmsTaskLine.objects.create(
            task=task,
            product=policy.product,
            from_location=detail.location,
            to_location=policy.target_location,
            qty_plan=take,
            qty_done=0,
            status=WmsTaskLine.Status.DRAFT,
            src_model="InventoryDetail",
            src_id=detail.pk,
            rule_key=trigger,
            plan_meta={
                "inventory_detail_id": detail.pk,
                "lot_no": detail.batch_no or "",
                "mfg_date": (
                    detail.production_date.isoformat()
                    if detail.production_date
                    else None
                ),
                "exp_date": (
                    detail.expiry_date.isoformat() if detail.expiry_date else None
                ),
                "serial_no": detail.serial_no or "",
                "package_multiple": str(multiple),
            },
            created_by=by_user,
            updated_by=by_user,
        )
        ReplenishLineExtra.objects.create(
            line=line,
            from_location=detail.location,
            to_location=policy.target_location,
            qty_move=0,
        )
        remaining = q3(remaining - take)

    if not task.lines.exists():
        raise ValidationError("没有生成可执行的补货任务行。")
    if auto_release:
        _release_task(task, by_user=by_user)
    logger.info(
        "replenishment.task.created task_id=%s trigger=%s policy_id=%s requested=%s planned=%s shortage=%s",
        task.pk,
        trigger,
        policy.pk,
        requested,
        executable,
        shortage,
    )
    return task


@transaction.atomic
def evaluate_policy(policy_id: int, *, by_user=None, force: bool = False):
    policy = (
        ReplenishmentPolicy.objects.select_for_update()
        .select_related(
            "owner", "warehouse", "product", "target_location", "replenish_uom"
        )
        .get(pk=policy_id, is_active=True)
    )
    Warehouse.objects.select_for_update().get(pk=policy.warehouse_id)
    projected = q3(target_available(policy) + in_transit_qty(policy))
    if not force and projected >= q3(policy.min_qty):
        return {"created": False, "reason": "ABOVE_MIN", "projected_qty": projected}
    needed = q3(Decimal(policy.target_qty) - projected)
    if needed <= 0:
        return {"created": False, "reason": "AT_TARGET", "projected_qty": projected}
    try:
        task = _create_task(
            policy=policy,
            qty=needed,
            trigger="MINMAX",
            by_user=by_user,
            auto_release=policy.auto_release,
        )
    except ValidationError as exc:
        return {
            "created": False,
            "reason": "NO_SOURCE_STOCK",
            "projected_qty": projected,
            "detail": str(exc),
        }
    return {"created": True, "task": task, "projected_qty": projected}


def evaluate_policies(
    *, policy_ids=None, owner_id=None, warehouse_id=None, product_id=None, by_user=None
):
    qs = ReplenishmentPolicy.objects.filter(is_active=True).order_by(
        "warehouse_id", "id"
    )
    if policy_ids is not None:
        qs = qs.filter(pk__in=policy_ids)
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if product_id:
        qs = qs.filter(product_id=product_id)
    results = []
    for policy_id in qs.values_list("id", flat=True):
        result = evaluate_policy(policy_id, by_user=by_user)
        results.append(
            {
                "policy_id": policy_id,
                "created": result["created"],
                "task_id": getattr(result.get("task"), "pk", None),
                "reason": result.get("reason", "CREATED"),
                "projected_qty": str(result.get("projected_qty", 0)),
            }
        )
    return results


@transaction.atomic
def create_demand_tasks(order, shortages: list[dict], *, by_user=None) -> list[WmsTask]:
    """Create or release tasks that cover an outbound pick-face shortage."""

    Warehouse.objects.select_for_update().get(pk=order.warehouse_id)
    tasks: list[WmsTask] = []
    for shortage in shortages:
        policy = (
            ReplenishmentPolicy.objects.select_for_update()
            .select_related(
                "owner", "warehouse", "product", "target_location", "replenish_uom"
            )
            .filter(
                owner_id=order.owner_id,
                warehouse_id=order.warehouse_id,
                product_id=shortage["product_id"],
                demand_enabled=True,
                is_active=True,
            )
            .order_by("-priority", "id")
            .first()
        )
        if policy is None:
            continue

        active = list(
            WmsTask.objects.select_for_update()
            .filter(
                task_type=WmsTask.TaskType.REPLEN,
                status__in=ACTIVE_TASK_STATUSES,
                lines__product_id=policy.product_id,
                lines__to_location_id=policy.target_location_id,
            )
            .distinct()
            .order_by("id")
        )
        for task in active:
            extra = (
                ReplenishTaskExtra.objects.select_for_update().filter(task=task).first()
            )
            if extra and order.pk not in (extra.demand_order_ids or []):
                extra.demand_order_ids = [*(extra.demand_order_ids or []), order.pk]
                extra.updated_by = by_user
                extra.save(
                    update_fields=["demand_order_ids", "updated_by", "updated_at"]
                )
            if task.status in {WmsTask.Status.DRAFT, WmsTask.Status.READY}:
                _release_task(task, by_user=by_user)
            tasks.append(task)

        current_target = target_available(policy)
        needed_for_target = max(
            Decimal(shortage["shortage"]), Decimal(policy.target_qty) - current_target
        )
        uncovered = q3(needed_for_target - in_transit_qty(policy))
        if uncovered <= 0:
            continue
        try:
            task = _create_task(
                policy=policy,
                qty=uncovered,
                trigger="DEMAND",
                by_user=by_user,
                source_model="OutboundOrder",
                source_pk=order.pk,
                ref_no=getattr(order, "order_no", "") or str(order.pk),
                auto_release=True,
            )
        except ValidationError:
            logger.warning(
                "replenishment.demand.no_source order_id=%s product_id=%s shortage=%s",
                order.pk,
                policy.product_id,
                shortage["shortage"],
            )
            continue
        tasks.append(task)
    unique = {task.pk: task for task in tasks}
    return list(unique.values())


@transaction.atomic
def approve_request(request_id: int, *, by_user, note: str = ""):
    request = (
        ReplenishmentRequest.objects.select_for_update()
        .select_related("owner", "warehouse", "product", "target_location")
        .get(pk=request_id)
    )
    if (
        request.status == ReplenishmentRequest.Status.APPROVED
        and request.generated_task_id
    ):
        return request.generated_task
    if request.status != ReplenishmentRequest.Status.PENDING:
        raise ValidationError("只有待审核的补货申请可以批准。")
    policy = (
        ReplenishmentPolicy.objects.select_for_update()
        .select_related(
            "owner", "warehouse", "product", "target_location", "replenish_uom"
        )
        .filter(
            owner_id=request.owner_id,
            warehouse_id=request.warehouse_id,
            product_id=request.product_id,
            target_location_id=request.target_location_id,
            is_active=True,
        )
        .first()
    )
    if policy is None:
        raise ValidationError("该商品与目标拣货位尚未配置补货策略。")
    Warehouse.objects.select_for_update().get(pk=request.warehouse_id)
    rounded = _round_up_multiple(request.requested_qty, _package_multiple(policy))
    maximum = source_available(policy)
    if maximum < rounded:
        raise ValidationError(f"来源库存不足；申请 {rounded}，当前最多可补 {maximum}。")
    task = _create_task(
        policy=policy,
        qty=request.requested_qty,
        trigger="MANUAL",
        by_user=by_user,
        request=request,
        source_model="ReplenishmentRequest",
        source_pk=request.pk,
        ref_no=f"REQ-{request.pk}",
        auto_release=True,
    )
    request.status = ReplenishmentRequest.Status.APPROVED
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
    request = ReplenishmentRequest.objects.select_for_update().get(pk=request_id)
    if request.status != ReplenishmentRequest.Status.PENDING:
        raise ValidationError("只有待审核的补货申请可以驳回。")
    request.status = ReplenishmentRequest.Status.REJECTED
    request.reviewed_by = by_user
    request.reviewed_at = timezone.now()
    request.review_note = note[:200]
    request.updated_by = by_user
    request.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "updated_by",
            "updated_at",
        ]
    )
    return request


def _canonical_payload(payload: dict) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _valid_scan_codes(line: WmsTaskLine) -> tuple[set[str], set[str]]:
    product = line.product
    values = {product.code, product.sku}
    values.update(
        product.packages.exclude(barcode__isnull=True).values_list("barcode", flat=True)
    )
    product_codes = {str(value).strip().upper() for value in values if value}
    try:
        extra = line.replenishlineextra
    except ReplenishLineExtra.DoesNotExist:
        extra = None
    container_codes = {
        str(value).strip().upper()
        for value in (
            getattr(extra, "from_lpn", ""),
            getattr(extra, "to_lpn", ""),
        )
        if value
    }
    return product_codes, container_codes


@transaction.atomic
def record_replenishment(
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
):
    task = WmsTask.objects.select_for_update().get(
        pk=task_id, task_type=WmsTask.TaskType.REPLEN
    )
    try:
        line = (
            WmsTaskLine.objects.select_for_update()
            .select_related("product", "from_location", "to_location")
            .get(pk=line_id, task=task)
        )
    except WmsTaskLine.DoesNotExist as exc:
        raise ValidationError("补货任务行不存在或不属于当前任务。") from exc

    from_code = (from_location_code or "").strip().upper()
    to_code = (to_location_code or "").strip().upper()
    scanned_product = (product_code or "").strip().upper()
    product_codes, container_codes = _valid_scan_codes(line)
    if from_code != (line.from_location.code or "").strip().upper():
        raise ValidationError("扫描的来源库位与任务不一致。")
    if to_code != (line.to_location.code or "").strip().upper():
        raise ValidationError("扫描的目标库位与任务不一致。")
    if scanned_product not in product_codes | container_codes:
        raise ValidationError("扫描的商品或容器与任务行不一致。")

    move_qty = q3(qty)
    if move_qty <= 0:
        raise ValidationError("补货数量必须大于零。")
    tracking = line.plan_meta or {}
    serial = (serial_no or "").strip().upper()
    if line.product.serial_control:
        if move_qty != Decimal("1.000"):
            raise ValidationError("序列号商品必须逐件补货，数量必须为1。")
        expected_serial = (tracking.get("serial_no") or "").strip().upper()
        if not serial or serial != expected_serial:
            raise ValidationError("序列号与任务来源库存不一致。")
    elif serial:
        raise ValidationError("非序列号商品不能提交序列号。")

    payload = {
        "line_id": line.pk,
        "from": from_code,
        "to": to_code,
        "product": scanned_product,
        "qty": str(move_qty),
        "serial_no": serial,
    }
    payload_hash = hashlib.sha256(
        _canonical_payload(payload).encode("utf-8")
    ).hexdigest()
    fp = hashlib.sha256(
        f"replen:{task.pk}:{by_user.pk}:{request_id}".encode("utf-8")
    ).hexdigest()
    expected_remark = f"IDEMPOTENCY:{payload_hash}"
    existing = TaskScanLog.objects.filter(fp=fp).first()
    if existing:
        if existing.remark != expected_remark:
            raise ReplenishmentIdempotencyConflict(
                "同一请求编号不能用于不同的补货内容。"
            )
        return {"idempotent": True, "task": task, "posting_required": False}

    if task.status != WmsTask.Status.IN_PROGRESS:
        raise ValidationError("补货任务必须先领取并开始。")
    if not TaskAssignment.objects.filter(
        task=task, assignee=by_user, finished_at__isnull=True
    ).exists():
        raise PermissionDenied("只能执行自己领取的补货任务。")
    if line.finished_at or line.status == WmsTaskLine.Status.COMPLETED:
        raise ValidationError("该补货任务行已完成。")
    pending = q3(Decimal(line.qty_plan or 0) - Decimal(line.qty_done or 0))
    if move_qty > pending:
        raise ValidationError("本次补货数量不能超过任务剩余数量。")

    line.scan_snapshot_rev = (line.scan_snapshot_rev or 0) + 1
    TaskScanLog.objects.create(
        owner=task.owner,
        warehouse=task.warehouse,
        task=task,
        task_line=line,
        product=line.product,
        location=line.to_location,
        barcode=scanned_product,
        label_key=serial or None,
        code_type=(
            "SERIAL"
            if serial
            else ("CONTAINER" if scanned_product in container_codes else "ITEM")
        ),
        by_user=by_user,
        method=TaskScanLog.Method.SCAN,
        source="PDA",
        qty_base_delta=move_qty,
        lot_no=tracking.get("lot_no") or None,
        mfg_date=tracking.get("mfg_date") or None,
        exp_date=tracking.get("exp_date") or None,
        serial_no=serial or None,
        container_no=(scanned_product if scanned_product in container_codes else None),
        fp=fp,
        scan_snapshot_rev=line.scan_snapshot_rev,
        remark=expected_remark,
    )
    line.qty_done = q3(Decimal(line.qty_done or 0) + move_qty)
    line.updated_by = by_user
    line.status = WmsTaskLine.Status.IN_PROGRESS
    fields = ["qty_done", "status", "scan_snapshot_rev", "updated_by", "updated_at"]
    if line.qty_done >= line.qty_plan:
        line.status = WmsTaskLine.Status.COMPLETED
        line.finished_at = timezone.now()
        line.finished_by = by_user
        fields.extend(["finished_at", "finished_by"])
        TaskAssignment.objects.filter(line=line, finished_at__isnull=True).update(
            finished_at=timezone.now()
        )
    line.save(update_fields=fields)
    ReplenishLineExtra.objects.filter(line=line).update(
        from_location=line.from_location,
        to_location=line.to_location,
        qty_move=line.qty_done,
    )

    posting_required = not task.lines.exclude(
        status__in=[WmsTaskLine.Status.COMPLETED, WmsTaskLine.Status.CANCELLED]
    ).exists()
    if posting_required:
        old_status = task.status
        task._allow_status_write = True
        task.status = WmsTask.Status.COMPLETED
        task.review_status = WmsTask.ReviewStatus.APPROVED
        task.posting_status = WmsTask.PostingStatus.PENDING
        task.approved_by = by_user
        task.approved_at = timezone.now()
        task.finished_at = timezone.now()
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
        TaskAssignment.objects.filter(task=task, finished_at__isnull=True).update(
            finished_at=timezone.now()
        )
        TaskStatusLog.objects.create(
            task=task,
            old_status=old_status,
            new_status=WmsTask.Status.COMPLETED,
            changed_by=by_user,
            note="补货作业完成，等待库存过账",
        )
    return {"idempotent": False, "task": task, "posting_required": posting_required}
