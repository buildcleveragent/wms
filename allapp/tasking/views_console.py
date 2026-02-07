# ===============================
# FILE: allapp/tasking/views_console.py
# 说明：基于 CBV 的任务列表/详情与原子操作端点。严格以基线代码为准，不自创字段。
# ===============================
from typing import Any, Dict, Iterable, List, Sequence
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.http import (
    HttpRequest,
    HttpResponseForbidden,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from .models import TaskAssignment, WmsTask, WmsTaskLine
from allapp.inventory import services as inv_services
from allapp.tasking import services as task_services


TASK_TYPE_SLUG_TO_URL = {
    "receive": "tasking_console:task_receive_list",
    "putaway": "tasking_console:task_putaway_list",
    "pick": "tasking_console:task_pick_list",
    "review": "tasking_console:task_review_list",
    "pack": "tasking_console:task_pack_list",
    "dispatch": "tasking_console:task_dispatch_list",
}


def _is_wh_operator(user) -> bool:
    return getattr(user, "is_superuser", False) or user.has_perm(
        "tasking.claim_task_as_wh_operator"
    )


def _is_wh_manager(user) -> bool:
    return getattr(user, "is_superuser", False) or user.has_perm(
        "tasking.taskconfirm_as_wh_manager"
    )


class TaskListView(LoginRequiredMixin, ListView):
    """任务列表（支持按 task_type/status 过滤 + 关键字模糊）。
    URL 示例：/tasking/console/tasks/?task_type=PICK&status=READY&q=SJ-
    """
    model = WmsTask
    template_name = "console/task_list.html"
    context_object_name = "tasks"
    paginate_by = 50

    filtered_queryset = None

    def get_base_queryset(self):
        return WmsTask.objects.select_related("warehouse", "owner").all().order_by("-id")

    def filter_queryset(self, qs):
        task_type = self.request.GET.get("task_type")
        status = self.request.GET.get("status")
        q = self.request.GET.get("q")
        if task_type:
            qs = qs.filter(task_type=task_type)
        if status:
            qs = qs.filter(status=status)
        if q:
            # 仅尝试 task_no 的模糊匹配，避免访问不存在字段
            try:
                qs = qs.filter(task_no__icontains=q)
            except Exception:
                pass
        return qs

    def get_queryset(self):
        qs = self.get_base_queryset()
        qs = self.filter_queryset(qs)
        self.filtered_queryset = qs
        return qs


class TaskTypeListView(TaskListView):
    """按任务类型拆分的列表视图（收货/上架/拣货/复核/打包/发运）。"""

    task_type_value: str | None = None
    origin_slug: str | None = None
    page_title: str | None = None
    intro_text: str = ""
    status_choices: Sequence[WmsTask.Status] = (
        WmsTask.Status.RELEASED,
        WmsTask.Status.READY,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses: Sequence[WmsTask.Status] = (
        WmsTask.Status.READY,
        WmsTask.Status.IN_PROGRESS,
    )
    paginate_by = 40
    template_name = "console/task_type_list.html"

    def get_base_queryset(self):
        qs = (
            WmsTask.objects.select_related("warehouse", "owner", "created_by", "updated_by")
            .all()
            .order_by("priority", "id")
        )
        if self.task_type_value:
            qs = qs.filter(task_type=self.task_type_value)
        return qs

    def _as_values(self, items: Iterable[WmsTask.Status | str]) -> List[str]:
        values: List[str] = []
        for item in items:
            if isinstance(item, WmsTask.Status):
                values.append(item.value)
            else:
                values.append(str(item))
        return values

    def get_status_filter(self) -> List[str]:
        allowed = set(self._as_values(self.status_choices))
        selected = [s for s in self.request.GET.getlist("status") if s in allowed]
        if selected:
            return selected
        defaults = [s for s in self._as_values(self.default_statuses) if s in allowed]
        return defaults

    def filter_queryset(self, qs):
        self.selected_statuses = []
        self.selected_warehouse = None
        self.search_query = ""

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(task_no__icontains=q)
        warehouse = self.request.GET.get("warehouse")
        if warehouse:
            qs = qs.filter(warehouse_id=warehouse)
        self.status_base_queryset = qs

        statuses = self.get_status_filter()
        if statuses:
            qs = qs.filter(status__in=statuses)

        self.selected_statuses = statuses
        self.selected_warehouse = warehouse
        self.search_query = q
        return qs

    def get_task_type_label(self) -> str:
        if self.task_type_value:
            try:
                return WmsTask.TaskType(self.task_type_value).label
            except ValueError:
                pass
        return self.page_title or "任务"

    def get_origin_slug(self) -> str:
        if self.origin_slug:
            return self.origin_slug
        if self.task_type_value:
            return self.task_type_value.lower()
        return "tasks"

    def _status_summary(self) -> List[Dict[str, Any]]:
        queryset = getattr(self, "status_base_queryset", None) or WmsTask.objects.none()
        counts = {
            row["status"]: row["cnt"]
            for row in queryset.values("status").annotate(cnt=Count("id"))
        }
        summary: List[Dict[str, Any]] = []
        for status in self.status_choices:
            value = status.value if isinstance(status, WmsTask.Status) else str(status)
            label = status.label if isinstance(status, WmsTask.Status) else str(status)
            summary.append({
                "value": value,
                "label": label,
                "count": counts.get(value, 0),
            })
        return summary

    def _get_warehouse_options(self) -> Iterable[Dict[str, Any]]:
        base_qs = WmsTask.objects
        if self.task_type_value:
            base_qs = base_qs.filter(task_type=self.task_type_value)
        return (
            base_qs.order_by("warehouse__name")
            .values("warehouse_id", "warehouse__name")
            .distinct()
        )

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        queryset = self.filtered_queryset or WmsTask.objects.none()
        params = self.request.GET.copy()
        if "page" in params:
            params.pop("page")
        querystring = params.urlencode()
        ctx.update(
            {
                "page_title": self.page_title or f"{self.get_task_type_label()}列表",
                "task_type_label": self.get_task_type_label(),
                "status_options": self._status_summary(),
                "selected_statuses": set(self.selected_statuses),
                "selected_warehouse": self.selected_warehouse,
                "search_query": self.search_query,
                "total_count": queryset.count(),
                "intro_text": self.intro_text,
                "origin_slug": self.get_origin_slug(),
                "warehouse_options": list(self._get_warehouse_options()),
                "querystring": querystring,
            }
        )
        return ctx


class ReceiveTaskListView(TaskTypeListView):
    task_type_value = WmsTask.TaskType.RECEIVE
    page_title = "收货任务"
    intro_text = "来自 ASN/收货登记的在仓作业，关注待收货与执行中的任务。"
    status_choices = (
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses = (
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    )


class PutawayTaskListView(TaskTypeListView):
    task_type_value = WmsTask.TaskType.PUTAWAY
    page_title = "上架任务"
    intro_text = "收货之后的二次搬运任务，跟踪待上架与执行中的波段。"
    status_choices = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    )


class PickTaskBoardView(TaskTypeListView):
    task_type_value = WmsTask.TaskType.PICK
    page_title = "拣货任务"
    intro_text = "波次/订单拣货执行监控，可快速定位待拣与执行中任务。"
    status_choices = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    )


class ReviewTaskListView(TaskTypeListView):
    task_type_value = WmsTask.TaskType.REVIEW
    page_title = "复核任务"
    intro_text = "出库复核/理货节点，关注待复核和执行中的波段。"
    status_choices = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    )


