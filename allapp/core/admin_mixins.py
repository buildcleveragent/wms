# core/admin_mixins.py
from decimal import Decimal
from django import forms
from django.db import models
from allapp.core.admin_widgets import TrimDecimalWidget

# allapp/core/admin_mixins.py
from django import forms
from allapp.core.admin_widgets import TrimDecimalWidget

class InlineForceTrimDecimalMixin:
    """
    绝对生效版：不依赖 formfield_for_dbfield。
    直接替换 Inline 最终使用的 Form 类，在 __init__ 中把所有 DecimalField 的 widget 改成 TrimDecimalWidget。
    """
    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)
        BaseForm = FormSet.form

        class PatchedForm(BaseForm):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                for f in self.fields.values():
                    if isinstance(f, forms.DecimalField):
                        # 强制换 widget（覆盖任何 Meta.widgets/NumberInput）
                        attrs = getattr(f.widget, "attrs", {}) | {"step": "any", "inputmode": "decimal"}
                        f.widget = TrimDecimalWidget(attrs=attrs)
                        # 新增空表单时把 0 显示为“0”
                        if f.initial in (None, '', 0):
                            f.initial = 0

        FormSet.form = PatchedForm
        return FormSet



class DecimalPrettyInitialMixin:
    """
    - 把所有 DecimalField 用 TrimDecimalWidget 渲染（去掉尾随0）
    - 仅在“新增页”把 initial 的 0 显示为 '0'（不是 '0.000'）
    """
    formfield_overrides = {
        models.DecimalField: {'widget': TrimDecimalWidget(attrs={'step': 'any'})}
    }

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # 新增页才改 initial 的展示
        if obj is None:
            for field in form.base_fields.values():
                if isinstance(field, forms.DecimalField):
                    if field.initial in (None, '', 0, '0', '0.000', Decimal('0')):
                        field.initial = 0  # 显示为整数0
        return form


AUDIT_FIELDS = (
    "created_at", "updated_at", "created_by", "updated_by",
    "is_deleted", "deleted_at", "deleted_by",
)

def existing_audit_fields(model):
    names = {f.name for f in model._meta.get_fields()}
    return [f for f in AUDIT_FIELDS if f in names]

class HideAuditFieldsMixin:
    """在表单页隐藏审计字段"""
    def get_exclude(self, request, obj=None):
        base = super().get_exclude(request, obj) if hasattr(super(), "get_exclude") else None
        base = list(base) if base else []
        return list(dict.fromkeys(base + existing_audit_fields(self.model)))

class HideAuditInlineMixin:
    def get_formset(self, request, obj=None, **kwargs):
        exclude = list(kwargs.get("exclude") or [])
        exclude += existing_audit_fields(self.model)
        kwargs["exclude"] = list(dict.fromkeys(exclude))
        return super().get_formset(request, obj, **kwargs)
