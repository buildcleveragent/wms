from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from allapp.baseinfo.models import Owner, OwnerWarehouseBinding
from allapp.core.models import DocSequence
from allapp.inventory.locking import lock_warehouses_for_inventory_write
from allapp.inventory.models import InventoryDetail, PostingJournal
from allapp.locations.models import Location, Subwarehouse, Warehouse
from allapp.products.models import Product
from allapp.tasking.models import (
    CountLineExtra,
    CountScopeLock,
    CountTaskExtra,
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)

QTY_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class CountScopeParams:
    warehouse_id: int
    owner_id: int
    scope: str = "ALL"
    subwarehouse_id: Optional[int] = None
    zone_type: Optional[int] = None
    location_id: Optional[int] = None
    location_prefix: Optional[str] = None
    product_id: Optional[int] = None
    batch_no: Optional[str] = None
    exclude_zero_onhand: bool = True
    max_lines: int = 1000
    blind: bool = True
    recount_threshold: Decimal = Decimal("0")
    task_remark: str = ""

    def payload(self) -> dict:
        return {
            "warehouse_id": self.warehouse_id,
            "owner_id": self.owner_id,
            "scope": self.scope,
            "subwarehouse_id": self.subwarehouse_id,
            "zone_type": self.zone_type,
            "location_id": self.location_id,
            "location_prefix": (self.location_prefix or "").strip().upper(),
            "product_id": self.product_id,
            "batch_no": (self.batch_no or "").strip().upper(),
            "exclude_zero_onhand": bool(self.exclude_zero_onhand),
            "max_lines": int(self.max_lines),
        }


def _q4(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0)).quantize(
            QTY_QUANT, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("数量格式不正确。") from exc


def _manager_allowed(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or user.has_perm("tasking.taskconfirm_as_wh_manager")
        )
    )


def _infer_scope(*, scope, zone_type, location_id, location_prefix, product_id) -> str:
    selected = (scope or "").strip().upper()
    if selected in {"LOC", "ZONE", "SKU", "ALL"}:
        return selected
    if product_id:
        return "SKU"
    if location_id or location_prefix:
        return "LOC"
    if zone_type:
        return "ZONE"
    return "ALL"


def _validate_params(params: CountScopeParams) -> tuple[Warehouse, Owner]:
    warehouse = Warehouse.objects.get(pk=params.warehouse_id)
    owner = Owner.objects.get(pk=params.owner_id)
    if not OwnerWarehouseBinding.objects.filter(
        owner=owner, warehouse=warehouse, is_active=True
    ).exists():
        raise ValidationError({"owner_id": "该货主未授权使用所选仓库。"})
    if params.max_lines < 1 or params.max_lines > 10000:
        raise ValidationError({"max_lines": "最多生成行数必须在 1 到 10000 之间。"})
    if params.recount_threshold < 0:
        raise ValidationError({"recount_threshold": "复盘阈值不能为负数。"})
    if params.subwarehouse_id:
        subwarehouse = Subwarehouse.objects.get(pk=params.subwarehouse_id)
        if subwarehouse.warehouse_id != warehouse.id:
            raise ValidationError({"subwarehouse_id": "子仓必须隶属于所选仓库。"})
    if params.location_id:
        location = Location.objects.get(pk=params.location_id)
        if location.warehouse_id != warehouse.id:
            raise ValidationError({"location_id": "库位必须隶属于所选仓库。"})
    if params.scope == "LOC" and not (params.location_id or params.location_prefix):
        raise ValidationError({"location_id": "按库位盘点必须指定库位或库位前缀。"})
    if params.scope == "ZONE" and params.zone_type is None:
        raise ValidationError({"zone_type": "按库区盘点必须指定库区。"})
    if params.scope == "SKU" and not params.product_id:
        raise ValidationError({"product_id": "按商品盘点必须指定商品。"})
    if (
        params.product_id
        and not Product.objects.filter(
            pk=params.product_id, owner_id=owner.id, is_active=True
        ).exists()
    ):
        raise ValidationError({"product_id": "商品必须属于所选货主且处于启用状态。"})
    return warehouse, owner


