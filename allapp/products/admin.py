from __future__ import annotations
from urllib.parse import quote

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.forms.models import BaseInlineFormSet
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
from .identifier_services import (
    add_external_identifier, add_product_barcode, set_barcode_primary,
    set_external_primary, set_identifier_active,
    validate_product_barcode_candidate,
)
from .identifier_lookup import filter_by_product_search
from .models import (
    ProductCategory, Brand, ProductUom, Product, ProductPackage,
    ProductBarcode, ProductExternalIdentifier,
)
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
    readonly_fields = ("volume_m3_status", "barcode")


class ProductBarcodeAddForm(forms.ModelForm):
    class Meta:
        model = ProductBarcode
        fields = (
            "barcode",
            "barcode_type",
            "package",
            "is_primary",
            "valid_from",
            "valid_to",
            "is_active",
        )

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.fields["package"].queryset = ProductPackage.all_objects.none()
        if product and product.pk:
            self.instance.product = product
            self.instance.owner_id = product.owner_id
            self.fields["package"].queryset = (
                ProductPackage.all_objects.filter(
                    product=product,
                    is_active=True,
                    is_deleted=False,
                )
                .select_related("product__base_uom", "uom")
                .order_by("sort_order", "uom__code")
            )


class ProductBarcodeAddFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["product"] = self.instance if self.instance.pk else None
        return kwargs

    @staticmethod
    def _add_validation_error(form, error):
        if hasattr(error, "error_dict"):
            for field, errors in error.error_dict.items():
                form.add_error(field if field in form.fields else None, errors)
            return
        form.add_error(None, error)

    def clean(self):
        super().clean()
        seen = {}
        for form in self.forms:
            if form.errors or not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            data = form.cleaned_data
            try:
                candidate = validate_product_barcode_candidate(
                    product=self.instance,
                    barcode=data["barcode"],
                    barcode_type=data["barcode_type"],
                    package=data.get("package"),
                    is_primary=data.get("is_primary", False),
                    valid_from=data.get("valid_from"),
                    valid_to=data.get("valid_to"),
                    is_active=data.get("is_active", True),
                )
            except ValidationError as exc:
                self._add_validation_error(form, exc)
                continue

            semantic_key = (candidate.package_id, candidate.qty_in_base)
            previous = seen.get(candidate.normalized_value)
            if previous and previous[0] != semantic_key:
                form.add_error(
                    "barcode",
                    "同一批新增行中，该条码具有不同包装或换算语义。",
                )
                continue
            exact_key = (
                candidate.normalized_value,
                candidate.barcode_type,
                candidate.package_id,
            )
            if previous and exact_key in previous[1]:
                form.add_error("barcode", "同一批新增行中已有相同条码记录。")
                continue
            if previous:
                previous[1].add(exact_key)
            else:
                seen[candidate.normalized_value] = (semantic_key, {exact_key})

    def save_new(self, form, commit=True):
        data = form.cleaned_data
        return add_product_barcode(
            product=self.instance,
            barcode=data["barcode"],
            barcode_type=data["barcode_type"],
            package=data.get("package"),
            is_primary=data.get("is_primary", False),
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
            is_active=data.get("is_active", True),
        )


class ProductBarcodeAddInline(admin.TabularInline):
    model = ProductBarcode
    form = ProductBarcodeAddForm
    formset = ProductBarcodeAddFormSet
    extra = 1
    fields = (
        "barcode",
        "barcode_type",
        "package",
        "is_primary",
        "valid_from",
        "valid_to",
        "is_active",
    )
    verbose_name = "新增商品条码"
    verbose_name_plural = "新增商品条码"

    def get_queryset(self, request):
        return super().get_queryset(request).none()

    def has_add_permission(self, request, obj=None):
        return bool(obj and obj.pk) and super().has_add_permission(request, obj)


class ProductBarcodeHistoryInline(admin.TabularInline):
    model = ProductBarcode
    extra = 0
    can_delete = False
    fields = ("barcode", "barcode_type", "package", "qty_in_base", "is_primary", "valid_from", "valid_to", "is_active")
    readonly_fields = fields
    verbose_name = "商品条码历史"
    verbose_name_plural = "商品条码历史"

    def has_add_permission(self, request, obj=None):
        return False


class ProductExternalIdentifierHistoryInline(admin.TabularInline):
    model = ProductExternalIdentifier
    extra = 0
    can_delete = False
    fields = ("source_system", "external_code", "is_primary", "valid_from", "valid_to", "is_active")
    readonly_fields = fields

