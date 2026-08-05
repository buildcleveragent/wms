from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope
from allapp.baseinfo.models import Owner
from allapp.inventory.models import InventoryDetail
from allapp.locations.models import Container, Location, Warehouse
from allapp.tasking import services as task_services
from allapp.tasking.models import RelocationRequest, TaskAssignment, WmsTask
from allapp.tasking.relocation import (
    RelocationIdempotencyConflict,
    approve_request,
    cancel_request,
    create_container_request,
    create_layer_request,
    record_relocation,
    reject_request,
    report_exception,
    resume_task,
    void_task,
)
from allapp.tasking.serializers_relocation import (
    RelocationExceptionSerializer,
    RelocationRecordSerializer,
    RelocationRequestCreateSerializer,
    RelocationRequestSerializer,
    RelocationReviewSerializer,
    RelocationTaskSerializer,
)


class RelocationFeatureDisabled(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "relocation_feature_disabled"


class RelocationPostingFailed(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "relocation_posting_failed"
    default_detail = "移库扫描已保存，但库存过账失败；请由仓库管理员处理后重试。"


def _raise_posting_failure(exc):
    if isinstance(exc, DjangoValidationError):
        raise ValidationError(exc.messages) from exc
    raise RelocationPostingFailed() from exc


def _require_feature(name, message):
    if not getattr(settings, name, False):
        raise RelocationFeatureDisabled(message)


def _scope(user):
    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        raise PermissionDenied("当前账号没有有效的货主/仓库范围。")
    return scope


def _is_manager(user, scope=None):
    if user.is_superuser:
        return True
    scope = scope or _scope(user)
    return UserRoleScope.Role.WAREHOUSE_MANAGER in scope.roles and user.has_perm(
        "tasking.taskconfirm_as_wh_manager"
    )


def _is_operator(user, scope=None):
    if user.is_superuser:
        return True
    scope = scope or _scope(user)
    return UserRoleScope.Role.WAREHOUSE_OPERATOR in scope.roles and user.has_perm(
        "tasking.claim_task_as_wh_operator"
    )


def _scoped(qs, user):
    scope = _scope(user)
    if scope.is_global:
        return qs
    return scope.filter_queryset(qs, owner_field="owner_id", warehouse_field="warehouse_id")


def _create_request_from_payload(payload, *, by_user, trigger):
    scope = _scope(by_user)
    owner = get_object_or_404(Owner, pk=payload["owner_id"])
    warehouse = get_object_or_404(Warehouse, pk=payload["warehouse_id"])
    if not scope.allows(owner_id=owner.pk, warehouse_id=warehouse.pk):
        raise PermissionDenied("移库申请超出当前账号范围。")
    if payload["mode"] == RelocationRequest.Mode.LAYER:
        return create_layer_request(
            owner=owner,
            warehouse=warehouse,
            lines=payload["lines"],
            reason=payload["reason"],
            by_user=by_user,
            trigger=trigger,
        )
    _require_feature("RELOCATION_CONTAINER_ENABLED", "整容器移库功能尚未启用。")
    source = get_object_or_404(Container, pk=payload["source_container_id"])
    target = get_object_or_404(Location, pk=payload["to_location_id"])
    parent = None
    if payload.get("target_parent_container_id"):
        parent = get_object_or_404(Container, pk=payload["target_parent_container_id"])
    return create_container_request(
        owner=owner,
        warehouse=warehouse,
        source_container=source,
        to_location=target,
        target_parent_container=parent,
        reason=payload["reason"],
        by_user=by_user,
        trigger=trigger,
    )


class RelocationRequestViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = RelocationRequest.objects.select_related(
            "owner", "warehouse", "source_container", "to_location", "target_parent_container",
            "created_by", "reviewed_by", "generated_task",
        ).prefetch_related(
            "lines__inventory_detail__product", "lines__inventory_detail__location",
            "lines__inventory_detail__container", "lines__to_location", "lines__to_container",
        )
        qs = _scoped(qs, self.request.user)
        if not _is_manager(self.request.user):
            qs = qs.filter(created_by=self.request.user)
        return qs.order_by("-created_at", "-id")

    def list(self, request):
        return Response(RelocationRequestSerializer(self.get_queryset(), many=True).data)

    def retrieve(self, request, pk=None):
        return Response(RelocationRequestSerializer(get_object_or_404(self.get_queryset(), pk=pk)).data)

    @transaction.atomic
    def create(self, request):
        _require_feature("RELOCATION_REQUEST_ENABLED", "移库申请功能尚未启用。")
        if not (_is_operator(request.user) and request.user.has_perm("tasking.request_relocation")):
            raise PermissionDenied("当前账号不能提交移库申请。")
        serializer = RelocationRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            obj = _create_request_from_payload(
                serializer.validated_data,
                by_user=request.user,
                trigger=RelocationRequest.Trigger.REQUEST,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        record_audit_event(
            action="relocation.request.create", module="tasking", request=request,
            obj=obj, after={"status": obj.status, "mode": obj.mode},
        )
        return Response(RelocationRequestSerializer(obj).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        _require_feature("RELOCATION_REQUEST_ENABLED", "移库申请功能尚未启用。")
        if not (_is_manager(request.user) and request.user.has_perm("tasking.approve_relocation")):
            raise PermissionDenied("只有仓库管理员可以审核移库申请。")
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = RelocationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            approve_request(obj.pk, by_user=request.user, note=serializer.validated_data["note"])
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        obj.refresh_from_db()
        record_audit_event(
            action="relocation.request.approve", module="tasking", request=request,
            obj=obj, after={"status": obj.status, "task_id": obj.generated_task_id},
        )
        return Response(RelocationRequestSerializer(obj).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        if not (_is_manager(request.user) and request.user.has_perm("tasking.approve_relocation")):
            raise PermissionDenied("只有仓库管理员可以驳回移库申请。")
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = RelocationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reject_request(obj.pk, by_user=request.user, note=serializer.validated_data["note"])
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        obj.refresh_from_db()
        record_audit_event(
            action="relocation.request.reject", module="tasking", request=request,
            obj=obj, after={"status": obj.status, "review_note": obj.review_note},
        )
        return Response(RelocationRequestSerializer(obj).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        try:
            cancel_request(obj.pk, by_user=request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        obj.refresh_from_db()
        record_audit_event(
            action="relocation.request.cancel", module="tasking", request=request,
            obj=obj, after={"status": obj.status},
        )
        return Response(RelocationRequestSerializer(obj).data)


class RelocationDirectReleaseView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        _require_feature("RELOCATION_REQUEST_ENABLED", "移库申请功能尚未启用。")
        if not (_is_manager(request.user) and request.user.has_perm("tasking.manage_relocation")):
            raise PermissionDenied("只有仓库管理员可以直接下发移库任务。")
        serializer = RelocationRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            obj = _create_request_from_payload(
                serializer.validated_data,
                by_user=request.user,
                trigger=RelocationRequest.Trigger.DIRECT,
            )
            task = approve_request(obj.pk, by_user=request.user, note="经理直接下发")
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        record_audit_event(
            action="relocation.direct_release", module="tasking", request=request,
            obj=task, after={"status": task.status, "request_id": obj.pk},
        )
        return Response(RelocationTaskSerializer(task, context={"request": request}).data, status=status.HTTP_201_CREATED)


class RelocationOptionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        _require_feature("RELOCATION_REQUEST_ENABLED", "移库申请功能尚未启用。")
        if not (_is_operator(request.user) or _is_manager(request.user)):
            raise PermissionDenied("当前账号不能查询移库选项。")
        scope = _scope(request.user)
        warehouse_qs = Warehouse.objects.filter(is_active=True)
        if not scope.is_global:
            if scope.warehouse_ids:
                warehouse_qs = warehouse_qs.filter(pk__in=scope.warehouse_ids)
            elif scope.owner_ids:
                warehouse_qs = warehouse_qs.filter(
                    owner_bindings__owner_id__in=scope.owner_ids,
                    owner_bindings__is_active=True,
                )
            else:
                warehouse_qs = warehouse_qs.none()
        warehouse_qs = warehouse_qs.distinct().order_by("code")
        warehouse_id = request.query_params.get("warehouse_id") or scope.single_warehouse_id
        if not warehouse_id and warehouse_qs.count() == 1:
            warehouse_id = warehouse_qs.values_list("pk", flat=True).first()
        if warehouse_id and not warehouse_qs.filter(pk=warehouse_id).exists():
            raise PermissionDenied("所选仓库超出当前账号范围。")

        owner_qs = Owner.objects.none()
        if warehouse_id:
            owner_qs = Owner.objects.filter(
                warehouse_bindings__warehouse_id=warehouse_id,
                warehouse_bindings__is_active=True,
                is_active=True,
            )
            if scope.owner_ids:
                owner_qs = owner_qs.filter(pk__in=scope.owner_ids)
            owner_qs = owner_qs.distinct().order_by("code")
        owner_id = request.query_params.get("owner_id") or scope.single_owner_id
        if not owner_id and owner_qs.count() == 1:
            owner_id = owner_qs.values_list("pk", flat=True).first()
        if owner_id and not owner_qs.filter(pk=owner_id).exists():
            raise PermissionDenied("所选货主未绑定当前仓库或超出账号范围。")
        search = (request.query_params.get("search") or "").strip()
        inventory_qs = InventoryDetail.objects.none()
        if owner_id and warehouse_id:
            inventory_qs = InventoryDetail.objects.filter(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                is_active=True,
                available_qty__gt=0,
                allocated_qty=0,
                locked_qty=0,
                damaged_qty=0,
                location__is_active=True,
                location__is_disabled=False,
                location__is_frozen=False,
            ).select_related("product", "location", "container")
        if search:
            inventory_qs = inventory_qs.filter(
                Q(product__code__icontains=search)
                | Q(product__name__icontains=search)
                | Q(location__code__icontains=search)
                | Q(container__container_no__icontains=search)
            )
        locations = Location.objects.none()
        containers = Container.objects.none()
        if warehouse_id:
            locations = Location.objects.filter(
                warehouse_id=warehouse_id, is_active=True, is_disabled=False, is_frozen=False
            ).order_by("code")
            containers = Container.objects.filter(
                warehouse_id=warehouse_id, is_active=True
            )
            if owner_id:
                containers = containers.filter(
                    Q(scope=Container.Scope.PUBLIC) | Q(owner_id=owner_id)
                )
            else:
                containers = containers.filter(scope=Container.Scope.PUBLIC)
            containers = containers.select_related("location", "parent").order_by("container_no")
        return Response(
            {
                "owner_id": int(owner_id) if owner_id else None,
                "warehouse_id": int(warehouse_id) if warehouse_id else None,
                "owners": [
                    {"id": row.pk, "code": row.code, "name": row.name}
                    for row in owner_qs
                ],
                "warehouses": [
                    {"id": row.pk, "code": row.code, "name": row.name}
                    for row in warehouse_qs
                ],
                "inventory": [
                    {
                        "id": row.pk,
                        "product_code": row.product.code,
                        "product_name": row.product.name,
                        "location_id": row.location_id,
                        "location_code": row.location.code,
                        "container_id": row.container_id,
                        "container_no": getattr(row.container, "container_no", ""),
                        "available_qty": str(row.available_qty),
                        "batch_no": row.batch_no or "",
                        "serial_no": row.serial_no or "",
                    }
                    for row in inventory_qs.order_by("location__code", "product__code", "id")[:200]
                ],
                "locations": [{"id": row.pk, "code": row.code, "name": row.name or ""} for row in locations],
                "containers": [
                    {
                        "id": row.pk,
                        "container_no": row.container_no,
                        "location_id": row.location_id,
                        "location_code": getattr(row.location, "code", ""),
                        "parent_id": row.parent_id,
                    }
                    for row in containers
                ],
            }
        )


class RelocationTaskViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RelocationTaskSerializer

    def get_queryset(self):
        _require_feature("RELOCATION_PDA_ENABLED", "移库 PDA 功能尚未启用。")
        active_assignment = TaskAssignment.objects.filter(task_id=OuterRef("pk"), finished_at__isnull=True)
        qs = (
            WmsTask.objects.filter(task_type=WmsTask.TaskType.RELOC)
            .select_related("owner", "warehouse", "reloctaskextra")
            .prefetch_related(
                "assignments", "lines__product__packages", "lines__from_location", "lines__to_location",
                "lines__reloclineextra__from_container", "lines__reloclineextra__to_container",
            )
            .annotate(has_active_assignment=Exists(active_assignment))
        )
        qs = _scoped(qs, self.request.user)
        if _is_operator(self.request.user) and not _is_manager(self.request.user):
            qs = qs.filter(
                Q(assignments__assignee=self.request.user, assignments__finished_at__isnull=True)
                | Q(status=WmsTask.Status.RELEASED, has_active_assignment=False)
                | Q(created_by=self.request.user)
            ).distinct()
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(task_no__icontains=search)
                | Q(lines__product__name__icontains=search)
                | Q(lines__product__code__icontains=search)
            ).distinct()
        statuses = self.request.query_params.getlist("status")
        if statuses:
            qs = qs.filter(status__in=statuses)
        return qs.order_by("-priority", "id")

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        if not _is_operator(request.user):
            raise PermissionDenied("只有仓库操作员可以领取移库任务。")
        task = self.get_object()
        try:
            task_services.claim_task(task, by_user=request.user, allowed_wh_ids={task.warehouse_id})
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.claim", module="tasking", request=request,
            obj=task, after={"status": task.status},
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        if not _is_operator(request.user):
            raise PermissionDenied("只有仓库操作员可以开始移库任务。")
        task = self.get_object()
        if not TaskAssignment.objects.filter(task=task, assignee=request.user, finished_at__isnull=True).exists():
            raise PermissionDenied("请先领取移库任务。")
        try:
            task_services.task_start(request=request, task=task)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.reloctaskextra.execution_state = "WORKING"
        task.reloctaskextra.save(update_fields=["execution_state"])
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.start", module="tasking", request=request,
            obj=task, after={"status": task.status},
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        if not _is_operator(request.user):
            raise PermissionDenied("只有仓库操作员可以执行移库任务。")
        task = self.get_object()
        serializer = RelocationRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = record_relocation(task_id=task.pk, by_user=request.user, **serializer.validated_data)
        except RelocationIdempotencyConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        if result["posting_required"]:
            try:
                task_services._run_posting_handler(task.pk, by_user=request.user, note="移库自动过账")
            except Exception as exc:
                from allapp.tasking.models import RelocTaskExtra

                if isinstance(exc, DjangoValidationError):
                    WmsTask.objects.filter(pk=task.pk).update(
                        status=WmsTask.Status.IN_PROGRESS,
                        posting_status=WmsTask.PostingStatus.FAILED,
                    )
                    RelocTaskExtra.objects.filter(task_id=task.pk).update(
                        execution_state="EXCEPTION",
                        exception_code="POSTING_REVALIDATION",
                        exception_note=(str(exc) or "移库过账业务校验失败")[:200],
                        exception_by=request.user,
                    )
                else:
                    WmsTask.objects.filter(pk=task.pk).update(
                        posting_status=WmsTask.PostingStatus.FAILED
                    )
                    RelocTaskExtra.objects.filter(task_id=task.pk).update(
                        execution_state="POSTING_FAILED",
                        exception_code="POSTING_FAILED",
                        exception_note="库存过账发生系统错误，请安全重试。",
                        exception_by=request.user,
                    )
                _raise_posting_failure(exc)
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.record", module="tasking", request=request,
            obj=task,
            after={
                "line_id": serializer.validated_data["line_id"],
                "qty": serializer.validated_data["qty"],
                "idempotent": result["idempotent"],
                "posting_status": task.posting_status,
            },
        )
        return Response({"idempotent": result["idempotent"], "task": self.get_serializer(task).data})

    @action(detail=True, methods=["post"], url_path="report-exception")
    def report_exception_action(self, request, pk=None):
        task = self.get_object()
        serializer = RelocationExceptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            report_exception(task.pk, by_user=request.user, **serializer.validated_data)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.exception", module="tasking", request=request,
            obj=task, after=serializer.validated_data,
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        if not (_is_manager(request.user) and request.user.has_perm("tasking.manage_relocation")):
            raise PermissionDenied("只有仓库管理员可以恢复异常任务。")
        task = self.get_object()
        try:
            resume_task(task.pk, by_user=request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.resume", module="tasking", request=request,
            obj=task, after={"execution_state": task.reloctaskextra.execution_state},
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        if not (_is_manager(request.user) and request.user.has_perm("tasking.manage_relocation")):
            raise PermissionDenied("只有仓库管理员可以作废移库任务。")
        task = self.get_object()
        note = (request.data.get("note") or "").strip()
        if not note:
            raise ValidationError({"note": "请填写作废原因。"})
        try:
            void_task(task.pk, by_user=request.user, note=note)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.void", module="tasking", request=request,
            obj=task, after={"status": task.status, "note": note},
        )
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"], url_path="retry-posting")
    def retry_posting(self, request, pk=None):
        if not (_is_manager(request.user) and request.user.has_perm("tasking.manage_relocation")):
            raise PermissionDenied("只有仓库管理员可以重试移库过账。")
        task = self.get_object()
        if task.status != WmsTask.Status.COMPLETED or task.posting_status not in {
            WmsTask.PostingStatus.PENDING, WmsTask.PostingStatus.FAILED,
        }:
            raise ValidationError("当前移库任务不允许重试过账。")
        try:
            task_services._run_posting_handler(task.pk, by_user=request.user, note="移库过账重试")
        except Exception as exc:
            from allapp.tasking.models import RelocTaskExtra
            RelocTaskExtra.objects.filter(task_id=task.pk).update(execution_state="POSTING_FAILED")
            _raise_posting_failure(exc)
        task.refresh_from_db()
        record_audit_event(
            action="relocation.task.retry_posting", module="tasking", request=request,
            obj=task, after={"posting_status": task.posting_status},
        )
        return Response(self.get_serializer(task).data)