def _scope_locations(params: CountScopeParams):
    qs = Location.objects.filter(warehouse_id=params.warehouse_id, is_disabled=False)
    if params.subwarehouse_id:
        qs = qs.filter(subwarehouse_id=params.subwarehouse_id)
    if params.zone_type is not None:
        qs = qs.filter(zone_type=params.zone_type)
    if params.location_id:
        qs = qs.filter(pk=params.location_id)
    elif params.location_prefix:
        prefix = params.location_prefix.strip().upper()
        if prefix:
            qs = qs.filter(Q(code__istartswith=prefix) | Q(name__istartswith=prefix))
    return qs.order_by("id")


def _inventory_queryset(params: CountScopeParams):
    location_ids = _scope_locations(params).values_list("id", flat=True)
    qs = InventoryDetail.objects.filter(
        owner_id=params.owner_id,
        warehouse_id=params.warehouse_id,
        location_id__in=location_ids,
        is_active=True,
    )
    if params.product_id:
        qs = qs.filter(product_id=params.product_id)
    if params.batch_no:
        qs = qs.filter(batch_no__iexact=params.batch_no.strip().upper())
    if params.exclude_zero_onhand:
        qs = qs.filter(onhand_qty__gt=0)
    return qs.select_related("product", "location").order_by(
        "location__code", "product__code", "expiry_date", "batch_no", "id"
    )


def _params_from_extra(extra: CountTaskExtra) -> CountScopeParams:
    payload = dict(extra.scope_payload or {})
    return CountScopeParams(
        warehouse_id=extra.task.warehouse_id,
        owner_id=extra.task.owner_id,
        scope=extra.scope,
        subwarehouse_id=payload.get("subwarehouse_id"),
        zone_type=payload.get("zone_type"),
        location_id=payload.get("location_id"),
        location_prefix=payload.get("location_prefix") or None,
        product_id=payload.get("product_id"),
        batch_no=payload.get("batch_no") or None,
        exclude_zero_onhand=payload.get("exclude_zero_onhand", True),
        max_lines=payload.get("max_lines", 1000),
        blind=extra.blind,
        recount_threshold=_q4(extra.recount_threshold),
        task_remark=extra.task.remark or "",
    )


def _rebuild_count_lines(task: WmsTask, params: CountScopeParams) -> tuple[int, bool]:
    if task.status not in {WmsTask.Status.DRAFT, WmsTask.Status.READY}:
        raise ValidationError("只有草稿或待发布盘点任务可以刷新范围。")
    details = list(
        _inventory_queryset(params).select_for_update()[: params.max_lines + 1]
    )
    truncated = len(details) > params.max_lines
    details = details[: params.max_lines]
    if not details:
        raise ValidationError("所选范围当前没有可盘库存明细。")

    TaskScanLog.objects.filter(task=task).delete()
    CountLineExtra.objects.filter(line__task=task).delete()
    task.lines.all().delete()
    for detail in details:
        line = WmsTaskLine.objects.create(
            task=task,
            product_id=detail.product_id,
            from_location_id=detail.location_id,
            qty_plan=detail.onhand_qty or Decimal("0"),
            qty_done=Decimal("0"),
            status=WmsTaskLine.Status.DRAFT,
            src_model="InventoryDetail",
            src_id=detail.id,
        )
        CountLineExtra.objects.create(
            line=line,
            lot_no=(detail.batch_no or "").strip().upper(),
            exp_date=detail.expiry_date,
            qty_book=detail.onhand_qty or Decimal("0"),
            qty_counted=Decimal("0"),
            qty_diff=Decimal("0"),
            count_status="NOT_COUNTED",
            method="BLIND" if params.blind else "VERIFY",
            countorder=CountLineExtra.CountOrder.FIRST,
        )
    return len(details), truncated


