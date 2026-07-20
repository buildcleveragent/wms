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
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils.html import format_html

from .audit import record_audit_event
from .models import AuditEvent, SystemLog, User, UserRoleScope
from .role_memberships import ROLE_GROUP_NAMES, sync_user_role_membership
from .roles import CANONICAL_ROLE_GROUP_NAMES, role_group_name

CANONICAL_GROUP_WARNING = (
    "这是 WMS 规范角色组：名称和角色身份固定，"
    "但超级管理员可以调整功能权限。"
    "修改权限不会改变 UserRoleScope 的货主/仓库数据范围；"
    "删除权限可能关闭 PDA 菜单"
    "或业务操作，增加权限可能开放敏感操作。"
)


def _permission_codes(manager):
    return sorted(
        f"{app_label}.{codename}"
        for app_label, codename in manager.select_related("content_type").values_list(
            "content_type__app_label",
            "codename",
        )
    )


def _group_authorization_snapshot(group):
    return {
        "name": group.name,
        "permissions": _permission_codes(group.permissions),
    }


def _user_authorization_snapshot(user):
    return {
        "is_superuser": bool(user.is_superuser),
        "groups": sorted(user.groups.values_list("name", flat=True)),
        "user_permissions": _permission_codes(user.user_permissions),
        "role_scopes": sorted(
            f"{role}:owner={owner_id or ''}:warehouse={warehouse_id or ''}:"
            f"active={int(is_active)}"
            for role, owner_id, warehouse_id, is_active in user.role_scopes.values_list(
                "role", "owner_id", "warehouse_id", "is_active"
            )
        ),
    }


def _authorization_diff(before, after, key):
    return {
        "added": sorted(set(after.get(key, ())) - set(before.get(key, ()))),
        "removed": sorted(set(before.get(key, ())) - set(after.get(key, ()))),
    }


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


class AuxiliaryGroupsMixin:
    def _configure_auxiliary_groups(self):
        field = self.fields.get("groups")
        if not field:
            return
        field.queryset = Group.objects.exclude(name__in=ROLE_GROUP_NAMES).order_by(
            "name"
        )
        field.label = "辅助权限组"
        field.help_text = (
            "这里只配置自定义辅助权限组；"
            "WMS 规范角色组由下方用户角色范围自动同步。"
        )


class CustomUserCreationForm(AuxiliaryGroupsMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        # 新增时展示的字段（含标准的 password1/password2）
        fields = ("username", "name", "email", "phone", "owner", "warehouse")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._configure_auxiliary_groups()


class CustomUserChangeForm(AuxiliaryGroupsMixin, PermissionMatrixMixin, UserChangeForm):
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
        self._configure_auxiliary_groups()
        self._apply_permission_matrix()


class UserRoleScopeInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        active_rows = []
        for form in self.forms:
            cleaned = getattr(form, "cleaned_data", {})
            if not cleaned or cleaned.get("DELETE") or not cleaned.get("is_active"):
                continue
            active_rows.append(cleaned)

        if self.instance.is_superuser and active_rows:
            raise forms.ValidationError(
                "超级管理员使用全局权限，不需要设置用户角色范围。"
            )

        roles = {row.get("role") for row in active_rows}
        roles.discard(None)
        if len(roles) > 1:
            raise forms.ValidationError("同一用户不能同时启用多个业务角色。")
        if roles:
            role = next(iter(roles))
            if role != UserRoleScope.Role.WAREHOUSE_BOSS and len(active_rows) > 1:
                raise forms.ValidationError(
                    "除仓库老板外，每个用户只能有一个活动范围。"
                )
            group_name = role_group_name(role)
            if not Group.objects.filter(name=group_name).exists():
                raise forms.ValidationError(
                    f"缺少规范角色组“{group_name}”，"
                    "请先执行 sync_wms_role_groups。"
                )


class UserRoleScopeInline(admin.TabularInline):
    model = UserRoleScope
    formset = UserRoleScopeInlineFormSet
    fields = ("role", "owner", "warehouse", "is_active", "created_at", "updated_at")
    raw_id_fields = ("owner", "warehouse")
    readonly_fields = ("created_at", "updated_at")
    extra = 1

    def get_extra(self, request, obj=None, **kwargs):
        return 1 if request.user.is_superuser else 0

    def has_add_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)


