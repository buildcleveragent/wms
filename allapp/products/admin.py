from __future__ import annotations
from django.db import models
from django import forms
from django.contrib import admin, messages
from .models import ( ProductCategory, Brand, ProductUom, Product, ProductPackage,)
from allapp.core.admin_base import AdvancedAdminBase, DeletedStatusFilter,BaseReadonlyAdmin
from allapp.core.admin_mixins import HideAuditFieldsMixin, HideAuditInlineMixin

@admin.register(ProductCategory)
class ProductCategoryAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 4
    list_display = ("code", "name", "parent", "is_active", )
    list_filter = (DeletedStatusFilter, "is_active", "parent")
    search_fields = ("code", "name")
    autocomplete_fields = ("parent",)
    ordering = ("code",)
    list_select_related = ("parent",)
    # 表单仅业务字段（审计/软删不露出）
    fields = ("code", "name", "parent", "is_active")

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
        DeletedStatusFilter, "is_active",
        "owner", "category", "brand","spec",
        "batch_control", "serial_control", "expiry_control", "pick_policy",
    )

    list_display_links = ("name",)
    
    search_fields = ("code", "name", "spec","sku", "gtin", "unit_barcode", "carton_barcode", "external_code")
    autocomplete_fields = ("owner", "category", "brand", "base_uom", "replenish_uom")
    list_select_related = ("owner", "category", "brand", "base_uom", "replenish_uom")
    ordering = ("owner", "code")
    fields = (
        "owner","name","spec","sku","code", "gtin", "unit_barcode", "carton_barcode",
        "base_uom", "price", "min_price", "max_discount", "pricing_strategy",
        "category", "vender","brand",
        "min_stock","max_stock","weight","net_content","volume",
        ("batch_control","expiry_control",), "expiry_basis","shelf_life_days","pick_policy",
        "product_image", "material_quality",
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