import logging
logger = logging.getLogger(__name__)
from collections import defaultdict
from datetime import date
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
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
from allapp.core.utils.log_context import build_log_payload

class ReceiveGoodsWithoutOrder(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        # allapp/inbound/views.py（关键片段）
        s = ReceiveWithoutOrderPayloadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payload = s.validated_data
        owner_id = int(payload["owner_id"])
        items = payload["items"]
        remark = (payload.get("remark") or "PDA无ASN收货").strip()

        wid = payload.get("warehouse_id") or getattr(request.user, "warehouse_id", None)
        if not wid:
            raise ValidationError("必须提供 warehouse_id 或为当前用户绑定 warehouse")

        try:
            wh = Warehouse.objects.only('id').get(id=wid)
        except Warehouse.DoesNotExist:
            raise ValidationError(f"warehouse_id 不存在：{wid}")

        try:
            owner = Owner.objects.only('id').get(id=owner_id)
        except Owner.DoesNotExist:
            raise ValidationError(f"owner_id 不存在：{owner_id}")

        location = None
        location_id = payload.get("location_id")
        if location_id:
            try:
                location = Location.objects.only("id", "warehouse_id").get(id=location_id)
            except Location.DoesNotExist:
                raise ValidationError(f"location_id 不存在：{location_id}")
            if location.warehouse_id != wh.id:
                raise ValidationError("location_id 必须属于当前 warehouse")

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
            warehouse_id=wh.id,
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
            snap_items = [{
                "product": p,                 # 关键：用 product 实例
                "qty_ok": total_qty,          # 关键：你的函数要的是 qty_ok
                "location": location,
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
        ctx, ctx_text = build_log_payload(task=task, user=request.user, owner=owner, warehouse=wh)
        logger.debug(
            "task after save in DB: id=%s status=%s review_status=%s posting_status=%s",
            task.id,
            task.status,
            task.review_status,
            task.posting_status,
        )

        # 4) 过账
        logger.info(
            "inbound.receive_without_order.posting.begin %s item_count=%s",
            ctx_text,
            len(grouped),
            extra=ctx,
        )
        result = _run_posting_handler(task_id=task.id, by_user=request.user, note="PDA无ASN收货")
        logger.info("inbound.receive_without_order.posting.completed %s", ctx_text, extra=ctx)
        return Response({
            "task_id": task.id,
            "task_no": getattr(task, "task_no", None),
            "posted": True,
            "message": "收货成功",
            **(result or {}),
        }, status=status.HTTP_201_CREATED)
