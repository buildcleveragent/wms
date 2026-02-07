# apps/products/forms.py
from django import forms
from dal import autocomplete

from .models import ProductPackage, ProductUom  # ← 别忘了导入

class ProductPackageInlineForm(forms.ModelForm):
    class Meta:
        model = ProductPackage
        fields = "__all__"
        widgets = {
            "uom": autocomplete.ModelSelect2(
                url="uom-autocomplete",
                # 常量转发：only_count=1，用于后端筛 COUNT 类型
                forward=(autocomplete.ForwardConst("only_count", "1"),),
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 本地 queryset 也过滤一层（用于初始值/无 AJAX 回退）
        self.fields["uom"].queryset = ProductUom.objects.filter(is_active=True, kind="COUNT")