@transaction.atomic
def create_count_task(*, created_by, **raw) -> tuple[WmsTask, int, bool]:
    scope = _infer_scope(
        scope=raw.get("scope"),
        zone_type=raw.get("zone_type"),
        location_id=raw.get("location_id"),
        location_prefix=raw.get("location_prefix"),
        product_id=raw.get("product_id"),
    )
    params = CountScopeParams(
        warehouse_id=int(raw["warehouse_id"]),
        owner_id=int(raw["owner_id"]),
        scope=scope,
        subwarehouse_id=raw.get("subwarehouse_id"),
        zone_type=raw.get("zone_type"),
        location_id=raw.get("location_id"),
        location_prefix=raw.get("location_prefix"),
        product_id=raw.get("product_id"),
        batch_no=raw.get("batch_no"),
        exclude_zero_onhand=raw.get("exclude_zero_onhand", True),
        max_lines=int(raw.get("max_lines") or 1000),
        blind=raw.get("blind", True),
        recount_threshold=_q4(raw.get("recount_threshold", 0)),
        task_remark=(raw.get("task_remark") or "").strip(),
    )
    warehouse, owner = _validate_params(params)
    task_no = DocSequence.next_code(
        doc_type="PD", warehouse=warehouse, owner=owner, biz_date=date.today()
    )
    task = WmsTask.objects.create(
        task_no=task_no,
        task_group_no=task_no,
        task_type=WmsTask.TaskType.COUNT,
        status=WmsTask.Status.DRAFT,
        owner=owner,
        warehouse=warehouse,
        created_by=created_by,
        remark=params.task_remark or "盘点任务",
    )
    CountTaskExtra.objects.create(
        task=task,
        scope=params.scope,
        blind=params.blind,
        freeze=True,
        recount_threshold=params.recount_threshold,
        scope_payload=params.payload(),
        root_task=task,
        round_no=1,
    )
    created, truncated = _rebuild_count_lines(task, params)
    return task, created, truncated


@transaction.atomic
def create_lines_from_scope(*, created_by, **raw):
    """Compatibility wrapper for the former scope generator."""

    try:
        task, created, truncated = create_count_task(created_by=created_by, **raw)
    except ValidationError as exc:
        if "没有可盘库存明细" in str(exc):
            return None, 0, False, []
        raise
    return task, created, truncated, []


def _lock_key(
    *, owner_id, warehouse_id, location_id, product_id=None, batch_no=""
) -> str:
    return ":".join(
        [
            str(owner_id),
            str(warehouse_id),
            str(location_id),
            str(product_id or "*"),
            (batch_no or "*").strip().upper() or "*",
        ]
    )


def _candidate_locks(task: WmsTask, params: CountScopeParams) -> list[dict]:
    product_id = params.product_id if params.scope == "SKU" else None
    batch_no = (params.batch_no or "").strip().upper() if product_id else ""
    rows = []
    for location_id in _scope_locations(params).values_list("id", flat=True):
        key = _lock_key(
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
            location_id=location_id,
            product_id=product_id,
            batch_no=batch_no,
        )
        rows.append(
            {
                "owner_id": task.owner_id,
                "warehouse_id": task.warehouse_id,
                "location_id": location_id,
                "product_id": product_id,
                "batch_no": batch_no,
                "lock_key": key,
                "active_key": key,
            }
        )
    if not rows:
        raise ValidationError("盘点范围内没有可用库位。")
    return rows


def _locks_overlap(existing: CountScopeLock, candidate: dict) -> bool:
    if (
        existing.owner_id != candidate["owner_id"]
        or existing.location_id != candidate["location_id"]
    ):
        return False
    if (
        existing.product_id
        and candidate["product_id"]
        and existing.product_id != candidate["product_id"]
    ):
        return False
    existing_batch = (existing.batch_no or "").upper()
    candidate_batch = (candidate["batch_no"] or "").upper()
    return (
        not existing_batch or not candidate_batch or existing_batch == candidate_batch
    )


def _assert_no_inflight_conflicts(task: WmsTask, candidates: list[dict]):
    location_ids = {row["location_id"] for row in candidates}
    qs = (
        WmsTaskLine.objects.filter(
            task__owner_id=task.owner_id,
            task__warehouse_id=task.warehouse_id,
            task__status__in=[
                WmsTask.Status.RESERVED,
                WmsTask.Status.RELEASED,
                WmsTask.Status.IN_PROGRESS,
            ],
        )
        .filter(
            Q(from_location_id__in=location_ids) | Q(to_location_id__in=location_ids)
        )
        .exclude(task_id=task.id)
    )
    product_ids = {row["product_id"] for row in candidates if row["product_id"]}
    if product_ids and all(row["product_id"] for row in candidates):
        qs = qs.filter(product_id__in=product_ids)
    conflict = (
        qs.select_related("task", "from_location", "to_location")
        .order_by("task_id", "id")
        .first()
    )
    if conflict:
        conflict_location = (
            conflict.from_location
            if conflict.from_location_id in location_ids
            else conflict.to_location
        )
        raise ValidationError(
            f"盘点范围与在途任务 {conflict.task.task_no} 冲突，库位 {conflict_location}。"
        )


