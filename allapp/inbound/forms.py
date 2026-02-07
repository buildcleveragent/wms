# -*- coding: utf-8 -*-
"""
allapp/inbound/forms.py  —  冻结版
要点：
1) InboundOrderFilterForm.status 与模型字段 submit_status 对齐（DRAFT/SUBMITTED）。
2) InboundOrderLineForm 的 aux_uom 使用 ModelChoiceField+Select，并按“已选商品优先，
   否则按父单据 owner”过滤；product 也按 owner 收敛。
3) 自定义 InlineFormSet 在未选择商品时，为每行兜底按 owner 收敛 aux_uom 列表。
"""

from django import forms
from django.forms import ModelChoiceField, BaseInlineFormSet, inlineformset_factory

from allapp.baseinfo.models import Owner, Supplier
from allapp.products.models import Product, ProductPackage

from .models import (
    InboundOrder, InboundOrderLine,
    InboundReceipt, InboundReceiptLine,
    InboundOrderReturnInfo, ReturnInspection,
    Lot,
)

# ============================ 过滤表单 ============================
class InboundOrderFilterForm(forms.Form):
    q = forms.CharField(label="单号关键词", required=False)
    owner = forms.ModelChoiceField(label="货主", required=False, queryset=Owner.objects.all())
    supplier = forms.ModelChoiceField(label="供应商", required=False, queryset=Supplier.objects.all())
    date_from = forms.DateField(label="起始日期", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="截止日期", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    # 与模型 submit_status 对齐
    _status_choices = getattr(
        InboundOrder._meta.get_field("submit_status"),
        "choices",
        [("DRAFT", "草稿"), ("SUBMITTED", "已提交")],
    )
    status = forms.ChoiceField(
        label="提交状态",
        required=False,
        choices=[("", "全部")] + list(_status_choices),
    )

# ============================ 订单主表 ============================
class InboundOrderForm(forms.ModelForm):
    class Meta:
        model = InboundOrder
        fields = [
            "biz_date", "owner", "supplier", "src_bill_no",
            "inbound_type", "delivery_method", "eta",
            "address", "memo", "submit_status",

        ]
        widgets = {
            "biz_date": forms.DateInput(attrs={"type": "date", "class": "input-cell"}),
            "eta": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "input-cell"}),
            "address": forms.TextInput(attrs={"class": "input-cell"}),
            "memo": forms.Textarea(attrs={"rows": 2, "class": "input-cell"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 可根据登录用户在 View 中进一步限制 owner 的 queryset
        if isinstance(self.fields.get("owner"), ModelChoiceField):
            self.fields["owner"].queryset = Owner.objects.all()
        if isinstance(self.fields.get("supplier"), ModelChoiceField):
            self.fields["supplier"].queryset = Supplier.objects.all()

# ============================ 订单明细 ============================
class InboundOrderLineForm(forms.ModelForm):
    """
    - product/aux_uom 的可选项按 owner/已选商品收敛
    - 其余校验交由 models.clean() 兜底
    """
    class Meta:
        model = InboundOrderLine
        fields = [
            "line_no",
            "product", "aux_qty", "aux_uom", "aux_price",
            "base_qty", "base_price", "base_uom",
            "lot_no",  "note",
        ]
        widgets = {
            "aux_qty": forms.NumberInput(attrs={"step": "0.001", "class": "input-cell"}),
            "aux_uom": forms.Select(attrs={"class": "input-cell"}),  # 改为下拉
            "aux_price": forms.NumberInput(attrs={"step": "0.0001", "class": "input-cell"}),
            "base_qty": forms.NumberInput(attrs={"step": "0.001", "class": "input-cell"}),
            "base_price": forms.NumberInput(attrs={"step": "0.0001", "class": "input-cell"}),
            "lot_no": forms.TextInput(attrs={"class": "input-cell"}),
            "note": forms.Textarea(attrs={"rows": 1, "class": "input-cell"}),
        }

    def __init__(self, *args, **kwargs):
        # 可由 formset 传入，便于无实例的新行也能拿到 owner
        self._parent_order = kwargs.pop("parent_order", None)
        super().__init__(*args, **kwargs)

        # 1) product 按 owner 收敛
        order = self._parent_order or getattr(self.instance, "order", None)
        owner_id = getattr(order, "owner_id", None)
        if owner_id and "product" in self.fields:
            self.fields["product"].queryset = Product.objects.filter(
                owner_id=owner_id
            ).select_related("base_uom")

        # 2) aux_uom：优先按“已选商品”过滤；否则按 owner 收敛
        # 提取当前行选中的 product_id（POST 优先）
        prod_key = f"{self.prefix}-product" if getattr(self, "prefix", None) else "product"
        prod_id = self.data.get(prod_key) or self.initial.get("product") or getattr(self.instance, "product_id", None)
        try:
            prod_id = int(prod_id) if prod_id not in ("", None) else None
        except (TypeError, ValueError):
            prod_id = None

        self.fields["aux_uom"] = ModelChoiceField(
            label="辅助包装", required=False, queryset=ProductPackage.objects.none(),
            widget=forms.Select(attrs={"class": "input-cell"})
        )
        if prod_id:
            self.fields["aux_uom"].queryset = ProductPackage.objects.filter(product_id=prod_id)
        elif owner_id:
            self.fields["aux_uom"].queryset = ProductPackage.objects.filter(product__owner_id=owner_id)

# ============================ 明细表单集 ============================
class _InboundOrderLineFormSet(BaseInlineFormSet):
    """
    兜底：如果某行没有选择商品，也没有 parent_order 传入，
    则按父单据 owner 收敛 aux_uom 的 queryset，避免出现全量包装列表。
    同时把 product 也按 owner 收敛，便于新建行选择。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        owner_id = getattr(self.instance, "owner_id", None)
        for form in self.forms:
            if isinstance(form, InboundOrderLineForm) and owner_id:
                # product 收敛
                if "product" in form.fields:
                    form.fields["product"].queryset = Product.objects.filter(
                        owner_id=owner_id
                    ).select_related("base_uom")
                # aux_uom 兜底收敛
                if "aux_uom" in form.fields and not form.fields["aux_uom"].queryset.exists():
                    form.fields["aux_uom"].queryset = ProductPackage.objects.filter(product__owner_id=owner_id)

    def get_form_kwargs(self, index):
        """
        把 parent_order 传给每个子表单，便于其在 __init__ 中按 owner 过滤。
        """
        kwargs = super().get_form_kwargs(index)
        kwargs["parent_order"] = self.instance
        return kwargs

# 对外导出的 formset（视图里直接引用）
InboundOrderLineFormSet = inlineformset_factory(
    InboundOrder,
    InboundOrderLine,
    form=InboundOrderLineForm,
    formset=_InboundOrderLineFormSet,
    extra=0,
    can_delete=True,
)








