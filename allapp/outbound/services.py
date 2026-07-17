# allapp/outbound/services.py
#过账（PICK 执行）时释放 allocated
from __future__ import annotations
import logging
from datetime import date
from decimal import Decimal

from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum

from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload
from allapp.inventory.models import InventoryDetail
from allapp.tasking.models import TaskStatusLog, WmsTask, WmsTaskLine


TASK_TYPE_PICK = getattr(WmsTask.TaskType, "PICK", "PICK")
logger = logging.getLogger(__name__)

ASSISTED_PROCESSING_MODE = "WAREHOUSE_ASSISTED"
ASSISTED_CLOSE_REASON = "仓库代办出库完成"


def get_default_product_price(product) -> Decimal:
    """Resolve the shared default sales price used by outbound entry flows.

    The drop-ship importer intentionally keeps its historical zero-price
    fallback.  Callers that require a sellable price must explicitly reject a
    non-positive result.
    """

    for attr in ("price", "sale_price", "base_price"):
        value = getattr(product, attr, None)
        if value in (None, ""):
            continue
        try:
            return Decimal(str(value))
        except (ArithmeticError, TypeError, ValueError):
            continue
    return Decimal("0")

# def _task_source_key(order):
#     """统一构造 WmsTask 的来源三元组"""
#     return {
#         # "source_app":   order._meta.app_label,   # e.g. "outbound"
#         "source_model": order._meta.model_name,  # e.g. "outboundorder"
#         "source_pk":    order.pk,
#     }

def _task_source_key(order):
    """统一构造 WmsTask 的来源键（canonical）"""
    return {
        "source_model": order._meta.model_name,   # outboundorder
        "source_pk": str(order.pk),               # CharField，统一用 str
    }

def _task_source_q(order):
    """兼容历史数据：既匹配 canonical(outboundorder)，也匹配 legacy(OutboundOrder)。"""
    return Q(source_pk=str(order.pk)) & (
        Q(source_model=order._meta.model_name) |
        Q(source_model=order.__class__.__name__)
    )


# 延迟导入 OutboundOrder
def get_outbound_order_model():
    from allapp.outbound.models import OutboundOrder  # 延迟导入，避免循环导入
    return OutboundOrder

# Helper: 获取或创建保留态任务（RESERVED）
def _get_or_create_reserved_task(order, by_user=None) -> WmsTask:
    """获取或创建保留态（RESERVED）的拣货任务，用来承载已冻结配额"""
    ctx, ctx_text = build_log_payload(order=order, user=by_user)
    logger.info("outbound.reserved_task.lookup.begin %s", ctx_text, extra=ctx)
    key = _task_source_key(order)
    task = (
        WmsTask.objects
        .filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))  # 兼容旧数据
        .exclude(status__in=["CANCELLED", "COMPLETED"])
        .first()
    )

    if task:
        task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
        logger.info("outbound.reserved_task.lookup.reuse %s", task_text, extra=task_ctx)
        return task

    # 2) 生成任务号（用你项目已有的 DocSequence）
    task_no = DocSequence.next_code(
        doc_type="JH",
        warehouse=order.warehouse,
        owner=order.owner,
        biz_date=order.biz_date,
    )

    task = WmsTask.objects.create(
        task_no=task_no,
        task_type=TASK_TYPE_PICK,
        owner_id=order.owner_id,
        warehouse_id=order.warehouse_id,
        **key,  # canonical 写入
        status="RESERVED",
        created_by=by_user,
        created_at=timezone.now(),
    )
    task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
    logger.info("outbound.reserved_task.created %s", task_text, extra=task_ctx)
    return task

    # return WmsTask.objects.create(
    #     task_no=task_no,
    #     task_type=TASK_TYPE_PICK,
    #     owner_id=order.owner_id,
    #     warehouse_id=order.warehouse_id,
    #     **key,  # canonical 写入
    #     status="RESERVED",
    #     created_by=by_user,
    #     created_at=timezone.now(),
    # )
    # return WmsTask.objects.create(
    #     task_no=task_no,
    #     task_type=TASK_TYPE_PICK,
    #     owner_id=order.owner_id,
    #     warehouse_id=order.warehouse_id,
    #     **key,  # canonical 写入
    #     status="RESERVED",
    #     created_by=by_user,
    #     created_at=timezone.now(),
    # )