@transaction.atomic
def release_count_task(task_id: int, *, by_user) -> WmsTask:
    if not _manager_allowed(by_user):
        raise PermissionDenied("无盘点发布权限。")
    warehouse_id = (
        WmsTask.objects.filter(pk=task_id).values_list("warehouse_id", flat=True).get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    task = (
        WmsTask.objects.select_for_update()
        .select_related("owner", "warehouse")
        .get(pk=task_id, task_type=WmsTask.TaskType.COUNT)
    )
    if task.status == WmsTask.Status.RELEASED:
        return task
    if task.status not in {WmsTask.Status.DRAFT, WmsTask.Status.READY}:
        raise ValidationError("仅草稿或待发布盘点任务可以发布。")
    if task.warehouse_id != warehouse_id:
        raise ValidationError("盘点任务仓库在发布期间发生变化，请重试。")
    extra = CountTaskExtra.objects.select_for_update().get(task=task)
    params = _params_from_extra(extra)
    _validate_params(params)
    _rebuild_count_lines(task, params)
    candidates = _candidate_locks(task, params)
    existing = list(
        CountScopeLock.objects.select_for_update().filter(
            warehouse_id=task.warehouse_id, released_at__isnull=True
        )
    )
    for row in candidates:
        conflict = next((lock for lock in existing if _locks_overlap(lock, row)), None)
        if conflict:
            raise ValidationError(
                f"盘点范围与任务 {conflict.active_task.task_no} 重叠，"
                f"冲突库位 {conflict.location}，发布已拒绝。"
            )
    _assert_no_inflight_conflicts(task, candidates)
    root_task = extra.root_task or task
    try:
        CountScopeLock.objects.bulk_create(
            [
                CountScopeLock(root_task=root_task, active_task=task, **row)
                for row in candidates
            ]
        )
    except IntegrityError as exc:
        raise ValidationError("盘点范围已被其他任务冻结，请刷新后重试。") from exc

    now = timezone.now()
    WmsTaskLine.objects.filter(task=task).update(status=WmsTaskLine.Status.RELEASED)
    old_status = task.status
    WmsTask.objects.filter(pk=task.pk).update(
        status=WmsTask.Status.RELEASED,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
        released_at=now,
        updated_by=by_user,
    )
    extra.snapshot_at = now
    extra.root_task = root_task
    extra.save(update_fields=["snapshot_at", "root_task"])
    TaskStatusLog.objects.create(
        task=task,
        old_status=old_status,
        new_status=WmsTask.Status.RELEASED,
        changed_by=by_user,
        note="盘点发布并冻结范围",
    )
    task.refresh_from_db()
    return task


def _root_task_id(task: WmsTask) -> int:
    extra = CountTaskExtra.objects.filter(task=task).only("root_task_id").first()
    return extra.root_task_id if extra and extra.root_task_id else task.id


def assert_inventory_not_count_locked(
    *,
    owner_id,
    warehouse_id,
    location_id,
    product_id,
    batch_no="",
    task: WmsTask | None = None,
):
    qs = CountScopeLock.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        location_id=location_id,
        released_at__isnull=True,
    ).filter(Q(product_id__isnull=True) | Q(product_id=product_id))
    normalized_batch = (batch_no or "").strip().upper()
    qs = qs.filter(Q(batch_no="") | Q(batch_no__iexact=normalized_batch))
    if task and task.task_type == WmsTask.TaskType.COUNT:
        qs = qs.exclude(root_task_id=_root_task_id(task))
    lock = qs.select_related("active_task", "location").order_by("id").first()
    if lock:
        raise ValidationError(
            f"库存处于盘点冻结中：任务 {lock.active_task.task_no}，库位 {lock.location}。"
        )


@transaction.atomic
def release_count_locks(task: WmsTask, *, by_user=None) -> int:
    now = timezone.now()
    return CountScopeLock.objects.filter(
        root_task_id=_root_task_id(task), released_at__isnull=True
    ).update(active_key=None, released_at=now, released_by=by_user)