class WmsGroupAdminForm(PermissionMatrixMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_permission_matrix()
        if self.instance.pk and self.instance.name in CANONICAL_ROLE_GROUP_NAMES:
            self.fields["name"].disabled = True
            self.fields["name"].help_text = "规范角色组名称固定，不能修改。"
            self.fields["permissions"].help_text = CANONICAL_GROUP_WARNING

    def clean_name(self):
        name = self.cleaned_data["name"]
        if (
            self.instance.pk
            and self.instance.name in CANONICAL_ROLE_GROUP_NAMES
            and name != self.instance.name
        ):
            raise forms.ValidationError("WMS 规范角色组名称固定，不能修改。")
        return name


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User
    inlines = (UserRoleScopeInline,)

    list_display = (
        "username",
        "name",
        "email",
        "phone",
        "business_role_display",
        "business_scope_display",
        "is_active",
        "is_staff",
        "is_superuser",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "role_scopes__role",
        "role_scopes__owner",
        "role_scopes__warehouse",
    )
    search_fields = ("username", "name", "email", "phone")
    ordering = ("id",)

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("个人信息", {"fields": ("name", "email", "phone")}),
        (
            "权限",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "canonical_role_group_display",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "非 WMS 兼容设置",
            {
                "fields": ("owner", "warehouse"),
                "classes": ("collapse",),
                "description": (
                    "仅供 POS、商城等旧模块作为默认归属使用；"
                    "不会决定 WMS 角色、权限或货主/仓库数据范围。"
                ),
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
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "canonical_role_group_display",
                    "groups",
                ),
            },
        ),
        (
            "非 WMS 兼容设置",
            {
                "fields": ("owner", "warehouse"),
                "classes": ("collapse",),
                "description": (
                    "仅供 POS、商城等旧模块作为默认归属使用；"
                    "WMS 数据范围请在下方用户角色范围中设置。"
                ),
            },
        ),
    )

    filter_horizontal = ("groups",)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        readonly.append("canonical_role_group_display")
        if not request.user.is_superuser:
            readonly.extend(("is_superuser", "groups", "user_permissions"))
        return tuple(dict.fromkeys(readonly))

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("role_scopes", "groups")

    @admin.display(description="业务角色")
    def business_role_display(self, obj):
        roles = {
            scope.get_role_display()
            for scope in obj.role_scopes.all()
            if scope.is_active
        }
        return "、".join(sorted(roles)) or "—"

    @admin.display(description="WMS 数据范围")
    def business_scope_display(self, obj):
        targets = []
        for scope in obj.role_scopes.all():
            if not scope.is_active:
                continue
            target = scope.owner if scope.owner_id else scope.warehouse
            targets.append(str(target))
        return "、".join(sorted(targets)) or "—"

    @admin.display(description="系统规范角色组")
    def canonical_role_group_display(self, obj):
        if not obj or not obj.pk:
            return "保存角色范围后自动同步"
        names = sorted(
            obj.groups.filter(name__in=CANONICAL_ROLE_GROUP_NAMES).values_list(
                "name", flat=True
            )
        )
        return "、".join(names) or "未分配（保存角色范围后自动同步）"

    def save_model(self, request, obj, form, change):
        request._wms_user_auth_before = (
            _user_authorization_snapshot(type(obj).objects.get(pk=obj.pk))
            if change and obj.pk
            else {
                "is_superuser": False,
                "groups": [],
                "user_permissions": [],
                "role_scopes": [],
            }
        )
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        membership_change = sync_user_role_membership(form.instance)
        before = getattr(request, "_wms_user_auth_before", {})
        after = _user_authorization_snapshot(form.instance)
        if before == after:
            return
        record_audit_event(
            action="USER_AUTHORIZATION_UPDATE",
            module="accounts.authorization",
            request=request,
            obj=form.instance,
            before=before,
            after=after,
            metadata={
                "groups": _authorization_diff(before, after, "groups"),
                "user_permissions": _authorization_diff(
                    before,
                    after,
                    "user_permissions",
                ),
                "role_scopes": _authorization_diff(before, after, "role_scopes"),
                "canonical_group_sync": {
                    "role": membership_change.role or "",
                    "desired_group": membership_change.desired_group or "",
                    "added": list(membership_change.added),
                    "removed": list(membership_change.removed),
                },
            },
        )


try:
    admin.site.unregister(Group)
except NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    form = WmsGroupAdminForm
    filter_horizontal = ()

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_active or not request.user.is_superuser:
            return False
        return obj is None or obj.name not in CANONICAL_ROLE_GROUP_NAMES

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def save_model(self, request, obj, form, change):
        request._wms_group_auth_before = (
            _group_authorization_snapshot(Group.objects.get(pk=obj.pk))
            if change and obj.pk
            else {}
        )
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        before = getattr(request, "_wms_group_auth_before", {})
        after = _group_authorization_snapshot(form.instance)
        if before == after:
            return
        record_audit_event(
            action="ROLE_GROUP_UPDATE" if change else "ROLE_GROUP_CREATE",
            module="accounts.authorization",
            request=request,
            obj=form.instance,
            before=before,
            after=after,
            metadata={
                "canonical": form.instance.name in CANONICAL_ROLE_GROUP_NAMES,
                "permissions": _authorization_diff(before, after, "permissions"),
            },
        )

    def delete_model(self, request, obj):
        before = _group_authorization_snapshot(obj)
        group_id = obj.pk
        group_name = obj.name
        super().delete_model(request, obj)
        deleted_group = Group(pk=group_id, name=group_name)
        record_audit_event(
            action="ROLE_GROUP_DELETE",
            module="accounts.authorization",
            request=request,
            obj=deleted_group,
            before=before,
            metadata={"canonical": False},
        )


@admin.register(UserRoleScope)
class UserRoleScopeAdmin(admin.ModelAdmin):
    list_display = (
        "user_admin_link",
        "role",
        "owner",
        "warehouse",
        "is_active",
        "updated_at",
    )
    list_filter = ("role", "is_active", "owner", "warehouse")
    search_fields = ("user__username", "user__name", "owner__name", "warehouse__name")
    raw_id_fields = ("user", "owner", "warehouse")
    readonly_fields = ("created_at", "updated_at")
    list_display_links = None

    @admin.display(description="用户")
    def user_admin_link(self, obj):
        url = reverse("admin:accounts_user_change", args=(obj.user_id,))
        return format_html('<a href="{}">{}</a>', url, obj.user)

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    readonly_fields = (
        "occurred_at",
        "username",
        "real_name",
        "log_type",
        "module",
        "content",
        "computer_name",
        "ip_address",
        "motherboard_sn",
        "hdd_sn",
        "owner",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # 展示操作内容的简短摘要（简化展示）
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            short_content=models.functions.Substr("content", 1, 50)
        )


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "username",
        "action",
        "module",
        "object_type",
        "object_id",
        "owner",
        "warehouse",
        "succeeded",
        "request_id",
    )
    list_filter = ("action", "module", "succeeded", "occurred_at")
    search_fields = ("username", "object_type", "object_id", "request_id", "event_hash")
    date_hierarchy = "occurred_at"
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
