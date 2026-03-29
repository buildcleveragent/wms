# allapp/inventory/admin.py
from .models import (
    InventoryDetail,
    InventorySnapshotDaily,
    InventorySummary,
    InventoryTransaction,

)
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.db import models as dj_models
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.urls import path, reverse

from allapp.baseinfo.models import Owner
from allapp.products.models import Product
from allapp.locations.models import Warehouse, Location

from .models import (
    InventoryQuickInboundAdjust,
    InventoryQuickOutboundAdjust,
)
from .services_quick_adjust import QuickAdjustInput, quick_adjust_via_post_task
# ============== 公共小工具 ==============
def _safe_fields(model, candidates):
    """仅返回当前模型真实存在的字段名，避免因为基类差异报错。"""
    model_fields = {f.name for f in model._meta.get_fields()}
    return [f for f in candidates if f in model_fields]

# ============== 1) 现存量 ==============
@admin.register(InventoryDetail)
class InventoryDetailAdmin(admin.ModelAdmin):
    list_display = _safe_fields(
        InventoryDetail,
        [
            "owner", "product", "subwarehouse","zone_type", "location",
            "batch_no", "production_date", "expiry_date", "serial_no",
            "onhand_qty", "allocated_qty", "locked_qty", "damaged_qty", "available_qty",
            "is_active",
        ],
    )
    list_filter = _safe_fields(
        InventoryDetail,
        ["owner", "subwarehouse","zone_type", "location", "product", "is_active"],
    )
    search_fields = [
        # 关联字段名请按你真实的 Product/Owner/Location 模型字段做微调
        "batch_no", "serial_no",
        "product__code", "product__name",
        "owner__code", "owner__name",
        "subwarehouse__code", "subwarehouse__name",
        "location__code", "location__name",
    ]
    # autocomplete_fields = _safe_fields(
    #     InventoryDetail,
    #     ["owner", "product",  "location", "lot"],
    # )
    ordering = _safe_fields(InventoryDetail, ["expiry_date", "owner", "product"])
    list_per_page = 50
    list_select_related = ("owner", "product", "warehouse", "subwarehouse", "location")

    fieldsets = (
        ("维度", {
            "fields": _safe_fields(InventoryDetail, ["owner", "product", "subwarehouse", "zone_type", "location", "is_active"])
        }),
        ("批次/效期/序列", {
            "fields": _safe_fields(InventoryDetail, ["lot", "batch_no", "production_date", "expiry_date", "serial_no"])
        }),
        ("数量（系统维护口径）", {
            "fields": _safe_fields(
                InventoryDetail,
                ["base_unit", "product_serial_control", "onhand_qty", "allocated_qty", "locked_qty", "damaged_qty", "available_qty"],
            )
        }),
    )

# ============== 2) 汇总 ==============
@admin.register(InventorySummary)
class InventorySummaryAdmin(admin.ModelAdmin):
    list_display = _safe_fields(
        InventorySummary,
        [
            "owner", "product",
            "onhand_qty", "allocated_qty", "locked_qty", "damaged_qty", "available_qty",
            "base_unit", "is_active",
        ],
    )
    list_filter = _safe_fields(InventorySummary, ["owner", "product", "is_active"])
    search_fields = [
        "product__code", "product__name",
        "owner__code", "owner__name",
    ]
    autocomplete_fields = _safe_fields(InventorySummary, ["owner", "product"])
    readonly_fields = _safe_fields(
        InventorySummary,
        ["base_unit", "onhand_qty", "allocated_qty", "locked_qty", "damaged_qty", "available_qty"],
    )
    ordering = _safe_fields(InventorySummary, ["owner", "product"])
    list_per_page = 50
    list_select_related = ("owner", "product")

    @admin.action(description="批量设为启用")
    def action_mark_active(self, request, queryset):
        if "is_active" in {f.name for f in InventorySummary._meta.get_fields()}:
            queryset.update(is_active=True)

    @admin.action(description="批量设为停用")
    def action_mark_inactive(self, request, queryset):
        if "is_active" in {f.name for f in InventorySummary._meta.get_fields()}:
            queryset.update(is_active=False)

    actions = ["action_mark_active", "action_mark_inactive"]