def _mark_count_post_failed(task: WmsTask, *, by_user, exc: Exception):
    message = (str(exc) or "盘点过账失败")[:200]
    WmsTask.objects.filter(pk=task.pk).exclude(
        posting_status=WmsTask.PostingStatus.POSTED
    ).update(
        posting_status=WmsTask.PostingStatus.FAILED,
        posting_note=message,
        posted_by=by_user,
        updated_by=by_user,
    )
    PostingJournal.objects.update_or_create(
        src_model="WmsTask",
        src_id=task.id,
        tx_type="POST",
        defaults={"status": "FAILED", "message": message},
    )


@transaction.atomic
def claim_count_task(task_id: int, *, by_user) -> TaskAssignment:
    task = WmsTask.objects.select_for_update().get(
        pk=task_id, task_type=WmsTask.TaskType.COUNT
    )
    if task.status != WmsTask.Status.RELEASED:
        raise ValidationError("仅已发布盘点任务可以认领。")
    active = (
        TaskAssignment.objects.select_for_update()
        .filter(task=task, finished_at__isnull=True)
        .first()
    )
    if active and active.assignee_id != by_user.id:
        raise ValidationError("盘点任务已被他人认领。")
    assignment, _ = TaskAssignment.objects.get_or_create(task=task, assignee=by_user)
    assignment.accepted_at = assignment.accepted_at or timezone.now()
    assignment.finished_at = None
    assignment.save(update_fields=["accepted_at", "finished_at"])
    return assignment


def _assert_operator(task: WmsTask, user):
    if _manager_allowed(user):
        return
    if not user or not user.has_perm("tasking.claim_task_as_wh_operator"):
        raise PermissionDenied("无盘点操作权限。")
    if not TaskAssignment.objects.filter(
        task=task, assignee=user, finished_at__isnull=True
    ).exists():
        raise PermissionDenied("仅当前盘点任务负责人可以录入。")


@transaction.atomic
def record_count(
    task_id: int,
    *,
    line_id: int,
    qty_counted,
    client_seq: str,
    by_user,
    barcode: str = "",
    device_id: str = "",
    source: str = "PDA",
) -> dict:
    if not (client_seq or "").strip():
        raise ValidationError({"client_seq": "缺少幂等序号。"})
    task = WmsTask.objects.select_for_update().get(
        pk=task_id, task_type=WmsTask.TaskType.COUNT
    )
    _assert_operator(task, by_user)
    if task.status not in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}:
        raise ValidationError("盘点任务当前状态不允许录入。")
    line = (
        WmsTaskLine.objects.select_for_update()
        .select_related("product", "from_location")
        .get(pk=line_id, task=task)
    )
    extra = CountLineExtra.objects.select_for_update().get(line=line)
    counted = _q4(qty_counted)
    if counted < 0:
        raise ValidationError({"qty_counted": "实盘数量不能为负数。"})
    fp = hashlib.sha256(
        f"COUNT:{task.id}:{line.id}:{client_seq.strip()}".encode()
    ).hexdigest()
    prior = TaskScanLog.objects.filter(fp=fp).first()
    if prior:
        return {
            "idempotent": True,
            "line_id": line.id,
            "qty_counted": str(extra.qty_counted),
            "qty_diff": str(extra.qty_diff),
            "scan_id": prior.id,
        }

    diff = _q4(counted - _q4(extra.qty_book))
    TaskScanLog.objects.filter(
        task_line=line, posted_at__isnull=True, posting_journal__isnull=True
    ).update(status=TaskScanLog.ScanStatus.IGNORED, remark="SNAPSHOT_REPLACED")
    revision = int(line.scan_snapshot_rev or 0) + 1
    scan = TaskScanLog.objects.create(
        owner_id=task.owner_id,
        warehouse_id=task.warehouse_id,
        task=task,
        task_line=line,
        product=line.product,
        location=line.from_location,
        method=TaskScanLog.Method.SCAN if barcode else TaskScanLog.Method.MANUAL,
        source=source,
        by_user=by_user,
        barcode=(barcode or "").strip() or None,
        device_id=(device_id or "").strip() or None,
        qty_base=diff,
        qty_base_delta=None,
        lot_no=extra.lot_no or None,
        exp_date=extra.exp_date,
        fp=fp,
        scan_snapshot_rev=revision,
    )
    CountLineExtra.objects.filter(pk=extra.pk).update(
        qty_counted=counted, qty_diff=diff, count_status="COUNTED"
    )
    WmsTaskLine.objects.filter(pk=line.pk).update(
        qty_done=counted,
        status=WmsTaskLine.Status.COMPLETED,
        scan_snapshot_rev=revision,
        finished_at=timezone.now(),
        finished_by=by_user,
    )
    if task.status == WmsTask.Status.RELEASED:
        WmsTask.objects.filter(pk=task.pk).update(
            status=WmsTask.Status.IN_PROGRESS,
            started_at=task.started_at or timezone.now(),
            updated_by=by_user,
        )
    return {
        "idempotent": False,
        "line_id": line.id,
        "qty_counted": str(counted),
        "qty_diff": str(diff),
        "scan_id": scan.id,
    }


