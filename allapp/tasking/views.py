# -*- coding: utf-8 -*-
"""
DRF views / viewsets for allapp.tasking

- 统一按用户 owner/warehouse 范围过滤（若用户模型含 owner/warehouse 字段，且非 superuser）
- 提供 Task / TaskLine 的标准 CRUD；Task 的 list=简要、retrieve=详情、其余=标准
- 提供任务生命周期动作：release / start / complete / cancel / assign / unassign
- 提供附属集合：/tasks/<id>/status-logs, /tasks/<id>/scan-logs, /tasks/<id>/assignments
- 提供扫码入口：/tasks/<id>/scan（仅记录扫描日志；业务过账建议放在 services 层 post_scan）

URL 参考（在 urls.py 中注册 router：见文件底部注释）
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from allapp.outbound.authz import (
    assisted_order_source_ids,
    get_assisted_order_for_task,
    is_assisted_operator,
)

from .models import (
    WmsTask, WmsTaskLine, TaskAssignment, TaskStatusLog, TaskScanLog,
)
from .serializers import (
    WmsTaskSerializer, WmsTaskBriefSerializer, WmsTaskDetailSerializer,
    WmsTaskLineSerializer,
    TaskAssignmentSerializer, TaskStatusLogSerializer, TaskScanLogSerializer,
)

# 如果已经实现了服务层，这里统一集中引用；若尚未实现，可先注释这些调用。
try:
    from . import services as task_svc  # noqa: F401
except Exception:  # pragma: no cover - 允许在服务层未就绪时先运行基本 CRUD
    task_svc = None  # type: ignore

User = get_user_model()
logger = logging.getLogger(__name__)


# ------------------------- 通用 Mixin：按用户范围过滤 -------------------------
class OwnerWarehouseScopedQuerysetMixin:
    """Resolve owner/warehouse scope for task resources, with legacy shadow mode."""

    scope_fields = {
        WmsTask: ("owner_id", "warehouse_id", ""),
        WmsTaskLine: ("task__owner_id", "task__warehouse_id", "task__"),
        TaskAssignment: ("task__owner_id", "task__warehouse_id", "task__"),
        TaskStatusLog: ("task__owner_id", "task__warehouse_id", "task__"),
        TaskScanLog: ("task__owner_id", "task__warehouse_id", "task__"),
    }

    def _resolved_scope(self, qs: QuerySet, user):
        if not user or not user.is_authenticated:
            return qs.none(), "unauthenticated"
        if getattr(user, "is_superuser", False):
            return qs, "superuser"

        fields = self.scope_fields.get(qs.model)
        if fields is None:
            # Unknown tasking resources fail closed instead of silently becoming global.
            return qs.none(), "unsupported_model"

        owner_field, warehouse_field, task_prefix = fields
        owner_id = getattr(user, "owner_id", None)
        warehouse_id = getattr(user, "warehouse_id", None)
        if owner_id:
            scoped = qs.filter(**{owner_field: owner_id})
            if warehouse_id:
                scoped = scoped.filter(**{warehouse_field: warehouse_id})
                return scoped, "owner_and_warehouse"
            return scoped, "owner"
        model_view_permission = (
            f"{qs.model._meta.app_label}.view_{qs.model._meta.model_name}"
        )
        if warehouse_id and user.has_perm(model_view_permission):
            return qs.filter(**{warehouse_field: warehouse_id}), "warehouse"

        if warehouse_id and is_assisted_operator(user):
            source_ids = assisted_order_source_ids(warehouse_id=warehouse_id)
            return qs.filter(
                **{
                    warehouse_field: warehouse_id,
                    f"{task_prefix}source_model__in": (
                        "outboundorder",
                        "OutboundOrder",
                    ),
                    f"{task_prefix}source_pk__in": source_ids,
                }
            ), "warehouse_assisted"
        if warehouse_id:
            return qs.none(), "warehouse_without_view_or_assisted_permission"
        return qs.none(), "missing_owner_and_warehouse"

    def _log_shadow_denial(self, qs: QuerySet, scoped: QuerySet, reason: str, user) -> None:
        try:
            would_deny = qs.exclude(pk__in=scoped.values("pk")).exists()
        except Exception:
            # A custom queryset should not make shadow mode break the legacy endpoint.
            logger.exception(
                "tasking.authz.shadow_check_failed user_id=%s model=%s reason=%s",
                getattr(user, "pk", None),
                qs.model._meta.label_lower,
                reason,
            )
            return
        if not would_deny:
            return
        request = getattr(self, "request", None)
        logger.warning(
            "tasking.authz.would_deny user_id=%s model=%s method=%s path=%s action=%s scope=%s",
            getattr(user, "pk", None),
            qs.model._meta.label_lower,
            getattr(request, "method", ""),
            getattr(request, "path", ""),
            getattr(self, "action", ""),
            reason,
        )

    def scope_queryset(self, qs: QuerySet):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        scoped, reason = self._resolved_scope(qs, user)
        if reason in {"superuser", "unauthenticated"}:
            return scoped
        if is_assisted_operator(user):
            return scoped

        mode = str(
            getattr(settings, "OUTBOUND_LEGACY_AUTHZ_MODE", "enforce")
        ).strip().lower()
        if mode == "shadow":
            self._log_shadow_denial(qs, scoped, reason, user)
            # Shadow compatibility is only for historical tasks.  Newly
            # introduced assisted-outbound tasks remain strictly isolated.
            _, _, task_prefix = self.scope_fields[qs.model]
            assisted_q = Q(
                **{
                    f"{task_prefix}source_model__in": (
                        "outboundorder",
                        "OutboundOrder",
                    ),
                    f"{task_prefix}source_pk__in": assisted_order_source_ids(),
                }
            )
            return qs.filter(~assisted_q | Q(pk__in=scoped.values("pk")))
        return scoped

    def get_queryset(self):  # type: ignore[override]
        qs = super().get_queryset()  # type: ignore
        return self.scope_queryset(qs)

    def _legacy_mode(self) -> str:
        return str(
            getattr(settings, "OUTBOUND_LEGACY_AUTHZ_MODE", "enforce")
        ).strip().lower()

    def _write_scope(self, model, validated_data, instance=None):
        if model is WmsTask:
            owner = validated_data.get("owner", getattr(instance, "owner", None))
            warehouse = validated_data.get(
                "warehouse", getattr(instance, "warehouse", None)
            )
        elif model is WmsTaskLine:
            task = validated_data.get("task", getattr(instance, "task", None))
            owner = getattr(task, "owner", None)
            warehouse = getattr(task, "warehouse", None)
        else:
            return None, None
        return getattr(owner, "pk", None), getattr(warehouse, "pk", None)

    def _check_generic_write(self, *, operation, model, validated_data=None, instance=None):
        user = getattr(getattr(self, "request", None), "user", None)
        if not user or not user.is_authenticated:
            return False, "unauthenticated"
        if user.is_superuser:
            return True, "superuser"

        permission = f"{model._meta.app_label}.{operation}_{model._meta.model_name}"
        if not user.has_perm(permission):
            return False, f"missing_{operation}_permission"

        owner_id, warehouse_id = self._write_scope(
            model,
            validated_data or {},
            instance=instance,
        )
        user_owner_id = getattr(user, "owner_id", None)
        user_warehouse_id = getattr(user, "warehouse_id", None)
        if user_owner_id:
            if owner_id != user_owner_id:
                return False, "owner_mismatch"
            if user_warehouse_id and warehouse_id != user_warehouse_id:
                return False, "warehouse_mismatch"
            return True, "owner_scope"
        if user_warehouse_id:
            return (
                (True, "warehouse_scope")
                if warehouse_id == user_warehouse_id
                else (False, "warehouse_mismatch")
            )
        return False, "missing_owner_and_warehouse"

    def _gate_generic_write(self, *, operation, model, validated_data=None, instance=None):
        allowed, reason = self._check_generic_write(
            operation=operation,
            model=model,
            validated_data=validated_data,
            instance=instance,
        )
        if allowed:
            return
        user = getattr(getattr(self, "request", None), "user", None)
        target_task = None
        if model is WmsTask:
            target_task = instance
        elif model is WmsTaskLine:
            target_task = (validated_data or {}).get(
                "task", getattr(instance, "task", None)
            )
        is_assisted = bool(
            target_task is not None
            and get_assisted_order_for_task(target_task) is not None
        )
        if self._legacy_mode() == "shadow" and not is_assisted:
            logger.warning(
                "tasking.authz.would_deny user_id=%s model=%s action=%s reason=%s",
                getattr(user, "pk", None),
                model._meta.label_lower,
                operation,
                reason,
            )
            return
        raise PermissionDenied("无权在当前货主/仓库范围内执行该操作。")

    def _gate_task_action(self, task, *, permission):
        user = getattr(getattr(self, "request", None), "user", None)
        if user and user.is_authenticated and user.is_superuser:
            return
        scoped, reason = self._resolved_scope(
            WmsTask.objects.filter(pk=task.pk), user
        )
        allowed = bool(
            user
            and user.is_authenticated
            and user.has_perm(permission)
            and scoped.exists()
        )
        if allowed:
            return
        is_assisted = get_assisted_order_for_task(task) is not None
        if self._legacy_mode() == "shadow" and not is_assisted:
            logger.warning(
                "tasking.authz.would_deny user_id=%s model=tasking.wmstask "
                "action=%s reason=%s",
                getattr(user, "pk", None),
                getattr(self, "action", "task_action"),
                "missing_permission" if user and not user.has_perm(permission) else reason,
            )
            return
        raise PermissionDenied("无权在当前货主/仓库范围内执行该任务操作。")

    def perform_create(self, serializer):
        model = serializer.Meta.model
        self._gate_generic_write(
            operation="add",
            model=model,
            validated_data=serializer.validated_data,
        )
        serializer.save()

    def perform_update(self, serializer):
        model = serializer.Meta.model
        self._gate_generic_write(
            operation="change",
            model=model,
            validated_data=serializer.validated_data,
            instance=serializer.instance,
        )
        serializer.save()

    def perform_destroy(self, instance):
        self._gate_generic_write(
            operation="delete",
            model=type(instance),
            instance=instance,
        )
        instance.delete()


# ------------------------- ViewSets -------------------------
class WmsTaskViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ModelViewSet):
    """任务头

    list -> 简要（WmsTaskBriefSerializer）
    retrieve -> 详情（WmsTaskDetailSerializer）
    create/update/partial_update -> 标准（WmsTaskSerializer）
    """

    permission_classes = [permissions.IsAuthenticated]
    queryset = (
        WmsTask.objects.select_related("owner", "warehouse", "created_by", "updated_by")
        .all()
    )

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "owner": ["exact"],
        "warehouse": ["exact"],
        "task_type": ["exact", "in"],
        "status": ["exact", "in"],
        "priority": ["exact", "in"],
        "task_no": ["exact", "icontains"],
        "task_group_no": ["exact", "icontains"],
        "ref_no": ["exact", "icontains"],
        "source_app": ["exact"],
        "source_model": ["exact"],
        "released_at": ["gte", "lte", "date"],
        "planned_start": ["gte", "lte", "date"],
        "planned_end": ["gte", "lte", "date"],
        "started_at": ["gte", "lte", "date"],
        "finished_at": ["gte", "lte", "date"],
        "created_at": ["gte", "lte", "date"],
    }
    search_fields = ["task_no", "task_group_no", "ref_no", "remark"]
    ordering_fields = [
        "id", "task_no", "task_type", "status", "priority",
        "released_at", "planned_start", "planned_end", "started_at", "finished_at", "created_at",
    ]
    ordering = ["-created_at"]

    def get_serializer_class(self):  # type: ignore[override]
        if self.action == "list":
            return WmsTaskBriefSerializer
        if self.action == "retrieve":
            return WmsTaskDetailSerializer
        return WmsTaskSerializer

    # -------- 任务生命周期动作（调用服务层；如未实现则返回 501） --------
    def _svc_or_501(self, fn_name: str, *args, **kwargs):
        if task_svc is None:
            return Response({"detail": f"services.{fn_name} 尚未实现"}, status=status.HTTP_501_NOT_IMPLEMENTED)
        fn = getattr(task_svc, fn_name, None)
        if not callable(fn):
            return Response({"detail": f"services.{fn_name} 未找到"}, status=status.HTTP_501_NOT_IMPLEMENTED)
        return fn(*args, **kwargs)

    @action(detail=True, methods=["post"], url_path="release")
    def release(self, request, pk=None):
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.taskconfirm_as_wh_manager"
        )
        res = self._svc_or_501("task_release", request=request, task=task)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.claim_task_as_wh_operator"
        )
        res = self._svc_or_501("task_start", request=request, task=task)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.claim_task_as_wh_operator"
        )
        res = self._svc_or_501("task_complete", request=request, task=task)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.claim_task_as_wh_operator"
        )
        reason = request.data.get("reason")
        res = self._svc_or_501("task_cancel", request=request, task=task, reason=reason)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        """指派任务给用户（body: {"user_id": <id>}）。"""
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.taskconfirm_as_wh_manager"
        )
        user_id = request.data.get("user_id")
        assignee = get_object_or_404(User, pk=user_id)
        res = self._svc_or_501("assign_task", request=request, task=task, assignee=assignee)
        if isinstance(res, Response):
            return res
        return Response({"detail": "assigned"})

    @action(detail=True, methods=["post"], url_path="unassign")
    def unassign(self, request, pk=None):
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.taskconfirm_as_wh_manager"
        )
        user_id = request.data.get("user_id")
        assignee = get_object_or_404(User, pk=user_id) if user_id else None
        res = self._svc_or_501("unassign_task", request=request, task=task, assignee=assignee)
        if isinstance(res, Response):
            return res
        return Response({"detail": "unassigned"})

    # -------- 附属集合 --------
    @action(detail=True, methods=["get"], url_path="status-logs")
    def status_logs(self, request, pk=None):
        task = self.get_object()
        qs = TaskStatusLog.objects.filter(task=task).order_by("-changed_at")
        page = self.paginate_queryset(qs)
        ser = TaskStatusLogSerializer(page or qs, many=True)
        return self.get_paginated_response(ser.data) if page is not None else Response(ser.data)

    @action(detail=True, methods=["get"], url_path="scan-logs")
    def scan_logs(self, request, pk=None):
        task = self.get_object()
        qs = TaskScanLog.objects.filter(task=task).order_by("-created_at", "-id")
        page = self.paginate_queryset(qs)
        ser = TaskScanLogSerializer(page or qs, many=True)
        return self.get_paginated_response(ser.data) if page is not None else Response(ser.data)

    @action(detail=True, methods=["get"], url_path="assignments")
    def assignments(self, request, pk=None):
        task = self.get_object()
        qs = TaskAssignment.objects.filter(task=task).order_by("-accepted_at", "-id")
        page = self.paginate_queryset(qs)
        ser = TaskAssignmentSerializer(page or qs, many=True)
        return self.get_paginated_response(ser.data) if page is not None else Response(ser.data)

    # -------- 扫码入口（记录日志；业务处理建议在 services.post_scan 完成） --------
    @action(detail=True, methods=["post"], url_path="scan")
    def scan(self, request, pk=None):
        task = self.get_object()
        self._gate_task_action(
            task, permission="tasking.claim_task_as_wh_operator"
        )
        # 如果实现了 services.post_scan，优先调用以保证幂等与形态约束
        if task_svc and hasattr(task_svc, "post_scan"):
            res = task_svc.post_scan(request=request, task=task)
            if isinstance(res, Response):
                return res
            return Response(res or {"detail": "scanned"})
        # 回退：仅创建一条扫描日志
        payload = dict(request.data)
        payload.setdefault("task", task.pk)
        ser = TaskScanLogSerializer(data=payload, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)


class WmsTaskLineViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WmsTaskLineSerializer
    queryset = (
        WmsTaskLine.objects.select_related("task", "product", "from_location", "to_location")
        .all()
    )

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        "task": ["exact"],
        "product": ["exact"],
        "from_location": ["exact"],
        "to_location": ["exact"],
    }
    search_fields = ["remark", "rule_key"]
    ordering_fields = ["id", "task", "product", "qty_plan", "qty_done"]
    ordering = ["id"]

    @action(detail=True, methods=["post"], url_path="bind")
    def bind(self, request, pk=None):
        line = self.get_object()
        self._gate_generic_write(
            operation="change", model=WmsTaskLine, instance=line
        )
        # 绑定外部对象（GenericForeignKey）
        content_type_id = request.data.get("content_type_id")
        object_id = request.data.get("object_id")
        partial = {"bound_content_type": content_type_id, "bound_object_id": object_id}
        ser = self.get_serializer(line, data=partial, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    @action(detail=True, methods=["post"], url_path="unbind")
    def unbind(self, request, pk=None):
        line = self.get_object()
        self._gate_generic_write(
            operation="change", model=WmsTaskLine, instance=line
        )
        partial = {"bound_content_type": None, "bound_object_id": None}
        ser = self.get_serializer(line, data=partial, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


# ---- 附属资源（如需要单独路由暴露） ----
class TaskAssignmentViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TaskAssignmentSerializer
    queryset = TaskAssignment.objects.select_related("task", "assignee").all()
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = {"task": ["exact"], "assignee": ["exact"]}
    ordering_fields = ["accepted_at", "finished_at", "id"]
    ordering = ["-accepted_at", "-id"]


class TaskStatusLogViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TaskStatusLogSerializer
    queryset = TaskStatusLog.objects.select_related("task", "changed_by").all()
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = {"task": ["exact"], "old_status": ["exact"], "new_status": ["exact"]}
    ordering_fields = ["changed_at", "id"]
    ordering = ["-changed_at", "-id"]


class TaskScanLogViewSet(OwnerWarehouseScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TaskScanLogSerializer
    queryset = TaskScanLog.objects.select_related("task", "task_line", "product", "location", "by_user").all()
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = {"task": ["exact"], "task_line": ["exact"], "product": ["exact"], "location": ["exact"]}
    ordering_fields = ["created_at", "id"]
    ordering = ["-created_at", "-id"]