# ============== 3) 事务流水 ==============
@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = _safe_fields(
        InventoryTransaction,
        [
            "tx_type", "owner", "product", "subwarehouse", "location",
            "qty_delta", "batch_no", "production_date", "expiry_date", "serial_no",
            "src_model", "src_id", "src_line_id", "src_no",
            "pair_id",
        ],
    )
    list_filter = _safe_fields(
        InventoryTransaction,
        ["tx_type", "owner", "subwarehouse", "product", "location"],
    )
    search_fields = [
        "src_model", "src_no", "batch_no", "serial_no",
        "product__code", "product__name",
        "owner__code", "owner__name",
        "warehouse__code", "warehouse__name",
        "location__code", "location__name",
    ]
    # autocomplete_fields = _safe_fields(
    #     InventoryTransaction,
    #     ["owner", "product", "warehouse", "location"],
    # )
    readonly_fields = _safe_fields(InventoryTransaction, ["base_unit"])
    ordering = _safe_fields(InventoryTransaction, ["-id"])
    list_per_page = 50
    list_select_related = ("owner", "product", "warehouse", "location")


@admin.register(InventorySnapshotDaily)
class InventorySnapshotDailyAdmin(admin.ModelAdmin):
    list_display = (
        "snapshot_date",
        "owner",
        "warehouse",
        "location",
        "product",
        "onhand_qty",
        "available_qty",
        "allocated_qty",
        "locked_qty",
        "damaged_qty",
        "snapshot_source",
        "created_at",
    )
    list_filter = ("snapshot_date", "owner", "warehouse", "location", "product", "snapshot_source")
    search_fields = (
        "batch_no",
        "serial_no",
        "product__code",
        "product__name",
        "owner__code",
        "owner__name",
        "warehouse__code",
        "warehouse__name",
        "location__code",
        "location__name",
    )
    readonly_fields = (
        "snapshot_date",
        "owner",
        "warehouse",
        "location",
        "product",
        "batch_no",
        "production_date",
        "expiry_date",
        "serial_no",
        "onhand_qty",
        "available_qty",
        "allocated_qty",
        "locked_qty",
        "damaged_qty",
        "unit_volume_m3_snapshot",
        "location_area_m2_snapshot",
        "snapshot_source",
        "created_at",
    )
    ordering = ("-snapshot_date", "owner", "warehouse", "location", "product")
    list_per_page = 50
    list_select_related = ("owner", "warehouse", "location", "product")



# === 表单：管理员库存快调（允许数量为正/负；0 不允许） ===
class QuickAdjustForm(forms.Form):
    owner = forms.ModelChoiceField(label="货主", queryset=Owner.objects.all(), required=True)
    warehouse = forms.ModelChoiceField(label="仓库", queryset=Warehouse.objects.all(), required=True)
    location = forms.ModelChoiceField(label="库位（可选，留空走默认）",
                                      queryset=Location.objects.all(), required=False)

    # 商品下拉：初始给 none，按所选货主收窄（JS 联动 + 服务器端兜底）
    product = forms.ModelChoiceField(label="商品", queryset=Product.objects.none(), required=True)

    qty_delta = forms.DecimalField(label="数量变动（+增/-减）",
                                   required=True,
                                   help_text="正数表示增加库存，负数表示减少库存；不允许 0。")

    reason = forms.CharField(label="调整理由", required=False, max_length=120, initial="ADMIN_QUICK_ADJUST")
    remark = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    allow_negative = forms.BooleanField(label="允许可用量为负（谨慎）", required=False, initial=False)

    class Media:
        # 简单联动脚本：owner 变化 → product 下拉按货主刷新
        js = ("admin/quick_adjust_owner_linkage.js",)

    def __init__(self, *args, **kwargs):
        owner_id = kwargs.pop("owner_id", None)
        super().__init__(*args, **kwargs)
        # 初始根据 owner 收窄商品 queryset；无 owner 则不给选项（避免误选）
        if owner_id:
            self.fields["product"].queryset = Product.objects.filter(owner_id=owner_id).order_by("name")
        else:
            self.fields["product"].queryset = Product.objects.none()

    def clean_qty_delta(self):
        v = self.cleaned_data.get("qty_delta")
        if v is None or Decimal(v) == 0:
            raise forms.ValidationError("数量变动不能为 0")
        return Decimal(v)



