# allapp/inbound/services.py
from django.db import transaction
from allapp.core.models import DocSequence
from allapp.tasking.models import WmsTask, WmsTaskLine,ReceiveTaskExtra,ReceiveLineExtra
from allapp.inventory.models import InventoryDetail, InventoryTransaction
from allapp.inbound.models import InboundOrder, InboundOrderLine

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
            taskline=WmsTaskLine.objects.create(
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


    return task


@transaction.atomic
def receive_goods_without_order(owner_id, items, remark="仓库操作员入库", warehouse_id=None, location_id=None):
    print("receive_goods_without_order 111111111111112222222222222")
    if not warehouse_id:
        raise ValueError("receive_goods_without_order 必须显式传 warehouse_id")
    inbound_order = InboundOrder.objects.create(owner_id=owner_id, warehouse_id=warehouse_id, remark=remark)
    print("22 receive_goods_without_order")
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

    return {"order_id": inbound_order.id, "status": "success"}
