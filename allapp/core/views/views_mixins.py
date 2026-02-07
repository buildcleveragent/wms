# views_mixins.py
from django.views.generic.base import ContextMixin

class RefreshOnChangeMixin(ContextMixin):
    """
    拦截 “仅刷新” 的 POST：
    - 表单不绑定 request.POST（避免必填项校验）
    - 用 initial 只传递你关心的上游字段值
    子类里设置 refresh_passthrough = ("owner", "carrierCompany", ...)
    """
    form_class = None
    refresh_passthrough: tuple[str, ...] = ()

    def build_refresh_initial(self, post):
        return {k: post.get(k) for k in self.refresh_passthrough if k in post}

    def post(self, request, *args, **kwargs):
        if "_refresh" in request.POST:
            assert self.form_class is not None, "form_class required"
            initial = self.build_refresh_initial(request.POST)
            form = self.form_class(initial=initial)   # 非绑定表单
            # CreateView/UpdateView 期望有 self.object
            if hasattr(self, "object"):
                self.object = None
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)