def _finish_assignments(task: WmsTask):
    TaskAssignment.objects.filter(task=task, finished_at__isnull=True).update(
        finished_at=timezone.now()
    )


def _assert_snapshot_unchanged(task: WmsTask):
    for extra in CountLineExtra.objects.filter(line__task=task).select_related("line"):
        detail = (
            InventoryDetail.objects.select_for_update()
            .filter(pk=extra.line.src_id, is_active=True)
            .first()
        )
        if not detail or _q4(detail.onhand_qty) != _q4(extra.qty_book):
            raise ValidationError(
                f"盘点行 {extra.line_id} 的账面库存已变化，请取消任务后重新发布。"
            )


def _create_recount(task: WmsTask, extras: list[CountLineExtra], *, by_user) -> WmsTask:
    current_extra = CountTaskExtra.objects.select_for_update().get(task=task)
    root_task = current_extra.root_task or task
    next_round = current_extra.round_no + 1
    task_no = DocSequence.next_code(
        doc_type="PD", warehouse=task.warehouse, owner=task.owner, biz_date=date.today()
    )
    recount = WmsTask.objects.create(
        task_no=task_no,
        task_group_no=task.task_group_no or root_task.task_no,
        task_type=WmsTask.TaskType.COUNT,
        status=WmsTask.Status.RELEASED,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
        owner=task.owner,
        warehouse=task.warehouse,
        created_by=by_user,
        released_at=timezone.now(),
        remark=f"任务号 {task.task_no} 的第 {next_round} 轮复盘",
    )
    order_values = [
        CountLineExtra.CountOrder.FIRST,
        CountLineExtra.CountOrder.SECOND,
        CountLineExtra.CountOrder.THIRD,
    ]
    order = order_values[min(next_round - 1, len(order_values) - 1)]
    CountTaskExtra.objects.create(
        task=recount,
        scope=current_extra.scope,
        blind=current_extra.blind,
        freeze=True,
        recount_threshold=current_extra.recount_threshold,
        scope_payload=current_extra.scope_payload,
        root_task=root_task,
        parent_task=task,
        round_no=next_round,
        snapshot_at=current_extra.snapshot_at,
    )
    for old in extras:
        if _q4(old.qty_diff) == 0:
            continue
        old_line = old.line
        line = WmsTaskLine.objects.create(
            task=recount,
            product=old_line.product,
            from_location=old_line.from_location,
            qty_plan=old.qty_book,
            qty_done=0,
            status=WmsTaskLine.Status.RELEASED,
            src_model=old_line.src_model,
            src_id=old_line.src_id,
        )
        CountLineExtra.objects.create(
            line=line,
            lot_no=old.lot_no,
            exp_date=old.exp_date,
            lpn_no=old.lpn_no,
            qty_book=old.qty_book,
            qty_counted=0,
            qty_diff=0,
            count_status="NOT_COUNTED",
            method="BLIND" if current_extra.blind else "VERIFY",
            countorder=order,
        )
    assignment = (
        TaskAssignment.objects.filter(task=task, finished_at__isnull=True)
        .select_related("assignee")
        .first()
    )
    _finish_assignments(task)
    if assignment:
        TaskAssignment.objects.create(
            task=recount, assignee=assignment.assignee, accepted_at=timezone.now()
        )
    CountScopeLock.objects.filter(root_task=root_task, released_at__isnull=True).update(
        active_task=recount
    )
    TaskStatusLog.objects.create(
        task=recount,
        old_status=WmsTask.Status.DRAFT,
        new_status=WmsTask.Status.RELEASED,
        changed_by=by_user,
        note=f"由盘点任务 {task.task_no} 自动发布复盘",
    )
    return recount


