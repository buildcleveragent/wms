# allapp/tasking/rcv_services.py
from datetime import date

from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import WmsTask, WmsTaskLine, TaskType, TaskStatus
from .utils import bind_triplet_from
from .rcv_models import RcvTaskExtra
from inbound.models import InboundOrder, InboundOrderLine

# 你的号段服务（沿用你现有 DocSequence）
from common.docno import DocSequence  # 若路径不同请调整

@transaction.atomic
def create_rcv_task(
    *,
    owner, warehouse,
    order: InboundOrder | None = None,
    source_type: str = "MANUAL",
    source_ref: str | None = None,
    plan_start=None, priority: int = 5,
    dock_door: str | None = None,
    vehicle=None, carrier=None,
) -> WmsTask:
    """新建一张收货任务（可来自订单，也可手工）并处于 RELEASED"""
    if order:
        # 强一致：owner/warehouse 来自订单
        owner = order.owner
        warehouse = order.warehouse

    task_no = DocSequence.next_code(
        doc_type="TASK",
        warehouse=warehouse,
        owner=owner,
        biz_date=date.today(),
    )

    task = WmsTask.objects.create(
        owner=owner,
        warehouse=warehouse,
        task_no=task_no,
        task_type=TaskType.RCV,
        status=TaskStatus.RELEASED,
        priority=priority,
        plan_start=plan_start,
        ref_no=(order.order_no if order else None),
        source_app=("inbound" if order else None),
        source_model=("inboundorder" if order else None),
        source_pk=(order.pk if order else None),
        # review_status=WmsTask.ReviewStatus.NOT_READY,
        # posting_status=WmsTask.PostingStatus.NOT_READY,
    )

    # 收货专属信息
    RcvTaskExtra.objects.create(
        task=task, order=order, source_type=source_type, source_ref=source_ref,
        dock_door=dock_door, vehicle_no=vehicle, carrier_company=carrier,
    )

    # 生成任务行：来自订单行；手工收货可在后续补行
    if order:
        # 仅选择仍需收货的行（示例：以 remain_qty>0 过滤，按你项目字段调整）
        lines = (InboundOrderLine.objects
                 .filter(order=order)
                 .select_related("product")
                 .all())
        for ol in lines:
            WmsTaskLine.objects.create(
                task=task,
                product=ol.product,
                to_location=None,          # 收货默认在待检区/暂存区，可由 PDA 首扫带入
                qty_plan=ol.qty - (getattr(ol, "qty_received", 0) or 0),
                qty_done=0,
                **bind_triplet_from(ol),
            )

    return task

@transaction.atomic
def post_receive(task_id: int, user=None):
    """收货过账：把任务行 qty_done 落到库存/收货记录（留空壳，交给 inbound/inventory 实做）"""
    from . import services
    task = WmsTask.objects.select_for_update().get(pk=task_id)
    if task.task_type != TaskType.RCV:
        raise ValidationError("仅收货任务可调用 post_receive")
    if task.status != TaskStatus.IN_PROGRESS:
        raise ValidationError("仅执行中的任务可过账")

    # TODO: 调用 inbound / inventory 的实际过账函数
    # 例如：inventory.apply_inbound(task.lines.select_related("product", ...))

    services.change_status(task, TaskStatus.POSTED, by_user=user, reason="收货过账完成")
    return task
