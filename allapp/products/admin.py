from __future__ import annotations
from urllib.parse import quote

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from .category_backfill import (
    CategoryBackfillError,
    build_category_backfill_workbook,
    import_category_backfill,
    scoped_products,
)
from .models import ( ProductCategory, Brand, ProductUom, Product, ProductPackage,)
from allapp.core.admin_base import AdvancedAdminBase, DeletedStatusFilter,BaseReadonlyAdmin


class CategoryCompletionFilter(admin.SimpleListFilter):
    title = "分类状态"
    parameter_name = "category_status"

    def lookups(self, request, model_admin):
        return (("missing", "未分类"), ("done", "已分类"))

    def queryset(self, request, queryset):
        if self.value() == "missing":
            return queryset.filter(category__isnull=True)
        if self.value() == "done":
            return queryset.filter(category__isnull=False)
        return queryset

@admin.register(ProductCategory)
class ProductCategoryAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 4
    list_display = (
        "code", "name", "level_label", "category_path", "parent",
        "sort_order", "is_active",
    )
    list_filter = (DeletedStatusFilter, "is_active", "parent")
    search_fields = ("code", "name")
    autocomplete_fields = ("parent",)
    ordering = ("sort_order", "code")
    list_select_related = ("parent", "parent__parent")
    # 表单仅业务字段（审计/软删不露出）
    fields = ("code", "name", "parent", "sort_order", "image", "is_active")

    @admin.display(description="层级")
    def level_label(self, obj):
        return obj.level_name

    @admin.display(description="完整路径")
    def category_path(self, obj):
        return obj.full_path

@admin.register(Brand)
class BrandAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 5
    fields = ("code", "name", "remark", "is_active")
    list_display = ("code", "name", "is_active", "is_deleted", "created_at", "updated_at")
    list_filter = (DeletedStatusFilter, "is_active")
    search_fields = ("code", "name")
    ordering = ("code",)

@admin.register(ProductUom)
class ProductUomAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 3
    list_display = ("code", "name", "kind", "decimal_places", "is_active", "is_deleted", "created_at", "updated_at")
    list_filter = (DeletedStatusFilter, "is_active", "kind")
    search_fields = ("code", "name")
    ordering = ("code",)
    fields = ("code", "name", "kind", "decimal_places",)

class ProductPackageInline(admin.TabularInline):
    model = ProductPackage
    extra = 1
    autocomplete_fields = ("uom",)
    fields = (
        "uom", "qty_in_base", "barcode",
        "length_cm", "width_cm", "height_cm",
        "gross_weight_kg", "volume_auto", "volume_m3", "volume_m3_status",
        "is_pickable", "is_purchase_default", "is_sales_default",
        "sort_order",
    )
    readonly_fields = ("volume_m3_status",)

