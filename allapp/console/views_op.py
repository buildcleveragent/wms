# -*- coding: utf-8 -*-
# 统一“仓库操作员控制台”（CBV）
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Tuple

from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views import View
from django.views.generic import ListView, DetailView

from allapp.tasking.models import WmsTask, WmsTaskLine
from allapp.tasking import services as tasking_services
from allapp.inventory import services as inventory_services

logger = logging.getLogger(__name__)

# ---- 常量 / 配置 ----
Q_DEC_PLACES = 3
SESSION_KEY = "operator_sessions"  # {task_id: { line_id: { qty, lot_no, expiry_date, serial_no, location_id, to_location_id } } }
PARSE_HINT = "L<行ID>;Q<数量>;LOT:批次;EXP:YYYYMMDD;SER:序列;LOC:库位;TOLOC:目标库位;FROMLOC:来源库位"

ALLOWED_STATUSES = {
    getattr(WmsTask.Status, "READY", "READY"),
    getattr(WmsTask.Status, "RELEASED", "RELEASED"),
    getattr(WmsTask.Status, "IN_PROGRESS", "IN_PROGRESS"),
}

# ---- 表单（扫码 + 手工 + 确认）----
class ScanForm(forms.Form):
    payload = forms.CharField(label="扫描/粘贴内容", max_length=200)

class ManualForm(forms.Form):
    line_id = forms.IntegerField(label="行ID")
    qty = forms.DecimalField(label="数量", max_digits=18, decimal_places=Q_DEC_PLACES, min_value=Decimal("0.001"))
    lot_no = forms.CharField(label="批次", max_length=40, required=False)
    expiry_date = forms.DateField(label="效期", required=False, input_formats=["%Y-%m-%d", "%Y%m%d"])
    serial_no = forms.CharField(label="序列号", max_length=60, required=False)
    location_id = forms.IntegerField(label="库位ID", required=False)
    to_location_id = forms.IntegerField(label="目标库位ID", required=False)
    def clean_qty(self):
        q = self.cleaned_data["qty"]
        return q.quantize(Decimal("1." + "0"*Q_DEC_PLACES), rounding=ROUND_HALF_UP)

class ConfirmForm(forms.Form):
    confirm = forms.BooleanField(required=True)

# ---- 会话存储 ----
def _session_bucket(request: HttpRequest, task_id: int) -> Dict[str, Any]:
    store = request.session.setdefault(SESSION_KEY, {})
    return store.setdefault(str(task_id), {})

# ---- 工具 ----
def parse_payload(raw: str) -> Dict[str, Any]:
    """支持：L<line_id>, Q<qty>, LOT:<lot>, EXP:YYYYMMDD, SER:<serial>, LOC:<id>, TOLOC:<id>, FROMLOC:<id>"""
    if not raw:
        return {}
    parts = [p.strip() for p in raw.replace(" ", ";").split(";") if p.strip()]
    data: Dict[str, Any] = {"qty": Decimal("1")}
    for p in parts:
        up = p.upper()
        if up.startswith("L") and up[1:].isdigit():
            data["line_id"] = int(up[1:])
        elif up.startswith("Q"):
            try:
                data["qty"] = Decimal(p[1:])
            except Exception:
                pass
        elif up.startswith("LOT:"):
            data["lot_no"] = p[4:]
        elif up.startswith("EXP:"):
            try:
                data["expiry_date"] = datetime.strptime(p[4:], "%Y%m%d").date()
            except Exception:
                pass
        elif up.startswith("SER:"):
            data["serial_no"] = p[4:]
        elif up.startswith("LOC:") and p[4:].isdigit():
            data["location_id"] = int(p[4:])
        elif up.startswith("TOLOC:") and p[6:].isdigit():
            data["to_location_id"] = int(p[6:])
        elif up.startswith("FROMLOC:") and p[8:].isdigit():
            data["location_id"] = int(p[8:])  # 兼容写法：FROMLOC 作为来源库位
    return data

def _get_task_or_404(request: HttpRequest, pk: int) -> WmsTask:
    task = get_object_or_404(WmsTask, pk=pk)
    # 如需权限控制/范围过滤（owner/warehouse/assigned_to），可在此加逻辑
    return task