# ——— 公共表单基类：owner/warehouse/location/product（商品随货主联动） ———
# class _BaseQuickAdjustForm(forms.Form):
#     owner = forms.ModelChoiceField(label="货主", queryset=Owner.objects.all(), required=True)
#     # warehouse = forms.ModelChoiceField(label="仓库", queryset=Warehouse.objects.all(), required=True)
#     # location = forms.ModelChoiceField(label="库位（可空：留空走默认）",
#     #                                   queryset=Location.objects.all(), required=False)
#
#     # 商品：初始不给选项，随 owner 联动；后端也会兜底收窄
#     product = forms.ModelChoiceField(label="商品", queryset=Product.objects.none(), required=True)
#
#     reason = forms.CharField(label="调整理由", required=False, max_length=120, initial="ADMIN_QUICK_ADJUST")
#     remark = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 2}))
#     allow_negative = forms.BooleanField(label="允许可用量为负（谨慎）", required=False, initial=False)
#
#     class Media:
#         # 复用我们之前的联动脚本：owner 改变会刷新商品下拉
#         js = ("admin/quick_adjust_owner_linkage.js",)
#
#     def __init__(self, *args, **kwargs):
#         owner_id = kwargs.pop("owner_id", None)
#         super().__init__(*args, **kwargs)
#         if owner_id:
#             self.fields["product"].queryset = Product.objects.filter(owner_id=owner_id).order_by("name")
#         else:
#             self.fields["product"].queryset = Product.objects.none()
#
#     # 给商品控件注入 data-source-url（供前端 JS 请求 JSON）
#     def inject_product_widget_attrs(self, request, url_name: str):
#         try:
#             w = self.fields["product"].widget
#             w.attrs["data-source-url"] = reverse(url_name)  # 由各 Admin 传入自己的 URL name
#             w.attrs["class"] = (w.attrs.get("class", "") + " vProductByOwner").strip()
#         except Exception:
#             pass


# ——— 入库快调：只允许“正数” ———