@admin.register(Product)
class ProductAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 1
    change_list_template = "admin/products/product/change_list.html"
    inlines = [ProductPackageInline]
    list_display = (
        "owner","name","spec","sku","code", "gtin", "unit_barcode", "carton_barcode",
        "base_uom", "price", "min_price", "max_discount", "pricing_strategy",
        "category", "vender","brand",
        "min_stock","max_stock","weight","net_content","volume",
        "batch_control","expiry_control", "expiry_basis","shelf_life_days","pick_policy",
        "product_image", "material_quality",
    )
    list_filter = (
        DeletedStatusFilter, CategoryCompletionFilter, "is_active",
        "owner", "category", "brand","spec",
        "batch_control", "serial_control", "expiry_control", "pick_policy",
    )

    list_display_links = ("name",)
    
    search_fields = ("code", "name", "spec","sku", "gtin", "unit_barcode", "carton_barcode", "external_code")
    autocomplete_fields = ("owner", "category", "brand", "base_uom", "replenish_uom")
    list_select_related = ("owner", "category", "brand", "base_uom", "replenish_uom")
    ordering = ("owner", "code")
    readonly_fields = ("sku",)
    fields = (
        "owner","name","spec","sku","code", "gtin", "unit_barcode", "carton_barcode",
        "base_uom", "price", "min_price", "max_discount", "pricing_strategy",
        "category", "vender","brand",
        "min_stock","max_stock","weight","net_content","volume",
        ("batch_control","expiry_control",), "expiry_basis","shelf_life_days","pick_policy",
        "product_image", "material_quality",
    )

    def get_urls(self):
        custom_urls = [
            path(
                "category-backfill/",
                self.admin_site.admin_view(self.category_backfill_view),
                name="products_product_category_backfill",
            ),
            path(
                "category-backfill/export/",
                self.admin_site.admin_view(self.category_backfill_export_view),
                name="products_product_category_backfill_export",
            ),
        ]
        return custom_urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["category_backfill_url"] = reverse(
            "admin:products_product_category_backfill"
        )
        return super().changelist_view(request, extra_context=extra_context)

    def category_backfill_export_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        products = scoped_products(
            request.user,
            Product.objects.filter(category__isnull=True).order_by(
                "owner__code", "code"
            ),
        )
        content = build_category_backfill_workbook(products)
        response = HttpResponse(
            content,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        filename = quote("商品分类补录.xlsx")
        response["Content-Disposition"] = (
            "attachment; filename=product_category_backfill.xlsx; "
            f"filename*=UTF-8''{filename}"
        )
        return response

    def category_backfill_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        form = forms.Form(request.POST or None, request.FILES or None)
        form.fields["file"] = forms.FileField(
            label="分类补录 Excel",
            help_text="请使用本页面下载的模板；任一行错误都会整批回滚。",
        )
        if request.method == "POST" and form.is_valid():
            try:
                result = import_category_backfill(
                    form.cleaned_data["file"], user=request.user, request=request
                )
            except CategoryBackfillError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
            else:
                self.message_user(
                    request,
                    f"分类补录完成：读取 {result['row_count']} 条，更新 {result['changed_count']} 条。",
                    level=messages.SUCCESS,
                )
                return redirect("admin:products_product_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "商品分类批量补录",
            "form": form,
            "export_url": reverse(
                "admin:products_product_category_backfill_export"
            ),
        }
        return TemplateResponse(
            request,
            "admin/products/product/category_backfill.html",
            context,
        )

    # fieldsets = (
    #     (None, {
    #         'fields': ('owner', 'name', 'code', 'spec', 'sku')
    #     }),
    #     ('分类信息', {
    #         'fields': ('category', 'brand', 'base_uom', 'price', 'min_price', 'max_discount', 'pricing_strategy', 'replenish_uom'),
    #     }),
    #     ('条形码', {
    #         'fields': ('gtin', 'unit_barcode', 'carton_barcode'),
    #     }),
    #     ('控制参数', {
    #         'fields': ('batch_control', 'serial_control', 'expiry_control', 'expiry_basis', 'shelf_life_days', 'pick_policy'),
    #     }),
    #     ('其他信息', {
    #         'fields': ('image', 'vender', 'material_quality', 'net_content'),
    #     }),
    # )
    def has_view_permission(self, request, obj=None):
        return True

    def has_view_or_change_permission(self, request, obj=None):
        # Django 5.x 的 Autocomplete 调这个；我们显式放行
        return True

    class Media:
        css = {
            "all": ("admin/product_changelist_fix.css",)
        }

@admin.register(ProductPackage)
class ProductPackageAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 2
    list_display = (
        "product", "uom", "qty_in_base", "barcode",
        "volume_m3", "volume_m3_status",
        "is_pickable", "is_purchase_default", "is_sales_default",
        "sort_order", "is_active", "is_deleted",
        "created_at", "updated_at",
    )
    list_filter = (DeletedStatusFilter, "is_active", "is_pickable", "is_purchase_default", "is_sales_default")
    search_fields = ("product__code", "product__name", "barcode", "uom__code")
    autocomplete_fields = ("product", "uom")
    list_select_related = ("product", "uom")
    ordering = ("product", "sort_order", "uom")
    fields = (
        "product", "uom", "qty_in_base", "barcode",
        "length_cm", "width_cm", "height_cm",
        "gross_weight_kg", "volume_auto", "volume_m3",
        "is_pickable", "is_purchase_default", "is_sales_default",
        "sort_order", "is_active",
    )

    formfield_overrides = {
        models.DecimalField: {"widget": forms.NumberInput(attrs={"style": "width:80px;"})},
    }

    class Media:
        css = {
            "all": ("admin_custom.css",)  # 放在 STATIC 下
        }