# Helper: 计算订单行需求
def _compute_line_demands(order) -> list:
    """计算出库单的总需求"""
    demands = []
    line_map = {}
    for line in order.lines.all().only("id", "product_id", "base_qty"):  # 可根据你的需求字段调整
        qty = getattr(line, "base_qty") or Decimal("0")
        if qty <= 0:
            continue
        line_map[line.id] = line.product_id
        demands.append({
            'line_id': line.id,
            'product_id': line.product_id,
            'demand': qty,
        })
    return demands

# Helper: 获取按 FEFO 排序的库存明细（冻结量）
def _fefo_details_qs(owner_id: int, warehouse_id: int, product_id: int):
    """获取某产品按 FEFO 排序的库存明细，按库存可用量递减"""
    return (
        InventoryDetail.objects
        .filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            is_active=True,  # 若使用 is_active 进行库存标记
            available_qty__gt=0,  # 仅可用库存
        )
        .select_for_update(skip_locked=True)  # 锁住行，防止并发冲突
        .order_by(
            "expiry_date",  # FEFO：早到期的先分配
            "-onhand_qty"  # 若效期相同，优先使用库存多的
        )
        .only("id", "location_id", "available_qty", "allocated_qty", "onhand_qty")
    )

# 冻结库存：available → allocated，并将切分结果写入保留态任务（RESERVED）
@transaction.atomic
def allocate_inventory(order, by_user=None, allow_backorder=True):
    """货主管理员确认时，冻结库存，并生成/刷新保留拣货任务（RESERVED）"""
    order = type(order).objects.select_for_update().get(pk=order.pk)
    ctx, ctx_text = build_log_payload(order=order, user=by_user)
    logger.info("outbound.allocate_inventory.begin %s", ctx_text, extra=ctx)
    task = _get_or_create_reserved_task(order, by_user=by_user)
    existing_lines = (
        WmsTaskLine.objects
        .select_for_update()
        .filter(task=task)
        .exclude(status=WmsTaskLine.Status.CANCELLED)
    )
    if existing_lines.exists():
        task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
        logger.info(
            "outbound.allocate_inventory.skip_existing %s line_count=%s",
            task_text,
            existing_lines.count(),
            extra=task_ctx,
        )
        return task
    demands = _compute_line_demands(order)
    if not demands:
        raise ValidationError("出库单没有有效需求行。")
    for d in demands:
        remaining = d["demand"]
        qs = _fefo_details_qs(order.owner_id, order.warehouse_id, d["product_id"])
        for det in qs:
            if remaining <= 0:
                break
            avail = det.available_qty
            if avail <= 0:
                continue

            alloc = min(avail, remaining)

            # 硬分配：冻结 available → allocated
            updated = (
                InventoryDetail.objects
                .filter(pk=det.pk, available_qty__gte=alloc)
                .update(
                    allocated_qty=F("allocated_qty") + alloc,
                    # Keep the inventory identity without depending on a
                    # database's evaluation order for multiple UPDATE
                    # assignments (MySQL differs from SQLite/PostgreSQL).
                    available_qty=F("available_qty") - alloc,
                )
            )

            if updated == 0:
                det.refresh_from_db(fields=["available_qty", "allocated_qty"])
                task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
                logger.warning(
                    "outbound.allocate_inventory.retryable_conflict %s detail_id=%s product_id=%s location_id=%s qty=%s",
                    task_text,
                    det.id,
                    d["product_id"],
                    det.location_id,
                    alloc,
                    extra=task_ctx,
                )
                continue

            # 在保留态任务中添加“冻结配额”行
            WmsTaskLine.objects.create(
                task=task,
                product_id=d["product_id"],
                from_location_id=det.location_id,
                to_location_id=None,  # 可选：集货/包装位
                qty_plan=alloc,  # 这就是冻结的配额量
                src_model="OutboundOrderLine",
                src_id=d["line_id"],
                rule_key="FEFO",
                status="RESERVED",
            )
            task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
            logger.info(
                "outbound.allocate_inventory.detail_allocated %s product_id=%s location_id=%s qty=%s line_id=%s detail_id=%s",
                task_text,
                d["product_id"],
                det.location_id,
                alloc,
                d["line_id"],
                det.id,
                extra=task_ctx,
            )
            remaining -= alloc

        # 如果库存不足，并且不允许补货，抛出错误
        if remaining > 0 and not allow_backorder:
            raise ValidationError(f"库存不足，产品 {d['product_id']} 缺口 {remaining}。")

        # 如果库存不足，并且允许补货，提醒用户
        if remaining > 0 and allow_backorder:
            task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
            logger.warning(
                "outbound.allocate_inventory.shortage %s product_id=%s shortage_qty=%s",
                task_text,
                d["product_id"],
                remaining,
                extra=task_ctx,
            )

    task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
    logger.info("outbound.allocate_inventory.completed %s", task_text, extra=task_ctx)
    return task


