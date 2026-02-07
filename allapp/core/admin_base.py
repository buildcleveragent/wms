# allapp/core/admin_base.py
from __future__ import annotations

import csv
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpResponse
from django.utils import timezone

# ========== 共享基类 ==========
class BaseReadonlyAdmin(admin.ModelAdmin):
    """基础配置，避免展示审计字段。"""
    list_per_page = 30
    save_on_top = True
    admin_priority = 100

class DeletedStatusFilter(admin.SimpleListFilter):
    """通用删除状态过滤器：未删除 / 已删除 / 全部"""
    title = "删除状态"
    parameter_name = "deleted"

    def lookups(self, request, model_admin):
        return (("no", "未删除"), ("yes", "已删除"), ("all", "全部"))

    def queryset(self, request, queryset: QuerySet):
        # 模型没有 is_deleted 字段则忽略
        if not hasattr(queryset.model, "is_deleted"):
            return queryset
        val = self.value() or "no"
        if val == "yes":
            return queryset.filter(is_deleted=True)
        if val == "all":
            return queryset
        return queryset.filter(is_deleted=False)


class SoftDeleteAdminMixin:
    """
    软删除/恢复（幂等）：
    - 需要模型包含: is_deleted(Boolean)
    - 如有 deleted_at(DateTime)、deleted_by(FK/Char) 将自动写入/清空
    """
    actions = ("action_soft_delete", "action_restore")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # 默认不显示已删除；通过 DeletedStatusFilter 切换“已删除/全部”可查看
        if hasattr(self.model, "is_deleted") and request.GET.get("deleted") not in {"yes", "all"}:
            qs = qs.filter(is_deleted=False)
        return qs

    @admin.action(description="软删除选中记录")
    def action_soft_delete(self, request, queryset: QuerySet):
        if not hasattr(self.model, "is_deleted"):
            self.message_user(request, "该模型未启用 is_deleted 字段。", messages.WARNING)
            return
        now = timezone.now()
        field_names = {f.name for f in self.model._meta.fields}
        updates = {"is_deleted": True}
        if "deleted_by" in field_names:
            updates["deleted_by"] = request.user
        if "deleted_at" in field_names:
            updates["deleted_at"] = now
        affected = queryset.filter(is_deleted=False).update(**updates)
        self.message_user(request, f"成功软删除 {affected} 条记录。", messages.SUCCESS)

    @admin.action(description="恢复选中记录")
    def action_restore(self, request, queryset: QuerySet):
        if not hasattr(self.model, "is_deleted"):
            self.message_user(request, "该模型未启用 is_deleted 字段。", messages.WARNING)
            return
        field_names = {f.name for f in self.model._meta.fields}
        updates = {"is_deleted": False}
        if "deleted_by" in field_names:
            updates["deleted_by"] = None
        if "deleted_at" in field_names:
            updates["deleted_at"] = None
        affected = queryset.filter(is_deleted=True).update(**updates)
        self.message_user(request, f"已恢复 {affected} 条记录。", messages.SUCCESS)


class ExportCsvMixin:
    """按 list_display 导出 CSV（若未设置 list_display，则导出所有字段）"""
    export_filename_prefix = None  # 子类可覆盖

    @admin.action(description="导出所选为 CSV")
    def action_export_csv(self, request, queryset: QuerySet):
        meta = self.model._meta
        fields = [f for f in getattr(self, "list_display", ()) if f not in ("action_checkbox",)]
        if not fields:
            fields = [f.name for f in meta.fields]
        filename_prefix = self.export_filename_prefix or meta.model_name
        filename = f"{filename_prefix}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.csv"

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f"attachment; filename={filename}"
        writer = csv.writer(response)
        writer.writerow(fields)
        for obj in queryset:
            row = []
            for field in fields:
                try:
                    val = getattr(obj, field)
                    val = val() if callable(val) else val
                except Exception as e:
                    val = f"<ERR:{e}>"
                row.append("" if val is None else str(val))
            writer.writerow(row)
        return response


class PrintViewMixin:
    """简易打印：把当前选择按 list_display 渲染成可打印 HTML"""
    @admin.action(description="打印所选（简易）")
    def action_print_selected(self, request, queryset: QuerySet):
        fields = [f for f in getattr(self, "list_display", ()) if f not in ("action_checkbox",)]
        title = f"{self.model._meta.verbose_name} 打印"
        html = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>{title}</title>",
            "<style>body{font-family:system-ui,Segoe UI,Arial;margin:20px;}table{border-collapse:collapse;width:100%;}"
            "th,td{border:1px solid #ddd;padding:6px;font-size:13px;}th{background:#f5f5f5;text-align:left;}"
            "@media print{.noprint{display:none}}</style>",
            "</head><body>",
            f"<h2>{title}</h2><button class='noprint' onclick='window.print()'>打印</button>",
            "<table><thead><tr>",
            *[f"<th>{f}</th>" for f in fields],
            "</tr></thead><tbody>",
        ]
        for obj in queryset:
            html.append("<tr>")
            for f in fields:
                try:
                    v = getattr(obj, f)
                    v = v() if callable(v) else v
                except Exception as e:
                    v = f"<ERR:{e}>"
                html.append(f"<td>{'' if v is None else v}</td>")
            html.append("</tr>")
        html.extend(["</tbody></table>", "</body></html>"])
        return HttpResponse("".join(html))


class ReviewFlowMixin:
    """轻量审核：审核=启用；反审核=停用（若模型有 is_active 字段）"""
    actions = ("action_review_approve", "action_review_revert")

    @admin.action(description="审核通过（设为启用）")
    def action_review_approve(self, request, queryset: QuerySet):
        if hasattr(self.model, "is_active"):
            cnt = queryset.update(is_active=True)
            self.message_user(request, f"已审核 {cnt} 条。", messages.SUCCESS)

    @admin.action(description="反审核（设为停用）")
    def action_review_revert(self, request, queryset: QuerySet):
        if hasattr(self.model, "is_active"):
            cnt = queryset.update(is_active=False)
            self.message_user(request, f"已反审核 {cnt} 条。", messages.WARNING)


class AuditStampedAdminMixin:
    """自动写入 created_by / updated_by（若模型包含这些字段）"""
    def save_model(self, request, obj, form, change):
        if not change and hasattr(obj, "created_by") and obj.created_by is None:
            obj.created_by = request.user
        if hasattr(obj, "updated_by"):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class AdvancedAdminBase(
    AuditStampedAdminMixin,
    ExportCsvMixin,
    PrintViewMixin,
    ReviewFlowMixin,
    SoftDeleteAdminMixin,
    admin.ModelAdmin,
):
    """
    统一基类：
    - 禁用硬删除（批量 & 详情页）
    - 提供：软删 / 恢复 / 导出 / 打印 / 审核
    - 配合 DeletedStatusFilter 使用可查看已删除记录
    """
    # 统一列出动作，避免被上游 mixin 的 actions 覆盖
    actions = (
        "action_soft_delete",
        "action_restore",
        "action_export_csv",
        "action_print_selected",
        "action_review_approve",
        "action_review_revert",
    )

    def get_actions(self, request):
        actions = super().get_actions(request)
        # 去掉 Django 自带“删除所选…”
        actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        # 彻底禁用详情页“删除”按钮（强制走软删）
        return False

    # class Media:
    #     css = {"all": ("admin_forms_right_labels.css",)}