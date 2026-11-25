"""Warehouse-manager recovery for an invalid, unposted sales reservation."""

from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.inventory.locking import lock_warehouses_for_inventory_write
from allapp.inventory.models import InventoryDetail, InventoryTransaction, PostingJournal
from allapp.inventory.sale_eligibility import locked_sale_layer_ids, sale_layers
from allapp.inventory.services import _refresh_summaries, lock_active_inventory_details_for_update
from allapp.outbound.ownership import inventory_snapshot, transfer_for_order
from allapp.outbound.services import (
    _fefo_details_qs,
    _order_for_outbound_task,
    get_review_task_for_pick,
)
from allapp.outbound.transfer_models import AssistedTransferReservation
from allapp.tasking.counting import assert_inventory_not_count_locked
from allapp.tasking.models import TaskScanLog, TaskStatusLog, WmsTask, WmsTaskLine


def can_reallocate(actor, task):
    scope = AccessScope.for_user(actor)
    return scope.is_global or (
        scope.is_valid
        and scope.source == "user_role_scope"
        and "warehouse_manager" in scope.roles
        and task.warehouse_id in scope.warehouse_ids
        and actor.has_perm("tasking.taskconfirm_as_wh_manager")
    )


def _source_id(line):
    meta = line.plan_meta or {}
    source_id = meta.get("inventory_detail_id") or meta.get("source_inventory_detail_id")
    return int(source_id) if str(source_id).isdigit() and int(source_id) > 0 else None


def mark_reallocatable_lines(actor, task, lines):
    if (
        task.task_type != "PICK"
        or task.source_app == "pos"
        or task.status == "CANCELLED"
        or task.posting_status == "POSTED"
        or not can_reallocate(actor, task)
    ):
        return
    order = _order_for_outbound_task(task)
    if not order or order.is_closed or order.outbound_type != "SALES":
        return
    transfer = transfer_for_order(order)
    source_owner = transfer.source_owner_id if transfer else task.owner_id
    ids = {line.pk: _source_id(line) for line in lines if line.status != "CANCELLED"}
    eligible = set(
        sale_layers().filter(pk__in=[i for i in ids.values() if i]).values_list("pk", flat=True)
    )
    invalid_sources = InventoryDetail.all_objects.filter(
        pk__in=set(ids.values()) - eligible - {None},
        owner_id=source_owner,
        warehouse_id=task.warehouse_id,
    ).in_bulk()
    revisions = dict(
        TaskScanLog.objects.filter(task=task, task_line_id__in=ids, status="OK")
        .values("task_line_id")
        .annotate(revision=Max("pk"))
        .values_list("task_line_id", "revision")
    )
    for line in lines:
        detail = invalid_sources.get(ids.get(line.pk))
        line.can_reallocate = bool(
            detail
            and detail.location_id == line.from_location_id
            and (transfer or detail.product_id == line.product_id)
        )
        if line.can_reallocate:
            line.invalid_source = detail
            line.reallocation_revision = revisions.get(line.pk, 0)


def _count_guard(detail, task):
    assert_inventory_not_count_locked(
        owner_id=detail.owner_id,
        warehouse_id=detail.warehouse_id,
        product_id=detail.product_id,
        location_id=detail.location_id,
        batch_no=detail.batch_no,
        task=task,
    )


def _reserve_replacements(task, line, source_owner, source_product, reservation, actor):
    remaining = line.qty_plan
    replacements = []
    for detail in _fefo_details_qs(source_owner, task.warehouse_id, source_product).defer(None):
        qty = min(remaining, detail.available_qty.quantize(Decimal("0.001"), rounding=ROUND_DOWN))
        if qty <= 0:
            continue
        _count_guard(detail, task)
        detail.allocated_qty += qty
        detail.save()
        meta = (
            {
                "source_inventory_detail_id": detail.pk,
                "source_owner_id": source_owner,
                "source_product_id": source_product,
            }
            if reservation
            else {"inventory_detail_id": detail.pk}
        )
        new_line = WmsTaskLine.objects.create(
            task=task,
            product_id=line.product_id,
            from_location_id=detail.location_id,
            to_location_id=line.to_location_id,
            qty_plan=qty,
            src_model=line.src_model,
            src_id=line.src_id,
            rule_key=line.rule_key,
            status=task.status if task.status in ("RESERVED", "READY", "DRAFT") else "RELEASED",
            plan_meta={**meta, "replaces_line_id": line.pk},
            created_by=actor,
            updated_by=actor,
        )
        if reservation:
            AssistedTransferReservation.objects.create(
                transfer_line=reservation.transfer_line,
                task_line=new_line,
                source_inventory=detail,
                quantity=qty,
                source_snapshot=inventory_snapshot(detail),
                created_by=actor,
            )
        replacements.append(
            {
                "line_id": new_line.pk,
                "inventory_detail_id": detail.pk,
                "quantity": str(qty),
                "source": inventory_snapshot(detail),
            }
        )
        remaining -= qty
        if not remaining:
            break
    if remaining:
        raise ValidationError(f"可售替代库存不足，缺少 {remaining}。原预留和扫描已保留。")
    return replacements


