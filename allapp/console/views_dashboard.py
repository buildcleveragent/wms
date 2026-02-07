# ===============================
# FILE: allapp/console/views_dashboard.py
# 说明：仪表盘首页（CBV）+ 数据汇总 API（容错不自创字段）
# ===============================
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import timedelta

from django.apps import apps
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse, HttpRequest
from django.utils import timezone
from django.views.generic import TemplateView, View


# ---------- 工具：安全获取 model/字段 ----------
def get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None

def has_field(model, name: str) -> bool:
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False

def first_existing_field(model, candidates: List[str]) -> Optional[str]:
    for n in candidates:
        if has_field(model, n):
            return n
    return None

FINISHED_VALUES = ["DONE", "FINISHED", "COMPLETED", "CLOSED", "POSTED", "SHIPPED", "RECEIVED"]


# ---------- 数据提供（存在即算；否则返回空） ----------
def tasks_overview() -> Dict[str, Any]:
    WmsTask = get_model("tasking", "WmsTask")
    data = {"putaway": {"total": 0, "done": 0}, "pick": {"total": 0, "done": 0}}
    if not WmsTask:
        return data

    # 基于你项目里已用过的字段：task_type / status（若不存在就回退为总数0）
    q_all = getattr(WmsTask.objects, "all", lambda: [])()
    # putaway
    try:
        data["putaway"]["total"] = q_all.filter(task_type="PUTAWAY").count()
        done_q = Q(status__in=FINISHED_VALUES) | Q(status__iexact="DONE") | Q(status__iexact="FINISHED") | Q(status__iexact="COMPLETED")
        data["putaway"]["done"] = q_all.filter(task_type="PUTAWAY").filter(done_q).count()
    except Exception:
        pass
    # pick
    try:
        data["pick"]["total"] = q_all.filter(task_type="PICK").count()
        done_q = Q(status__in=FINISHED_VALUES) | Q(status__iexact="DONE") | Q(status__iexact="FINISHED") | Q(status__iexact="COMPLETED")
        data["pick"]["done"] = q_all.filter(task_type="PICK").filter(done_q).count()
    except Exception:
        pass
    return data

def orders_timeseries(app_label: str, model_name: str, days: int = 60) -> Dict[str, Any]:
    """返回 {dates:[...], total:[...], finished:[...]}，按天统计。字段名自动探测。"""
    M = get_model(app_label, model_name)
    if not M:
        return {"dates": [], "total": [], "finished": []}

    date_field = first_existing_field(M, ["created_at", "created_on", "create_time", "order_date", "biz_date", "date", "created"])
    status_field = "status" if has_field(M, "status") else None
    if not date_field:
        return {"dates": [], "total": [], "finished": []}

    end = timezone.now().date()
    start = end - timedelta(days=days-1)

    base = M.objects.filter(**{f"{date_field}__date__range": (start, end)})
    qs_total = base.annotate(d=TruncDate(date_field)).values("d").annotate(c=Count("id")).order_by("d")
    total_map = {x["d"]: x["c"] for x in qs_total}

    finished_map = {}
    if status_field:
        qdone = Q()
        for v in FINISHED_VALUES:
            qdone |= Q(**{f"{status_field}__iexact": v})
        qs_done = base.filter(qdone).annotate(d=TruncDate(date_field)).values("d").annotate(c=Count("id")).order_by("d")
        finished_map = {x["d"]: x["c"] for x in qs_done}

    # 补全日期
    dates, total, finished = [], [], []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        total.append(int(total_map.get(cur, 0)))
        finished.append(int(finished_map.get(cur, 0)))
        cur += timedelta(days=1)
    return {"dates": dates, "total": total, "finished": finished}

def backlog_by_status(app_label: str, model_name: str) -> Dict[str, Any]:
    """未完成订单状态分布：若无法识别完成状态，就给出全量状态分布。"""
    M = get_model(app_label, model_name)
    if not M or not has_field(M, "status"):
        return {"labels": [], "values": []}

    qs = M.objects.all()
    try:
        qdone = Q()
        for v in FINISHED_VALUES:
            qdone |= Q(status__iexact=v)
        qs = qs.exclude(qdone)
    except Exception:
        pass
    rows = qs.values("status").annotate(c=Count("id")).order_by("-c")
    labels = [str(r["status"]) for r in rows]
    values = [int(r["c"]) for r in rows]
    return {"labels": labels, "values": values}

def efficiency_ranking(task_type: str) -> Dict[str, Any]:
    """
    当日人效排名（可选）：
      尝试用 WmsTask 的 operator/assignee/owner 等常见字段之一 + 今天创建/完成量统计。
      如果字段缺失，则返回空数据，前端渲染“暂无数据”。
    """
    WmsTask = get_model("tasking", "WmsTask")
    if not WmsTask:
        return {"labels": [], "values": []}

    assignee_field = None
    for f in ["operator", "assignee", "owner", "user", "created_by"]:
        if has_field(WmsTask, f):
            assignee_field = f
            break
    date_field = first_existing_field(WmsTask, ["updated_at", "created_at", "created_on", "create_time", "date"])

    if not assignee_field or not date_field:
        return {"labels": [], "values": []}

    today = timezone.now().date()
    qs = WmsTask.objects.filter(task_type=task_type)
    qs = qs.filter(**{f"{date_field}__date": today})
    rows = qs.values(assignee_field).annotate(c=Count("id")).order_by("-c")[:10]
    labels = [str(r[assignee_field]) for r in rows]
    values = [int(r["c"]) for r in rows]
    return {"labels": labels, "values": values}


# ---------- 视图 ----------
class DashboardHomeView(LoginRequiredMixin, TemplateView):
    template_name = "console/home.html"


class DashboardSummaryApi(LoginRequiredMixin, View):
    """统一返回 homepage 所需全部数据（GET）"""
    def get(self, request: HttpRequest):
        data = {
            "kpi": tasks_overview(),
            "inbound_ts": orders_timeseries("inbound", "InboundOrder", days=60),
            "outbound_ts": orders_timeseries("outbound", "OutboundOrder", days=60),
            "inbound_backlog": backlog_by_status("inbound", "InboundOrder"),
            "outbound_backlog": backlog_by_status("outbound", "OutboundOrder"),
            "eff_putaway": efficiency_ranking("PUTAWAY"),
            "eff_pick": efficiency_ranking("PICK"),
            "eff_pack": efficiency_ranking("PACK"),
        }
        return JsonResponse({"ok": True, "data": data})
