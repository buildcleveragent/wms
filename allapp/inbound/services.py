# allapp/inbound/services.py
import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload
from allapp.inbound.models import InboundOrder, InboundOrderLine
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.tasking.models import (
    ReceiveLineExtra,
    ReceiveTaskExtra,
    TaskAssignment,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)


logger = logging.getLogger(__name__)


@transaction.atomic
def finalize_receive_line_with_variance(line_id, *, by_user, variance_reason=""):
    """Finish a RECEIVE line without changing its original planned quantity.

    Full/over receipts keep using the canonical tasking finalizer.  A short or
    zero receipt is allowed only through this explicit endpoint and must carry
    a variance reason, preserving plan-vs-actual for reconciliation.
    """

    line = (
        WmsTaskLine.objects.select_for_update()
        .select_related("task")
        .get(pk=line_id)
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

    owns_line = TaskAssignment.objects.select_for_update().filter(
        task=task,
        assignee=by_user,
        finished_at__isnull=True,
    ).filter(models.Q(line=line) | models.Q(line__isnull=True))
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
        task_updates.update(status=WmsTask.Status.IN_PROGRESS, started_at=task.started_at or now)
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
    exists = (WmsTask.objects
              .filter(task_type=WmsTask.TaskType.RECEIVE,
                      source_app="inbound",
                      source_model="InboundOrder",
                      source_pk=str(order.pk))
              .exclude(status=WmsTask.Status.CANCELLED)
              .first())
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

    ReceiveTaskExtra.objects.create(
        task=task
    )

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

            ReceiveLineExtra.objects.create(
                line=taskline,
                lot_no=orderline.lot_no
            )

    ctx, ctx_text = build_log_payload(order=order, task=task, user=by_user)
    logger.info("inbound.receive_task.created %s", ctx_text, extra=ctx)
    return task


@transaction.atomic
def receive_goods_without_order(owner_id, items, remark="仓库操作员入库", warehouse_id=None, location_id=None):
    ctx, ctx_text = build_log_payload(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    logger.info(
        "inbound.receive_without_order.begin %s item_count=%s location_id=%s",
        ctx_text,
        len(items),
        location_id,
        extra=ctx,
    )
    if not warehouse_id:
        raise ValueError("receive_goods_without_order 必须显式传 warehouse_id")
    inbound_order = InboundOrder.objects.create(owner_id=owner_id, warehouse_id=warehouse_id, remark=remark)
    order_ctx, order_text = build_log_payload(
        order=inbound_order,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )
    logger.info("inbound.receive_without_order.order_created %s", order_text, extra=order_ctx)
    for item in items:
        product_id = item["product_id"]
        qty = item["qty"]

        inbound_order_line = InboundOrderLine.objects.create(
            inbound_order=inbound_order,
            product_id=product_id,
            qty=qty,
            uom="PCS"
        )

        # 更新库存
        InventoryDetail.objects.create(
            owner_id=owner_id,
            product_id=product_id,
            qty_on_hand=qty,
            uom="PCS",
            location_id=location_id,
        )

        # 创建库存事务
        InventoryTransaction.objects.create(
            inventory_detail=inbound_order_line,
            qty_received=qty,
            transaction_type="RECEIVE",
            reference=inbound_order.id,
        )
        logger.info(
            "inbound.receive_without_order.item_received %s product_id=%s qty=%s location_id=%s",
            order_text,
            product_id,
            qty,
            location_id,
            extra=order_ctx,
        )

    return {"order_id": inbound_order.id, "status": "success"}
