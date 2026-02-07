# console/views.py
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, get_object_or_404

from django.views.generic import ListView
from allapp.tasking.models import WmsTask
from .mixins import HtmxMixin
from allapp.tasking.models import WmsTask
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse, HttpResponseBadRequest
from django.db.models import Q
from allapp.inventory.models import InventoryTransaction  # 按你的路径改

from django.views.generic import DetailView, View
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from allapp.tasking.models import WmsTask, TaskScanLog
from allapp.tasking import services as task_services

PAGE_SIZE = 25

def _filtered_qs(request):
    qs = InventoryTransaction.objects.select_related("owner","product","warehouse","location")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(code__icontains=q) | Q(product__name__icontains=q) |
            Q(product__code__icontains=q) | Q(owner__name__icontains=q)
        )
    return qs, q

@login_required
@permission_required("inventory.view_inventorytransaction", raise_exception=True)
def tx_list(request):
    qs, q = _filtered_qs(request)
    page = int(request.GET.get("page", 1))
    start, end = (page-1)*PAGE_SIZE, page*PAGE_SIZE
    items = qs.order_by("-id")[start:end]
    has_more = qs.count() > end
    ctx = {"items": items, "q": q, "page": page, "has_more": has_more}
    return render(request, "console/inventory_tx_list.html", ctx)

@login_required
@permission_required("inventory.view_inventorytransaction", raise_exception=True)
def tx_table(request):
    # 仅返回表格片段，供 HTMX 局部刷新（搜索/分页）
    qs, q = _filtered_qs(request)
    page = int(request.GET.get("page", 1))
    start, end = (page-1)*PAGE_SIZE, page*PAGE_SIZE
    items = qs.order_by("-id")[start:end]
    has_more = qs.count() > end
    ctx = {"items": items, "q": q, "page": page, "has_more": has_more}
    return render(request, "console/_inventory_tx_table.html", ctx)

@login_required
@permission_required("inventory.change_inventorytransaction", raise_exception=True)
def tx_toggle_flag(request, pk):
    # 示例：行内切换一个布尔字段（比如 is_frozen 或 verified）
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    obj = get_object_or_404(InventoryTransaction, pk=pk)
    # 假设有个布尔字段 verified
    obj.verified = not bool(obj.verified)
    obj.save(update_fields=["verified"])
    # 返回“单行片段”，HTMX 会把这一行替换掉
    return render(request, "console/_inventory_tx_row.html", {"it": obj})

@login_required
@permission_required("inventory.view_inventorytransaction", raise_exception=True)
def tx_detail(request, pk):
    obj = get_object_or_404(InventoryTransaction, pk=pk)
    return render(request, "console/_modal.html", {
        "title": f"明细：{obj.code}",
        "body_html": (
            f"<div class='space-y-1 text-sm'>"
            f"<div><b>货主：</b>{obj.owner}</div>"
            f"<div><b>商品：</b>{obj.product}</div>"
            f"<div><b>仓库：</b>{obj.warehouse}</div>"
            f"<div><b>数量：</b>{obj.qty}</div>"
            f"<div><b>时间：</b>{obj.created_at}</div>"
            f"</div>"
        )
    })

@login_required
@permission_required("tasking.view_wmstask", raise_exception=True)
def pick_task_list(request):
    tasks = (WmsTask.objects
             .filter(task_type="PICK", status__in=["READY","IN_PROGRESS"])
             .select_related("warehouse","owner")
             .order_by("priority","id"))
    return render(request, "console/pick_list.html", {"tasks": tasks})

class PickTaskListView(HtmxMixin, ListView):
    model = WmsTask
    context_object_name = "tasks"
    paginate_by = 20
    full_template_name = "console/pick/list.html"
    partial_template_name = "console/pick/_table.html"

    def get_queryset(self):
        qs = (WmsTask.objects
              .filter(task_type="PICK")
              .select_related("warehouse", "owner")
              .order_by("priority", "id"))
        wh = self.request.GET.get("warehouse")
        st = self.request.GET.get("status")
        if wh: qs = qs.filter(warehouse_id=wh)
        if st: qs = qs.filter(status=st)
        return qs

    def render_to_response(self, context, **response_kwargs):
        # HTMX 请求只回表格片段；普通请求回整页
        return self.render(context, **response_kwargs)

class PickTaskDetailView(HtmxMixin, DetailView):
    model = WmsTask
    full_template_name = "console/pick/detail.html"
    partial_template_name = "console/pick/_lines.html"
    context_object_name = "task"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lines"] = self.object.wmstaskline_set.select_related("product", "location")
        return ctx

    def render_to_response(self, context, **response_kwargs):
        # 明细页也允许只换表格区域（例如筛选/排序行）
        return self.render(context, **response_kwargs)

class PickScanView(View):
    """hx-post 扫描提交：返回 OOB 提示 + 局部行表刷新"""
    def post(self, request, pk):
        task = get_object_or_404(WmsTask, pk=pk)
        barcode = request.POST.get("barcode", "").strip().upper()
        qty = request.POST.get("qty") or "0"
        fp = request.POST.get("fp")  # 幂等指纹

        TaskScanLog.objects.get_or_create(
            task=task, barcode=barcode, fp=fp, defaults={"qty": qty, "created_by": request.user}
        )
        # 回前端：1) 触发刷新行表；2) OOB 弹出 Toast
        from django.template.loader import render_to_string
        oob_toast = render_to_string("console/_toast_success.html", {"msg": f"已接收：{barcode} × {qty}"})
        html_lines = render_to_string("console/pick/_lines.html", {
            "task": task, "lines": task.wmstaskline_set.select_related("product","location")
        }, request=request)
        resp = JsonResponse({"html": html_lines})
        resp["HX-Trigger"] = "lines:refresh"  # 也可用 HX-Trigger-After-Swap
        resp["HX-Reswap"] = "innerHTML"
        resp["HX-Retarget"] = "#lines"
        # OOB 更新提示区
        resp.content = oob_toast.encode() + resp.content  # 简便做法；或单独返回 OOB 片段
        return resp

class PickPostView(View):
    """过账动作：调用服务层，返回重定向或刷新"""
    def post(self, request, pk):
        res = task_services.post_task(task_id=pk, by_user=request.user)
        from django.http import HttpResponse
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = f"/console/pick/{pk}/"  # 过账后刷新当前页
        resp["HX-Trigger"] = "toast:ok"
        return resp

