# allapp/outbound/services.py
# 过账（PICK 执行）时释放 allocated
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from allapp.accounts.access import AccessScope
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.owner_warehouse_access import owner_can_use_warehouse
from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload
from allapp.inventory.locking import lock_warehouses_for_inventory_write
from allapp.inventory.models import InventoryDetail
from allapp.products.pricing import InvalidSalePriceRule, minimum_sale_price
from allapp.tasking.models import (
    DispatchTaskExtra,
    PackTaskExtra,
    ReplenishmentPolicy,
    ReviewLineExtra,
    ReviewTaskExtra,
    TaskStatusLog,
    WmsTask,
    WmsTaskLine,
)

TASK_TYPE_PICK = getattr(WmsTask.TaskType, "PICK", "PICK")
logger = logging.getLogger(__name__)

ASSISTED_PROCESSING_MODE = "WAREHOUSE_ASSISTED"
ASSISTED_CLOSE_REASON = "仓库代办出库完成"


def require_assisted_owner_warehouse(owner, warehouse_id) -> None:
    """Enforce the assisted-outbound business boundary at the write service."""

    if (
        not owner
        or not owner.is_active
        or owner.is_deleted
        or not owner.allow_warehouse_assisted_outbound
        or not owner_can_use_warehouse(owner.pk, warehouse_id)
    ):
        raise PermissionDenied("该货主未关联当前授权仓库。")


def validate_sale_mini_payment_for_fulfillment(order) -> None:
    """Block sale-mini fulfillment until its settlement is authoritative."""

    from allapp.salesapp.models import SaleMiniOrderMapping

    mappings = SaleMiniOrderMapping.objects.filter(outbound_order_id=order.pk)
    if transaction.get_connection().in_atomic_block:
        mappings = mappings.select_for_update()
    mapping = mappings.first()
    if not mapping:
        return
    allowed = {
        SaleMiniOrderMapping.PaymentStatus.PAID,
        SaleMiniOrderMapping.PaymentStatus.OFFLINE,
    }
    if mapping.payment_status not in allowed:
        raise ValidationError(
            f"商城订单支付状态为 {mapping.get_payment_status_display()}，"
            "付款确认前禁止仓库履约。"
        )


def validate_pick_task_sale_mini_payment(task) -> None:
    """Apply the sale-mini payment gate to a PICK task from an outbound order."""

    source_model = (task.source_model or "").lower()
    if task.task_type != TASK_TYPE_PICK or not source_model.endswith("outboundorder"):
        return
    OutboundOrder = get_outbound_order_model()
    try:
        order = OutboundOrder.objects.get(pk=int(task.source_pk))
    except (TypeError, ValueError, OutboundOrder.DoesNotExist) as exc:
        raise ValidationError("拣货任务无法解析对应的出库订单。") from exc
    validate_sale_mini_payment_for_fulfillment(order)


def validate_owner_approval_preconditions(order) -> None:
    """Validate the business state required before a owner approval.

    Keeping this check in the service layer lets every UI entry point use the
    same rule instead of relying on which buttons happen to be visible.  It is
    deliberately separate from authorization: callers must still prove the
    approving user's tenant scope before invoking it.
    """

    if order.submit_status != "SUBMITTED":
        raise ValidationError("仅已提交订单可由货主管理员审核。")
    if order.is_closed:
        raise ValidationError("已关闭订单不能执行货主管理员审核。")
    if order.approval_status != "OWNER_PENDING":
        raise ValidationError(f"订单审核状态 {order.approval_status} 不允许货主管理员审核。")


def can_edit_standard_draft(order, user, *, scope=None) -> bool:
    """Return whether ``user`` is the original salesperson for this draft."""

    if not getattr(user, "is_authenticated", False):
        return False
    if order.processing_mode != "STANDARD" or order.is_closed:
        return False
    if order.submit_status != "DRAFT" or order.approval_status not in {
        "OWNER_PENDING",
        "OWNER_REJECTED",
    }:
        return False
    if order.created_by_id != user.id:
        return False
    scope = scope or AccessScope.for_user(user)
    return bool(
        user.has_perm("outbound.submit_outbound_as_owner_buyers")
        and scope.is_valid
        and UserRoleScope.Role.OWNER_SALESPERSON in scope.roles
        and order.owner_id in scope.owner_ids
    )


def validate_standard_draft_edit_preconditions(order, user) -> None:
    if not can_edit_standard_draft(order, user):
        raise ValidationError("仅原创建业务员可修改或重新提交可编辑的标准草稿订单。")


@transaction.atomic
def approve_owner_order(order, *, by_user, allow_backorder=True):
    """Lock, revalidate and approve one owner order atomically."""

    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在货主审核期间发生变化，请重试。")
    validate_owner_approval_preconditions(locked)
    locked._apply_owner_approval(
        by_user=by_user,
        allow_backorder=allow_backorder,
    )
    locked.refresh_from_db()
    return locked


