from collections import OrderedDict

from django import forms
from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.db import models

from .models import SystemLog, User


class PermissionMatrixWidget(forms.SelectMultiple):
    template_name = "admin/widgets/permission_matrix.html"

    action_order = ("view", "add", "change", "delete")
    action_labels = {
        "view": "查看",
        "add": "新增",
        "change": "编辑",
        "delete": "删除",
    }

    class Media:
        css = {"all": ("admin/custom/permission_matrix.css",)}
        js = ("admin/custom/permission_matrix.js",)

    def get_context(self, name, value, attrs):
        context = forms.Widget.get_context(self, name, value, attrs)
        selected_values = self._selected_values(value)
        groups = self._build_groups(selected_values)
        context["widget"].update(
            {
                "groups": groups,
                "action_order": [
                    {"key": key, "label": self.action_labels[key]}
                    for key in self.action_order
                ],
                "selected_count": sum(group["selected_count"] for group in groups),
                "total_count": sum(group["total_count"] for group in groups),
            }
        )
        return context

    def _selected_values(self, value):
        if value is None:
            return set()
        if hasattr(value, "values_list"):
            value = value.values_list("pk", flat=True)
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        return {str(item) for item in value}

    def _queryset(self):
        queryset = getattr(self.choices, "queryset", None)
        if queryset is None and getattr(self.choices, "field", None):
            queryset = self.choices.field.queryset
        if queryset is None:
            queryset = Permission.objects.all()
        return queryset.select_related("content_type").order_by(
            "content_type__app_label",
            "content_type__model",
            "codename",
        )

    def _build_groups(self, selected_values):
        app_groups = OrderedDict()
        for permission in self._queryset():
            content_type = permission.content_type
            app_label = content_type.app_label
            app_group = app_groups.setdefault(
                app_label,
                {
                    "key": app_label,
                    "label": self._app_label(app_label),
                    "models": OrderedDict(),
                    "selected_count": 0,
                    "total_count": 0,
                },
            )
            model_key = content_type.model
            model_group = app_group["models"].setdefault(
                model_key,
                {
                    "key": f"{app_label}.{model_key}",
                    "label": self._model_label(content_type),
                    "code": f"{app_label}.{model_key}",
                    "actions": {},
                    "extras": [],
                    "selected_count": 0,
                    "total_count": 0,
                },
            )

            option = {
                "id": str(permission.pk),
                "label": self._permission_label(permission),
                "codename": permission.codename,
                "checked": str(permission.pk) in selected_values,
            }
            action = self._default_action(permission, content_type)
            if action:
                option["label"] = self.action_labels[action]
                model_group["actions"][action] = option
            else:
                model_group["extras"].append(option)

            model_group["total_count"] += 1
            app_group["total_count"] += 1
            if option["checked"]:
                model_group["selected_count"] += 1
                app_group["selected_count"] += 1

        groups = []
        for app_group in app_groups.values():
            models = []
            for model_group in app_group["models"].values():
                model_group["cells"] = [
                    {
                        "key": key,
                        "label": self.action_labels[key],
                        "permission": model_group["actions"].get(key),
                    }
                    for key in self.action_order
                ]
                search_parts = [
                    app_group["label"],
                    model_group["label"],
                ]
                for permission in model_group["actions"].values():
                    search_parts.append(permission["label"])
                for permission in model_group["extras"]:
                    search_parts.append(permission["label"])
                model_group["search_text"] = " ".join(
                    str(part) for part in search_parts
                ).lower()
                models.append(model_group)
            app_group["models"] = models
            groups.append(app_group)
        return groups

    def _default_action(self, permission, content_type):
        for action in self.action_order:
            if permission.codename == f"{action}_{content_type.model}":
                return action
        return ""

    def _app_label(self, app_label):
        try:
            return str(apps.get_app_config(app_label).verbose_name)
        except LookupError:
            return app_label

    def _model_label(self, content_type):
        model_class = content_type.model_class()
        if model_class:
            return str(model_class._meta.verbose_name_plural)
        return content_type.model

    def _permission_label(self, permission):
        label = str(permission.name)
        replacements = {
            "Can view ": "可查看",
            "Can add ": "可新增",
            "Can change ": "可编辑",
            "Can delete ": "可删除",
        }
        for prefix, translated in replacements.items():
            if label.startswith(prefix):
                return f"{translated}{label.removeprefix(prefix)}"
        return label


class PermissionMatrixMixin:
    def _apply_permission_matrix(self):
        field = self.fields.get("permissions") or self.fields.get("user_permissions")
        if field:
            field.required = False
            field.widget = PermissionMatrixWidget()
            field.widget.choices = field.choices
            field.help_text = "按应用和功能项勾选权限；建议优先通过组授权。"


class CustomUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        # 新增时展示的字段（含标准的 password1/password2）
        fields = ("username", "name", "email", "phone", "owner", "warehouse")


class CustomUserChangeForm(PermissionMatrixMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        # 编辑时展示的字段（密码是已加密的 password 字段，走只读小部件）
        fields = (
            "username",
            "name",
            "email",
            "phone",
            "owner",
            "warehouse",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_permission_matrix()


class WmsGroupAdminForm(PermissionMatrixMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_permission_matrix()


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User

    list_display = (
        "username",
        "name",
        "email",
        "phone",
        "owner",
        "warehouse",
        "is_active",
        "is_staff",
        "is_superuser",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "owner",
        "warehouse",
        "groups",
    )
    search_fields = ("username", "name", "email", "phone")
    ordering = ("id",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("个人信息", {"fields": ("name", "email", "phone", "owner", "warehouse")}),
        (
            "权限",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("重要日期", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                # 注意：这里必须是 password1/password2（来自 UserCreationForm）
                "fields": (
                    "username",
                    "name",
                    "email",
                    "phone",
                    "owner",
                    "warehouse",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                ),
            },
        ),
    )

    filter_horizontal = ("groups",)


try:
    admin.site.unregister(Group)
except NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    form = WmsGroupAdminForm
    filter_horizontal = ()


@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "username",
        "log_type",
        "module",
        "owner",
        "ip_address",
        "real_name",
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
        (None, {"fields": ("username", "real_name", "log_type", "module", "content")}),
        (
            "系统信息",
            {"fields": ("computer_name", "ip_address", "motherboard_sn", "hdd_sn")},
        ),
        ("日志相关", {"fields": ("owner", "occurred_at"), "classes": ("collapse",)}),
    )
    readonly_fields = ("occurred_at",)  # 禁止编辑操作日期

    # 展示操作内容的简短摘要（简化展示）
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            short_content=models.functions.Substr("content", 1, 50)
        )