def _retire_review(task, actor):
    review = get_review_task_for_pick(task, for_update=True)
    if review is None:
        return None
    if review.posting_status == WmsTask.PostingStatus.POSTED:
        raise ValidationError("复核已经过账，不能重配。")
    previous = {
        "id": review.pk,
        "status": review.status,
        "review_status": review.review_status,
        "posting_status": review.posting_status,
        "approved_by_id": review.approved_by_id,
        "approved_at": review.approved_at.isoformat() if review.approved_at else None,
    }
    TaskStatusLog.objects.create(
        task=review,
        old_status=review.status,
        new_status=WmsTask.Status.CANCELLED,
        changed_by=actor,
        note="原库存失效，重配后需重新复核",
    )
    review.status = WmsTask.Status.CANCELLED
    review.review_status = WmsTask.ReviewStatus.NONE
    review.posting_status = WmsTask.PostingStatus.NONE
    review.updated_by = actor
    review.save(
        update_fields=["status", "review_status", "posting_status", "updated_by", "updated_at"]
    )
    return previous


@transaction.atomic
def reallocate_sale_line(
    *, task, line_id, reason, expected_qty, expected_revision, physical_confirmed, actor
):
    """Keep unrelated lines and replace the whole invalid layer with exact new reservations."""
    if not can_reallocate(actor, task):
        raise PermissionDenied("只有本仓库管理员可重配失效预留。")
    reason = (reason or "").strip()
    if not reason or len(reason) > 250:
        raise ValidationError("请填写不超过 250 字的重配原因。")
    original_warehouse = task.warehouse_id
    lock_warehouses_for_inventory_write(original_warehouse)
    order = _order_for_outbound_task(task, for_update=True)
    task = WmsTask.objects.select_for_update().get(pk=task.pk)
    if (
        task.task_type != "PICK"
        or not order
        or order.outbound_type != "SALES"
        or task.source_app == "pos"
    ):
        raise ValidationError("仅销售出库的拣货任务可重配。")
    if (
        task.warehouse_id != original_warehouse
        or task.warehouse_id != order.warehouse_id
        or task.owner_id != order.owner_id
    ):
        raise ValidationError("任务归属已变化，请刷新。")
    line = WmsTaskLine.objects.select_for_update().filter(pk=line_id, task=task).first()
    if not line:
        raise ValidationError("找不到本任务的预留行。")
    request_facts = {
        "reason": reason,
        "expected_qty": str(expected_qty),
        "expected_revision": expected_revision,
        "physical_confirmed": physical_confirmed,
        "actor_id": actor.pk,
    }
    prior = (line.plan_meta or {}).get("sale_reallocation")
    if prior and line.status == "CANCELLED":
        if prior["request"] == request_facts:
            return {"replacement_lines": prior["replacement_lines"], "idempotent": True}
        raise ValidationError("原预留已重配，请刷新任务。")
    if (
        order.is_closed
        or order.approval_status in ("CANCELLED", "WHS_REJECTED", "OWNER_REJECTED")
        or task.status == "CANCELLED"
        or line.status == "CANCELLED"
    ):
        raise ValidationError("订单、任务或预留行已结束。")
    journal = (
        PostingJournal.objects.select_for_update()
        .filter(src_model="WmsTask", src_id=task.pk, tx_type="POST")
        .first()
    )
    if (
        task.posting_status == "POSTED"
        or (journal and journal.status == "POSTED")
        or InventoryTransaction.objects.filter(src_model="WmsTask", src_id=task.pk).exists()
        or TaskScanLog.objects.filter(task=task, posted_at__isnull=False).exists()
    ):
        raise ValidationError("库存已经过账，不能重配。")
    # Picking appends facts (including signed manual adjustments); receipt snapshot
    # revisions do not change when a PICK quantity changes and returns to its old value.
    latest_scan_id = line.scan_logs.filter(status="OK").aggregate(latest=Max("pk"))["latest"] or 0
    if line.qty_done != expected_qty or latest_scan_id != expected_revision:
        raise ValidationError("拣货进度发生变化，请刷新后重新核实。")
    if expected_qty > 0 and not physical_confirmed:
        raise ValidationError("请先核实原已扫实物已归位或隔离。")
    transfer = transfer_for_order(order)
    reservation = None
    source_owner, source_product = task.owner_id, line.product_id
    if transfer:
        if transfer.status != transfer.Status.RESERVED:
            raise ValidationError("当前转货状态不允许重配。")
        reservation = (
            AssistedTransferReservation.objects.select_for_update()
            .filter(task_line=line, transfer_line__transfer=transfer, status="RESERVED")
            .select_related("transfer_line")
            .first()
        )
        if (
            not reservation
            or reservation.quantity != line.qty_plan
            or reservation.source_inventory_id != _source_id(line)
        ):
            raise ValidationError("转货来源预留待核对。")
        source_owner, source_product = (
            transfer.source_owner_id,
            reservation.transfer_line.source_product_id,
        )
    if (line.src_model or "").lower() not in (
        "outboundorderline",
        "outbound.outboundorderline",
    ) or not order.lines.filter(pk=line.src_id, product_id=line.product_id).exists():
        raise ValidationError("预留与销售订单行不一致。")
    pair = (source_owner, source_product)
    lock_active_inventory_details_for_update([pair])
    old = (
        InventoryDetail.all_objects.select_for_update()
        .filter(
            pk=_source_id(line),
            owner_id=source_owner,
            product_id=source_product,
            warehouse_id=task.warehouse_id,
            location_id=line.from_location_id,
        )
        .first()
    )
    if not old or old.allocated_qty < line.qty_plan or not line.qty_plan:
        raise ValidationError("原库存或预留数量待核对。")
    if old.pk in locked_sale_layer_ids([old]):
        raise ValidationError("原预留仍可销售，无需重配。")
    _count_guard(old, task)
    replacements = _reserve_replacements(
        task, line, source_owner, source_product, reservation, actor
    )
    InventoryDetail.all_objects.filter(pk=old.pk).update(
        allocated_qty=F("allocated_qty") - line.qty_plan,
        available_qty=F("available_qty") + line.qty_plan,
    )
    if reservation:
        reservation.status = "RELEASED"
        reservation.save(update_fields=["status", "updated_at"])
    scans = TaskScanLog.objects.select_for_update().filter(task=task, task_line=line, status="OK")
    scan_ids = list(scans.order_by("pk").values_list("pk", flat=True))
    scans.update(status="IGNORED")
    review = _retire_review(task, actor)
    evidence = {
        "request": request_facts,
        "at": timezone.now().isoformat(),
        "original_source": inventory_snapshot(old),
        "original_inventory_detail_id": old.pk,
        "original_qty_plan": str(line.qty_plan),
        "original_qty_done": str(line.qty_done),
        "ignored_scan_ids": scan_ids,
        "cancelled_review_id": review["id"] if review else None,
        "previous_review": review,
        "replacement_lines": replacements,
    }
    line.plan_meta = {**(line.plan_meta or {}), "sale_reallocation": evidence}
    line.status = "CANCELLED"
    line.qty_done = 0
    line.updated_by = actor
    line.save(update_fields=["plan_meta", "status", "qty_done", "updated_by", "updated_at"])
    if (
        review
        or task.status == "COMPLETED"
        or task.review_status in ("APPROVED", "PENDING")
        or task.posting_status == "FAILED"
    ):
        TaskStatusLog.objects.create(
            task=task,
            old_status=task.status,
            new_status="IN_PROGRESS",
            changed_by=actor,
            note="失效库存重配，重新拣货和复核",
        )
        WmsTask.objects.filter(pk=task.pk).update(
            status="IN_PROGRESS",
            review_status="NOT_READY",
            posting_status="NOT_READY",
            approved_by=None,
            approved_at=None,
            finished_at=None,
            updated_by=actor,
            updated_at=timezone.now(),
        )
    if journal and journal.status == "FAILED":
        journal.status = "PENDING"
        journal.save(update_fields=["status", "updated_at"])
    _refresh_summaries([pair])
    record_audit_event(
        action="outbound.pick.reallocate",
        module="outbound",
        user=actor,
        obj=task,
        metadata={"original_line_id": line.pk, **evidence},
    )
    return {"replacement_lines": replacements, "idempotent": False}
