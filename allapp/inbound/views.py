import logging
logger = logging.getLogger(__name__)
from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.exceptions import ValidationError

from allapp.baseinfo.models import Owner
from allapp.core.models import DocSequence
from allapp.inbound.serializers import ReceiveWithoutOrderPayloadSerializer
from allapp.locations.models import Warehouse, Location
from allapp.products.models import Product
from allapp.tasking.models import WmsTask, WmsTaskLine
from allapp.tasking.services import save_receiving_snapshot, _run_posting_handler

class ReceiveGoodsWithoutOrder(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        # allapp/inbound/views.py（关键片段）
        s = ReceiveWithoutOrderPayloadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payload = s.validated_data
        # payload = request.data
        owner_id = int(payload["owner_id"])
        items = payload["items"]

        owner_id = payload["owner_id"]
        items = payload["items"]
        remark = (payload.get("remark") or "PDA无ASN收货").strip()

        wid = getattr(settings, 'DEFAULT_WAREHOUSE_ID', None)
        if not wid:
            raise ValueError("settings.DEFAULT_WAREHOUSE_ID 未配置")

        try:
            wh = Warehouse.objects.only('id').get(id=wid)
        except Warehouse.DoesNotExist:
            raise ValueError(f"默认仓库不存在：{wid}")

        try:
            owner = Owner.objects.only('id').get(id=owner_id)
        except Owner.DoesNotExist:
            raise ValueError(f"owner_id：{owner_id}")

        task_no = DocSequence.next_code(
            doc_type="RK",
            warehouse=wh,
            owner=owner,
            biz_date=date.today(),
        )

        # 1) 任务头
        task = WmsTask.objects.create(
            task_no=task_no,
            task_type=WmsTask.TaskType.RECEIVE,
            owner_id=owner_id,
            warehouse_id=getattr(settings, "DEFAULT_WAREHOUSE_ID", None),
            created_by=request.user,
            created_at=timezone.now(),
            posting_note="PDA无ASN收货",

            status=WmsTask.Status.RELEASED,
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )

        # 2) 聚合数量
        grouped = defaultdict(Decimal)
        for it in items:
            pid = int(it["product_id"])
            q = Decimal(str(it["qty"]))
            if q <= 0:
                raise ValidationError(f"产品 {pid} 的数量必须 > 0")
            grouped[pid] += q

        # 3) 行 + 快照（快照会直接生成 TaskScanLog）
        for pid, total_qty in grouped.items():
            line = (WmsTaskLine.objects
                    .filter(task_id=task.id, product_id=pid)
                    .order_by("id").first())
            if not line:
                line = WmsTaskLine.objects.create(
                    task_id=task.id,
                    product_id=pid,
                    status=WmsTaskLine.Status.RELEASED,
                    qty_plan=total_qty,   # 可选：计划=本次合计，便于对账
                )

            p = Product.objects.only("id").get(id=pid)
            default_loc = Location.objects.get(pk=1)
            snap_items = [{
                "product": p,                 # 关键：用 product 实例
                "qty_ok": total_qty,          # 关键：你的函数要的是 qty_ok
                "location":default_loc,
                # 可选：批次/效期/库位等： "lot_no": "...", "expiry_date": date(...)
            }]
            save_receiving_snapshot(
                task_line_id=line.id,
                items=snap_items,
                operator=request.user,
                source="PDA",
            )
        task.status = WmsTask.Status.COMPLETED
        task.review_status = WmsTask.ReviewStatus.APPROVED
        task.posting_status = WmsTask.PostingStatus.PENDING

        task.save(update_fields=["status", "review_status", "posting_status"])

        task.refresh_from_db()
        logger.debug(
            "task after save in DB: id=%s status=%s review_status=%s posting_status=%s",
            task.id,
            task.status,
            task.review_status,
            task.posting_status,
        )

        # 4) 过账
        print("过账 _run_posting_handler")
        result = _run_posting_handler(task_id=task.id, by_user=request.user, note="PDA无ASN收货")
        print("after 过账 _run_posting_handler")
        return Response({
            "task_id": task.id,
            "task_no": getattr(task, "task_no", None),
            "posted": True,
            "message": "收货成功",
            **(result or {}),
        }, status=status.HTTP_201_CREATED)
