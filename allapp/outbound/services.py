# allapp/outbound/services.py
#过账（PICK 执行）时释放 allocated
from __future__ import annotations
import logging
from decimal import Decimal

from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q

from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload
from allapp.inventory.models import InventoryDetail
from allapp.tasking.models import WmsTask, WmsTaskLine


TASK_TYPE_PICK = getattr(WmsTask.TaskType, "PICK", "PICK")
logger = logging.getLogger(__name__)

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
                .update(allocated_qty=F("allocated_qty") + alloc,
                        available_qty=F("onhand_qty") - F("allocated_qty") - F("locked_qty") - F("damaged_qty")
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


# 仓库管理员确认：将保留态任务（RESERVED）升级为 DRAFT 或 READY，不再重新切分
@transaction.atomic
def promote_reserved_pick(order, new_status="DRAFT") -> WmsTask:
    """仓库确认：将保留态（RESERVED）的任务升级为 DRAFT/READY"""
    # key = _task_source_key(order)
    # task = (
    #     WmsTask.objects
    #     .select_for_update()
    #     .filter(task_type=TASK_TYPE_PICK, **key)
    #     .first()
    # )

    task = (
        WmsTask.objects
        .select_for_update()
        .filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))  # 兼容旧数据
        .first()
    )

    if not task:
        raise ValidationError("未找到保留态的拣货任务，请先执行货主确认冻结。")

    # 1) 更新任务头状态
    task.status = new_status
    task.save(update_fields=["status"])

    # 2) 同步任务行状态（排除已完成/已取消）
    #    allocate_inventory 里曾把行 status 写成 "RESERVED"，这里一并切成目标状态
    #    new_status 是字符串（如 "DRAFT"/"READY"/"RELEASED"），与 WmsTaskLine.Status 的 value 一致
    valid_line_status_values = {choice.value for choice in WmsTaskLine.Status}
    if new_status in valid_line_status_values:
        (
            WmsTaskLine.objects
            .filter(task=task)
            .exclude(status__in=[WmsTaskLine.Status.COMPLETED, WmsTaskLine.Status.CANCELLED])
            .update(status=new_status)
        )

    task_ctx, task_text = build_log_payload(order=order, task=task)
    logger.info(
        "outbound.promote_reserved_pick.completed %s new_status=%s",
        task_text,
        new_status,
        extra=task_ctx,
    )
    return task

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
            ).update(allocated_qty=F("allocated_qty") - qty,
                     available_qty=F("onhand_qty") - F("allocated_qty") - F("locked_qty") - F("damaged_qty")
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

            # 生成任务行
            WmsTaskLine.objects.create(
                task=task,
                product_id=d['product_id'],
                from_location_id=det.location_id,
                to_location_id=None,  # 集货位
                qty_plan=alloc,
                # src_model=order.__class__.__name__,
                src_model=order._meta.model_name,
                src_id=order.id,
                src_line_id=d['line_id'],
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
