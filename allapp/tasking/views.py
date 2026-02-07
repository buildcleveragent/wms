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

from typing import Any, Dict

from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from rest_framework import mixins, permissions, status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

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


# ------------------------- 通用 Mixin：按用户范围过滤 -------------------------
class OwnerWarehouseScopedQuerysetMixin:
    """默认按登录用户的 owner/warehouse 限定可见数据范围。

    - 若用户为 superuser：不限制
    - 若用户含同名字段（owner/warehouse）且有值：按等值过滤
    - 若无该字段或为空：不做过滤（留给上层权限策略控制）
    """

    def scope_queryset(self, qs: QuerySet):
        request = getattr(self, "request", None)
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return qs.none()
        if getattr(user, "is_superuser", False):
            return qs
        owner_id = getattr(user, "owner_id", None)
        wh_id = getattr(user, "warehouse_id", None)
        # 仅在字段存在时才过滤
        model = qs.model
        if owner_id and hasattr(model, "owner_id"):
            qs = qs.filter(owner_id=owner_id)
        if wh_id and hasattr(model, "warehouse_id"):
            qs = qs.filter(warehouse_id=wh_id)
        return qs

    def get_queryset(self):  # type: ignore[override]
        qs = super().get_queryset()  # type: ignore
        return self.scope_queryset(qs)


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
        res = self._svc_or_501("task_release", request=request, task=task)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="start")
    def start(self, request, pk=None):
        task = self.get_object()
        res = self._svc_or_501("task_start", request=request, task=task)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        task = self.get_object()
        res = self._svc_or_501("task_complete", request=request, task=task)
        if isinstance(res, Response):
            return res
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        task = self.get_object()
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
        user_id = request.data.get("user_id")
        assignee = get_object_or_404(User, pk=user_id)
        res = self._svc_or_501("assign_task", request=request, task=task, assignee=assignee)
        if isinstance(res, Response):
            return res
        return Response({"detail": "assigned"})

    @action(detail=True, methods=["post"], url_path="unassign")
    def unassign(self, request, pk=None):
        task = self.get_object()
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