@transaction.atomic
def submit_count_task(task_id: int, *, by_user) -> dict:
    warehouse_id = (
        WmsTask.objects.filter(pk=task_id).values_list("warehouse_id", flat=True).get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    task = (
        WmsTask.objects.select_for_update()
        .select_related("owner", "warehouse")
        .get(pk=task_id, task_type=WmsTask.TaskType.COUNT)
    )
    if task.warehouse_id != warehouse_id:
        raise ValidationError("盘点任务仓库在提交期间发生变化，请重试。")
    _assert_operator(task, by_user)
    if task.status not in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}:
        raise ValidationError("盘点任务当前状态不允许提交。")
    extras = list(
        CountLineExtra.objects.select_for_update()
        .filter(line__task=task)
        .select_related("line", "line__product", "line__from_location")
        .order_by("line_id")
    )
    if not extras or any(extra.count_status != "COUNTED" for extra in extras):
        raise ValidationError("仍有未盘点明细，不能提交。")
    if TaskScanLog.objects.filter(
        task=task, status=TaskScanLog.ScanStatus.OK
    ).count() != len(extras):
        raise ValidationError("盘点有效快照数量与任务行不一致。")
    _assert_snapshot_unchanged(task)
    count_extra = CountTaskExtra.objects.select_for_update().get(task=task)
    differences = [extra for extra in extras if _q4(extra.qty_diff) != 0]
    significant = [
        extra
        for extra in differences
        if abs(_q4(extra.qty_diff)) > _q4(count_extra.recount_threshold)
    ]
    max_rounds = max(1, min(int(getattr(settings, "COUNT_MAX_TIMES", 2)), 3))
    now = timezone.now()
    old_status = task.status

    if significant and count_extra.round_no < max_rounds:
        WmsTask.objects.filter(pk=task.pk).update(
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.NEED_RECOUNT,
            posting_status=WmsTask.PostingStatus.NEED_RECOUNT,
            finished_at=now,
            updated_by=by_user,
        )
        recount = _create_recount(task, differences, by_user=by_user)
        TaskStatusLog.objects.create(
            task=task,
            old_status=old_status,
            new_status=WmsTask.Status.COMPLETED,
            changed_by=by_user,
            note=f"盘点完成，自动进入第 {count_extra.round_no + 1} 轮复盘",
        )
        return {"outcome": "RECOUNT_RELEASED", "next_task_id": recount.id}

    WmsTask.objects.filter(pk=task.pk).update(
        status=WmsTask.Status.COMPLETED,
        review_status=(
            WmsTask.ReviewStatus.PENDING
            if differences
            else WmsTask.ReviewStatus.APPROVED
        ),
        posting_status=(
            WmsTask.PostingStatus.NOT_READY
            if differences
            else WmsTask.PostingStatus.PENDING
        ),
        finished_at=now,
        approved_by=None if differences else by_user,
        approved_at=None if differences else now,
        approval_note="" if differences else "系统自动批准：盘点无差异",
        updated_by=by_user,
    )
    _finish_assignments(task)
    TaskStatusLog.objects.create(
        task=task,
        old_status=old_status,
        new_status=WmsTask.Status.COMPLETED,
        changed_by=by_user,
        note="盘点完成，等待审核" if differences else "盘点无差异，系统自动审核",
    )
    task.refresh_from_db()
    if differences:
        return {"outcome": "PENDING_APPROVAL", "task_id": task.id}

    from allapp.inventory import services as inventory_services

    scans = list(
        TaskScanLog.objects.filter(task=task, status=TaskScanLog.ScanStatus.OK)
    )
    try:
        result = inventory_services.post_task(
            task=task, user=by_user, scans=scans, note="盘点无差异自动闭环"
        )
    except Exception as exc:
        _mark_count_post_failed(task, by_user=by_user, exc=exc)
        return {"outcome": "POSTING_FAILED", "task_id": task.id, "detail": str(exc)}
    release_count_locks(task, by_user=by_user)
    return {"outcome": "AUTO_POSTED_NO_DIFF", "task_id": task.id, "posting": result}