class PackTaskListView(TaskTypeListView):
    task_type_value = WmsTask.TaskType.PACK
    page_title = "打包任务"
    intro_text = "打包/装箱作业看板，监控任务推进情况。"
    status_choices = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    )


class DispatchTaskListView(TaskTypeListView):
    task_type_value = WmsTask.TaskType.DISPATCH
    page_title = "发运任务"
    intro_text = "波次出库之后的发运交接，关注待发运与运输中的任务。"
    status_choices = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
        WmsTask.Status.COMPLETED,
    )
    default_statuses = (
        WmsTask.Status.READY,
        WmsTask.Status.RELEASED,
        WmsTask.Status.IN_PROGRESS,
    )


class TaskLineWorkListView(LoginRequiredMixin, TemplateView):
    """展示仓库作业员“我的任务行”和“可抢单”列表。"""

    template_name = "console/task_line_work_list.html"

    def dispatch(self, request: HttpRequest, *args, **kwargs):  # type: ignore[override]
        if not (_is_wh_operator(request.user) or _is_wh_manager(request.user)):
            return HttpResponseForbidden("需要仓库操作权限")
        return super().dispatch(request, *args, **kwargs)

    def _base_queryset(self):
        return (
            WmsTaskLine.objects.select_related(
                "task",
                "task__owner",
                "task__warehouse",
                "product",
                "from_location",
                "to_location",
            ).all()
        )

    def _filter_queryset(self, qs):
        task_type = (self.request.GET.get("task_type") or "").strip().upper()
        search = (self.request.GET.get("q") or "").strip()

        self.selected_task_type = task_type
        self.search_query = search

        if task_type:
            qs = qs.filter(task__task_type=task_type)
        if search:
            qs = qs.filter(
                Q(task__task_no__icontains=search)
                | Q(product__name__icontains=search)
                | Q(product__sku__icontains=search)
            )
        return qs

    def _annotate_scope(self, qs):
        user = self.request.user

        active_line_any = TaskAssignment.objects.filter(
            line_id=OuterRef("pk"), finished_at__isnull=True
        )
        active_head_any = TaskAssignment.objects.filter(
            task_id=OuterRef("task_id"),
            line__isnull=True,
            finished_at__isnull=True,
        )

        return qs.annotate(
            _mine_line=Exists(active_line_any.filter(assignee=user)),
            _mine_head=Exists(active_head_any.filter(assignee=user)),
            _has_line=Exists(active_line_any),
            _has_head=Exists(active_head_any),
        )

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:  # type: ignore[override]
        ctx = super().get_context_data(**kwargs)
        qs = self._base_queryset()
        qs = self._filter_queryset(qs)
        qs = self._annotate_scope(qs)

        cond_mine = Q(_mine_line=True) | (Q(_mine_head=True) & Q(_has_line=False))
        cond_pool = (
            Q(task__status__in=[WmsTask.Status.RELEASED, WmsTask.Status.IN_PROGRESS])
            & Q(_has_head=False)
            & Q(_has_line=False)
        )

        my_lines = list(
            qs.filter(cond_mine).order_by("-_mine_line", "-_mine_head", "-id")
        )
        pool_lines = list(qs.filter(cond_pool).order_by("-id"))

        ctx.update(
            {
                "my_lines": my_lines,
                "pool_lines": pool_lines,
                "task_type_choices": WmsTask.TaskType.choices,
                "selected_task_type": getattr(self, "selected_task_type", ""),
                "search_query": getattr(self, "search_query", ""),
            }
        )
        return ctx


