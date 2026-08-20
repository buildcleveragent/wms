from django import forms
from django.contrib import admin
from django.db import transaction

from .models import PrintConfig, SecretSettingError, SystemSetting


class SystemSettingAdminForm(forms.ModelForm):
    value = forms.CharField(
        label="配置值",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    class Meta:
        model = SystemSetting
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        self._original_secret = bool(instance.pk and instance.is_secret)
        self._original_secret_value = instance.value if self._original_secret else ""
        if instance.is_secret:
            self.fields["value"].widget = forms.PasswordInput(
                render_value=False,
                attrs={"placeholder": "已配置；留空保持原值"},
            )
            self.fields["value"].help_text = "密钥将加密保存，页面不会回显原值。"
            self.initial["value"] = ""

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_secret") and cleaned.get("client_visible"):
            self.add_error("client_visible", "密钥配置禁止向前端公开。")
        if cleaned.get("is_secret") and cleaned.get("default_value"):
            self.add_error("default_value", "密钥配置禁止使用默认值。")
        if (
            cleaned.get("is_secret")
            and not self._original_secret
            and self.instance.pk
            and not cleaned.get("value")
        ):
            self.add_error("value", "切换为密钥配置时必须输入新的密钥值。")
        if cleaned.get("is_secret") and cleaned.get("value"):
            candidate = SystemSetting(is_secret=True)
            try:
                candidate.set_secret_value(cleaned["value"])
            except SecretSettingError as exc:
                self.add_error("value", str(exc))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.is_secret:
            plaintext = self.cleaned_data.get("value") or ""
            if plaintext:
                instance.set_secret_value(plaintext)
            elif self._original_secret:
                instance.value = self._original_secret_value
            else:
                instance.value = ""
            instance.client_visible = False
            instance.default_value = ""
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
            self.message_user(
                request, "当前模型没有 `status` 字段，无法执行审核操作。", level="error"
            )

    def unapprove_selected(self, request, queryset):
        # 确保模型有 status 字段且可以修改
        if hasattr(queryset.model, "status"):
            # 使用事务处理，确保批量操作的原子性
            with transaction.atomic():
                count = queryset.update(status="NEW")
                self.message_user(request, f"成功反审核了 {count} 条记录。")
        else:
            self.message_user(
                request,
                "当前模型没有 `status` 字段，无法执行反审核操作。",
                level="error",
            )


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    form = SystemSettingAdminForm
    list_display = (
        "namespace",
        "key",
        "name",
        "value_type",
        "display_value",
        "is_secret",
        "client_visible",
        "is_active",
        "sort_order",
    )
    list_filter = (
        "namespace",
        "value_type",
        "is_secret",
        "client_visible",
        "is_active",
    )
    search_fields = ("namespace", "key", "name", "description")
    ordering = ("namespace", "sort_order", "key")
    fieldsets = (
        ("基础信息", {"fields": ("namespace", "key", "name", "description")}),
        (
            "配置值",
            {"fields": ("value_type", "is_secret", "value", "default_value", "options")},
        ),
        ("使用范围", {"fields": ("client_visible", "is_active", "sort_order")}),
    )

    @admin.display(description="配置值")
    def display_value(self, obj):
        return (
            "••••••••（已配置）"
            if obj.is_secret and obj.value
            else ("（未配置）" if obj.is_secret else obj.value)
        )


@admin.register(PrintConfig)
class PrintConfigAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "module",
        "print_method",
        "printer_type",
        "paper_width",
        "paper_height",
        "sheet_width",
        "is_default",
        "is_active",
        "sort_order",
        "updated_at",
    )
    list_filter = (
        "module",
        "print_method",
        "printer_type",
        "is_default",
        "is_active",
    )
    search_fields = ("code", "name", "remark")
    ordering = ("module", "sort_order", "code")
    fieldsets = (
        (
            "基础信息",
            {
                "fields": (
                    "code",
                    "name",
                    "module",
                    "print_method",
                    "printer_type",
                    "paper_mode",
                    "remark",
                )
            },
        ),
        (
            "纸张与版面",
            {
                "fields": (
                    "paper_width",
                    "paper_height",
                    "page_size_css",
                    "page_margin",
                    "sheet_width",
                    (
                        "sheet_padding_top",
                        "sheet_padding_right",
                        "sheet_padding_bottom",
                        "sheet_padding_left",
                    ),
                )
            },
        ),
        (
            "字体与字号",
            {
                "fields": (
                    "font_family",
                    "body_font_size",
                    "company_font_size",
                    "title_font_size",
                    "meta_font_size",
                    "table_font_size",
                    "table_header_font_size",
                    "money_font_size",
                    "footer_font_size",
                )
            },
        ),
        (
            "行距与间距",
            {
                "fields": (
                    "body_line_height",
                    "meta_line_height",
                    "table_line_height",
                    "money_line_height",
                    "footer_line_height",
                    "table_cell_padding",
                    "money_gap",
                    "money_margin_top",
                )
            },
        ),
        ("扩展与状态", {"fields": ("extra", "is_default", "is_active", "sort_order")}),
    )