# 仓库管理员确认：严格将保留态任务发布，不再重新切分
@transaction.atomic
def promote_reserved_pick(
    order,
    new_status=WmsTask.Status.RELEASED,
    *,
    by_user=None,
) -> WmsTask:
    """Publish exactly one matching RESERVED PICK task.

    ``RELEASED`` is an idempotent terminal result for this conversion.  Other
    source states are rejected so an unrelated or already-running task cannot
    be silently rewritten by an approval action.
    """

    if new_status != WmsTask.Status.RELEASED:
        raise ValidationError("保留拣货任务仅允许发布为 RELEASED。")

    candidates = list(
        WmsTask.objects.select_for_update()
        .filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))
        .exclude(status=WmsTask.Status.CANCELLED)
        .order_by("id")[:2]
    )
    if not candidates:
        raise ValidationError("未找到保留态的拣货任务，请先执行货主确认冻结。")
    if len(candidates) != 1:
        raise ValidationError("订单关联了多个有效拣货任务，禁止自动发布。")

    task = candidates[0]
    if task.owner_id != order.owner_id or task.warehouse_id != order.warehouse_id:
        raise ValidationError("拣货任务与订单的货主或仓库不一致。")
    if str(task.source_pk) != str(order.pk):
        raise ValidationError("拣货任务来源与订单不一致。")

    if task.status == WmsTask.Status.RELEASED:
        return task
    if task.status != WmsTask.Status.RESERVED:
        raise ValidationError(f"任务状态为 {task.status}，仅 RESERVED 可发布。")

    old_status = task.status
    now = timezone.now()
    task.status = WmsTask.Status.RELEASED
    task.released_at = now
    task.updated_at = now
    task.updated_by = by_user
    task.save(
        update_fields=["status", "released_at", "updated_at", "updated_by"]
    )

    WmsTaskLine.objects.filter(task=task).exclude(
        status__in=[WmsTaskLine.Status.COMPLETED, WmsTaskLine.Status.CANCELLED]
    ).update(status=WmsTaskLine.Status.RELEASED, updated_at=now, updated_by=by_user)
    TaskStatusLog.objects.create(
        task=task,
        old_status=old_status,
        new_status=WmsTask.Status.RELEASED,
        changed_by=by_user,
        note="出库订单仓库确认发布",
    )

    task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
    logger.info(
        "outbound.promote_reserved_pick.completed %s new_status=%s",
        task_text,
        new_status,
        extra=task_ctx,
    )
    return task


@transaction.atomic
def approve_and_release_order(order, *, by_user, allow_backorder=True) -> WmsTask:
    """Perform owner approval, warehouse approval and PICK publication atomically."""

    Order = type(order)
    order = Order.objects.select_for_update().select_related("owner", "warehouse").get(pk=order.pk)

    if order.submit_status != "SUBMITTED":
        raise ValidationError("仅已提交订单可执行确认发布。")
    if order.approval_status in {"OWNER_PENDING", "OWNER_REJECTED"}:
        order.owner_approve(by_user=by_user, allow_backorder=allow_backorder)
        order.refresh_from_db()
    elif order.approval_status not in {"OWNER_APPROVED", "WHS_PENDING", "WHS_APPROVED"}:
        raise ValidationError(f"订单审核状态 {order.approval_status} 不允许确认发布。")

    if order.approval_status in {"OWNER_APPROVED", "WHS_PENDING"}:
        order.approval_status = "WHS_APPROVED"
        order.approved_by_warehouse = by_user
        order.approved_at_warehouse = timezone.now()
        order.updated_by = by_user
        order.save(
            update_fields=[
                "approval_status",
                "approved_by_warehouse",
                "approved_at_warehouse",
                "updated_by",
                "updated_at",
            ]
        )

    return promote_reserved_pick(
        order,
        new_status=WmsTask.Status.RELEASED,
        by_user=by_user,
    )