class TaskLineWorkView(LoginRequiredMixin, View):
    """点击任务行即尝试认领，随后跳转到 Admin 录入界面。"""

    http_method_names = ["get", "post"]

    def dispatch(self, request: HttpRequest, *args, **kwargs):  # type: ignore[override]
        if not (_is_wh_operator(request.user) or _is_wh_manager(request.user)):
            return HttpResponseForbidden("需要仓库操作权限")
        return super().dispatch(request, *args, **kwargs)

    def _redirect_back(self):
        return redirect("tasking_console:task_line_work_list")

    def _ensure_assignment(self, *, line: WmsTaskLine, user) -> bool:
        """返回 True 表示已经持有/成功认领该行。"""

        task = line.task

        active_line_qs = TaskAssignment.objects.select_for_update().filter(
            line=line, finished_at__isnull=True
        )
        active_head_qs = TaskAssignment.objects.select_for_update().filter(
            task=task, line__isnull=True, finished_at__isnull=True
        )

        mine_line = active_line_qs.filter(assignee=user).exists()
        mine_head = active_head_qs.filter(assignee=user).exists()
        has_line = active_line_qs.exists()
        has_head = active_head_qs.exists()

        if mine_line:
            return True

        need_claim = False
        if mine_head and not has_line:
            need_claim = True
        else:
            if has_line or has_head:
                return False
            allowed_status = {
                WmsTask.Status.RELEASED,
                getattr(WmsTask.Status, "ASSIGNED", WmsTask.Status.RELEASED),
                WmsTask.Status.IN_PROGRESS,
            }
            if task.status not in allowed_status:
                return False
            need_claim = True

        if not need_claim:
            return True

        now = timezone.now()
        try:
            ta, _created = TaskAssignment.objects.get_or_create(
                task=task,
                line=line,
                assignee=user,
                defaults={"accepted_at": now},
            )
        except TaskAssignment.MultipleObjectsReturned:
            ta = (
                TaskAssignment.objects.select_for_update()
                .filter(task=task, line=line, assignee=user)
                .order_by("-id")
                .first()
            )

        if ta is None:
            return False

        update_fields = []
        if ta.accepted_at is None:
            ta.accepted_at = now
            update_fields.append("accepted_at")
        if ta.finished_at is not None:
            ta.finished_at = None
            update_fields.append("finished_at")
        if update_fields:
            ta.save(update_fields=update_fields)

        if task.status == WmsTask.Status.RELEASED:
            next_status = getattr(WmsTask.Status, "ASSIGNED", None)
            if next_status and next_status != WmsTask.Status.RELEASED:
                task._allow_status_write = True
                task.status = next_status
                task.save(update_fields=["status"])

        return True

    def _handle(self, request: HttpRequest, pk: int):
        try:
            with transaction.atomic():
                line = (
                    WmsTaskLine.objects.select_for_update()
                    .select_related("task", "task__owner", "task__warehouse")
                    .get(pk=pk)
                )

                ok = self._ensure_assignment(line=line, user=request.user)
        except WmsTaskLine.DoesNotExist:
            messages.error(request, "任务行不存在或已被删除。")
            return self._redirect_back()

        if not ok:
            messages.error(request, "该任务行当前不可抢单，可能已被他人占用。")
            return self._redirect_back()

        try:
            change_url = reverse("admin:tasking_wmstaskline_change", args=[line.pk])
        except NoReverseMatch:
            messages.info(request, "已认领任务行，但未找到录入界面。")
            return self._redirect_back()

        messages.success(request, "已就绪，请录入任务行。")
        if "?" in change_url:
            return HttpResponseRedirect(f"{change_url}&from=console")
        return HttpResponseRedirect(f"{change_url}?from=console")

    def get(self, request: HttpRequest, pk: int):  # type: ignore[override]
        return self._handle(request, pk)

    def post(self, request: HttpRequest, pk: int):  # type: ignore[override]
        return self._handle(request, pk)


