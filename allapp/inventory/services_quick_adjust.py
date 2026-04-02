# allapp/inventory/services_quick_adjust.py
from __future__ import annotations
from allapp.tasking.models import  WmsTask,WmsTaskLine,TaskScanLog
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

# 统一过账入口（按你冻结版已有的 services ）
from allapp.inventory import services as inv_services
from allapp.core.models import DocSequence
from allapp.locations.models import Location
from allapp.baseinfo.models import Owner
from allapp.products.models import Product
from allapp.locations.models import Warehouse


@dataclass(frozen=True)
class QuickAdjustInput:
    user: object
    owner: Owner
    product: Product
    qty_base_delta: Decimal
    warehouse: Optional[Warehouse] = None
    location: Optional[Location] = None
    lot: Optional[object] = None
    expiry: Optional[object] = None
    serial: Optional[str] = None
    reason: str = "ADMIN_QUICK_ADJUST"
    remark: Optional[str] = None
    allow_negative: bool = False
    barcode: Optional[str] = None  # 添加 barcode 属性

@transaction.atomic
def quick_adjust_via_post_task(data: QuickAdjustInput) -> dict:
    if not data.qty_base_delta or Decimal(data.qty_base_delta) == 0:
        raise ValueError("数量变动不能为 0")

    # 解析默认库位和仓库
    location = data.location or Location.objects.get(pk=getattr(settings, "DEFAULT_ADJUST_LOCATION_ID"))
    location_warehouse = getattr(location, "warehouse", None)
    warehouse = data.warehouse or location_warehouse
    if warehouse is None:
        raise ValueError("无法确定调整任务所属仓库，请显式传 warehouse 或传入带仓库的 location。")
    if data.warehouse and getattr(location, "warehouse_id", None) and data.warehouse.id != location.warehouse_id:
        raise ValueError("warehouse 必须与 location.warehouse 一致")

    task_no = DocSequence.next_code(
        doc_type="TJ",
        warehouse=warehouse,
        owner=data.owner,
        biz_date=timezone.now().date(),
    )

    # 创建 WmsTask 实例
    task = WmsTask.objects.create(
        task_no=task_no,
        task_type="ADJUST",
        owner=data.owner,
        warehouse=warehouse,
        created_by=data.user,
        remark=data.remark or "ADMIN_QUICK_ADJUST",
        review_status=WmsTask.ReviewStatus.APPROVED,
        posting_status=WmsTask.PostingStatus.PENDING,
        status = WmsTask.Status.COMPLETED,
    )

    # task.review_status = WmsTask.ReviewStatus.APPROVED
    # task.save()

    # 创建 WmsTaskLine 实例
    task_line = WmsTaskLine.objects.create(
        task=task,
        product=data.product,
        from_location=location,
        to_location=None,
        qty_plan=Decimal(data.qty_base_delta),  # 使用 qty_plan 代替 qty_base
    )

    # 创建 TaskScanLog 实例（用于记录扫描日志）
    task_scan_log = TaskScanLog.objects.create(
        task=task,
        task_line=task_line,  # 正确传递 task_line（而不是 line）
        owner=data.owner,
        warehouse=warehouse,
        location=location,
        product=data.product,
        barcode=data.barcode or "DEFAULT_BARCODE",  # 如果没有条码，传递默认值
        method="API",  # 默认方法可以设为"API"
        # 确保 fp 唯一：使用 UUID 或其他唯一标识
        fp=f"task-{task.id}-line-{task_line.id}",
        scan_snapshot_rev=1,  # 设置扫描快照版本号
        qty_base_delta=Decimal(data.qty_base_delta),  # 确保数量传递
    )

    # 设置 TaskScanLog 的审核状态为 "APPROVED"，表示已通过审核
    task_scan_log.review_status = TaskScanLog.ReviewStatus.APPROVED
    task_scan_log.save()

    # 调用 post_task 传递任务、扫描记录和其他参数
    return inv_services.post_task(
        task=task,
        user=data.user,
        scans=[task_scan_log],  # 可选，传递 TaskScanLog 列表（如果需要）
        note=data.remark or "ADMIN_QUICK_ADJUST",
        now=timezone.now(),
    )




# —— 兼容层：如果你在别处已经写死调用 adjust_stock()，保留这个薄封装 —— #
def adjust_stock(
    *, user, owner, warehouse, product,
    qty_delta, reason="ADMIN_QUICK_ADJUST",
    location=None, lot=None, expiry=None, serial=None,
    remark="", allow_negative=False,
):
    """
    兼容旧接口，内部走 quick_adjust_via_post_task（统一过账），不直接 UPDATE 库存表。
    """
    inp = QuickAdjustInput(
        user=user,
        owner=owner,
        warehouse=warehouse,
        product=product,
        qty_base_delta=Decimal(qty_delta),
        location=location,
        lot=lot,
        expiry=expiry,
        serial=serial,
        reason=reason or "ADMIN_QUICK_ADJUST",
        remark=remark or "ADMIN_QUICK_ADJUST",
        allow_negative=bool(allow_negative),
    )
    return quick_adjust_via_post_task(inp)
