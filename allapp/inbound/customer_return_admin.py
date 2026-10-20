"""Read-only admin record with CSRF-protected workflow actions."""

from uuid import uuid4

from django.contrib import admin, messages
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from rest_framework.exceptions import APIException

from .customer_return_api import actions, payload
from .customer_return_models import CustomerReturn
from .customer_return_services import command, queryset


@admin.register(CustomerReturn)
class CustomerReturnAdmin(admin.ModelAdmin):
    list_display = ("number", "original_order", "warehouse", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("number", "original_order__order_no")

    def get_queryset(self, request):
        try:
            return queryset(request.user)
        except APIException:
            return CustomerReturn.objects.none()

    def has_module_permission(self, request):
        return request.user.has_perm("inbound.manage_customer_return") or request.user.has_perm(
            "inbound.approve_customer_return"
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (
            obj is None or self.get_queryset(request).filter(pk=obj.pk).exists()
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [
            path(
                "<int:object_id>/change/",
                self.admin_site.admin_view(self.workflow_view),
                name="inbound_customerreturn_change",
            )
        ] + super().get_urls()

    def workflow_view(self, request, object_id):
        order = self.get_queryset(request).filter(pk=object_id).first()
        if not order or not self.has_view_permission(request, order):
            raise Http404
        allowed = actions(order, request.user)
        if request.method == "POST":
            try:
                operation = request.POST.get("operation")
                if operation not in allowed or operation not in {
                    "warehouse-confirm",
                    "warehouse-reject",
                    "close",
                    "owner-approve",
                    "owner-reject",
                    "note",
                }:
                    raise Http404
                command(
                    request.user,
                    operation,
                    {
                        "version": int(request.POST["version"]),
                        "request_id": uuid4(),
                        "reason": request.POST.get("reason", "").strip()[:500],
                    },
                    order.pk,
                )
                self.message_user(request, "退货处理已保存。", messages.SUCCESS)
                return HttpResponseRedirect(
                    reverse("admin:inbound_customerreturn_change", args=[order.pk])
                )
            except (APIException, ValueError, KeyError) as exc:
                self.message_user(
                    request,
                    str(getattr(exc, "detail", "操作参数无效，请刷新后重试。")),
                    messages.ERROR,
                )
        labels = {
            "warehouse-confirm": "仓库确认",
            "warehouse-reject": "退回申请",
            "close": "关闭剩余数量",
            "owner-approve": "货主审核通过",
            "owner-reject": "退回修改",
            "note": "追加说明",
        }
        return TemplateResponse(
            request,
            "admin/inbound/customer_return.html",
            {
                **self.admin_site.each_context(request),
                "title": order.number,
                "record": payload(order, request.user),
                "opts": self.model._meta,
                "action_choices": [(a, labels[a]) for a in allowed if a in labels],
            },
        )
