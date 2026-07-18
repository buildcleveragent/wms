import hashlib
import json
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.core.models import DocSequence
from allapp.core.utils.log_context import build_log_payload
from allapp.inbound.constants import (
    PDA_NO_ORDER_RECEIVE_NOTE,
    PDA_NO_ORDER_RECEIVE_SOURCE_APP,
    PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
)
from allapp.inbound.models import InboundOrder, NoOrderReceiveRequest
from allapp.inbound.permissions import (
    CanReceiveWithoutOrder,
    can_operate_inbound_tasks,
    can_view_receive_tasks,
    scoped_receive_tasks,
)
from allapp.inbound.serializers import (
    InboundOrderCreateSerializer,
    InboundOrderReadSerializer,
    InboundTaskSerializer,
    PutawayRecordSerializer,
    ReceiptRecordSerializer,
    ReceiveWithoutOrderPayloadSerializer,
)
from allapp.inbound.services import finalize_receive_line_with_variance
from allapp.locations.models import Location, Warehouse
from allapp.products.models import Product
from allapp.tasking import services as task_services
from allapp.tasking.models import (
    PutawayLineExtra,
    ReceiveLineExtra,
    TaskAssignment,
    TaskScanLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.services import _run_posting_handler, save_receiving_snapshot

logger = logging.getLogger(__name__)


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "request_id 已被不同的收货内容使用"
    default_code = "idempotency_conflict"


class ReceiveGoodsWithoutOrder(APIView):
    permission_classes = [permissions.IsAuthenticated, CanReceiveWithoutOrder]

    def _resolve_scope(self, request, payload):
        user = request.user
        payload_owner_id = int(payload["owner_id"])
        payload_warehouse_id = payload.get("warehouse_id")

        warehouse_id = payload_warehouse_id or getattr(user, "warehouse_id", None)
        if not warehouse_id:
            raise ValidationError("必须提供 warehouse_id 或为当前用户绑定 warehouse")

        scope = AccessScope.for_user(user)
        if not scope.allows(owner_id=payload_owner_id, warehouse_id=warehouse_id):
            raise PermissionDenied("无权处理指定货主或仓库")
        return payload_owner_id, warehouse_id

    @staticmethod
    def _payload_hash(*, owner_id, warehouse_id, location_id, remark, items):
        normalized_items = sorted(
            (
                {
                    "product_id": int(item["product_id"]),
                    "qty": format(Decimal(item["qty"]), "f"),
                    "lot_no": (item.get("lot_no") or "").strip().upper(),
                    "mfg_date": (
                        item.get("mfg_date").isoformat()
                        if item.get("mfg_date")
                        else None
                    ),
                    "exp_date": (
                        item.get("exp_date").isoformat()
                        if item.get("exp_date")
                        else None
                    ),
                }
                for item in items
            ),
            key=lambda item: (
                item["product_id"],
                item["lot_no"],
                item["mfg_date"] or "",
                item["exp_date"] or "",
                item["qty"],
            ),
        )
        canonical = json.dumps(
            {
                "owner_id": int(owner_id),
                "warehouse_id": int(warehouse_id),
                "location_id": int(location_id) if location_id else None,
                "remark": remark,
                "items": normalized_items,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _idempotent_response(task):
        return Response(
            {
                "task_id": task.id,
                "task_no": task.task_no,
                "posted": task.posting_status == WmsTask.PostingStatus.POSTED,
                "idempotent": True,
                "message": "该请求已处理，返回原收货结果",
            },
            status=status.HTTP_200_OK,
        )

    @transaction.atomic
    def post(self, request):
        # allapp/inbound/views.py（关键片段）
        raw_payload = request.data.copy()
        if not raw_payload.get("request_id"):
            header_request_id = request.headers.get("Idempotency-Key")
            if header_request_id:
                raw_payload["request_id"] = header_request_id
        s = ReceiveWithoutOrderPayloadSerializer(data=raw_payload)
        s.is_valid(raise_exception=True)
        payload = s.validated_data
        request_id = payload["request_id"]
        owner_id, wid = self._resolve_scope(request, payload)
        items = payload["items"]
        remark = (payload.get("remark") or PDA_NO_ORDER_RECEIVE_NOTE).strip()

        try:
            wh = Warehouse.objects.only('id').get(id=wid)
        except Warehouse.DoesNotExist:
            raise ValidationError(f"warehouse_id 不存在：{wid}")

        try:
            owner = Owner.objects.only('id').get(id=owner_id)
        except Owner.DoesNotExist:
            raise ValidationError(f"owner_id 不存在：{owner_id}")

        product_ids = {int(item["product_id"]) for item in items}
        product_map = {
            product.id: product
            for product in Product.objects.only("id", "owner_id").filter(id__in=product_ids)
        }
        missing_product_ids = sorted(product_ids - set(product_map))
        if missing_product_ids:
            raise ValidationError({"items": f"product_id 不存在：{missing_product_ids}"})
        bad_product_ids = sorted(
            product_id
            for product_id, product in product_map.items()
            if product.owner_id != owner_id
        )
        if bad_product_ids:
            raise PermissionDenied(f"存在不属于当前货主的商品：{bad_product_ids}")

        location = None
        location_id = payload.get("location_id")
        if location_id:
            try:
                location = Location.objects.only("id", "warehouse_id").get(id=location_id)
            except Location.DoesNotExist:
                raise ValidationError(f"location_id 不存在：{location_id}")
            if location.warehouse_id != wh.id:
                raise ValidationError("location_id 必须属于当前 warehouse")

        payload_hash = self._payload_hash(
            owner_id=owner_id,
            warehouse_id=wh.id,
            location_id=location_id,
            remark=remark,
            items=items,
        )
        request_record = (
            NoOrderReceiveRequest.objects.select_for_update()
            .filter(created_by=request.user, request_id=request_id)
            .select_related("task")
            .first()
        )
        if request_record is None:
            try:
                with transaction.atomic():
                    request_record = NoOrderReceiveRequest.objects.create(
                        request_id=request_id,
                        payload_hash=payload_hash,
                        created_by=request.user,
                        owner=owner,
                        warehouse=wh,
                    )
            except IntegrityError:
                request_record = (
                    NoOrderReceiveRequest.objects.select_for_update()
                    .select_related("task")
                    .get(created_by=request.user, request_id=request_id)
                )

        if (
            request_record.payload_hash != payload_hash
            or request_record.owner_id != owner_id
            or request_record.warehouse_id != wh.id
        ):
            raise IdempotencyConflict()
        if request_record.task_id:
            return self._idempotent_response(request_record.task)

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
            source_app=PDA_NO_ORDER_RECEIVE_SOURCE_APP,
            source_model=PDA_NO_ORDER_RECEIVE_SOURCE_MODEL,
            remark=remark,
            posting_note=PDA_NO_ORDER_RECEIVE_NOTE,

            status=WmsTask.Status.RELEASED,
            review_status=WmsTask.ReviewStatus.NOT_READY,
            posting_status=WmsTask.PostingStatus.NOT_READY,
        )

        # # 2) 聚合数量
        # grouped = defaultdict(Decimal)
        # for it in items:
        #     pid = int(it["product_id"])
        #     q = Decimal(str(it["qty"]))
        #     if q <= 0:
        #         raise ValidationError(f"产品 {pid} 的数量必须 > 0")
        #     grouped[pid] += q

        # 2) 聚合数量，同时保留批次/生产日期/有效期维度生成扫描快照。
        # grouped = defaultdict(Decimal)
        # snapshot_grouped = defaultdict(Decimal)
        # for it in items:
        #     pid = int(it["product_id"])
        #     q = Decimal(str(it["qty"]))
        #     if q <= 0:
        #         raise ValidationError(f"产品 {pid} 的数量必须 > 0")
        #     grouped[pid] += q
        #     snapshot_grouped[
        #         (
        #             pid,
        #             (it.get("lot_no") or "").strip().upper(),
        #             it.get("mfg_date"),
        #             it.get("exp_date"),
        #         )
        #     ] += q

        # # 3) 行 + 快照（快照会直接生成 TaskScanLog）
        # for pid, total_qty in grouped.items():
        #     line = (WmsTaskLine.objects
        #             .filter(task_id=task.id, product_id=pid)
        #             .order_by("id").first())
        #     if not line:
        #         line = WmsTaskLine.objects.create(
        #             task_id=task.id,
        #             product_id=pid,
        #             status=WmsTaskLine.Status.RELEASED,
        #             qty_plan=total_qty,   # 可选：计划=本次合计，便于对账
        #         )
        #
        #     p = Product.objects.only("id").get(id=pid)
        #     snap_items = [{
        #         "product": p,                 # 关键：用 product 实例
        #         "qty_ok": total_qty,          # 关键：你的函数要的是 qty_ok
        #         "location": location,
        #         # 可选：批次/效期/库位等： "lot_no": "...", "expiry_date": date(...)
        #     }]
        #     save_receiving_snapshot(
        #         task_line_id=line.id,
        #         items=snap_items,
        #         operator=request.user,
        #         source="PDA",
        #     )
        # task.status = WmsTask.Status.COMPLETED
        # task.review_status = WmsTask.ReviewStatus.APPROVED
        # task.posting_status = WmsTask.PostingStatus.PENDING
        #
        # task.save(update_fields=["status", "review_status", "posting_status"])
        #
        # task.refresh_from_db()
        # ctx, ctx_text = build_log_payload(task=task, user=request.user, owner=owner, warehouse=wh)
        # logger.debug(
        #     "task after save in DB: id=%s status=%s review_status=%s posting_status=%s",
        #     task.id,
        #     task.status,
        #     task.review_status,
        #     task.posting_status,
        # )


        # 2) 聚合数量，同时保留批次/生产日期/有效期维度生成扫描快照。
        grouped = defaultdict(Decimal)
        snapshot_grouped = defaultdict(Decimal)
        received_items = []
        for it in items:
            pid = int(it["product_id"])
            q = Decimal(str(it["qty"]))
            if q <= 0:
                raise ValidationError(f"产品 {pid} 的数量必须 > 0")
            lot_no = (it.get("lot_no") or "").strip().upper()
            mfg_date = it.get("mfg_date")
            exp_date = it.get("exp_date")
            grouped[pid] += q
            snapshot_grouped[(pid, lot_no, mfg_date, exp_date)] += q
            received_items.append({
                "product_id": pid,
                "qty": str(q),
                "lot_no": lot_no,
                "mfg_date": str(mfg_date) if mfg_date else None,
                "exp_date": str(exp_date) if exp_date else None,
            })

        logger.info(
            "inbound.receive_without_order.normalized_items owner_id=%s warehouse_id=%s items=%s",
            owner_id,
            wh.id,
            received_items,
        )

        ctx, ctx_text = build_log_payload(task=task, user=request.user, owner=owner, warehouse=wh)

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

            p = product_map[pid]
            snap_items = []
            for (item_pid, lot_no, mfg_date, exp_date), item_qty in snapshot_grouped.items():
                if item_pid != pid:
                    continue
                snap_items.append({
                    "product": p,                 # 关键：用 product 实例
                    "qty_ok": item_qty,           # 关键：你的函数要的是 qty_ok
                    "location": location,
                    "lot_no": lot_no,
                    "mfg_date": mfg_date,
                    "exp_date": exp_date,
                })
            save_receiving_snapshot(
                task_line_id=line.id,
                items=snap_items,
                operator=request.user,
                source="PDA",
            )

            scan_mfg_backfilled = 0
            for snap in snap_items:
                mfg_date = snap.get("mfg_date")
                if not mfg_date:
                    continue
                scan_mfg_backfilled += TaskScanLog.objects.filter(
                    task_id=task.id,
                    task_line_id=line.id,
                    product_id=pid,
                    lot_no=(snap.get("lot_no") or None),
                    exp_date=snap.get("exp_date"),
                    mfg_date__isnull=True,
                    posted_at__isnull=True,
                ).update(mfg_date=mfg_date)

            if scan_mfg_backfilled:
                logger.info(
                    "inbound.receive_without_order.scan_mfg_backfilled %s line_id=%s rows=%s",
                    ctx_text,
                    line.id,
                    scan_mfg_backfilled,
                    extra=ctx,
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
        result = _run_posting_handler(
            task_id=task.id,
            by_user=request.user,
            note=PDA_NO_ORDER_RECEIVE_NOTE,
        )
        request_record.task = task
        request_record.save(update_fields=["task", "updated_at"])
        record_audit_event(
            action="inbound.receive_without_order.post",
            module="inbound",
            request=request,
            obj=task,
            before={},
            after={
                "status": task.status,
                "review_status": task.review_status,
                "posting_status": task.posting_status,
            },
            metadata={"request_id": request_id, "item_count": len(items)},
        )
        logger.info(
            "inbound.receive_without_order.posting.completed %s", ctx_text, extra=ctx
        )
        return Response(
            {
                "task_id": task.id,
                "task_no": getattr(task, "task_no", None),
                "posted": True,
                "idempotent": False,
                "message": "收货成功",
                **(result or {}),
            },
            status=status.HTTP_201_CREATED,
        )


class InboundOrderPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _drf_validation_error(exc):
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError(getattr(exc, "messages", [str(exc)]))


class InboundOrderViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Owner-facing ASN API backed by the existing inbound state machine."""

    permission_classes = [permissions.IsAuthenticated]
    pagination_class = InboundOrderPagination

    def get_serializer_class(self):
        if self.action == "create":
            return InboundOrderCreateSerializer
        return InboundOrderReadSerializer

    @staticmethod
    def _has_role(user, role):
        if getattr(user, "is_superuser", False):
            return True
        scope = AccessScope.for_user(user)
        return bool(scope.is_valid and role in scope.roles)

    @staticmethod
    def _can_read(user):
        return bool(
            user.is_superuser
            or any(
                user.has_perm(permission)
                for permission in (
                    "inbound.view_inboundorder",
                    "inbound.add_inboundorder",
                    "inbound.submit_as_owner_buyers",
                    "inbound.approve_as_owner_manager",
                    "inbound.approve_as_wh_manager",
                )
            )
        )

    def get_queryset(self):
        user = self.request.user
        queryset = (
            InboundOrder.objects.select_related(
                "owner",
                "warehouse",
                "supplier",
                "created_by",
            )
            .prefetch_related("lines__product__base_uom")
            .annotate(planned_qty_value=Sum("lines__base_qty"))
        )
        if not self._can_read(user):
            return queryset.none()
        scope = AccessScope.for_user(user)
        queryset = scope.filter_queryset(
            queryset,
            owner_field="owner_id",
            warehouse_field="warehouse_id",
        )
        if UserRoleScope.Role.OWNER_SALESPERSON in scope.roles:
            queryset = queryset.filter(created_by=user)

        query = (self.request.query_params.get("search") or "").strip()
        if query:
            queryset = queryset.filter(
                Q(order_no__icontains=query)
                | Q(src_bill_no__icontains=query)
                | Q(supplier__name__icontains=query)
                | Q(lines__product__sku__icontains=query)
                | Q(lines__product__name__icontains=query)
            ).distinct()
        approval_status = (self.request.query_params.get("approval_status") or "").strip()
        if approval_status:
            queryset = queryset.filter(approval_status=approval_status)
        submit_status = (self.request.query_params.get("submit_status") or "").strip()
        if submit_status:
            queryset = queryset.filter(submit_status=submit_status)
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            queryset = queryset.filter(biz_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(biz_date__lte=date_to)
        return queryset.order_by("-biz_date", "-id")

    def list(self, request, *args, **kwargs):
        if not self._can_read(request.user):
            raise PermissionDenied("没有查看入库单的权限。")
        response = super().list(request, *args, **kwargs)
        record_audit_event(
            action="inbound.order.query",
            module="inbound",
            request=request,
            metadata={
                "search": request.query_params.get("search", ""),
                "approval_status": request.query_params.get("approval_status", ""),
            },
        )
        return response

    def retrieve(self, request, *args, **kwargs):
        order = self.get_object()
        response = Response(self.get_serializer(order).data)
        record_audit_event(
            action="inbound.order.view",
            module="inbound",
            request=request,
            obj=order,
        )
        return response

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if not (
            request.user.is_superuser
            or request.user.has_perm("inbound.add_inboundorder")
        ):
            raise PermissionDenied("没有创建入库单的权限。")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        record_audit_event(
            action="inbound.order.create",
            module="inbound",
            request=request,
            obj=order,
            before={},
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
            },
            metadata={"line_count": order.lines.count()},
        )
        output = InboundOrderReadSerializer(order, context={"request": request})
        return Response(output.data, status=status.HTTP_201_CREATED)

    def _locked_scoped_order(self, pk):
        return get_object_or_404(self.get_queryset().select_for_update(), pk=pk)

    def _action_response(self, request, order):
        order.refresh_from_db()
        return Response(InboundOrderReadSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="submit")
    @transaction.atomic
    def submit(self, request, pk=None):
        if not (
            request.user.has_perm("inbound.submit_as_owner_buyers")
            and self._has_role(request.user, UserRoleScope.Role.OWNER_SALESPERSON)
        ):
            raise PermissionDenied("没有提交入库单的权限。")
        order = self._locked_scoped_order(pk)
        if not request.user.is_superuser and order.created_by_id != request.user.pk:
            raise PermissionDenied("货主业务员只能提交本人创建的入库单。")
        before = {
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
        }
        try:
            order.submit_by_owner_buyers(request.user)
        except DjangoValidationError as exc:
            raise _drf_validation_error(exc) from exc
        record_audit_event(
            action="inbound.order.submit",
            module="inbound",
            request=request,
            obj=order,
            before=before,
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
            },
        )
        return self._action_response(request, order)

    def _review_action(self, request, pk, *, permission, role, method_name, audit_action):
        if not (request.user.has_perm(permission) and self._has_role(request.user, role)):
            raise PermissionDenied("没有执行该审核动作的权限。")
        order = self._locked_scoped_order(pk)
        before = {
            "submit_status": order.submit_status,
            "approval_status": order.approval_status,
        }
        try:
            getattr(order, method_name)(request.user)
        except DjangoValidationError as exc:
            raise _drf_validation_error(exc) from exc
        record_audit_event(
            action=audit_action,
            module="inbound",
            request=request,
            obj=order,
            before=before,
            after={
                "submit_status": order.submit_status,
                "approval_status": order.approval_status,
            },
        )
        return self._action_response(request, order)

    @action(detail=True, methods=["post"], url_path="owner-approve")
    @transaction.atomic
    def owner_approve(self, request, pk=None):
        return self._review_action(
            request,
            pk,
            permission="inbound.approve_as_owner_manager",
            role=UserRoleScope.Role.OWNER_MANAGER,
            method_name="owner_approve",
            audit_action="inbound.order.owner_approve",
        )

    @action(detail=True, methods=["post"], url_path="owner-reject")
    @transaction.atomic
    def owner_reject(self, request, pk=None):
        return self._review_action(
            request,
            pk,
            permission="inbound.approve_as_owner_manager",
            role=UserRoleScope.Role.OWNER_MANAGER,
            method_name="owner_reject",
            audit_action="inbound.order.owner_reject",
        )

    @action(detail=True, methods=["post"], url_path="warehouse-confirm")
    @transaction.atomic
    def warehouse_confirm(self, request, pk=None):
        return self._review_action(
            request,
            pk,
            permission="inbound.approve_as_wh_manager",
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            method_name="wh_confirm",
            audit_action="inbound.order.warehouse_confirm",
        )

    @action(detail=True, methods=["post"], url_path="warehouse-reject")
    @transaction.atomic
    def warehouse_reject(self, request, pk=None):
        return self._review_action(
            request,
            pk,
            permission="inbound.approve_as_wh_manager",
            role=UserRoleScope.Role.WAREHOUSE_MANAGER,
            method_name="wh_reject",
            audit_action="inbound.order.warehouse_reject",
        )


def _canonical_hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class InboundTaskViewSet(viewsets.ReadOnlyModelViewSet):
    """JWT PDA entry point for ordered RECEIVE and PUTAWAY work."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InboundTaskSerializer
    pagination_class = InboundOrderPagination

    def get_queryset(self):
        user = self.request.user
        scope = AccessScope.for_user(user)
        if not (
            can_view_receive_tasks(user)
            and (
                scope.is_global
                or scope.roles.intersection(
                    {
                        UserRoleScope.Role.WAREHOUSE_OPERATOR,
                        UserRoleScope.Role.WAREHOUSE_MANAGER,
                    }
                )
            )
        ):
            return WmsTask.objects.none()
        queryset = scoped_receive_tasks(
            user,
            WmsTask.objects.filter(
                task_type__in=(WmsTask.TaskType.RECEIVE, WmsTask.TaskType.PUTAWAY)
            )
            .select_related("owner", "warehouse")
            .prefetch_related(
                "assignments",
                "lines__product",
                "lines__from_location",
                "lines__to_location",
                "lines__receivelineextra",
                "lines__putawaylineextra__to_location",
            ),
        )
        task_type = (self.request.query_params.get("task_type") or "").upper()
        if task_type:
            if task_type not in {WmsTask.TaskType.RECEIVE, WmsTask.TaskType.PUTAWAY}:
                return queryset.none()
            queryset = queryset.filter(task_type=task_type)
        statuses = self.request.query_params.getlist("status")
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        elif self.action == "list":
            queryset = queryset.filter(
                status__in=(
                    WmsTask.Status.RELEASED,
                    WmsTask.Status.IN_PROGRESS,
                    WmsTask.Status.COMPLETED,
                )
            )
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(task_no__icontains=search)
                | Q(ref_no__icontains=search)
                | Q(lines__product__sku__icontains=search)
                | Q(lines__product__name__icontains=search)
            ).distinct()
        return queryset.order_by("-priority", "id")

    def _require_operator(self):
        if not can_operate_inbound_tasks(self.request.user):
            raise PermissionDenied("只有仓库操作员可以执行收货或上架动作。")

    def _locked_task(self, pk):
        return get_object_or_404(self.get_queryset().select_for_update(), pk=pk)

    @staticmethod
    def _owns_task(user, task, line=None):
        assignments = TaskAssignment.objects.filter(
            task=task,
            assignee=user,
            finished_at__isnull=True,
        )
        if line is None:
            return assignments.exists()
        return assignments.filter(Q(line=line) | Q(line__isnull=True)).exists()

    def _require_owned(self, task, line=None):
        if not self._owns_task(self.request.user, task, line=line):
            raise PermissionDenied("请先领取任务；不能操作他人的任务。")

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def claim(self, request, pk=None):
        self._require_operator()
        task = self._locked_task(pk)
        try:
            task_services.claim_task(
                task,
                by_user=request.user,
                allowed_wh_ids={task.warehouse_id},
            )
        except (DjangoValidationError, ValidationError) as exc:
            if isinstance(exc, DjangoValidationError):
                raise _drf_validation_error(exc) from exc
            raise
        record_audit_event(
            action="inbound.task.claim",
            module="inbound",
            request=request,
            obj=task,
            metadata={"task_type": task.task_type},
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def start(self, request, pk=None):
        self._require_operator()
        task = self._locked_task(pk)
        self._require_owned(task)
        try:
            task_services.task_start(request=request, task=task)
        except (DjangoValidationError, ValidationError) as exc:
            if isinstance(exc, DjangoValidationError):
                raise _drf_validation_error(exc) from exc
            raise
        task.refresh_from_db()
        record_audit_event(
            action="inbound.task.start",
            module="inbound",
            request=request,
            obj=task,
            before={"status": WmsTask.Status.RELEASED},
            after={"status": task.status},
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["get"])
    def locations(self, request, pk=None):
        task = self.get_object()
        query = (request.query_params.get("search") or "").strip()
        queryset = Location.objects.filter(
            warehouse_id=task.warehouse_id,
            is_disabled=False,
            is_frozen=False,
        )
        if query:
            queryset = queryset.filter(Q(code__icontains=query) | Q(name__icontains=query))
        data = [
            {"id": row.pk, "code": row.code, "name": row.name or "", "label": row.code}
            for row in queryset.order_by("code")[:100]
        ]
        return Response(data)

    @staticmethod
    def _idempotency_marker(*, task, user, request_id, payload_hash, product=None, location=None):
        marker_fp = hashlib.sha256(
            f"inbound-receipt:{task.pk}:{user.pk}:{request_id}".encode("utf-8")
        ).hexdigest()
        expected_remark = f"IDEMPOTENCY:{payload_hash}"
        existing = TaskScanLog.objects.filter(fp=marker_fp).first()
        if existing:
            if existing.remark != expected_remark:
                raise IdempotencyConflict()
            return True
        try:
            with transaction.atomic():
                TaskScanLog.objects.create(
                    owner_id=task.owner_id,
                    warehouse_id=task.warehouse_id,
                    task=task,
                    task_line=None,
                    product=product,
                    location=location,
                    method=TaskScanLog.Method.API,
                    source="PDA",
                    by_user=user,
                    status=TaskScanLog.ScanStatus.IGNORED,
                    fp=marker_fp,
                    scan_snapshot_rev=0,
                    remark=expected_remark,
                )
        except IntegrityError:
            existing = TaskScanLog.objects.get(fp=marker_fp)
            if existing.remark != expected_remark:
                raise IdempotencyConflict()
            return True
        return False

    @action(detail=True, methods=["post"], url_path="record-receipt")
    @transaction.atomic
    def record_receipt(self, request, pk=None):
        self._require_operator()
        serializer = ReceiptRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        task = self._locked_task(pk)
        if task.task_type != WmsTask.TaskType.RECEIVE:
            raise ValidationError("该任务不是收货任务。")
        line = get_object_or_404(
            WmsTaskLine.objects.select_for_update().select_related("product"),
            pk=payload["line_id"],
            task=task,
        )
        location = get_object_or_404(
            Location.objects.filter(
                warehouse_id=task.warehouse_id,
                is_disabled=False,
                is_frozen=False,
            ),
            pk=payload["location_id"],
        )
        processed_total = (
            payload["qty_ok"] + payload["qty_damage"] + payload["qty_reject"]
        )
        planned_total = Decimal(line.qty_plan or 0)
        variance_reason = (payload.get("variance_reason") or "").strip()
        payload_hash = _canonical_hash(payload)
        if self._idempotency_marker(
            task=task,
            user=request.user,
            request_id=payload["request_id"],
            payload_hash=payload_hash,
            product=line.product,
            location=location,
        ):
            task.refresh_from_db()
            return Response({"idempotent": True, "task": self.get_serializer(task).data})

        if processed_total > planned_total and not variance_reason:
            raise ValidationError("超收必须填写差异原因。")
        if payload.get("finalize") and processed_total != planned_total and not variance_reason:
            raise ValidationError("结束差异收货行时必须填写差异原因。")
        self._require_owned(task, line=line)
        if task.status not in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}:
            raise ValidationError("任务未处于可收货状态。")
        try:
            extra = ReceiveLineExtra.objects.select_for_update().get(line=line)
        except ReceiveLineExtra.DoesNotExist as exc:
            raise ValidationError("任务行缺少收货扩展。") from exc

        save_receiving_snapshot(
            task_line_id=line.pk,
            items=[
                {
                    "product": line.product,
                    "location": location,
                    "qty_ok": payload["qty_ok"],
                    "lot_no": payload.get("lot_no") or "",
                    "mfg_date": payload.get("mfg_date"),
                    "exp_date": payload.get("exp_date"),
                }
            ],
            operator=request.user,
            source="PDA",
        )
        extra.qty_ok = payload["qty_ok"]
        extra.qty_damage = payload["qty_damage"]
        extra.qty_reject = payload["qty_reject"]
        extra.lot_no = payload.get("lot_no") or None
        extra.mfg_date = payload.get("mfg_date")
        extra.exp_date = payload.get("exp_date")
        extra.damage_reason_code = payload.get("damage_reason_code") or ""
        extra.reject_reason_code = payload.get("reject_reason_code") or ""
        extra._by_user = request.user
        try:
            extra.save()
        except DjangoValidationError as exc:
            raise _drf_validation_error(exc) from exc

        if processed_total != planned_total and variance_reason:
            WmsTaskLine.objects.filter(pk=line.pk).update(
                remark=variance_reason[:200],
                updated_by=request.user,
                updated_at=timezone.now(),
            )

        line.refresh_from_db()
        if payload.get("finalize") and not line.finished_at:
            try:
                finalize_receive_line_with_variance(
                    line.pk,
                    by_user=request.user,
                    variance_reason=variance_reason,
                )
            except DjangoValidationError as exc:
                raise _drf_validation_error(exc) from exc
        task.refresh_from_db()
        record_audit_event(
            action="inbound.receive.record",
            module="inbound",
            request=request,
            obj=task,
            metadata={
                "line_id": line.pk,
                "location_id": location.pk,
                "qty_ok": str(payload["qty_ok"]),
                "qty_damage": str(payload["qty_damage"]),
                "qty_reject": str(payload["qty_reject"]),
                "variance_reason": variance_reason,
                "request_id": payload["request_id"],
            },
        )
        return Response({"idempotent": False, "task": self.get_serializer(task).data})

    @action(detail=True, methods=["post"], url_path="record-putaway")
    @transaction.atomic
    def record_putaway(self, request, pk=None):
        self._require_operator()
        serializer = PutawayRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        task = self._locked_task(pk)
        if task.task_type != WmsTask.TaskType.PUTAWAY:
            raise ValidationError("该任务不是上架任务。")
        line = get_object_or_404(
            WmsTaskLine.objects.select_for_update().select_related("product", "from_location"),
            pk=payload["line_id"],
            task=task,
        )
        location = get_object_or_404(
            Location.objects.filter(
                warehouse_id=task.warehouse_id,
                is_disabled=False,
                is_frozen=False,
            ),
            pk=payload["to_location_id"],
        )
        if line.from_location_id == location.pk:
            raise ValidationError("目标库位不能与来源库位相同。")
        payload_hash = _canonical_hash(payload)
        scan_fp = hashlib.sha256(
            f"inbound-putaway:{task.pk}:{request.user.pk}:{payload['request_id']}".encode("utf-8")
        ).hexdigest()
        expected_remark = f"IDEMPOTENCY:{payload_hash}"
        existing = TaskScanLog.objects.filter(fp=scan_fp).first()
        if existing:
            if existing.remark != expected_remark:
                raise IdempotencyConflict()
            task.refresh_from_db()
            return Response({"idempotent": True, "task": self.get_serializer(task).data})

        self._require_owned(task, line=line)
        if task.status not in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}:
            raise ValidationError("任务未处于可上架状态。")
        if line.to_location_id and line.to_location_id != location.pk:
            raise ValidationError("该任务行已选择其他目标库位，不支持跨库位混合上架。")
        current = Decimal(line.qty_done or 0)
        total = current + payload["qty"]
        if total > Decimal(line.qty_plan or 0):
            raise ValidationError("上架数量不能超过计划数量。")

        line.to_location = location
        line.scan_snapshot_rev = (line.scan_snapshot_rev or 0) + 1
        try:
            line.save(update_fields=["to_location", "scan_snapshot_rev", "updated_at"])
        except DjangoValidationError as exc:
            raise _drf_validation_error(exc) from exc
        try:
            with transaction.atomic():
                TaskScanLog.objects.create(
                    owner_id=task.owner_id,
                    warehouse_id=task.warehouse_id,
                    task=task,
                    task_line=line,
                    product=line.product,
                    location=location,
                    method=TaskScanLog.Method.MANUAL,
                    source="PDA",
                    by_user=request.user,
                    qty_base_delta=payload["qty"],
                    status=TaskScanLog.ScanStatus.OK,
                    fp=scan_fp,
                    scan_snapshot_rev=line.scan_snapshot_rev,
                    remark=expected_remark,
                )
        except IntegrityError:
            existing = TaskScanLog.objects.get(fp=scan_fp)
            if existing.remark != expected_remark:
                raise IdempotencyConflict()
            task.refresh_from_db()
            return Response({"idempotent": True, "task": self.get_serializer(task).data})

        extra, _ = PutawayLineExtra.objects.select_for_update().get_or_create(line=line)
        if extra.to_location_id and extra.to_location_id != location.pk:
            raise ValidationError("该任务行已记录其他目标库位。")
        extra.to_location = location
        extra.qty_moved = total
        extra._by_user = request.user
        try:
            extra.save()
        except DjangoValidationError as exc:
            raise _drf_validation_error(exc) from exc
        task.refresh_from_db()
        record_audit_event(
            action="inbound.putaway.record",
            module="inbound",
            request=request,
            obj=task,
            metadata={
                "line_id": line.pk,
                "to_location_id": location.pk,
                "qty": str(payload["qty"]),
                "request_id": payload["request_id"],
            },
        )
        return Response({"idempotent": False, "task": self.get_serializer(task).data})