class TaskDetailView(LoginRequiredMixin, DetailView):
    """任务详情。仅展示基本信息 + 明细行清单。"""
    model = WmsTask
    template_name = "console/task_detail.html"
    context_object_name = "task"

    def get_queryset(self):
        return WmsTask.objects.select_related(
            "owner", "warehouse", "created_by", "updated_by"
        )

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        lines = (
            WmsTaskLine.objects.filter(task_id=self.object.id)
            .select_related("product", "from_location", "to_location")
            .order_by("id")
        )
        ctx["lines"] = lines
        origin = self.request.GET.get("origin")
        back_url = None
        if origin in TASK_TYPE_SLUG_TO_URL:
            try:
                back_url = reverse(TASK_TYPE_SLUG_TO_URL[origin])
            except Exception:
                back_url = None
        if back_url is None:
            back_url = reverse("tasking_console:task_list")
        ctx["back_url"] = back_url
        ctx["task_type_label"] = self.object.get_task_type_display()
        return ctx


class _JsonMixin:
    def _json_body(self, request: HttpRequest) -> Dict[str, Any]:
        try:
            data = json.loads(request.body.decode("utf-8")) if request.body else {}
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data
        except Exception as e:
            raise ValueError(f"Bad JSON: {e}")

    def _ok(self, data: Dict[str, Any] | List[Dict[str, Any]] | None = None, status: int = 200):
        return JsonResponse({"ok": True, "data": data or {}}, status=status)

    def _err(self, msg: str, status: int = 400):
        return JsonResponse({"ok": False, "error": msg}, status=status)


class TaskPostView(LoginRequiredMixin, View, _JsonMixin):
    """触发任务过账（post）；统一委托 inventory.services.post_task。"""
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int):
        task = get_object_or_404(WmsTask, pk=pk)
        try:
            result = inv_services.post_task(task_id=task.id, by_user=request.user)
            return self._ok({"task_id": task.id, "result": result})
        except Exception as e:
            return self._err(str(e))


class TaskClaimView(LoginRequiredMixin, View, _JsonMixin):
    """领取/占用任务（如果你已有 tasking.services.claim_task）。"""
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int):
        task = get_object_or_404(WmsTask, pk=pk)
        try:
            if hasattr(task_services, "claim_task"):
                result = task_services.claim_task(task_id=task.id, by_user=request.user)
            else:
                result = {"message": "claim_task() 未在 tasking.services 中找到，跳过。"}
            return self._ok({"task_id": task.id, "result": result})
        except Exception as e:
            return self._err(str(e))


class TaskLineSaveSnapshotView(LoginRequiredMixin, View, _JsonMixin):
    """以“快照覆盖”的方式保存行扫描（优先调用 save_receiving_snapshot）。"""
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int):  # pk = line_id
        line = get_object_or_404(WmsTaskLine, pk=pk)
        try:
            payload = self._json_body(request)
            items = payload.get("items") or []
            source = payload.get("source") or "WEB"
            if not isinstance(items, list):
                return self._err("items 必须是数组")

            if hasattr(task_services, "save_receiving_snapshot"):
                result = task_services.save_receiving_snapshot(
                    task_line_id=line.id, items=items, operator=request.user, source=source
                )
            elif hasattr(task_services, "scan_task"):
                result = task_services.scan_task(
                    task_line_id=line.id, items=items, operator=request.user, source=source
                )
            else:
                return self._err("未找到 save_receiving_snapshot/scan_task，请在 tasking.services 中对接。")

            return self._ok({"line_id": line.id, "result": result})
        except ValueError as ve:
            return self._err(str(ve))
        except Exception as e:
            return self._err(str(e))


class TaskLineFinalizeView(LoginRequiredMixin, View, _JsonMixin):
    """行完成/固化（如果你有 finalize_task_line）。"""
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request: HttpRequest, pk: int):  # pk = line_id
        line = get_object_or_404(WmsTaskLine, pk=pk)
        try:
            if hasattr(task_services, "finalize_task_line"):
                result = task_services.finalize_task_line(line_id=line.id, by_user=request.user)
            else:
                return self._err("未找到 finalize_task_line()，请在 tasking.services 中对接。")
            return self._ok({"line_id": line.id, "result": result})
        except Exception as e:
            return self._err(str(e))
