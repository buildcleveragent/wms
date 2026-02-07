
from django.http import HttpResponse
from django.views.generic import DetailView
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken

from allapp.tasking.models import WmsTask


class ReceiveTaskPrintView(DetailView):
    """
    收货任务打印页面（PC + PDA）
    - PC：已登录 session 直接打印
    - PDA：用 ?token=<access> 认证（window.open/openURL 不带 Authorization 头）
    """
    model = WmsTask
    pk_url_kwarg = "task_id"
    template_name = "inbound/print/receive_task.html"

    def dispatch(self, request, *args, **kwargs):
        # 1) session 已登录
        if request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        # 2) PDA: query token
        token = request.GET.get("token") or request.GET.get("access")
        if not token:
            return HttpResponse("Unauthorized: missing token", status=401)

        try:
            at = AccessToken(token)
            user_id = at.get("user_id")
            if not user_id:
                return HttpResponse("Unauthorized: invalid token", status=401)
            user = get_user_model().objects.get(id=user_id)
            request.user = user
        except Exception:
            return HttpResponse("Unauthorized: invalid token", status=401)

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().select_related("owner", "warehouse")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        task = self.object
        lines = task.lines.select_related("product").order_by("id")
        ctx["lines"] = lines
        return ctx


# # allapp/console/views.py
# from django.contrib.auth.mixins import LoginRequiredMixin
# from django.views.generic import DetailView
#
# from allapp.tasking.models import WmsTask
#
# class ReceiveTaskPrintView(LoginRequiredMixin, DetailView):
#     """
#     收货任务打印页面（PC 浏览器打印用）
#     """
#     model = WmsTask
#     pk_url_kwarg = "task_id"
#     template_name = "inbound/print/receive_task.html"
#
#     def get_queryset(self):
#         # 可选：按当前用户权限/owner 做过滤
#         qs = super().get_queryset().select_related("owner", "warehouse")
#         return qs
#
#     def get_context_data(self, **kwargs):
#         ctx = super().get_context_data(**kwargs)
#         task = self.object
#         lines = (
#             task.lines
#             .select_related("product")
#             .order_by("id")
#         )
#         ctx["lines"] = lines
#         return ctx
