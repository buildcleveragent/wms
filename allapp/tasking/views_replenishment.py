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
from allapp.products.identifier_lookup import product_search_q
from allapp.tasking import services as task_services
from allapp.tasking.models import (
    ReplenishmentPolicy,
    ReplenishmentRequest,
    TaskAssignment,
    WmsTask,
)
from allapp.tasking.replenishment import (
    ReplenishmentIdempotencyConflict,
    approve_request,
    evaluate_policies,
    record_replenishment,
    reject_request,
)
from allapp.tasking.serializers_replenishment import (
    ReplenishmentEvaluateSerializer,
    ReplenishmentPolicySerializer,
    ReplenishmentRecordSerializer,
    ReplenishmentRequestCreateSerializer,
    ReplenishmentRequestSerializer,
    ReplenishmentReviewSerializer,
    ReplenishmentTaskSerializer,
)


def _scope(user):
    scope = AccessScope.for_user(user)
    if not scope.is_valid:
        raise PermissionDenied("当前账号没有有效的货主/仓库范围。")
    return scope


class ReplenishmentFeatureDisabled(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "replenishment_feature_disabled"


def _require_feature(setting_name, message):
    if not getattr(settings, setting_name, False):
        raise ReplenishmentFeatureDisabled(message)


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
    return scope.filter_queryset(
        qs, owner_field="owner_id", warehouse_field="warehouse_id"
    )


class ReplenishmentPolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReplenishmentPolicySerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = ReplenishmentPolicy.objects.select_related(
            "owner", "warehouse", "product", "target_location", "replenish_uom"
        ).order_by("warehouse_id", "owner_id", "product_id", "target_location_id")
        return _scoped(qs, self.request.user)

    def _require_manager(self):
        if not (
            _is_manager(self.request.user)
            and self.request.user.has_perm("tasking.manage_replenishment_policy")
        ):
            raise PermissionDenied("只有仓库管理员可以管理补货策略。")

    def _require_target_scope(self, serializer):
        instance = getattr(serializer, "instance", None)
        owner = serializer.validated_data.get("owner") or getattr(
            instance, "owner", None
        )
        warehouse = serializer.validated_data.get("warehouse") or getattr(
            instance, "warehouse", None
        )
        if (
            not owner
            or not warehouse
            or not _scope(self.request.user).allows(
                owner_id=owner.pk, warehouse_id=warehouse.pk
            )
        ):
            raise PermissionDenied("补货策略超出当前账号范围。")

    def perform_create(self, serializer):
        self._require_manager()
        self._require_target_scope(serializer)
        obj = serializer.save(
            created_by=self.request.user, updated_by=self.request.user
        )
        record_audit_event(
            action="replenishment.policy.create",
            module="tasking",
            request=self.request,
            obj=obj,
            after={"is_active": obj.is_active, "auto_release": obj.auto_release},
        )

    def perform_update(self, serializer):
        self._require_manager()
        self._require_target_scope(serializer)
        obj = serializer.save(updated_by=self.request.user)
        record_audit_event(
            action="replenishment.policy.update",
            module="tasking",
            request=self.request,
            obj=obj,
            after={"is_active": obj.is_active, "auto_release": obj.auto_release},
        )


class ReplenishmentRequestViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ReplenishmentRequest.objects.select_related(
            "owner",
            "warehouse",
            "product",
            "target_location",
            "created_by",
            "reviewed_by",
            "generated_task",
        )
        qs = _scoped(qs, self.request.user)
        if not _is_manager(self.request.user):
            qs = qs.filter(created_by=self.request.user)
        return qs.order_by("-created_at", "-id")

    def list(self, request):
        return Response(
            ReplenishmentRequestSerializer(self.get_queryset(), many=True).data
        )

    def retrieve(self, request, pk=None):
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(ReplenishmentRequestSerializer(obj).data)

    @transaction.atomic
    def create(self, request):
        _require_feature("REPLENISHMENT_MANUAL_ENABLED", "手工补货申请功能尚未启用。")
        scope = _scope(request.user)
        if not (
            _is_operator(request.user, scope)
            and request.user.has_perm("tasking.request_replenishment")
        ):
            raise PermissionDenied("当前账号不能提交补货申请。")
        serializer = ReplenishmentRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy = get_object_or_404(
            _scoped(ReplenishmentPolicy.objects.filter(is_active=True), request.user),
            pk=serializer.validated_data["policy_id"],
        )
        obj = ReplenishmentRequest.objects.create(
            owner=policy.owner,
            warehouse=policy.warehouse,
            product=policy.product,
            target_location=policy.target_location,
            requested_qty=serializer.validated_data["requested_qty"],
            reason=serializer.validated_data["reason"].strip(),
            created_by=request.user,
            updated_by=request.user,
        )
        record_audit_event(
            action="replenishment.request.create",
            module="tasking",
            request=request,
            obj=obj,
            after={"status": obj.status, "requested_qty": obj.requested_qty},
        )
        return Response(
            ReplenishmentRequestSerializer(obj).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        _require_feature("REPLENISHMENT_MANUAL_ENABLED", "手工补货申请功能尚未启用。")
        if not (
            _is_manager(request.user)
            and request.user.has_perm("tasking.approve_replenishment")
        ):
            raise PermissionDenied("只有仓库管理员可以审核补货申请。")
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = ReplenishmentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            approve_request(
                obj.pk, by_user=request.user, note=serializer.validated_data["note"]
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        obj.refresh_from_db()
        record_audit_event(
            action="replenishment.request.approve",
            module="tasking",
            request=request,
            obj=obj,
            before={"status": ReplenishmentRequest.Status.PENDING},
            after={"status": obj.status, "generated_task_id": obj.generated_task_id},
        )
        return Response(ReplenishmentRequestSerializer(obj).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        _require_feature("REPLENISHMENT_MANUAL_ENABLED", "手工补货申请功能尚未启用。")
        if not (
            _is_manager(request.user)
            and request.user.has_perm("tasking.approve_replenishment")
        ):
            raise PermissionDenied("只有仓库管理员可以审核补货申请。")
        obj = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = ReplenishmentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reject_request(
                obj.pk, by_user=request.user, note=serializer.validated_data["note"]
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        obj.refresh_from_db()
        record_audit_event(
            action="replenishment.request.reject",
            module="tasking",
            request=request,
            obj=obj,
            before={"status": ReplenishmentRequest.Status.PENDING},
            after={"status": obj.status, "review_note": obj.review_note},
        )
        return Response(ReplenishmentRequestSerializer(obj).data)


class ReplenishmentEvaluateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        _require_feature("REPLENISHMENT_MINMAX_ENABLED", "阈值补货评估功能尚未启用。")
        if not (
            _is_manager(request.user)
            and request.user.has_perm("tasking.evaluate_replenishment")
        ):
            raise PermissionDenied("只有仓库管理员可以触发补货策略评估。")
        serializer = ReplenishmentEvaluateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        scoped_policies = _scoped(
            ReplenishmentPolicy.objects.filter(is_active=True), request.user
        )
        if filters.get("policy_id"):
            scoped_policies = scoped_policies.filter(pk=filters["policy_id"])
        if filters.get("owner_id"):
            scoped_policies = scoped_policies.filter(owner_id=filters["owner_id"])
        if filters.get("warehouse_id"):
            scoped_policies = scoped_policies.filter(
                warehouse_id=filters["warehouse_id"]
            )
        if filters.get("product_id"):
            scoped_policies = scoped_policies.filter(product_id=filters["product_id"])
        results = evaluate_policies(
            policy_ids=list(scoped_policies.values_list("pk", flat=True)),
            by_user=request.user,
        )
        record_audit_event(
            action="replenishment.evaluate",
            module="tasking",
            request=request,
            metadata={
                "policy_ids": [row["policy_id"] for row in results],
                "created_task_ids": [
                    row["task_id"] for row in results if row["task_id"]
                ],
            },
        )
        return Response({"results": results})


class ReplenishmentTaskViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ReplenishmentTaskSerializer

    def get_queryset(self):
        _require_feature("REPLENISHMENT_PDA_ENABLED", "补货 PDA 功能尚未启用。")
        active_assignment = TaskAssignment.objects.filter(
            task_id=OuterRef("pk"), finished_at__isnull=True
        )
        qs = (
            WmsTask.objects.filter(task_type=WmsTask.TaskType.REPLEN)
            .select_related("owner", "warehouse", "replenishtaskextra")
            .prefetch_related(
                "assignments",
                "lines__product__packages",
                "lines__from_location",
                "lines__to_location",
            )
            .annotate(has_active_assignment=Exists(active_assignment))
        )
        qs = _scoped(qs, self.request.user)
        if _is_operator(self.request.user) and not _is_manager(self.request.user):
            qs = qs.filter(
                Q(
                    assignments__assignee=self.request.user,
                    assignments__finished_at__isnull=True,
                )
                | Q(status=WmsTask.Status.RELEASED, has_active_assignment=False)
                | Q(created_by=self.request.user)
            ).distinct()
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(task_no__icontains=search)
                | product_search_q(search, product_field="lines__product_id")
            ).distinct()
        statuses = self.request.query_params.getlist("status")
        if statuses:
            qs = qs.filter(status__in=statuses)
        return qs.order_by("-priority", "id")

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        if not _is_operator(request.user):
            raise PermissionDenied("只有仓库操作员可以领取补货任务。")
        task = self.get_object()
        try:
            task_services.claim_task(
                task, by_user=request.user, allowed_wh_ids={task.warehouse_id}
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        if not _is_operator(request.user):
            raise PermissionDenied("只有仓库操作员可以开始补货任务。")
        task = self.get_object()
        if not TaskAssignment.objects.filter(
            task=task, assignee=request.user, finished_at__isnull=True
        ).exists():
            raise PermissionDenied("请先领取补货任务后再开始。")
        try:
            task_services.task_start(request=request, task=task)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        if not _is_operator(request.user):
            raise PermissionDenied("只有仓库操作员可以执行补货任务。")
        task = self.get_object()
        serializer = ReplenishmentRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = record_replenishment(
                task_id=task.pk, by_user=request.user, **serializer.validated_data
            )
        except ReplenishmentIdempotencyConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        if result["posting_required"]:
            try:
                task_services._run_posting_handler(
                    task.pk, by_user=request.user, note="补货自动过账"
                )
            except DjangoValidationError as exc:
                raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        return Response(
            {"idempotent": result["idempotent"], "task": self.get_serializer(task).data}
        )

    @action(detail=True, methods=["post"], url_path="retry-posting")
    def retry_posting(self, request, pk=None):
        if not _is_manager(request.user):
            raise PermissionDenied("只有仓库管理员可以重试补货过账。")
        task = self.get_object()
        if task.status != WmsTask.Status.COMPLETED or task.posting_status not in {
            WmsTask.PostingStatus.PENDING,
            WmsTask.PostingStatus.FAILED,
        }:
            raise ValidationError("当前补货任务不允许重试过账。")
        try:
            task_services._run_posting_handler(
                task.pk, by_user=request.user, note="补货过账重试"
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        task.refresh_from_db()
        return Response(self.get_serializer(task).data)