@transaction.atomic
def create_warehouse_assisted_order(*, validated_data, by_user):
    """Create, fully allocate, approve and release one assisted SALES order."""

    OutboundOrder = get_outbound_order_model()
    OutboundOrderLine = OutboundOrder._meta.apps.get_model("outbound", "OutboundOrderLine")
    owner = validated_data["owner"]
    customer = validated_data["customer"]
    items = validated_data["items"]
    now = timezone.now()

    order = OutboundOrder.objects.create(
        owner=owner,
        warehouse_id=by_user.warehouse_id,
        customer=customer,
        supplier=None,
        outbound_type="SALES",
        delivery_method=validated_data.get("delivery_method"),
        etd=validated_data.get("etd"),
        memo=validated_data.get("remark", ""),
        src_bill_no=validated_data.get("src_bill_no") or None,
        contact=validated_data.get("contact") or None,
        contact_phone=validated_data.get("contact_phone") or None,
        ship_to=validated_data.get("ship_to") or None,
        biz_date=date.today(),
        submit_status="SUBMITTED",
        approval_status="OWNER_PENDING",
        processing_mode=ASSISTED_PROCESSING_MODE,
        assisted_by=by_user,
        assisted_at=now,
        assistance_reason=validated_data.get("assistance_reason", ""),
        assistance_request_id=validated_data["request_id"],
        created_by=by_user,
        updated_by=by_user,
    )

    for item in items:
        product = item["product"]
        OutboundOrderLine.objects.create(
            order=order,
            product=product,
            base_uom=product.base_uom,
            base_qty=item["qty"],
            base_price=item["price"],
            aux_uom=item.get("package"),
            aux_qty=item.get("package_qty"),
            ratio=(
                Decimal(item["package"].qty_in_base)
                if item.get("package") is not None
                else None
            ),
            created_by=by_user,
            updated_by=by_user,
        )

    task = approve_and_release_order(
        order,
        by_user=by_user,
        allow_backorder=False,
    )
    order.refresh_from_db()
    return order, task


@transaction.atomic
def close_assisted_order_for_posted_task(task):
    """Idempotently close a fully allocated one-order/one-PICK assisted order."""

    from .authz import get_assisted_order_for_task

    order = get_assisted_order_for_task(task, for_update=True)
    if order is None:
        return None

    matching_tasks = list(
        WmsTask.objects.select_for_update()
        .filter(task_type=WmsTask.TaskType.PICK)
        .filter(_task_source_q(order))
        .exclude(status=WmsTask.Status.CANCELLED)
        .values_list("id", flat=True)[:2]
    )
    if matching_tasks != [task.id]:
        raise ValidationError("代办订单不是一单一 PICK，禁止自动关闭。")

    demand_by_line = {
        line.id: Decimal(line.base_qty or 0)
        for line in order.lines.filter(is_deleted=False)
    }
    planned_by_line = {
        row["src_id"]: Decimal(row["qty"] or 0)
        for row in (
            WmsTaskLine.objects.filter(task=task)
            .exclude(status=WmsTaskLine.Status.CANCELLED)
            .values("src_id")
            .annotate(qty=Sum("qty_plan"))
        )
    }
    if demand_by_line != planned_by_line:
        raise ValidationError("代办订单未全量分配，禁止自动关闭。")

    if not order.is_closed:
        order.is_closed = True
        order.close_reason = ASSISTED_CLOSE_REASON
        update_fields = ["is_closed", "close_reason", "updated_at"]
        if getattr(task, "posted_by_id", None):
            order.updated_by_id = task.posted_by_id
            update_fields.append("updated_by")
        order.save(update_fields=update_fields)
    return order