@admin.register(Product)
class ProductAdmin(AdvancedAdminBase,BaseReadonlyAdmin):
    admin_priority = 1
    change_list_template = "admin/products/product/change_list.html"
    inlines = [
        ProductPackageInline,
        ProductBarcodeAddInline,
        ProductBarcodeHistoryInline,
        ProductExternalIdentifierHistoryInline,
    ]
    list_display = (
        "owner","name","spec","sku","code", "gtin", "unit_barcode", "carton_barcode",
        "carton_package",
        "base_uom", "price", "purchase_price", "min_price", "max_discount",
        "pricing_strategy",
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
    
    search_fields = ("code", "name", "spec", "sku")
    autocomplete_fields = (
        "owner", "category", "brand", "base_uom", "replenish_uom", "carton_package",
    )
    list_select_related = (
        "owner", "category", "brand", "base_uom", "replenish_uom", "carton_package",
    )
    ordering = ("owner", "code")
    readonly_fields = ("sku", "gtin", "unit_barcode", "carton_barcode", "external_code")
    fields = (
        "owner","name","spec","sku","code", "gtin", "unit_barcode", "carton_barcode",
        "carton_package",
        "base_uom", "price", "purchase_price", "min_price", "max_discount",
        "pricing_strategy",
        "category", "vender","brand",
        "min_stock","max_stock","weight","net_content","volume",
        ("batch_control","expiry_control",), "expiry_basis","shelf_life_days","pick_policy",
        "product_image", "material_quality",
    )

    def get_search_results(self, request, queryset, search_term):
        """Use the same full-phrase, current-identifier search as the API."""
        return (
            filter_by_product_search(
                queryset, search_term, product_field="pk"
            ),
            False,
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
    readonly_fields = ("barcode",)

    formfield_overrides = {
        models.DecimalField: {"widget": forms.NumberInput(attrs={"style": "width:80px;"})},
    }

    class Media:
        css = {
            "all": ("admin_custom.css",)  # 放在 STATIC 下
        }


@admin.register(ProductBarcode)
class ProductBarcodeAdmin(admin.ModelAdmin):
    list_display = ("owner", "product", "barcode", "barcode_type", "package", "qty_in_base", "is_primary", "is_active", "valid_from", "valid_to")
    list_filter = ("owner", "barcode_type", "is_primary", "is_active")
    search_fields = ("barcode", "normalized_value", "product__code", "product__name")
    autocomplete_fields = ("product", "package")
    readonly_fields = ("owner", "normalized_value", "qty_in_base")
    actions = ("make_primary", "retire", "reactivate")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(["product", "barcode", "barcode_type", "package", "is_primary"])
        return tuple(fields)

    def save_model(self, request, obj, form, change):
        if not change:
            saved = add_product_barcode(
                product=obj.product, barcode=obj.barcode,
                barcode_type=obj.barcode_type, package=obj.package,
                is_primary=obj.is_primary, valid_from=obj.valid_from,
                valid_to=obj.valid_to,
            )
            obj.pk = saved.pk
            obj._state.adding = False
            return
        obj._identifier_service_write = True
        obj.save()

    def delete_model(self, request, obj):
        set_identifier_active(obj, False)

    @admin.action(description="设为主条码")
    def make_primary(self, request, queryset):
        for record in queryset:
            set_barcode_primary(record)

    @admin.action(description="退役所选条码")
    def retire(self, request, queryset):
        for record in queryset:
            set_identifier_active(record, False)

    @admin.action(description="重新启用所选条码")
    def reactivate(self, request, queryset):
        for record in queryset:
            set_identifier_active(record, True)


@admin.register(ProductExternalIdentifier)
class ProductExternalIdentifierAdmin(admin.ModelAdmin):
    list_display = ("owner", "product", "source_system", "external_code", "is_primary", "is_active", "valid_from", "valid_to")
    list_filter = ("owner", "source_system", "is_primary", "is_active")
    search_fields = ("source_system", "external_code", "normalized_value", "product__code", "product__name")
    autocomplete_fields = ("product",)
    readonly_fields = ("owner", "normalized_value")
    actions = ("make_primary", "retire", "reactivate")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.extend(["product", "source_system", "external_code", "is_primary"])
        return tuple(fields)

    def save_model(self, request, obj, form, change):
        if not change:
            saved = add_external_identifier(
                product=obj.product, source_system=obj.source_system,
                external_code=obj.external_code, is_primary=obj.is_primary,
                valid_from=obj.valid_from, valid_to=obj.valid_to,
            )
            obj.pk = saved.pk
            obj._state.adding = False
            return
        obj._identifier_service_write = True
        obj.save()

    def delete_model(self, request, obj):
        set_identifier_active(obj, False)

    @admin.action(description="设为主外部标识")
    def make_primary(self, request, queryset):
        for record in queryset:
            set_external_primary(record)

    @admin.action(description="退役所选外部标识")
    def retire(self, request, queryset):
        for record in queryset:
            set_identifier_active(record, False)

    @admin.action(description="重新启用所选外部标识")
    def reactivate(self, request, queryset):
        for record in queryset:
            set_identifier_active(record, True)