def _add_to_bucket(task: WmsTask, bucket: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[bool, str]:
    line_id = payload.get("line_id")
    qty = payload.get("qty") or Decimal("1")
    if not line_id:
        return False, "缺少 L<行ID>"
    try:
        WmsTaskLine.objects.only("id", "task_id").get(pk=line_id, task_id=task.id)
    except WmsTaskLine.DoesNotExist:
        return False, f"行{line_id}不属于任务{task.id}"
    e = bucket.setdefault(str(line_id), {})
    e["qty"] = Decimal(e.get("qty") or 0) + Decimal(qty)
    for k in ("lot_no", "expiry_date", "serial_no", "location_id", "to_location_id"):
        if k in payload:
            e[k] = payload[k]
    return True, f"L{line_id} +{qty} 已暂存"

# ---- 每类任务的保存策略 ----
SaveResult = Tuple[int, List[str]]  # (成功行数, 消息)

def _save_receive(task: WmsTask, bucket: Dict[str, Any], user) -> SaveResult:
    """收货：调用 save_receiving_snapshot（幂等覆盖“未过账快照”）。"""
    ok, msgs = 0, []
    for line_id_str, e in bucket.items():
        line_id = int(line_id_str)
        try:
            line = WmsTaskLine.objects.only("id", "task_id").get(pk=line_id, task_id=task.id)
        except WmsTaskLine.DoesNotExist:
            msgs.append(f"行{line_id}不属于任务{task.id}")
            continue
        item = {
            "product": getattr(line, "product", None),  # 服务层可用 line 识别 product
            "location": None,                           # 如需，可由 e['location_id'] 查询后传入
            "lot_no": e.get("lot_no"),
            "expiry_date": e.get("expiry_date"),
            "serial": e.get("serial_no"),
            "qty": Decimal(e.get("qty") or 0),
        }
        tasking_services.save_receiving_snapshot(line_id, [item], operator=user, source="WEB")
        ok += 1
    return ok, msgs

def _save_via_scan_task(task: WmsTask, bucket: Dict[str, Any], user) -> SaveResult:
    """上架/拣货/复核/打包/发运：统一走 scan_task；from/to/lot/serial/expiry 由服务层解释。"""
    ok, msgs = 0, []
    for line_id_str, e in bucket.items():
        line_id = int(line_id_str)
        try:
            WmsTaskLine.objects.only("id", "task_id").get(pk=line_id, task_id=task.id)
        except WmsTaskLine.DoesNotExist:
            msgs.append(f"行{line_id}不属于任务{task.id}")
            continue
        item = {
            "qty": Decimal(e.get("qty") or 0),
            "lot_no": e.get("lot_no"),
            "expiry_date": e.get("expiry_date"),
            "serial": e.get("serial_no"),
            "location_id": e.get("location_id"),       # 来源库位（如拣货/复核）
            "to_location_id": e.get("to_location_id"), # 目标库位（如上架/移库）
        }
        tasking_services.scan_task(task_line_id=line_id, items=[item], operator=user, source="WEB")
        ok += 1
    return ok, msgs

# ---- 任务类型 -> 面板与保存函数 映射 ----
@dataclass
class OpHandler:
    slug: str
    panel_tpl: str
    save_snapshot: Callable[[WmsTask, Dict[str, Any], Any], SaveResult]
    parse_hint: str = PARSE_HINT

HANDLERS: Dict[str, OpHandler] = {
    WmsTask.TaskType.RECEIVE:  OpHandler("receive",  "console/op/panels/receive.html",  _save_receive),
    WmsTask.TaskType.PUTAWAY:  OpHandler("putaway",  "console/op/panels/putaway.html",  _save_via_scan_task),
    WmsTask.TaskType.PICK:     OpHandler("pick",     "console/op/panels/pick.html",     _save_via_scan_task),
    WmsTask.TaskType.REVIEW:   OpHandler("review",   "console/op/panels/review.html",   _save_via_scan_task),
    WmsTask.TaskType.PACK:     OpHandler("pack",     "console/op/panels/pack.html",     _save_via_scan_task),
    WmsTask.TaskType.DISPATCH: OpHandler("dispatch", "console/op/panels/dispatch.html", _save_via_scan_task),
}

# ---- CBV：列表页 ----
class OpTaskListView(LoginRequiredMixin, ListView):
    model = WmsTask
    template_name = "console/op/task_list.html"
    paginate_by = 30

    def get_queryset(self):
        qs = (WmsTask.objects
              .filter(task_type__in=HANDLERS.keys(), status__in=ALLOWED_STATUSES)
              .order_by("-id"))
        t = self.request.GET.get("type")
        w = self.request.GET.get("warehouse")
        o = self.request.GET.get("owner")
        if t: qs = qs.filter(task_type=t)
        if w: qs = qs.filter(warehouse_id=w)
        if o: qs = qs.filter(owner_id=o)
        return qs

# ---- CBV：通用任务页 ----
class OpTaskDetailView(LoginRequiredMixin, DetailView):
    model = WmsTask
    template_name = "console/op/task_detail.html"

    def get_object(self, queryset=None):
        return _get_task_or_404(self.request, self.kwargs["pk"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        task: WmsTask = ctx["object"]
        handler = HANDLERS.get(task.task_type)
        bucket = _session_bucket(self.request, task.id)
        ctx.update({
            "handler": handler,
            "session_items": bucket,
            "scan_form": ScanForm(),
            "manual_form": ManualForm(),
            "confirm_form": ConfirmForm(),
            "parse_hint": handler.parse_hint if handler else PARSE_HINT,
            "lines": WmsTaskLine.objects.only("id").filter(task_id=task.id).order_by("id"),
            # ↓↓↓ 新增：用于预填手工录入的行ID
            "focus_line_id": self.request.GET.get("line") or "",
        })
        return ctx

# ---- 交互端点（HTMX）----
class OpScanView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        task = _get_task_or_404(request, pk)
        f = ScanForm(request.POST); bucket = _session_bucket(request, task.id)
        if f.is_valid():
            payload = parse_payload(f.cleaned_data["payload"])
            ok, msg = _add_to_bucket(task, bucket, payload)
            request.session.modified = True
            return render(request, "console/op/_session_items.html",
                          {"task": task, "session_items": bucket, "message": msg, "ok": ok})
        return render(request, "console/op/_session_items.html",
                      {"task": task, "session_items": bucket, "message": "无效输入", "ok": False})

class OpManualView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        task = _get_task_or_404(request, pk)
        f = ManualForm(request.POST); bucket = _session_bucket(request, task.id)
        if f.is_valid():
            payload = {k: f.cleaned_data.get(k)
                       for k in ("line_id","qty","lot_no","expiry_date","serial_no","location_id","to_location_id")
                       if f.cleaned_data.get(k) not in (None, "")}
            ok, msg = _add_to_bucket(task, bucket, payload)
            request.session.modified = True
            return render(request, "console/op/_session_items.html",
                          {"task": task, "session_items": bucket, "message": msg, "ok": ok})
        return render(request, "console/op/_session_items.html",
                      {"task": task, "session_items": bucket, "message": "手工录入校验失败", "ok": False})

class OpSaveSnapshotView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        task = _get_task_or_404(request, pk)
        if not ConfirmForm(request.POST).is_valid():
            bucket = _session_bucket(request, task.id)
            return render(request, "console/op/_session_items.html",
                          {"task": task, "session_items": bucket, "message": "请勾选确认保存", "ok": False})
        handler = HANDLERS.get(task.task_type)
        if not handler:
            bucket = _session_bucket(request, task.id)
            return render(request, "console/op/_session_items.html",
                          {"task": task, "session_items": bucket, "message": f"未注册的任务类型：{task.task_type}", "ok": False})
        bucket = _session_bucket(request, task.id)
        ok_count, msgs = handler.save_snapshot(task, bucket, request.user)
        return render(request, "console/op/_session_items.html",
                      {"task": task, "session_items": bucket, "message": f"已保存 {ok_count} 行快照" + (("; " + "; ".join(msgs)) if msgs else ""), "ok": True})

class OpPostView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        task = _get_task_or_404(request, pk)
        if not ConfirmForm(request.POST).is_valid():
            bucket = _session_bucket(request, task.id)
            return render(request, "console/op/_session_items.html",
                          {"task": task, "session_items": bucket, "message": "请勾选确认过账", "ok": False})
        try:
            inventory_services.post_task(task_id=task.id, by_user=request.user)
            msg, ok = "过账成功", True
        except Exception as e:
            msg, ok = f"过账失败：{e}", False
        bucket = _session_bucket(request, task.id)
        return render(request, "console/op/_session_items.html",
                      {"task": task, "session_items": bucket, "message": msg, "ok": ok})

class OpClearView(LoginRequiredMixin, View):
    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        store = request.session.get(SESSION_KEY, {})
        if str(pk) in store:
            store[str(pk)] = {}
            request.session.modified = True
        task = _get_task_or_404(request, pk)
        return render(request, "console/op/_session_items.html",
                      {"task": task, "session_items": {}, "message": "已清空暂存", "ok": True})


from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, Exists, OuterRef, BooleanField, Value, Case, When
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from allapp.tasking.models import WmsTask, WmsTaskLine, TaskAssignment
from allapp.locations.models import Warehouse
from allapp.tasking import services as tasking_services


def _allowed_wh_ids_for(user) -> set[int]:
    if getattr(user, "is_superuser", False):
        return set(Warehouse.objects.values_list("id", flat=True))
    return {user.warehouse_id} if getattr(user, "warehouse_id", None) else set()


class OpLineListView(LoginRequiredMixin, TemplateView):
    """
    合并“我负责的”和“可抢单”的任务行到一张表：
    - is_mine: 我在该行有活动指派或我在该任务有头级活动指派
    - is_claimable: 任务=RELEASED 且任务没有任何活动指派
    支持 GET ?scope=all|mine|claimable
    """
    template_name = "console/op/line_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        allowed_wh = _allowed_wh_ids_for(user)
        scope = (self.request.GET.get("scope") or "all").lower()
        logger.debug(
            "console.op_line_list.begin user_id=%s allowed_wh=%s scope=%s",
            getattr(user, "id", None),
            sorted(allowed_wh),
            scope,
        )
        if not allowed_wh:
            ctx.update({"lines": [], "scope": scope, "totals": {"all": 0, "mine": 0, "claimable": 0}})
            return ctx

        # 子查询：活动指派
        active_assign_task_any = TaskAssignment.objects.filter(
            task_id=OuterRef("task_id"), finished_at__isnull=True
        )

        active_assign_task_for_me = active_assign_task_any.filter(assignee=user)
        active_assign_line_for_me = TaskAssignment.objects.filter(
            line_id=OuterRef("id"), finished_at__isnull=True, assignee=user
        )

        base = (
            WmsTaskLine.objects
            .select_related("task", "product", "from_location", "to_location")
            .filter(task__warehouse_id__in=allowed_wh)
            .exclude(status=WmsTaskLine.Status.CANCELLED)
        )

        # 先注入三个布尔注解
        qs = (
            base
            .annotate(
                _mine_line=Exists(active_assign_line_for_me),
                _mine_task_head=Exists(active_assign_task_for_me.filter(line__isnull=True)),
                _any_assign=Exists(active_assign_task_any),
            )
            .annotate(
                is_mine=Case(
                    When(_mine_line=True, then=Value(True)),
                    When(_mine_task_head=True, then=Value(True)),
                    default=Value(False), output_field=BooleanField(),
                ),
                is_claimable=Case(
                    When(
                        Q(task__status=WmsTask.Status.RELEASED) & Q(_any_assign=False),
                        then=Value(True)
                    ),
                    default=Value(False), output_field=BooleanField(),
                ),
            )
            # 只保留“我负责”或“可抢”的行
            .filter(Q(is_mine=True) | Q(is_claimable=True))
        )

        # 统计总数（供顶部筛选显示）
        totals = {
            "all": qs.count(),
            "mine": qs.filter(is_mine=True).count(),
            "claimable": qs.filter(is_claimable=True).count(),
        }

        # 按 scope 过滤
        if scope == "mine":
            qs = qs.filter(is_mine=True)
        elif scope == "claimable":
            qs = qs.filter(is_claimable=True)

        # 排序：我负责的优先，然后按 id 倒序
        qs = qs.order_by("-is_mine", "-id")
        # qs = WmsTaskLine.objects.all()
        logger.debug(
            "console.op_line_list.completed user_id=%s scope=%s count=%s",
            getattr(user, "id", None),
            scope,
            qs.count(),
        )

        ctx.update({
            "lines": qs,
            "scope": scope,
            "totals": totals,
        })
        return ctx



class OpLineDetailRedirectView(LoginRequiredMixin, View):
    """
    行级数据录入 → 复用现有任务详情页：
    将用户带到 op:task_detail，并携带 ?line=<line_id>，以便预填“手工录入”的行号。
    """
    def get(self, request, line_id: int):
        line = WmsTaskLine.objects.select_related("task").only("id", "task_id").get(pk=line_id)
        url = f"{reverse('op:task_detail', args=[line.task_id])}?line={line.id}"
        return redirect(url)


class OpLineClaimView(LoginRequiredMixin, View):
    """抢单（按任务维度）：行所在的任务必须处于 RELEASED，且任务上没有活动指派。"""
    def post(self, request, line_id: int):
        user = request.user
        allowed_wh = _allowed_wh_ids_for(user)
        line = WmsTaskLine.objects.select_related("task").only("id", "task_id").get(pk=line_id)
        task = line.task
        try:
            tasking_services.claim_task(task, by_user=user, allowed_wh_ids=allowed_wh,
                                        to_status=WmsTask.Status.IN_PROGRESS)
            messages.success(request, f"已抢到任务 {task.task_no}。")
        except Exception as e:
            messages.error(request, f"抢单失败：{e}")
        return redirect(reverse("op:line_list"))


class OpLineUnclaimView(LoginRequiredMixin, View):
    """放回（按任务维度）：把当前用户的活动指派完成（finished_at），可回退任务状态到 RELEASED。"""
    def post(self, request, line_id: int):
        user = request.user
        allowed_wh = _allowed_wh_ids_for(user)
        line = WmsTaskLine.objects.select_related("task").only("id", "task_id").get(pk=line_id)
        task = line.task
        try:
            tasking_services.unclaim_task(task, by_user=user, allowed_wh_ids=allowed_wh,
                                          back_to_status=WmsTask.Status.RELEASED)
            messages.success(request, f"已放回任务 {task.task_no}。")
        except Exception as e:
            messages.error(request, f"放回失败：{e}")
        return redirect(reverse("op:line_list"))

from django.apps import apps
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, render, redirect
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.views import View
from django.urls import reverse

from allapp.tasking.models import WmsTaskLine
from django.db import transaction
from django.apps import apps
from django.forms import modelform_factory
from django.shortcuts import get_object_or_404, render, redirect
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.views import View
from django.urls import reverse

from allapp.tasking.models import WmsTaskLine


def _extra_model_for(task_type: str):
    """
    约定：RECEIVE -> ReceiveLineExtra, PUTAWAY -> PutawayLineExtra, PICK -> PickLineExtra ...
    在 app 'tasking' 中动态查找；找不到则返回 None。
    """
    if not task_type:
        return None
    want = f"{task_type.title()}LineExtra"
    try:
        tasking_app = apps.get_app_config("tasking")
    except LookupError:
        return None
    for mdl in tasking_app.get_models():
        if mdl.__name__.lower() == want.lower():
            return mdl
    return None


# === 字段白名单（按需再调）===

def _line_form_fields(task_type: str) -> list[str] | None:
    """
    WmsTaskLine 可编辑字段白名单：
    - 返回 None 表示退回“自动生成”（不限制字段）
    - 这里只给出常见的安全小集合，避免把外键/状态/审计等露出来
    """
    common = ["qty_done", "remark"]  # 通用：已执行数量 + 备注
    per_type = {
        "RECEIVE": common,   # 收货：通常行本身只需要 qty_done/remark，其它在 extra
        "PUTAWAY": common,
        "PICK":    common,
        "REVIEW":  common,
        "PACK":    common,
        "LOAD":    common,
        "DISPATCH":common,
        "REPLEN":  common,
        "RELOC":   common,
        "COUNT":   common,
        "QC":      common,
        "ADJUST":  common,
    }
    return per_type.get((task_type or "").upper(), common)


def _extra_form_fields(extra_model_name: str) -> list[str] | None:
    """
    *LineExtra 可编辑字段白名单：
    - 入库 ReceiveLineExtra：批次/效期/三段数/原因/上游容器
    - 其他类型若未配置，则返回 None（退回自动生成）
    """
    return {
        "ReceiveLineExtra": [
            "from_lpn",
            "lot_no", "mfg_date", "exp_date",
            "qty_ok", "qty_damage", "qty_reject",
            "damage_reason_code", "reject_reason_code",
        ],
        # 需要时再补：比如 PutawayLineExtra / PickLineExtra 等
        # "PutawayLineExtra": [...],
        # "PickLineExtra":    [...],
    }.get(extra_model_name, None)


def _exclude_fields(model_cls, extra_exclude=()):
    """根据存在与否排除常见外键/审计字段，避免自创字段名。"""
    cands = {
        "id", "line", "task", "owner", "warehouse",
        "created_at", "updated_at", "created_by", "updated_by",
    }
    cands |= set(extra_exclude)
    present = {f.name for f in model_cls._meta.get_fields() if hasattr(f, "name")}
    return [n for n in cands if n in present]


class OpLineEditView(LoginRequiredMixin, View):
    template_name = "console/op/line_edit.html"

    def _check_wh(self, user, line) -> bool:
        allowed = _allowed_wh_ids_for(user)
        if getattr(user, "is_superuser", False):
            return True
        return (not allowed) or (line.task.warehouse_id in allowed)

    def get(self, request, line_id: int):
        line = get_object_or_404(WmsTaskLine.objects.select_related("task", "product"), pk=line_id)
        if not self._check_wh(request.user, line):
            return HttpResponseForbidden("无权限")

        # —— 行表单（白名单）——
        lf = _line_form_fields(line.task.task_type)
        LineForm = (modelform_factory(WmsTaskLine, fields=lf)
                    if lf else modelform_factory(WmsTaskLine,
                           exclude=_exclude_fields(WmsTaskLine, extra_exclude={"product","task","status"})))
        line_form = LineForm(instance=line, prefix="line")

        # —— 唯一的 *LineExtra ——
        ExtraModel = _extra_model_for(line.task.task_type)
        extra_form, extra_model_name = None, None
        if ExtraModel:
            ef = _extra_form_fields(ExtraModel.__name__)
            ExtraForm = (modelform_factory(ExtraModel, fields=ef)
                         if ef else modelform_factory(ExtraModel, exclude=_exclude_fields(ExtraModel)))
            extra_inst = ExtraModel.objects.filter(line_id=line.id).first()
            extra_form = ExtraForm(instance=extra_inst, prefix="extra")
            extra_model_name = ExtraModel.__name__

        return render(request, self.template_name, {
            "line": line,
            "line_form": line_form,
            "extra_form": extra_form,
            "extra_model_name": extra_model_name,
            "task": line.task,
            "back_to_task_url": f"{reverse('op:task_detail', args=[line.task_id])}?line={line.id}",
            "back_to_list_url": reverse("op:line_list"),
        })

    def post(self, request, line_id: int):
        line = get_object_or_404(WmsTaskLine.objects.select_related("task", "product"), pk=line_id)
        if not self._check_wh(request.user, line):
            return HttpResponseForbidden("无权限")

        # 构建两个表单（同一个 <form> 提交，前缀区分）
        lf = _line_form_fields(line.task.task_type)
        LineForm = (modelform_factory(WmsTaskLine, fields=lf)
                    if lf else modelform_factory(WmsTaskLine,
                           exclude=_exclude_fields(WmsTaskLine, extra_exclude={"product","task","status"})))
        line_form = LineForm(request.POST, request.FILES, instance=line, prefix="line")

        ExtraModel = _extra_model_for(line.task.task_type)
        extra_form, extra_model_name = None, None
        if ExtraModel:
            ef = _extra_form_fields(ExtraModel.__name__)
            ExtraForm = (modelform_factory(ExtraModel, fields=ef)
                         if ef else modelform_factory(ExtraModel, exclude=_exclude_fields(ExtraModel)))
            instance = ExtraModel.objects.filter(line_id=line.id).first() or ExtraModel(line=line)
            extra_form = ExtraForm(request.POST, request.FILES, instance=instance, prefix="extra")
            extra_model_name = ExtraModel.__name__

        all_valid = line_form.is_valid() and (extra_form.is_valid() if extra_form else True)
        if all_valid:
            with transaction.atomic():
                line_form.save()
                if extra_form:
                    extra_form.save()
            messages.success(request, "已保存：行信息与扩展信息。")
            return redirect(request.path)   # 或改为 back_to_task_url
        else:
            messages.error(request, "保存失败：请检查标红字段。")
            return render(request, self.template_name, {
                "line": line,
                "line_form": line_form,
                "extra_form": extra_form,
                "extra_model_name": extra_model_name,
                "task": line.task,
                "back_to_task_url": f"{reverse('op:task_detail', args=[line.task_id])}?line={line.id}",
                "back_to_list_url": reverse("op:line_list"),
            })