@transaction.atomic
def reject_owner_order(order, *, by_user, reason):
    """Return a submitted pending order to its creator as an editable draft."""

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "请填写退回原因。"})
    if len(reason) > 200:
        raise ValidationError({"reason": "退回原因不能超过 200 个字符。"})

    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在货主驳回期间发生变化，请重试。")
    validate_owner_approval_preconditions(locked)
    _validate_allocation_can_be_released(locked)
    unallocate_for_order(locked, by_user=by_user)
    locked.submit_status = "DRAFT"
    locked.approval_status = "OWNER_REJECTED"
    locked.owner_reject_reason = reason
    locked.approved_by_ownermanager = by_user
    locked.approved_at_ownermanager = timezone.now()
    locked.updated_by = by_user
    locked.save(
        update_fields=[
            "submit_status",
            "approval_status",
            "owner_reject_reason",
            "approved_by_ownermanager",
            "approved_at_ownermanager",
            "updated_by",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def submit_owner_draft(order, *, by_user):
    """Submit an editable draft while preserving the latest reject reason."""

    Order = type(order)
    locked = Order.objects.select_for_update().get(pk=order.pk)
    validate_standard_draft_edit_preconditions(locked, by_user)
    locked.submit_status = "SUBMITTED"
    locked.approval_status = "OWNER_PENDING"
    locked.approved_by_ownermanager = None
    locked.approved_at_ownermanager = None
    locked.approved_by_warehouse = None
    locked.approved_at_warehouse = None
    locked.pricing_status = "PENDING"
    locked.priced_at = None
    locked.priced_by = None
    locked.final_order_amount = Decimal("0.00")
    locked.close_reason = None
    locked.updated_by = by_user
    locked.save(
        update_fields=[
            "submit_status",
            "approval_status",
            "approved_by_ownermanager",
            "approved_at_ownermanager",
            "approved_by_warehouse",
            "approved_at_warehouse",
            "pricing_status",
            "priced_at",
            "priced_by",
            "final_order_amount",
            "close_reason",
            "updated_by",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def confirm_warehouse_order(order, *, by_user):
    """Lock and confirm a still-submitted owner-approved standard order."""

    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在仓库确认期间发生变化，请重试。")
    if (
        locked.submit_status != "SUBMITTED"
        or locked.is_closed
        or locked.approval_status not in {"OWNER_APPROVED", "WHS_PENDING"}
    ):
        raise ValidationError("仅已提交、货主审核通过且未关闭的订单可由仓库确认。")

    validate_sale_mini_payment_for_fulfillment(locked)
    shortages = allocation_shortfalls(locked)
    if shortages and settings.REPLENISHMENT_DEMAND_ENABLED:
        from allapp.tasking.replenishment import create_demand_tasks

        create_demand_tasks(locked, shortages, by_user=by_user)
        locked.approval_status = "WHS_PENDING"
        locked.approved_by_warehouse = by_user
        locked.approved_at_warehouse = timezone.now()
        locked.updated_by = by_user
        locked.save(
            update_fields=[
                "approval_status",
                "approved_by_warehouse",
                "approved_at_warehouse",
                "updated_by",
                "updated_at",
            ]
        )
        task = (
            WmsTask.objects.filter(task_type=TASK_TYPE_PICK)
            .filter(_task_source_q(locked))
            .exclude(status=WmsTask.Status.CANCELLED)
            .order_by("id")
            .first()
        )
        return locked, task

    locked.approval_status = "WHS_APPROVED"
    locked.approved_by_warehouse = by_user
    locked.approved_at_warehouse = timezone.now()
    locked.updated_by = by_user
    locked.save(
        update_fields=[
            "approval_status",
            "approved_by_warehouse",
            "approved_at_warehouse",
            "updated_by",
            "updated_at",
        ]
    )
    task = promote_reserved_pick(locked, by_user=by_user)
    return locked, task


@transaction.atomic
def resume_waiting_orders_after_replenishment(replenishment_task, *, by_user=None):
    """Retry pick allocation and release orders unblocked by a replenishment."""

    from allapp.outbound.models import OutboundOrder

    lock_warehouses_for_inventory_write(replenishment_task.warehouse_id)
    product_ids = set(replenishment_task.lines.values_list("product_id", flat=True))
    if not product_ids:
        return []
    order_ids = list(
        OutboundOrder.objects.filter(
            owner_id=replenishment_task.owner_id,
            warehouse_id=replenishment_task.warehouse_id,
            approval_status="WHS_PENDING",
            is_closed=False,
            lines__product_id__in=product_ids,
        )
        .distinct()
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )
    resumed = []
    for order_id in order_ids:
        order = OutboundOrder.objects.select_for_update().get(pk=order_id)
        allocate_inventory(order, by_user=by_user, allow_backorder=True)
        if allocation_shortfalls(order):
            continue
        order.approval_status = "WHS_APPROVED"
        order.updated_by = by_user
        order.save(update_fields=["approval_status", "updated_by", "updated_at"])
        promote_reserved_pick(order, by_user=by_user)
        resumed.append(order.pk)
    return resumed


def replenishment_waiting_detail(order) -> dict:
    """Return the deterministic shortage and active demand-task view for an order."""

    shortages = allocation_shortfalls(order)
    tasks = (
        WmsTask.objects.filter(
            task_type=WmsTask.TaskType.REPLEN,
            status__in=[
                WmsTask.Status.DRAFT,
                WmsTask.Status.READY,
                WmsTask.Status.RELEASED,
                WmsTask.Status.IN_PROGRESS,
            ],
        )
        .filter(
            Q(source_model="OutboundOrder", source_pk=str(order.pk))
            | Q(replenishtaskextra__demand_order_ids__contains=[order.pk])
        )
        .distinct()
        .order_by("id")
    )
    return {
        "task_nos": list(tasks.values_list("task_no", flat=True)),
        "shortages": [
            {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
            }
            for row in shortages
        ],
    }


def validate_standard_order_sale_prices(order) -> None:
    """Reject unsafe line prices before a standard sales order is approved."""

    if order.processing_mode != "STANDARD" or order.outbound_type != "SALES":
        return

    errors = []
    lines = order.lines.filter(is_deleted=False).select_related("product")
    for line in lines:
        product = line.product
        label = product.code or product.sku or str(product.pk)
        price = Decimal(line.base_price or 0)

        if product.owner_id != order.owner_id or not product.is_active:
            errors.append(f"订单行 {line.line_no} 的商品 {label} 不可用。")
            continue
        if price <= 0:
            errors.append(f"订单行 {line.line_no} 的商品 {label} 成交价必须大于 0。")
            continue

        try:
            lowest = minimum_sale_price(
                base_price=product.price,
                min_price=product.min_price,
                max_discount=product.max_discount,
            )
        except InvalidSalePriceRule as exc:
            errors.append(f"商品 {label} 价格配置错误：{exc}")
            continue
        if lowest is not None and price < lowest:
            errors.append(f"订单行 {line.line_no} 的商品 {label} 成交价不能低于 {lowest}。")

    if errors:
        raise ValidationError({"lines": errors})


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
        "source_model": order._meta.model_name,  # outboundorder
        "source_pk": str(order.pk),  # CharField，统一用 str
    }


def _task_source_q(order):
    """兼容历史数据：既匹配 canonical(outboundorder)，也匹配 legacy(OutboundOrder)。"""
    return Q(source_pk=str(order.pk)) & (
        Q(source_model=order._meta.model_name) | Q(source_model=order.__class__.__name__)
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
        WmsTask.objects.filter(task_type=TASK_TYPE_PICK)
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
        demands.append(
            {
                "line_id": line.id,
                "product_id": line.product_id,
                "demand": qty,
            }
        )
    return demands


# Helper: 获取按 FEFO 排序的库存明细（冻结量）
def _fefo_details_qs(owner_id: int, warehouse_id: int, product_id: int):
    """获取某产品按 FEFO 排序的库存明细，按库存可用量递减"""
    qs = (
        InventoryDetail.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            is_active=True,  # 若使用 is_active 进行库存标记
            available_qty__gt=0,  # 仅可用库存
        )
        .select_for_update(skip_locked=True)  # 锁住行，防止并发冲突
        .order_by(
            "expiry_date",  # FEFO：早到期的先分配
            "-onhand_qty",  # 若效期相同，优先使用库存多的
        )
        .only("id", "location_id", "available_qty", "allocated_qty", "onhand_qty")
    )
    target_ids = []
    if settings.REPLENISHMENT_DEMAND_ENABLED:
        target_ids = list(
            ReplenishmentPolicy.objects.filter(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                product_id=product_id,
                demand_enabled=True,
                is_active=True,
            ).values_list("target_location_id", flat=True)
        )
    if target_ids:
        qs = qs.filter(location_id__in=target_ids)
    return qs


# 冻结库存：available → allocated，并将切分结果写入保留态任务（RESERVED）
@transaction.atomic
def allocate_inventory(order, by_user=None, allow_backorder=True):
    """货主管理员确认时，冻结库存，并生成/刷新保留拣货任务（RESERVED）"""
    warehouse_id = (
        type(order).objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    order = type(order).objects.select_for_update().get(pk=order.pk)
    if order.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在分配期间发生变化，请重试。")
    ctx, ctx_text = build_log_payload(order=order, user=by_user)
    logger.info("outbound.allocate_inventory.begin %s", ctx_text, extra=ctx)
    task = _get_or_create_reserved_task(order, by_user=by_user)
    existing_lines = (
        WmsTaskLine.objects.select_for_update()
        .filter(task=task)
        .exclude(status=WmsTaskLine.Status.CANCELLED)
    )
    allocated_by_line = {
        row["src_id"]: Decimal(row["qty"] or 0)
        for row in existing_lines.values("src_id").annotate(qty=Sum("qty_plan"))
    }
    demands = _compute_line_demands(order)
    if not demands:
        raise ValidationError("出库单没有有效需求行。")
    for d in demands:
        already_allocated = allocated_by_line.get(d["line_id"], Decimal("0"))
        if already_allocated > d["demand"]:
            raise ValidationError(f"订单行 {d['line_id']} 已分配数量超过需求，禁止继续分配。")
        remaining = d["demand"] - already_allocated
        if remaining <= 0:
            continue
        qs = _fefo_details_qs(order.owner_id, order.warehouse_id, d["product_id"])
        for det in qs:
            if remaining <= 0:
                break
            avail = det.available_qty
            if avail <= 0:
                continue

            alloc = min(avail, remaining)

            from allapp.tasking.counting import assert_inventory_not_count_locked

            assert_inventory_not_count_locked(
                owner_id=det.owner_id,
                warehouse_id=det.warehouse_id,
                product_id=det.product_id,
                location_id=det.location_id,
                batch_no=det.batch_no,
                task=task,
            )

            # 硬分配：冻结 available → allocated
            updated = InventoryDetail.objects.filter(pk=det.pk, available_qty__gte=alloc).update(
                allocated_qty=F("allocated_qty") + alloc,
                # Keep the inventory identity without depending on a
                # database's evaluation order for multiple UPDATE
                # assignments (MySQL differs from SQLite/PostgreSQL).
                available_qty=F("available_qty") - alloc,
            )

            if updated == 0:
                det.refresh_from_db(fields=["available_qty", "allocated_qty"])
                task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
                logger.warning(
                    "outbound.allocate_inventory.retryable_conflict %s "
                    "detail_id=%s product_id=%s location_id=%s qty=%s",
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
                plan_meta={"inventory_detail_id": det.id},
            )
            task_ctx, task_text = build_log_payload(order=order, task=task, user=by_user)
            logger.info(
                "outbound.allocate_inventory.detail_allocated %s product_id=%s "
                "location_id=%s qty=%s line_id=%s detail_id=%s",
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


def allocation_shortfalls(order, task=None) -> list[dict]:
    """Return order-line shortages against the active reserved PICK allocation."""

    task = task or (
        WmsTask.objects.filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))
        .exclude(status=WmsTask.Status.CANCELLED)
        .order_by("id")
        .first()
    )
    allocated_by_line = {}
    if task is not None:
        allocated_by_line = {
            row["src_id"]: Decimal(row["qty"] or 0)
            for row in (
                WmsTaskLine.objects.filter(task=task)
                .exclude(status=WmsTaskLine.Status.CANCELLED)
                .values("src_id")
                .annotate(qty=Sum("qty_plan"))
            )
        }

    shortages = []
    for demand in _compute_line_demands(order):
        allocated = allocated_by_line.get(demand["line_id"], Decimal("0"))
        shortage = demand["demand"] - allocated
        if shortage > 0:
            shortages.append(
                {
                    "line_id": demand["line_id"],
                    "product_id": demand["product_id"],
                    "demand": demand["demand"],
                    "allocated": allocated,
                    "shortage": shortage,
                }
            )
    return shortages


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
    if order.approval_status != "WHS_APPROVED":
        raise ValidationError("订单尚未完成仓库确认，禁止发布拣货任务。")
    validate_sale_mini_payment_for_fulfillment(order)

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

    shortages = allocation_shortfalls(order, task=task)
    if shortages:
        detail = "；".join(
            f"订单行 {row['line_id']} 缺口 {row['shortage']}" for row in shortages[:10]
        )
        raise ValidationError(f"订单尚未完整分配，禁止发布拣货任务：{detail}")

    old_status = task.status
    now = timezone.now()
    task.status = WmsTask.Status.RELEASED
    task.released_at = now
    task.updated_at = now
    task.updated_by = by_user
    task.save(update_fields=["status", "released_at", "updated_at", "updated_by"])

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
    """Publish a warehouse-assisted order after its controlled approval flow.

    Standard orders deliberately cannot use this convenience routine.  Their
    owner approval and warehouse confirmation are separate, auditable duties;
    allowing a Django Admin action to call this routine for a standard order
    would let a warehouse manager impersonate the owner approval step.
    """

    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    order = Order.objects.select_for_update().select_related("owner", "warehouse").get(pk=order.pk)
    if order.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在审核期间发生变化，请重试。")

    if order.processing_mode != ASSISTED_PROCESSING_MODE:
        raise ValidationError("标准出库必须先由货主管理员审核，再由仓库管理员确认发布。")
    if order.is_closed:
        raise ValidationError("已关闭订单不能执行确认发布。")
    if order.approval_status in {"OWNER_PENDING", "OWNER_REJECTED"}:
        validate_owner_approval_preconditions(order)
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

    scope = AccessScope.for_user(by_user)
    if not scope.is_valid or len(scope.warehouse_ids) != 1:
        raise ValidationError("代办出库必须具有单一有效仓库操作范围。")
    warehouse_id = next(iter(scope.warehouse_ids))
    lock_warehouses_for_inventory_write(warehouse_id)
    OutboundOrder = get_outbound_order_model()
    OutboundOrderLine = OutboundOrder._meta.apps.get_model("outbound", "OutboundOrderLine")
    owner = validated_data["owner"]
    require_assisted_owner_warehouse(owner, warehouse_id)
    customer = validated_data["customer"]
    items = validated_data["items"]
    now = timezone.now()

    order = OutboundOrder.objects.create(
        owner=owner,
        warehouse_id=warehouse_id,
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
                Decimal(item["package"].qty_in_base) if item.get("package") is not None else None
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
        line.id: Decimal(line.base_qty or 0) for line in order.lines.filter(is_deleted=False)
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


def _order_for_outbound_task(task, *, for_update=False):
    """Resolve the standard outbound order carried by a workflow task."""

    OutboundOrder = get_outbound_order_model()
    source_model = (getattr(task, "source_model", "") or "").lower()
    source_pk = getattr(task, "source_pk", None)
    if source_model == "outboundorder":
        qs = OutboundOrder.objects
        if for_update:
            qs = qs.select_for_update()
        try:
            return qs.filter(
                pk=int(source_pk),
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
            ).first()
        except (TypeError, ValueError):
            return None
    if source_model == "wmstask":
        try:
            parent = WmsTask.objects.get(pk=int(source_pk))
        except (TypeError, ValueError, WmsTask.DoesNotExist):
            return None
        return _order_for_outbound_task(parent, for_update=for_update)
    return None


def get_review_task_for_pick(pick_task, *, for_update=False):
    """Return the single active REVIEW derived from a PICK, failing on ambiguity."""

    qs = WmsTask.objects.filter(
        task_type=WmsTask.TaskType.REVIEW,
        source_model="WmsTask",
        source_pk=str(pick_task.pk),
        owner_id=pick_task.owner_id,
        warehouse_id=pick_task.warehouse_id,
    ).exclude(status=WmsTask.Status.CANCELLED)
    if for_update:
        qs = qs.select_for_update()
    tasks = list(qs.order_by("id")[:2])
    if len(tasks) > 1:
        raise ValidationError("拣货任务关联了多个有效复核任务，禁止继续处理。")
    return tasks[0] if tasks else None


@transaction.atomic
def create_review_task_for_pick(pick_task, *, by_user=None):
    """Idempotently complete a fully picked PICK and create a real REVIEW task."""

    pick_task = WmsTask.objects.select_for_update().get(pk=pick_task.pk)
    if pick_task.task_type != WmsTask.TaskType.PICK:
        raise ValidationError("仅 PICK 任务可以创建复核任务。")
    if pick_task.status not in {
        WmsTask.Status.RESERVED,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    }:
        raise ValidationError(f"当前任务状态为 {pick_task.status}，不能提交复核。")

    lines = list(
        WmsTaskLine.objects.select_for_update()
        .filter(task=pick_task)
        .exclude(status=WmsTaskLine.Status.CANCELLED)
        .order_by("id")
    )
    if not lines:
        raise ValidationError("拣货任务没有有效明细，不能提交复核。")
    if any(Decimal(line.qty_done or 0) < Decimal(line.qty_plan or 0) for line in lines):
        raise ValidationError("还有未拣完的明细，不能提交复核。")

    existing = get_review_task_for_pick(pick_task, for_update=True)
    if existing is not None:
        return existing

    now = timezone.now()
    order = _order_for_outbound_task(pick_task)
    review_task = WmsTask.objects.create(
        task_no=DocSequence.next_code(
            doc_type="FH",
            warehouse=pick_task.warehouse,
            owner=pick_task.owner,
            biz_date=date.today(),
        ),
        task_type=WmsTask.TaskType.REVIEW,
        status=WmsTask.Status.RELEASED,
        owner=pick_task.owner,
        warehouse=pick_task.warehouse,
        released_at=now,
        ref_no=getattr(order, "order_no", "") or pick_task.task_no,
        task_group_no=pick_task.task_no,
        source_app="tasking",
        source_model="WmsTask",
        source_pk=str(pick_task.pk),
        created_by=by_user or pick_task.created_by,
        updated_by=by_user,
    )
    ReviewTaskExtra.objects.create(
        task=review_task,
        review_mode="PDA",
        review_date=date.today(),
    )
    for pick_line in lines:
        review_line = WmsTaskLine.objects.create(
            task=review_task,
            product_id=pick_line.product_id,
            from_location_id=pick_line.from_location_id,
            to_location_id=pick_line.to_location_id,
            qty_plan=pick_line.qty_done,
            status=WmsTaskLine.Status.RELEASED,
            src_model="WmsTaskLine",
            src_id=pick_line.id,
            plan_meta={
                "outbound_order_line_id": pick_line.src_id,
                "pick_task_id": pick_task.id,
            },
            created_by=by_user,
            updated_by=by_user,
        )
        ReviewLineExtra.objects.create(
            line=review_line,
            from_location_id=pick_line.from_location_id,
            qty_plan_origin=pick_line.qty_plan,
            qty_picked_origin=pick_line.qty_done,
        )

    old_status = pick_task.status
    pick_task.status = WmsTask.Status.COMPLETED
    pick_task.review_status = WmsTask.ReviewStatus.PENDING
    pick_task.finished_at = pick_task.finished_at or now
    pick_task.picked_by = pick_task.picked_by or by_user
    pick_task.updated_by = by_user
    pick_task.save(
        update_fields=[
            "status",
            "review_status",
            "finished_at",
            "picked_by",
            "updated_by",
            "updated_at",
        ]
    )
    if old_status != WmsTask.Status.COMPLETED:
        TaskStatusLog.objects.create(
            task=pick_task,
            old_status=old_status,
            new_status=WmsTask.Status.COMPLETED,
            changed_by=by_user,
            note=f"提交真实复核任务 {review_task.task_no}",
        )
    return review_task


@transaction.atomic
def approve_review_task_for_pick(pick_task, *, by_user):
    """Record an approved physical REVIEW and ready the source PICK for posting."""

    pick_task = WmsTask.objects.select_for_update().get(pk=pick_task.pk)
    order = _order_for_outbound_task(pick_task)
    if (
        order is not None
        and order.processing_mode != ASSISTED_PROCESSING_MODE
        and pick_task.picked_by_id
        and pick_task.picked_by_id == getattr(by_user, "pk", None)
    ):
        raise ValidationError("标准出库必须由不同于拣货人的操作员独立复核。")
    review_task = get_review_task_for_pick(pick_task, for_update=True)
    if review_task is None:
        raise ValidationError("缺少实际复核任务，请先提交拣货复核。")
    if pick_task.status != WmsTask.Status.COMPLETED:
        raise ValidationError("拣货任务尚未完成，不能复核。")
    if review_task.status == WmsTask.Status.COMPLETED:
        if review_task.review_status != WmsTask.ReviewStatus.APPROVED:
            raise ValidationError("复核任务已结束但未审核通过。")
        return review_task
    if review_task.status not in {
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    }:
        raise ValidationError(f"复核任务状态 {review_task.status} 不允许审核。")

    now = timezone.now()
    review_lines = WmsTaskLine.objects.select_for_update().filter(task=review_task)
    if not review_lines.exists():
        raise ValidationError("复核任务没有明细。")
    review_lines.update(
        qty_done=F("qty_plan"),
        status=WmsTaskLine.Status.COMPLETED,
        finished_at=now,
        finished_by=by_user,
        updated_at=now,
        updated_by=by_user,
    )
    for line in review_lines:
        ReviewLineExtra.objects.filter(line=line).update(
            qty_reviewed=line.qty_plan,
            qty_discrepancy_plan=Decimal("0"),
            qty_discrepancy_picked=Decimal("0"),
            review_status_rev=ReviewLineExtra.REVIEW_Status.REVIEWED,
        )

    old_review_status = review_task.status
    review_task.status = WmsTask.Status.COMPLETED
    review_task.review_status = WmsTask.ReviewStatus.APPROVED
    review_task.posting_status = WmsTask.PostingStatus.PENDING
    review_task.approved_by = by_user
    review_task.approved_at = now
    review_task.finished_at = now
    review_task.updated_by = by_user
    review_task.save(
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
    TaskStatusLog.objects.create(
        task=review_task,
        old_status=old_review_status,
        new_status=WmsTask.Status.COMPLETED,
        changed_by=by_user,
        note="PDA 实物复核通过",
    )

    pick_task.review_status = WmsTask.ReviewStatus.APPROVED
    pick_task.posting_status = WmsTask.PostingStatus.PENDING
    pick_task.approved_by = by_user
    pick_task.approved_at = now
    pick_task.updated_by = by_user
    pick_task.save(
        update_fields=[
            "review_status",
            "posting_status",
            "approved_by",
            "approved_at",
            "updated_by",
            "updated_at",
        ]
    )
    return review_task


def _review_workflow_payload(review_task):
    """Return normalized reviewed quantities tied to original order lines."""

    payload = []
    for review_line in review_task.lines.select_related("product").order_by("id"):
        order_line_id = (review_line.plan_meta or {}).get("outbound_order_line_id")
        if not order_line_id and review_line.src_id:
            order_line_id = (
                WmsTaskLine.objects.filter(pk=review_line.src_id)
                .values_list("src_id", flat=True)
                .first()
            )
        qty = Decimal(review_line.qty_done or review_line.qty_plan or 0)
        if order_line_id and qty > 0:
            payload.append(
                {
                    "order_line_id": int(order_line_id),
                    "product_id": review_line.product_id,
                    "qty": qty,
                    "from_location_id": review_line.to_location_id or review_line.from_location_id,
                }
            )
    return payload


def _create_workflow_task(
    *,
    task_type,
    order,
    payload,
    group_no,
    by_user,
):
    existing = (
        WmsTask.objects.filter(
            task_type=task_type,
            source_model="outboundorder",
            source_pk=str(order.pk),
            task_group_no=group_no,
        )
        .exclude(status=WmsTask.Status.CANCELLED)
        .first()
    )
    if existing is not None:
        return existing
    doc_type = "PKG" if task_type == WmsTask.TaskType.PACK else "FY"
    now = timezone.now()
    task = WmsTask.objects.create(
        task_no=DocSequence.next_code(
            doc_type=doc_type,
            warehouse=order.warehouse,
            owner=order.owner,
            biz_date=date.today(),
        ),
        task_type=task_type,
        status=WmsTask.Status.RELEASED,
        owner=order.owner,
        warehouse=order.warehouse,
        released_at=now,
        ref_no=order.order_no,
        task_group_no=group_no,
        source_app="outbound",
        source_model="outboundorder",
        source_pk=str(order.pk),
        created_by=by_user,
        updated_by=by_user,
    )
    if task_type == WmsTask.TaskType.PACK:
        PackTaskExtra.objects.create(task=task)
    else:
        DispatchTaskExtra.objects.create(task=task)
    for item in payload:
        WmsTaskLine.objects.create(
            task=task,
            product_id=item["product_id"],
            from_location_id=item.get("from_location_id"),
            qty_plan=item["qty"],
            status=WmsTaskLine.Status.RELEASED,
            src_model="OutboundOrderLine",
            src_id=item["order_line_id"],
            created_by=by_user,
            updated_by=by_user,
        )
    return task


@transaction.atomic
def create_followups_from_review(review_task, *, by_user=None):
    """Create released PACK/DISPATCH work from a posted standard review."""

    review_task = WmsTask.objects.select_for_update().get(pk=review_task.pk)
    if review_task.task_type != WmsTask.TaskType.REVIEW:
        raise ValidationError("仅 REVIEW 任务可以派生后续任务。")
    if review_task.review_status != WmsTask.ReviewStatus.APPROVED:
        raise ValidationError("复核任务未通过，不能派生后续任务。")
    order = _order_for_outbound_task(review_task, for_update=True)
    if order is None:
        raise ValidationError("复核任务无法解析对应的出库订单。")
    if order.processing_mode == ASSISTED_PROCESSING_MODE:
        return {}

    OutboundOrderLine = order.lines.model
    order_lines = {
        line.id: line for line in OutboundOrderLine.objects.filter(order=order, is_deleted=False)
    }
    pack_payload, dispatch_payload = [], []
    for item in _review_workflow_payload(review_task):
        order_line = order_lines.get(item["order_line_id"])
        if order_line is None:
            raise ValidationError("复核行无法匹配原出库订单行。")
        if order_line.pack_requirement != "NONE":
            pack_payload.append(item)
        else:
            dispatch_payload.append(item)

    created = {}
    if pack_payload:
        created["pack_task"] = _create_workflow_task(
            task_type=WmsTask.TaskType.PACK,
            order=order,
            payload=pack_payload,
            group_no=review_task.task_no,
            by_user=by_user,
        )
    if dispatch_payload:
        created["dispatch_task"] = _create_workflow_task(
            task_type=WmsTask.TaskType.DISPATCH,
            order=order,
            payload=dispatch_payload,
            group_no=review_task.task_no,
            by_user=by_user,
        )
    if not created:
        raise ValidationError("复核任务没有可派生的有效数量。")
    return created


@transaction.atomic
def finalize_review_after_pick_post(review_task, *, by_user):
    """Mark the REVIEW posted only after source PICK inventory posting succeeds."""

    review_task = WmsTask.objects.select_for_update().get(pk=review_task.pk)
    if review_task.status != WmsTask.Status.COMPLETED:
        raise ValidationError("复核任务未完成。")
    if review_task.review_status != WmsTask.ReviewStatus.APPROVED:
        raise ValidationError("复核任务未审核通过。")
    try:
        pick_task = WmsTask.objects.select_for_update().get(
            pk=int(review_task.source_pk),
            task_type=WmsTask.TaskType.PICK,
        )
    except (TypeError, ValueError, WmsTask.DoesNotExist) as exc:
        raise ValidationError("复核任务无法解析来源拣货任务。") from exc
    if pick_task.posting_status != WmsTask.PostingStatus.POSTED:
        raise ValidationError("来源拣货任务尚未成功过账，不能完成复核。")
    review_task.posting_status = WmsTask.PostingStatus.POSTED
    review_task.posted_by = review_task.posted_by or by_user
    review_task.posted_at = review_task.posted_at or timezone.now()
    review_task.updated_by = by_user
    review_task.save(
        update_fields=[
            "posting_status",
            "posted_by",
            "posted_at",
            "updated_by",
            "updated_at",
        ]
    )
    return create_followups_from_review(review_task, by_user=by_user)


@transaction.atomic
def create_dispatch_from_pack(pack_task, *, by_user=None):
    """Idempotently create a released DISPATCH after a PACK is complete."""

    pack_task = WmsTask.objects.select_for_update().get(pk=pack_task.pk)
    if pack_task.task_type != WmsTask.TaskType.PACK:
        raise ValidationError("仅 PACK 任务可以派生发运任务。")
    if pack_task.status != WmsTask.Status.COMPLETED:
        raise ValidationError("打包任务未完成，不能创建发运任务。")
    order = _order_for_outbound_task(pack_task, for_update=True)
    if order is None:
        raise ValidationError("打包任务无法解析对应的出库订单。")
    payload = [
        {
            "order_line_id": line.src_id,
            "product_id": line.product_id,
            "qty": Decimal(line.qty_done or line.qty_plan or 0),
            "from_location_id": line.to_location_id or line.from_location_id,
        }
        for line in pack_task.lines.exclude(status=WmsTaskLine.Status.CANCELLED)
        if line.src_id and Decimal(line.qty_done or line.qty_plan or 0) > 0
    ]
    if not payload:
        raise ValidationError("打包任务没有可发运数量。")
    return _create_workflow_task(
        task_type=WmsTask.TaskType.DISPATCH,
        order=order,
        payload=payload,
        group_no=pack_task.task_no,
        by_user=by_user,
    )


@transaction.atomic
def close_order_after_dispatch(dispatch_task, *, by_user=None):
    """Close a standard order only after all released dispatch work is complete."""

    dispatch_task = WmsTask.objects.select_for_update().get(pk=dispatch_task.pk)
    if dispatch_task.task_type != WmsTask.TaskType.DISPATCH:
        raise ValidationError("仅 DISPATCH 任务可以触发订单关闭。")
    if dispatch_task.status != WmsTask.Status.COMPLETED:
        raise ValidationError("发运任务未完成，不能关闭订单。")
    order = _order_for_outbound_task(dispatch_task, for_update=True)
    if order is None:
        raise ValidationError("发运任务无法解析对应的出库订单。")

    dispatch_tasks = (
        WmsTask.objects.select_for_update()
        .filter(
            task_type=WmsTask.TaskType.DISPATCH,
            source_model="outboundorder",
            source_pk=str(order.pk),
        )
        .exclude(status=WmsTask.Status.CANCELLED)
    )
    if (
        not dispatch_tasks.exists()
        or dispatch_tasks.exclude(status=WmsTask.Status.COMPLETED).exists()
    ):
        return order

    dispatched_by_line = {
        row["src_id"]: Decimal(row["qty"] or 0)
        for row in (
            WmsTaskLine.objects.filter(task__in=dispatch_tasks)
            .exclude(status=WmsTaskLine.Status.CANCELLED)
            .values("src_id")
            .annotate(qty=Sum("qty_done"))
        )
    }
    shortages = []
    for line in order.lines.filter(is_deleted=False):
        dispatched = dispatched_by_line.get(line.id, Decimal("0"))
        if dispatched < Decimal(line.base_qty or 0):
            shortages.append(f"订单行 {line.id} 尚差 {line.base_qty - dispatched}")
    if shortages:
        raise ValidationError("发运数量未覆盖订单需求：" + "；".join(shortages[:10]))

    if not order.is_closed:
        order.is_closed = True
        order.close_reason = "全部发运完成"
        order.updated_by = by_user
        order.save(update_fields=["is_closed", "close_reason", "updated_by", "updated_at"])
    return order


# 仓库管理员拒绝：释放已冻结的库存并取消任务
@transaction.atomic
def _validate_allocation_can_be_released(order) -> None:
    tasks = (
        WmsTask.objects.select_for_update()
        .filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))
        .exclude(status=WmsTask.Status.CANCELLED)
    )
    blocked = tasks.filter(
        status__in=[WmsTask.Status.IN_PROGRESS, WmsTask.Status.COMPLETED]
    ).first()
    if blocked is not None:
        raise ValidationError(f"拣货任务 {blocked.task_no} 已开始或完成，禁止取消/撤回订单。")
    started_line = WmsTaskLine.objects.filter(
        task__in=tasks,
        qty_done__gt=0,
    ).first()
    if started_line is not None:
        raise ValidationError("拣货任务已有执行数量，禁止释放库存分配。")


@transaction.atomic
def unallocate_for_order(order, *, by_user=None) -> Decimal:
    """Release frozen inventory and retain cancelled task lines as audit evidence."""
    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在释放库存期间发生变化，请重试。")
    released = Decimal("0")
    ctx, ctx_text = build_log_payload(order=order)
    logger.info("outbound.unallocate.begin %s", ctx_text, extra=ctx)
    _validate_allocation_can_be_released(order)
    tasks = (
        WmsTask.objects.select_for_update()
        .filter(task_type=TASK_TYPE_PICK)
        .filter(_task_source_q(order))  # 兼容旧数据
        .exclude(status__in=["CANCELLED", "COMPLETED"])
    )

    for task in tasks:
        lines = (
            WmsTaskLine.objects.select_for_update()
            .filter(task=task)
            .exclude(status=WmsTaskLine.Status.CANCELLED)
        )
        for tl in lines:
            remaining = Decimal(tl.qty_plan or 0)
            detail_id = (tl.plan_meta or {}).get("inventory_detail_id")
            details = InventoryDetail.objects.select_for_update().filter(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                product_id=tl.product_id,
                location_id=tl.from_location_id,
                allocated_qty__gt=0,
            )
            if detail_id:
                details = details.filter(pk=detail_id)
            for detail in details.order_by("id"):
                if remaining <= 0:
                    break
                qty = min(Decimal(detail.allocated_qty or 0), remaining)
                if qty <= 0:
                    continue
                updated = InventoryDetail.objects.filter(
                    pk=detail.pk,
                    allocated_qty__gte=qty,
                ).update(
                    allocated_qty=F("allocated_qty") - qty,
                    available_qty=F("available_qty") + qty,
                )
                if updated != 1:
                    raise ValidationError("释放冻结库存时发生并发冲突，请重试。")
                remaining -= qty
                released += qty
            if remaining > 0:
                raise ValidationError(
                    f"任务行 {tl.id} 的冻结库存不足，尚差 {remaining}，禁止取消。"
                )
            task_ctx, task_text = build_log_payload(order=order, task=task)
            logger.info(
                "outbound.unallocate.line_released %s product_id=%s location_id=%s qty=%s",
                task_text,
                tl.product_id,
                tl.from_location_id,
                tl.qty_plan,
                extra=task_ctx,
            )

        now = timezone.now()
        lines.update(
            status=WmsTaskLine.Status.CANCELLED,
            updated_at=now,
            updated_by=by_user,
        )
        old_status = task.status
        task.status = WmsTask.Status.CANCELLED
        task.finished_at = now
        task.updated_by = by_user
        task.save(update_fields=["status", "finished_at", "updated_by", "updated_at"])
        TaskStatusLog.objects.create(
            task=task,
            old_status=old_status,
            new_status=WmsTask.Status.CANCELLED,
            changed_by=by_user,
            note="订单取消/撤回，释放库存分配",
        )
        task_ctx, task_text = build_log_payload(order=order, task=task)
        logger.info("outbound.unallocate.task_cancelled %s", task_text, extra=task_ctx)
    logger.info(
        "outbound.unallocate.completed %s released_qty=%s",
        ctx_text,
        released,
        extra=ctx,
    )
    return released


def _cancel_unstarted_demand_replenishments(order, *, by_user=None):
    tasks = (
        WmsTask.objects.select_for_update()
        .select_related("replenishtaskextra")
        .filter(
            task_type=WmsTask.TaskType.REPLEN,
            status__in=[
                WmsTask.Status.DRAFT,
                WmsTask.Status.READY,
                WmsTask.Status.RELEASED,
            ],
        )
        .filter(
            Q(source_model="OutboundOrder", source_pk=str(order.pk))
            | Q(replenishtaskextra__demand_order_ids__contains=[order.pk])
        )
    )
    for task in tasks:
        extra = getattr(task, "replenishtaskextra", None)
        linked_order_ids = set(getattr(extra, "demand_order_ids", None) or [])
        other_order_ids = linked_order_ids - {order.pk}
        if other_order_ids:
            extra.demand_order_ids = sorted(other_order_ids)
            extra.updated_by = by_user
            extra.save(update_fields=["demand_order_ids", "updated_by", "updated_at"])
            continue
        if task.lines.filter(qty_done__gt=0).exists():
            continue
        old_status = task.status
        task.lines.exclude(status=WmsTaskLine.Status.CANCELLED).update(
            status=WmsTaskLine.Status.CANCELLED, updated_by=by_user
        )
        task.status = WmsTask.Status.CANCELLED
        task.finished_at = timezone.now()
        task.updated_by = by_user
        task.save(update_fields=["status", "finished_at", "updated_by", "updated_at"])
        TaskStatusLog.objects.create(
            task=task,
            old_status=old_status,
            new_status=WmsTask.Status.CANCELLED,
            changed_by=by_user,
            note="来源出库单取消，补货任务自动取消",
        )


@transaction.atomic
def cancel_order(order, *, by_user, reason="货主管理员取消订单"):
    """Cancel an unstarted order and release every outstanding allocation."""

    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在取消期间发生变化，请重试。")
    if order.approval_status == "CANCELLED":
        raise ValidationError("订单已取消，请勿重复操作。")
    if order.is_closed:
        raise ValidationError("已关闭订单不能取消。")
    _validate_allocation_can_be_released(order)
    _cancel_unstarted_demand_replenishments(order, by_user=by_user)
    unallocate_for_order(order, by_user=by_user)
    order.approval_status = "CANCELLED"
    order.close_reason = (reason or "取消订单").strip()[:50]
    order.updated_by = by_user
    order.save(update_fields=["approval_status", "close_reason", "updated_by", "updated_at"])
    return order


@transaction.atomic
def withdraw_order(order, *, by_user, reason="撤销提交"):
    """Return an unstarted submitted order to editable draft state."""

    Order = type(order)
    warehouse_id = Order.objects.filter(pk=order.pk).values_list("warehouse_id", flat=True).get()
    lock_warehouses_for_inventory_write(warehouse_id)
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.warehouse_id != warehouse_id:
        raise ValidationError("出库单仓库在撤回期间发生变化，请重试。")
    if order.submit_status != "SUBMITTED":
        raise ValidationError("仅已提交订单可以撤回。")
    if order.approval_status == "CANCELLED" or order.is_closed:
        raise ValidationError("已取消或已关闭订单不能撤回。")
    _validate_allocation_can_be_released(order)
    _cancel_unstarted_demand_replenishments(order, by_user=by_user)
    unallocate_for_order(order, by_user=by_user)

    order.submit_status = "DRAFT"
    order.approval_status = "OWNER_PENDING"
    order.approved_by_ownermanager = None
    order.approved_at_ownermanager = None
    order.approved_by_warehouse = None
    order.approved_at_warehouse = None
    order.pricing_status = "PENDING"
    order.priced_at = None
    order.priced_by = None
    order.final_order_amount = Decimal("0.00")
    order.close_reason = (reason or "撤销提交").strip()[:50]
    order.updated_by = by_user
    order.save(
        update_fields=[
            "submit_status",
            "approval_status",
            "approved_by_ownermanager",
            "approved_at_ownermanager",
            "approved_by_warehouse",
            "approved_at_warehouse",
            "pricing_status",
            "priced_at",
            "priced_by",
            "final_order_amount",
            "close_reason",
            "updated_by",
            "updated_at",
        ]
    )
    return order


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
        remaining = d["demand"]
        qs = _fefo_details_qs(order.owner_id, order.warehouse_id, d["product_id"])

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
                product_id=d["product_id"],
                from_location_id=det.location_id,
                to_location_id=None,  # 集货位
                qty_plan=alloc,
                src_model="OutboundOrderLine",
                src_id=d["line_id"],
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
    logger.info(
        "outbound.wave_release.completed task_count=%s task_ids=%s",
        updated_count,
        task_ids,
    )

    return updated_count
