from django.contrib import admin
from django.db import transaction

from .models import SystemSetting

class BaseAdmin(admin.ModelAdmin):
    list_per_page = 30
    actions = ["approve_selected", "unapprove_selected"]

    def approve_selected(self, request, queryset):
        # 确保模型有 status 字段且可以修改
        if hasattr(queryset.model, "status"):
            # 使用事务处理，确保批量操作的原子性
            with transaction.atomic():
                count = queryset.update(status="APPROVED")
                self.message_user(request, f"成功审核了 {count} 条记录。")
        else:
            self.message_user(request, "当前模型没有 `status` 字段，无法执行审核操作。", level="error")

    def unapprove_selected(self, request, queryset):
        # 确保模型有 status 字段且可以修改
        if hasattr(queryset.model, "status"):
            # 使用事务处理，确保批量操作的原子性
            with transaction.atomic():
                count = queryset.update(status="NEW")
                self.message_user(request, f"成功反审核了 {count} 条记录。")
        else:
            self.message_user(request, "当前模型没有 `status` 字段，无法执行反审核操作。", level="error")


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = (
        "namespace",
        "key",
        "name",
        "value_type",
        "value",
        "client_visible",
        "is_active",
        "sort_order",
    )
    list_filter = ("namespace", "value_type", "client_visible", "is_active")
    search_fields = ("namespace", "key", "name", "description")
    ordering = ("namespace", "sort_order", "key")
    fieldsets = (
        ("基础信息", {"fields": ("namespace", "key", "name", "description")}),
        ("配置值", {"fields": ("value_type", "value", "default_value", "options")}),
        ("使用范围", {"fields": ("client_visible", "is_active", "sort_order")}),
    )
