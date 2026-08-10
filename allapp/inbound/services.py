# allapp/inbound/services.py
import hashlib
import json
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from allapp.accounts.audit import record_audit_event
from allapp.baseinfo.models import Owner
from allapp.baseinfo.owner_warehouse_access import owner_can_use_warehouse
from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload
from allapp.inbound.constants import (
    PDA_NO_ORDER_RECEIVE_NOTE,
    PDA_NO_ORDER_RECEIVE_SOURCE_APP,
    PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
)
from allapp.inbound.models import InboundOrder, NoOrderReceiveRequest
from allapp.locations.models import Location, Warehouse
from allapp.products.models import Product
from allapp.tasking.models import (
    ReceiveLineExtra,
    ReceiveTaskExtra,
    TaskAssignment,
    TaskScanLog,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.services import _run_posting_handler, save_receiving_snapshot

logger = logging.getLogger(__name__)


class NoOrderReceiveConflict(Exception):
    """The same idempotency key was reused with different receiving content."""


def canonical_no_order_items(items):
    """Return the stable, JSON-safe representation used for signing and idempotency."""

    normalized = []
    for item in items:
        mfg_date = item.get("mfg_date")
        exp_date = item.get("exp_date")
        normalized.append(
            {
                "product_id": int(item["product_id"]),
                "qty": format(Decimal(str(item["qty"])), "f"),
                "lot_no": (item.get("lot_no") or "").strip().upper(),
                "mfg_date": (
                    mfg_date.isoformat()
                    if hasattr(mfg_date, "isoformat")
                    else (mfg_date or None)
                ),
                "exp_date": (
                    exp_date.isoformat()
                    if hasattr(exp_date, "isoformat")
                    else (exp_date or None)
                ),
            }
        )
    return sorted(
        normalized,
        key=lambda item: (
            item["product_id"],
            item["lot_no"],
            item["mfg_date"] or "",
            item["exp_date"] or "",
            item["qty"],
        ),
    )


def no_order_items_hash(items):
    normalized_items = canonical_no_order_items(items)
    for item in normalized_items:
        item["qty"] = format(Decimal(item["qty"]).normalize(), "f")
    raw = json.dumps(
        normalized_items,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _no_order_payload_hash(*, owner_id, warehouse_id, location_id, remark, items):
    canonical = json.dumps(
        {
            "owner_id": int(owner_id),
            "warehouse_id": int(warehouse_id),
            "location_id": int(location_id) if location_id else None,
            "remark": remark,
            "items": canonical_no_order_items(items),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@transaction.atomic
def finalize_receive_line_with_variance(line_id, *, by_user, variance_reason=""):
    """Finish a RECEIVE line without changing its original planned quantity.

    Full/over receipts keep using the canonical tasking finalizer.  A short or
    zero receipt is allowed only through this explicit endpoint and must carry
    a variance reason, preserving plan-vs-actual for reconciliation.
    """

    line = (
        WmsTaskLine.objects.select_for_update().select_related("task").get(pk=line_id)
    )
    task = line.task
    if task.task_type != WmsTask.TaskType.RECEIVE:
        raise ValidationError("仅收货任务行支持收货差异结束。")
    if line.finished_at:
        return {
            "line": line.pk,
            "qty_total": str(line.qty_done or 0),
            "task_status": task.status,
            "idempotent": True,
        }
    if task.status not in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}:
        raise ValidationError("任务未处于可执行状态。")

    owns_line = (
        TaskAssignment.objects.select_for_update()
        .filter(
            task=task,
            assignee=by_user,
            finished_at__isnull=True,
        )
        .filter(models.Q(line=line) | models.Q(line__isnull=True))
    )
    if not owns_line.exists():
        raise PermissionDenied("仅当前任务负责人可以结束收货行。")

    try:
        extra = ReceiveLineExtra.objects.select_for_update().get(line=line)
    except ReceiveLineExtra.DoesNotExist as exc:
        raise ValidationError("缺少收货扩展，无法结束收货行。") from exc
    total = (
        Decimal(extra.qty_ok or 0)
        + Decimal(extra.qty_damage or 0)
        + Decimal(extra.qty_reject or 0)
    )
    plan = Decimal(line.qty_plan or 0)
    reason = (variance_reason or "").strip()
    if total != plan and not reason:
        raise ValidationError("收货数量与计划不一致时必须填写差异原因。")

    now = timezone.now()
    WmsTaskLine.objects.filter(pk=line.pk).update(
        qty_done=total,
        status=WmsTaskLine.Status.COMPLETED,
        finished_at=now,
        finished_by=by_user,
        remark=(reason or line.remark or "")[:200],
        updated_by=by_user,
        updated_at=now,
    )
    TaskAssignment.objects.filter(
        line=line,
        finished_at__isnull=True,
    ).update(finished_at=now)

    old_status = task.status
    all_done = not WmsTaskLine.objects.filter(
        task=task,
        finished_at__isnull=True,
    ).exists()
    task_updates = {"updated_by": by_user, "updated_at": now}
    if all_done:
        task_updates.update(
            status=WmsTask.Status.COMPLETED,
            review_status=WmsTask.ReviewStatus.PENDING,
            finished_at=now,
        )
    elif task.status == WmsTask.Status.RELEASED:
        task_updates.update(
            status=WmsTask.Status.IN_PROGRESS, started_at=task.started_at or now
        )
    WmsTask.objects.filter(pk=task.pk).update(**task_updates)
    task.refresh_from_db()
    if task.status != old_status:
        TaskStatusLog.objects.create(
            task=task,
            old_status=old_status,
            new_status=task.status,
            changed_by=by_user,
            note=(reason or "收货行完成")[:200],
        )
    return {
        "line": line.pk,
        "qty_total": str(total),
        "task_status": task.status,
        "all_done": all_done,
        "idempotent": False,
    }


@transaction.atomic
def close_inbound_order_after_putaway(putaway_task, *, by_user=None):
    """Close the source ASN only after every derived putaway task is posted."""

    task = WmsTask.objects.select_for_update().get(pk=putaway_task.pk)
    if task.task_type != WmsTask.TaskType.PUTAWAY:
        return None
    if task.posting_status != WmsTask.PostingStatus.POSTED:
        raise ValidationError("上架任务尚未过账，不能关闭入库单。")
    if task.source_model != "WmsTask" or not str(task.source_pk).isdigit():
        return None
    receive_task = WmsTask.objects.filter(
        pk=int(task.source_pk),
        task_type=WmsTask.TaskType.RECEIVE,
        owner_id=task.owner_id,
        warehouse_id=task.warehouse_id,
    ).first()
    if not receive_task:
        return None
    if (
        receive_task.source_app != "inbound"
        or receive_task.source_model != "InboundOrder"
        or not str(receive_task.source_pk).isdigit()
    ):
        return None

    unposted_exists = (
        WmsTask.objects.filter(
            task_type=WmsTask.TaskType.PUTAWAY,
            source_app="tasking",
            source_model="WmsTask",
            source_pk=str(receive_task.pk),
        )
        .exclude(status=WmsTask.Status.CANCELLED)
        .exclude(posting_status=WmsTask.PostingStatus.POSTED)
        .exists()
    )
    if unposted_exists:
        return None

    order = (
        InboundOrder.objects.select_for_update()
        .filter(
            pk=int(receive_task.source_pk),
            owner_id=task.owner_id,
            warehouse_id=task.warehouse_id,
        )
        .first()
    )
    if not order or order.is_closed:
        return order
    order.is_closed = True
    order.close_reason = "上架完成并已过账"
    order.updated_by = by_user
    order.save(update_fields=["is_closed", "close_reason", "updated_by", "updated_at"])

    from allapp.accounts.audit import record_audit_event

    record_audit_event(
        action="inbound.order.close_after_putaway",
        module="inbound",
        user=by_user,
        obj=order,
        before={"is_closed": False},
        after={"is_closed": True, "close_reason": order.close_reason},
        metadata={"putaway_task_id": task.pk, "receive_task_id": receive_task.pk},
    )
    return order


@transaction.atomic
def create_receive_task_draft(order, by_user=None):
    """
    根据入库订单生成一张【收货(RECEIVE)】任务草稿（幂等：同源只建一张）。
    """
    order = type(order).objects.select_for_update().get(pk=order.pk)

    # 1) 幂等：已有同源任务则直接返回（排除已取消）
    exists = (
        WmsTask.objects.filter(
            task_type=WmsTask.TaskType.RECEIVE,
            source_app="inbound",
            source_model="InboundOrder",
            source_pk=str(order.pk),
        )
        .exclude(status=WmsTask.Status.CANCELLED)
        .first()
    )
    if exists:
        ctx, ctx_text = build_log_payload(order=order, task=exists, user=by_user)
        logger.info("inbound.receive_task.reuse %s", ctx_text, extra=ctx)
        return exists

    # 2) 生成任务号（用你项目已有的 DocSequence）
    task_no = DocSequence.next_code(
        doc_type="SH",
        warehouse=order.warehouse,
        owner=order.owner,
        biz_date=order.biz_date,
    )

    # 3) 任务头：草稿
    task = WmsTask.objects.create(
        owner=order.owner,
        warehouse=order.warehouse,
        task_no=task_no,
        task_type=WmsTask.TaskType.RECEIVE,
        status=WmsTask.Status.DRAFT,
        ref_no=order.order_no,
        source_app="inbound",
        source_model="InboundOrder",
        source_pk=str(order.pk),
        created_by=by_user,
        remark="系统：仓库确认后自动创建收货任务草稿",
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
    )

    ReceiveTaskExtra.objects.create(task=task)

    # 4) 任务行：按订单行生成计划数量（这里使用 base_qty；如有“已收数量”，你可自行扣减）
    for orderline in order.lines.select_related("product").all():
        plan = orderline.base_qty or 0
        if plan and plan > 0:
            taskline = WmsTaskLine.objects.create(
                task=task,
                product=orderline.product,
                qty_plan=plan,
                qty_done=0,
                src_model="inbound.InboundOrderLine",
                src_id=orderline.pk,
            )

            ReceiveLineExtra.objects.create(line=taskline, lot_no=orderline.lot_no)

    ctx, ctx_text = build_log_payload(order=order, task=task, user=by_user)
    logger.info("inbound.receive_task.created %s", ctx_text, extra=ctx)
    return task


@transaction.atomic
def receive_goods_without_order(
    *,
    owner_id,
    warehouse_id,
    items,
    request_id,
    by_user,
    remark=PDA_NO_ORDER_RECEIVE_NOTE,
    location_id=None,
    request=None,
    source="manual",
):
    """Create, snapshot, and post one idempotent no-order receiving task."""

    if not warehouse_id:
        raise ValidationError("receive_goods_without_order 必须显式传 warehouse_id")
    if not items:
        raise ValidationError({"items": "至少需要一条收货明细"})

    try:
        warehouse = Warehouse.objects.get(pk=warehouse_id, is_active=True)
    except Warehouse.DoesNotExist as exc:
        raise ValidationError(f"warehouse_id 不存在或已停用：{warehouse_id}") from exc
    try:
        owner = Owner.objects.get(pk=owner_id, is_active=True)
    except Owner.DoesNotExist as exc:
        raise ValidationError(f"owner_id 不存在或已停用：{owner_id}") from exc
    if not owner_can_use_warehouse(owner.id, warehouse.id):
        raise PermissionDenied("该货主未授权当前仓库。")

    product_ids = {int(item["product_id"]) for item in items}
    product_map = {
        product.id: product
        for product in Product.objects.filter(id__in=product_ids, is_active=True)
    }
    missing_product_ids = sorted(product_ids - set(product_map))
    if missing_product_ids:
        raise ValidationError(
            {"items": f"product_id 不存在或已停用：{missing_product_ids}"}
        )
    foreign_product_ids = sorted(
        product_id
        for product_id, product in product_map.items()
        if product.owner_id != int(owner_id)
    )
    if foreign_product_ids:
        raise PermissionDenied(f"存在不属于当前货主的商品：{foreign_product_ids}")

    location = None
    if location_id:
        try:
            location = Location.objects.get(pk=location_id, is_active=True)
        except Location.DoesNotExist as exc:
            raise ValidationError(f"location_id 不存在或已停用：{location_id}") from exc
        if location.warehouse_id != warehouse.id:
            raise ValidationError("location_id 必须属于当前 warehouse")

    remark = (remark or PDA_NO_ORDER_RECEIVE_NOTE).strip()
    payload_hash = _no_order_payload_hash(
        owner_id=owner.id,
        warehouse_id=warehouse.id,
        location_id=location_id,
        remark=remark,
        items=items,
    )
    request_record = (
        NoOrderReceiveRequest.objects.select_for_update()
        .filter(created_by=by_user, request_id=request_id)
        .select_related("task")
        .first()
    )
    if request_record is None:
        try:
            with transaction.atomic():
                request_record = NoOrderReceiveRequest.objects.create(
                    request_id=request_id,
                    payload_hash=payload_hash,
                    created_by=by_user,
                    owner=owner,
                    warehouse=warehouse,
                )
        except IntegrityError:
            request_record = (
                NoOrderReceiveRequest.objects.select_for_update()
                .select_related("task")
                .get(created_by=by_user, request_id=request_id)
            )

    if (
        request_record.payload_hash != payload_hash
        or request_record.owner_id != owner.id
        or request_record.warehouse_id != warehouse.id
    ):
        raise NoOrderReceiveConflict
    if request_record.task_id:
        task = request_record.task
        return {
            "task_id": task.id,
            "task_no": task.task_no,
            "posted": task.posting_status == WmsTask.PostingStatus.POSTED,
            "idempotent": True,
            "message": "该请求已处理，返回原收货结果",
        }

    task_no = DocSequence.next_code(
        doc_type="RK",
        warehouse=warehouse,
        owner=owner,
        biz_date=date.today(),
    )
    task = WmsTask.objects.create(
        task_no=task_no,
        task_type=WmsTask.TaskType.RECEIVE,
        owner=owner,
        warehouse=warehouse,
        created_by=by_user,
        source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
        source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
        remark=remark,
        posting_note=PDA_NO_ORDER_RECEIVE_NOTE,
        status=WmsTask.Status.RELEASED,
        review_status=WmsTask.ReviewStatus.NOT_READY,
        posting_status=WmsTask.PostingStatus.NOT_READY,
    )

    grouped = defaultdict(Decimal)
    snapshot_grouped = defaultdict(Decimal)
    normalized_items = []
    for item in items:
        product_id = int(item["product_id"])
        qty = Decimal(str(item["qty"]))
        if qty <= 0:
            raise ValidationError(f"产品 {product_id} 的数量必须 > 0")
        lot_no = (item.get("lot_no") or "").strip().upper()
        mfg_date = item.get("mfg_date")
        exp_date = item.get("exp_date")
        grouped[product_id] += qty
        snapshot_grouped[(product_id, lot_no, mfg_date, exp_date)] += qty
        normalized_items.append(
            {
                "product_id": product_id,
                "qty": str(qty),
                "lot_no": lot_no,
                "mfg_date": str(mfg_date) if mfg_date else None,
                "exp_date": str(exp_date) if exp_date else None,
            }
        )

    ctx, ctx_text = build_log_payload(
        task=task,
        user=by_user,
        owner=owner,
        warehouse=warehouse,
    )
    logger.info(
        "inbound.receive_without_order.normalized_items %s items=%s",
        ctx_text,
        normalized_items,
        extra=ctx,
    )

    for product_id, total_qty in grouped.items():
        line = WmsTaskLine.objects.create(
            task=task,
            product_id=product_id,
            status=WmsTaskLine.Status.RELEASED,
            qty_plan=total_qty,
        )
        product = product_map[product_id]
        snap_items = [
            {
                "product": product,
                "qty_ok": item_qty,
                "location": location,
                "lot_no": lot_no,
                "mfg_date": mfg_date,
                "exp_date": exp_date,
            }
            for (
                item_product_id,
                lot_no,
                mfg_date,
                exp_date,
            ), item_qty in snapshot_grouped.items()
            if item_product_id == product_id
        ]
        save_receiving_snapshot(
            task_line_id=line.id,
            items=snap_items,
            operator=by_user,
            source="PDA",
        )

        for snap in snap_items:
            mfg_date = snap.get("mfg_date")
            if not mfg_date:
                continue
            TaskScanLog.objects.filter(
                task=task,
                task_line=line,
                product_id=product_id,
                lot_no=(snap.get("lot_no") or None),
                exp_date=snap.get("exp_date"),
                mfg_date__isnull=True,
                posted_at__isnull=True,
            ).update(mfg_date=mfg_date)

    task.status = WmsTask.Status.COMPLETED
    task.review_status = WmsTask.ReviewStatus.APPROVED
    task.posting_status = WmsTask.PostingStatus.PENDING
    task.save(update_fields=["status", "review_status", "posting_status"])

    logger.info(
        "inbound.receive_without_order.posting.begin %s item_count=%s",
        ctx_text,
        len(grouped),
        extra=ctx,
    )
    posting_result = _run_posting_handler(
        task_id=task.id,
        by_user=by_user,
        note=PDA_NO_ORDER_RECEIVE_NOTE,
    )
    request_record.task = task
    request_record.save(update_fields=["task", "updated_at"])
    record_audit_event(
        action="inbound.receive_without_order.post",
        module="inbound",
        request=request,
        user=by_user,
        obj=task,
        before={},
        after={
            "status": task.status,
            "review_status": task.review_status,
            "posting_status": task.posting_status,
        },
        metadata={
            "request_id": request_id,
            "item_count": len(items),
            "source": source,
        },
    )
    logger.info(
        "inbound.receive_without_order.posting.completed %s", ctx_text, extra=ctx
    )
    return {
        "task_id": task.id,
        "task_no": task.task_no,
        "posted": True,
        "idempotent": False,
        "message": "收货成功",
        **(posting_result or {}),
    }