# ——— 公共表单基类：owner/warehouse/location/product（商品随货主联动） ———
class _BaseQuickAdjustForm(forms.Form):
    owner = forms.ModelChoiceField(label="货主", queryset=Owner.objects.all(), required=True)

    # 商品：初始不给选项，随 owner 联动；后端也会兜底收窄
    product = forms.ModelChoiceField(label="商品", queryset=Product.objects.none(), required=True)

    reason = forms.CharField(label="调整理由", required=False, max_length=120, initial="ADMIN_QUICK_ADJUST")
    remark = forms.CharField(label="备注", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    allow_negative = forms.BooleanField(label="允许可用量为负（谨慎）", required=False, initial=False)

    # 增加包装单位选择和包装数量字段
    pack_qty = forms.DecimalField(label="每包装的基本单位数量", required=True, initial=1)  # 每个包装包含的基本单位数量
    box_qty = forms.IntegerField(label="包装数量（箱数）", required=True, initial=0)  # 用户输入的包装数
    base_qty = forms.IntegerField(label="基本数量（零散的基本单位数）", required=True, initial=0)  # 零散的基本单位数量

    class Media:
        js = ("admin/quick_adjust_owner_linkage.js",)

    def __init__(self, *args, **kwargs):
        owner_id = kwargs.pop("owner_id", None)
        super().__init__(*args, **kwargs)
        if owner_id:
            self.fields["product"].queryset = Product.objects.filter(owner_id=owner_id).order_by("name")
        else:
            self.fields["product"].queryset = Product.objects.none()

    def inject_product_widget_attrs(self, request, url_name: str):
        try:
            w = self.fields["product"].widget
            w.attrs["data-source-url"] = reverse(url_name)  # 由各 Admin 传入自己的 URL name
            w.attrs["class"] = (w.attrs.get("class", "") + " vProductByOwner").strip()
        except Exception:
            pass


# class InboundQuickAdjustForm(_BaseQuickAdjustForm):
#     qty_in = forms.DecimalField(label="入库数量", required=True)
#
#     def clean_qty_in(self):
#         v = self.cleaned_data.get("qty_in")
#         try:
#             v = Decimal(v)
#         except Exception:
#             raise forms.ValidationError("请输入有效的数字")
#         if v <= 0:
#             raise forms.ValidationError("入库数量必须大于 0")
#         return v


# ——— 出库快调：只允许“正数”，提交时转为负数 ———

# ——— 入库快调：只允许“正数” ———
class InboundQuickAdjustForm(_BaseQuickAdjustForm):
    qty_in = forms.DecimalField(label="入库数量", required=True)

    def clean_qty_in(self):
        v = self.cleaned_data.get("qty_in")
        try:
            v = Decimal(v)
        except Exception:
            raise forms.ValidationError("请输入有效的数字")
        if v <= 0:
            raise forms.ValidationError("入库数量必须大于 0")
        return v

    def clean(self):
        cleaned_data = super().clean()
        # 获取用户输入的包装数量和基本数量
        box_qty = cleaned_data.get("box_qty")
        base_qty = cleaned_data.get("base_qty")
        pack_qty = cleaned_data.get("pack_qty")

        # 计算总的基本单位数量
        if box_qty is not None and base_qty is not None and pack_qty is not None:
            total_qty = (box_qty * pack_qty) + base_qty
            cleaned_data["qty_in"] = total_qty  # 将计算的总数量赋值给入库数量
        return cleaned_data


class OutboundQuickAdjustForm(_BaseQuickAdjustForm):
    qty_out = forms.DecimalField(label="出库数量", required=True)

    def clean_qty_out(self):
        v = self.cleaned_data.get("qty_out")
        try:
            v = Decimal(v)
        except Exception:
            raise forms.ValidationError("请输入有效的数字")
        if v <= 0:
            raise forms.ValidationError("出库数量必须大于 0")
        return v


# ——— 公共的 JSON 端点（按 owner 过滤商品）mixin ———
class _ProductOptionsMixin:
    def product_options_view(self, request):
        owner_id = request.GET.get("owner")
        q = (request.GET.get("q") or "").strip()
        qs = Product.objects.none()
        if owner_id:
            qs = Product.objects.filter(owner_id=owner_id)
        if q:
            qs = qs.filter(
                dj_models.Q(name__icontains=q) |
                dj_models.Q(sku__icontains=q) |
                dj_models.Q(gtin__icontains=q)
            )
        qs = qs.order_by("name")[:200]
        return JsonResponse({"results": [{"id": p.pk, "text": str(p)} for p in qs]})


# ——— 入库快调 Admin ———
@admin.register(InventoryQuickInboundAdjust)
class InboundQuickAdjustAdmin(_ProductOptionsMixin, admin.ModelAdmin):
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:inventory_inventoryquickinboundadjust_quick_adjust"))

    def get_urls(self):
        urls = super().get_urls()
        my = [
            path(
                "quick-adjust/",
                self.admin_site.admin_view(self.quick_adjust_view),
                name="inventory_inventoryquickinboundadjust_quick_adjust",
            ),
            path(
                "product-options/",
                self.admin_site.admin_view(self.product_options_view),
                name="inventory_inventoryquickinboundadjust_product_options",
            ),
        ]
        return my + urls

    def quick_adjust_view(self, request):
        if not request.user.is_superuser:
            return HttpResponseForbidden("仅限超级用户或库存管理员使用")

        owner_id = request.POST.get("owner") or request.GET.get("owner")
        if request.method == "POST":
            form = InboundQuickAdjustForm(request.POST, owner_id=owner_id)
            form.inject_product_widget_attrs(request, "admin:inventory_inventoryquickinboundadjust_product_options")
            if form.is_valid():
                try:
                    inp = QuickAdjustInput(
                        user=request.user,
                        owner=form.cleaned_data["owner"],
                        # warehouse=form.cleaned_data["warehouse"],
                        product=form.cleaned_data["product"],
                        qty_base_delta=form.cleaned_data["qty_in"],  # 入库=正数
                        # location=form.cleaned_data.get("location"),
                        reason=form.cleaned_data.get("reason") or "ADMIN_QUICK_ADJUST",
                        remark=form.cleaned_data.get("remark") or "ADMIN_QUICK_ADJUST",
                        allow_negative=bool(form.cleaned_data.get("allow_negative")),
                    )
                    quick_adjust_via_post_task(inp)
                    messages.success(request, "入库快调已过账。")
                    return redirect(
                        f"{reverse('admin:inventory_inventoryquickinboundadjust_quick_adjust')}?owner={inp.owner.pk}"
                    )
                except Exception as e:
                    messages.error(request, f"入库快调失败：{e}")
        else:
            form = InboundQuickAdjustForm(owner_id=owner_id)
            form.inject_product_widget_attrs(request, "admin:inventory_inventoryquickinboundadjust_product_options")

        ctx = {
            "opts": InventoryQuickInboundAdjust._meta,
            "form": form,
            "title": "入库快调",
            "media": self.media + form.media,
        }
        return render(request, "admin/inventory/quick_adjust_inbound_form.html", ctx)


# ——— 出库快调 Admin ———
@admin.register(InventoryQuickOutboundAdjust)
class OutboundQuickAdjustAdmin(_ProductOptionsMixin, admin.ModelAdmin):
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:inventory_inventoryquickoutboundadjust_quick_adjust"))

    def get_urls(self):
        urls = super().get_urls()
        my = [
            path(
                "quick-adjust/",
                self.admin_site.admin_view(self.quick_adjust_view),
                name="inventory_inventoryquickoutboundadjust_quick_adjust",
            ),
            path(
                "product-options/",
                self.admin_site.admin_view(self.product_options_view),
                name="inventory_inventoryquickoutboundadjust_product_options",  # 供前端反向
            ),
        ]
        return my + urls

    def quick_adjust_view(self, request):
        if not request.user.is_superuser:
            return HttpResponseForbidden("仅限超级用户或库存管理员使用")

        owner_id = request.POST.get("owner") or request.GET.get("owner")
        if request.method == "POST":
            form = OutboundQuickAdjustForm(request.POST, owner_id=owner_id)
            form.inject_product_widget_attrs(request, "admin:inventory_inventoryquickoutboundadjust_product_options")
            if form.is_valid():
                try:
                    qty_out = form.cleaned_data["qty_out"]
                    inp = QuickAdjustInput(
                        user=request.user,
                        owner=form.cleaned_data["owner"],
                        # warehouse=form.cleaned_data["warehouse"],
                        product=form.cleaned_data["product"],
                        qty_base_delta=Decimal(0) - qty_out,  # 出库=负数
                        # location=form.cleaned_data.get("location"),
                        reason=form.cleaned_data.get("reason") or "ADMIN_QUICK_ADJUST",
                        remark=form.cleaned_data.get("remark") or "ADMIN_QUICK_ADJUST",
                        allow_negative=bool(form.cleaned_data.get("allow_negative")),
                    )
                    quick_adjust_via_post_task(inp)
                    messages.success(request, "出库快调已过账。")
                    return redirect(
                        f"{reverse('admin:inventory_inventoryquickoutboundadjust_quick_adjust')}?owner={inp.owner.pk}"
                    )
                except Exception as e:
                    messages.error(request, f"出库快调失败：{e}")
        else:
            form = OutboundQuickAdjustForm(owner_id=owner_id)
            form.inject_product_widget_attrs(request, "admin:inventory_inventoryquickoutboundadjust_product_options")

        ctx = {
            "opts": InventoryQuickOutboundAdjust._meta,
            "form": form,
            "title": "出库快调",
            "media": self.media + form.media,
        }
        return render(request, "admin/inventory/quick_adjust_outbound_form.html", ctx)
