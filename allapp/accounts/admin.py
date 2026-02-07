from django import forms
from django.contrib import admin
from django.db import models
from .models import SystemLog,User

from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import User

class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        # 新增时展示的字段（含标准的 password1/password2）
        fields = ("username", "name", "email", "phone", "owner",)

class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        # 编辑时展示的字段（密码是已加密的 password 字段，走只读小部件）
        fields = ("username", "name", "email", "phone", "owner",
                  "is_active", "is_staff", "is_superuser", "groups", "user_permissions")

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User

    list_display = ("username", "name", "email", "phone", "owner",
                    "is_active", "is_staff", "is_superuser")
    list_filter = ("is_active", "is_staff", "is_superuser", "owner", "groups")
    search_fields = ("username", "name", "email", "phone")
    ordering = ("id",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("个人信息", {"fields": ("name", "email", "phone", "owner", )}),
        ("权限", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("重要日期", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            # 注意：这里必须是 password1/password2（来自 UserCreationForm）
            "fields": ("username", "name", "email", "phone", "owner",
                       "password1", "password2", "is_active", "is_staff", "is_superuser", "groups"),
        }),
    )

    filter_horizontal = ("groups", "user_permissions")



@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at", "username", "log_type", "module", "owner", "ip_address", "real_name"
    )
    list_filter = ("log_type", "owner", "module", "occurred_at")
    search_fields = ("username", "real_name", "content", "ip_address")
    ordering = ("-occurred_at", "-id")  # 默认按操作时间降序排序
    date_hierarchy = "occurred_at"  # 为操作时间创建可过滤的日期层级

    # 展示清晰的日志内容摘要（避免内容过长）
    def short_content(self, obj):
        return obj.content[:50]  # 显示操作内容的前 50 个字符

    short_content.short_description = "操作内容（简短）"  # 自定义列标题

    # 让编辑页面显示更友好
    fieldsets = (
        (None, {
            "fields": ("username", "real_name", "log_type", "module", "content")
        }),
        ("系统信息", {
            "fields": ("computer_name", "ip_address", "motherboard_sn", "hdd_sn")
        }),
        ("日志相关", {
            "fields": ("owner", "occurred_at"),
            "classes": ("collapse",)
        })
    )
    readonly_fields = ("occurred_at",)  # 禁止编辑操作日期

    # 展示操作内容的简短摘要（简化展示）
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(short_content=models.functions.Substr("content", 1, 50))