@transaction.atomic
def approve_count_task(task_id: int, *, by_user, note="") -> WmsTask:
    if not _manager_allowed(by_user):
        raise PermissionDenied("无盘点审核权限。")
    task = WmsTask.objects.select_for_update().get(
        pk=task_id, task_type=WmsTask.TaskType.COUNT
    )
    if (
        task.status != WmsTask.Status.COMPLETED
        or task.review_status != WmsTask.ReviewStatus.PENDING
    ):
        raise ValidationError("当前盘点任务不在待审核状态。")
    now = timezone.now()
    WmsTask.objects.filter(pk=task.pk).update(
        review_status=WmsTask.ReviewStatus.APPROVED,
        posting_status=WmsTask.PostingStatus.PENDING,
        approved_by=by_user,
        approved_at=now,
        approval_note=(note or "").strip(),
    )
    task.refresh_from_db()
    return task


@transaction.atomic
def reject_count_task(task_id: int, *, by_user, note: str) -> WmsTask:
    if not _manager_allowed(by_user):
        raise PermissionDenied("无盘点审核权限。")
    if not (note or "").strip():
        raise ValidationError({"note": "驳回原因不能为空。"})
    task = WmsTask.objects.select_for_update().get(
        pk=task_id, task_type=WmsTask.TaskType.COUNT
    )
    if (
        task.status != WmsTask.Status.COMPLETED
        or task.review_status != WmsTask.ReviewStatus.PENDING
    ):
        raise ValidationError("当前盘点任务不在待审核状态。")
    WmsTask.objects.filter(pk=task.pk).update(
        review_status=WmsTask.ReviewStatus.REJECTED,
        posting_status=WmsTask.PostingStatus.NONE,
        approved_by=by_user,
        approved_at=timezone.now(),
        approval_note=note.strip(),
    )
    release_count_locks(task, by_user=by_user)
    task.refresh_from_db()
    return task


def post_count_task(task_id: int, *, by_user, note="") -> dict:
    if not _manager_allowed(by_user):
        raise PermissionDenied("无盘点过账权限。")
    with transaction.atomic():
        task = WmsTask.objects.select_for_update().get(
            pk=task_id, task_type=WmsTask.TaskType.COUNT
        )
        if task.review_status != WmsTask.ReviewStatus.APPROVED:
            raise ValidationError("盘点任务尚未审核通过。")
        scans = list(
            TaskScanLog.objects.filter(task=task, status=TaskScanLog.ScanStatus.OK)
        )
    from allapp.inventory import services as inventory_services

    try:
        result = inventory_services.post_task(
            task=task, user=by_user, scans=scans, note=note or "盘点差异过账"
        )
    except Exception as exc:
        with transaction.atomic():
            _mark_count_post_failed(task, by_user=by_user, exc=exc)
        raise
    release_count_locks(task, by_user=by_user)
    return result


@transaction.atomic
def cancel_count_task(task_id: int, *, by_user, note="") -> WmsTask:
    if not _manager_allowed(by_user):
        raise PermissionDenied("无盘点取消权限。")
    task = WmsTask.objects.select_for_update().get(
        pk=task_id, task_type=WmsTask.TaskType.COUNT
    )
    if task.posting_status == WmsTask.PostingStatus.POSTED:
        raise ValidationError("已过账盘点任务不能取消。")
    if task.status == WmsTask.Status.COMPLETED:
        raise ValidationError("已完成盘点任务请使用审核驳回。")
    old_status = task.status
    WmsTask.objects.filter(pk=task.pk).update(
        status=WmsTask.Status.CANCELLED,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
        finished_at=timezone.now(),
        updated_by=by_user,
        remark=((task.remark or "") + f" 取消原因：{note or '未填写'}")[:200],
    )
    _finish_assignments(task)
    release_count_locks(task, by_user=by_user)
    TaskStatusLog.objects.create(
        task=task,
        old_status=old_status,
        new_status=WmsTask.Status.CANCELLED,
        changed_by=by_user,
        note=note or "取消盘点",
    )
    task.refresh_from_db()
    return task