# 仓库管理员拒绝：释放已冻结的库存并取消任务
@transaction.atomic
def unallocate_for_order(order) -> Decimal:
    """仓库拒绝：释放库存（allocated_qty -= qty_plan），取消相关任务"""
    released = Decimal("0")
    ctx, ctx_text = build_log_payload(order=order)
    logger.info("outbound.unallocate.begin %s", ctx_text, extra=ctx)
    # key = _task_source_key(order)
    # tasks = (
    #     WmsTask.objects
    #     .select_for_update()
    #     .filter(task_type=TASK_TYPE_PICK, **key)
    #     .exclude(status__in=["CANCELLED", "COMPLETED"])
    # )

    tasks = (
        WmsTask.objects
        .select_for_update()
        .filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))  # 兼容旧数据
        .exclude(status__in=["CANCELLED", "COMPLETED"])
    )

    for task in tasks:
        for tl in WmsTaskLine.objects.filter(task=task):
            qty = tl.qty_plan
            # 释放已冻结的 allocated_qty
            InventoryDetail.objects.filter(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                product_id=tl.product_id,
                location_id=tl.from_location_id,
                allocated_qty__gte=qty,
            ).update(
                allocated_qty=F("allocated_qty") - qty,
                available_qty=F("available_qty") + qty,
            )
            released += qty
            task_ctx, task_text = build_log_payload(order=order, task=task)
            logger.info(
                "outbound.unallocate.line_released %s product_id=%s location_id=%s qty=%s",
                task_text,
                tl.product_id,
                tl.from_location_id,
                qty,
                extra=task_ctx,
            )

        # 取消任务
        WmsTaskLine.objects.filter(task=task).delete()
        task.status = "CANCELLED"
        task.save(update_fields=["status"])
        task_ctx, task_text = build_log_payload(order=order, task=task)
        logger.info("outbound.unallocate.task_cancelled %s", task_text, extra=task_ctx)
    logger.info("outbound.unallocate.completed %s released_qty=%s", ctx_text, released, extra=ctx)
    return released

# 生成拣货任务草稿：把 RESERVE 任务升级为 DRAFT/READY
@transaction.atomic
def create_pick_task(order, task_status="DRAFT") -> WmsTask:
    """生成拣货任务草稿"""
    task = _get_or_create_reserved_task(order)

    demands = _compute_line_demands(order)
    if not demands:
        raise ValidationError("出库单没有有效需求行。")

    # 生成拣货任务行
    for d in demands:
        remaining = d['demand']
        qs = _fefo_details_qs(order.owner_id, order.warehouse_id, d['product_id'])

        for det in qs:
            if remaining <= 0:
                break
            avail = det.available_qty
            if avail <= 0:
                continue

            alloc = min(avail, remaining)

            # 生成任务行（指向 OutboundOrderLine，与 allocate_inventory 一致）
            WmsTaskLine.objects.create(
                task=task,
                product_id=d['product_id'],
                from_location_id=det.location_id,
                to_location_id=None,  # 集货位
                qty_plan=alloc,
                src_model="OutboundOrderLine",
                src_id=d['line_id'],
                rule_key="FEFO",
            )
            remaining -= alloc

        if remaining > 0:
            raise ValidationError(f"库存不足，产品 {d['product_id']} 缺口 {remaining}。")

    task.status = task_status
    task.save(update_fields=["status"])
    task_ctx, task_text = build_log_payload(order=order, task=task)
    logger.info(
        "outbound.create_pick_task.completed %s task_status=%s",
        task_text,
        task_status,
        extra=task_ctx,
    )
    return task

# 放行拣货任务：DRAFT → READY
@transaction.atomic
def wave_release(task_ids: list[int]) -> int:
    """
    将一批 DRAFT 状态的拣货任务（PICK）升级为 READY。
    这通常是波次放行的操作，用于仓库确认后触发的操作。
    """

    tasks = WmsTask.objects.filter(id__in=task_ids, task_type=TASK_TYPE_PICK, status="DRAFT")

    if not tasks.exists():
        raise ValidationError("没有找到符合条件的拣货任务。")

    updated_count = tasks.update(status="READY")  # 批量更新状态为 READY
    logger.info("outbound.wave_release.completed task_count=%s task_ids=%s", updated_count, task_ids)

    return updated_count
