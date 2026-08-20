import hashlib
import json
import logging
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
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
from allapp.inbound.constants import (
    PDA_NO_ORDER_RECEIVE_NOTE,
)
from allapp.inbound.models import InboundOrder
from allapp.inbound.no_order_access import resolve_no_order_receive_scope
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
from allapp.inbound.services import (
    NoOrderReceiveConflict,
    finalize_receive_line_with_variance,
    receive_goods_without_order,
)
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Location
from allapp.products.identifier_lookup import product_search_q
from allapp.tasking import services as task_services
from allapp.tasking.models import (
    PutawayLineExtra,
    ReceiveLineExtra,
    TaskAssignment,
    TaskScanLog,
    WmsTask,
    WmsTaskLine,
)
from allapp.tasking.services import save_receiving_snapshot

logger = logging.getLogger(__name__)


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "request_id 已被不同的收货内容使用"
    default_code = "idempotency_conflict"


class ReceiveGoodsWithoutOrder(APIView):
    permission_classes = [permissions.IsAuthenticated, CanReceiveWithoutOrder]

    def _resolve_scope(self, request, payload):
        owner, warehouse_id = resolve_no_order_receive_scope(
            request.user,
            payload["owner_id"],
            payload.get("warehouse_id"),
        )
        return owner.id, warehouse_id

    def post(self, request):
        raw_payload = request.data.copy()
        if not raw_payload.get("request_id"):
            header_request_id = request.headers.get("Idempotency-Key")
            if header_request_id:
                raw_payload["request_id"] = header_request_id
        s = ReceiveWithoutOrderPayloadSerializer(data=raw_payload)
        s.is_valid(raise_exception=True)
        payload = s.validated_data
        owner_id, warehouse_id = self._resolve_scope(request, payload)
        try:
            result = receive_goods_without_order(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                location_id=payload.get("location_id"),
                items=payload["items"],
                request_id=payload["request_id"],
                by_user=request.user,
                remark=payload.get("remark") or PDA_NO_ORDER_RECEIVE_NOTE,
                request=request,
            )
        except DjangoValidationError as exc:
            raise _drf_validation_error(exc) from exc
        except NoOrderReceiveConflict as exc:
            raise IdempotencyConflict from exc
        response_status = status.HTTP_200_OK if result["idempotent"] else status.HTTP_201_CREATED
        return Response(result, status=response_status)


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
                | product_search_q(query, product_field="lines__product_id")
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
        if not (request.user.is_superuser or request.user.has_perm("inbound.add_inboundorder")):
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
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
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
                | product_search_q(search, product_field="lines__product_id")
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
        processed_total = payload["qty_ok"] + payload["qty_damage"] + payload["qty_reject"]
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
        if (
            not line.product.serial_control
            and payload.get("finalize")
            and processed_total != planned_total
            and not variance_reason
        ):
            raise ValidationError("结束差异收货行时必须填写差异原因。")
        self._require_owned(task, line=line)
        if task.status not in {WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS}:
            raise ValidationError("任务未处于可收货状态。")
        try:
            extra = ReceiveLineExtra.objects.select_for_update().get(line=line)
        except ReceiveLineExtra.DoesNotExist as exc:
            raise ValidationError("任务行缺少收货扩展。") from exc
        serial_no = (payload.get("serial_no") or "").strip().upper()
        is_serial = bool(line.product.serial_control)
        if is_serial:
            if not serial_no:
                raise ValidationError({"serial_no": "序列号商品必须逐件录入序列号。"})
            if (
                payload["qty_ok"] != Decimal("1")
                or payload["qty_damage"] != 0
                or payload["qty_reject"] != 0
            ):
                raise ValidationError("序列号商品上架链路仅接受逐件合格收货（qty_ok=1）。")
            duplicate_scan = TaskScanLog.objects.filter(
                owner_id=task.owner_id,
                product_id=line.product_id,
                serial_no__iexact=serial_no,
                status=TaskScanLog.ScanStatus.OK,
            ).exists()
            duplicate_inventory = InventoryDetail.objects.filter(
                owner_id=task.owner_id,
                product_id=line.product_id,
                serial_no_norm=serial_no,
                is_active=True,
            ).exists()
            if duplicate_scan or duplicate_inventory:
                raise ValidationError({"serial_no": "该序列号已存在，不能重复收货。"})

            new_qty_ok = Decimal(extra.qty_ok or 0) + Decimal("1")
            cumulative_total = (
                new_qty_ok + Decimal(extra.qty_damage or 0) + Decimal(extra.qty_reject or 0)
            )
            if cumulative_total > planned_total and not variance_reason:
                raise ValidationError("超收必须填写差异原因。")
            WmsTaskLine.objects.filter(pk=line.pk).update(
                scan_snapshot_rev=F("scan_snapshot_rev") + 1
            )
            line.refresh_from_db(fields=["scan_snapshot_rev"])
            TaskScanLog.objects.create(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                task=task,
                task_line=line,
                product=line.product,
                location=location,
                method=TaskScanLog.Method.SCAN,
                source="PDA",
                by_user=request.user,
                barcode=serial_no,
                label_key=serial_no,
                code_type="SERIAL",
                qty_base_delta=Decimal("1"),
                lot_no=payload.get("lot_no") or None,
                mfg_date=payload.get("mfg_date"),
                exp_date=payload.get("exp_date"),
                serial_no=serial_no,
                status=TaskScanLog.ScanStatus.OK,
                fp=hashlib.sha256(
                    f"inbound-receipt-serial:{task.pk}:{line.pk}:{serial_no}".encode("utf-8")
                ).hexdigest(),
                scan_snapshot_rev=line.scan_snapshot_rev,
            )
            extra.qty_ok = new_qty_ok
            effective_total = cumulative_total
        else:
            if serial_no:
                raise ValidationError({"serial_no": "非序列号商品不能录入序列号。"})
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
            effective_total = processed_total
        if payload.get("finalize") and effective_total != planned_total and not variance_reason:
            raise ValidationError("结束差异收货行时必须填写差异原因。")
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

        if effective_total != planned_total and variance_reason:
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
                "serial_no": serial_no,
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
        tracking = line.plan_meta or {}
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
                    lot_no=tracking.get("lot_no") or None,
                    mfg_date=tracking.get("mfg_date") or None,
                    exp_date=tracking.get("exp_date") or None,
                    serial_no=tracking.get("serial_no") or None,
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
