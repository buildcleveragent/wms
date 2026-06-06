from django.contrib import admin
from django.db.models import Sum

from allapp.outbound.models import OutboundOrderLine
from .models import (
    PosAvailableInventory,
    PosProduct,
    PosProductPackage,
    PosSaleOrder,
    PosSaleOrderLine,
)


class PosReadonlyAdminMixin:
    """POS admin views expose operational data without allowing manual mutation."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PosSaleOrderLineInline(admin.TabularInline):
    model = OutboundOrderLine
    extra = 0
    can_delete = False
    fields = (
        "line_no",
        "product",
        "base_qty",
        "base_uom",
        "base_price",
        "final_line_amount",
        "note",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PosProduct)
class PosProductAdmin(PosReadonlyAdminMixin, admin.ModelAdmin):
    admin_priority = 1
    list_display = (
        "owner",
        "code",
        "sku",
        "name",
        "gtin",
        "unit_barcode",
        "carton_barcode",
        "base_uom",
        "price",
        "min_price",
        "max_discount",
        "available_qty_total",
        "is_active",
    )
    list_filter = ("is_active", "owner", "category", "brand")
    search_fields = (
        "code",
        "sku",
        "name",
        "gtin",
        "unit_barcode",
        "carton_barcode",
        "product_package__barcode",
    )
    list_select_related = ("owner", "category", "brand", "base_uom")
    ordering = ("owner", "code")
    show_full_result_count = False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(is_active=True)
            .annotate(pos_available_qty=Sum("inventorydetail__available_qty"))
        )

    @admin.display(description="可售库存合计", ordering="pos_available_qty")
    def available_qty_total(self, obj):
        return obj.pos_available_qty or 0


@admin.register(PosProductPackage)
class PosProductPackageAdmin(PosReadonlyAdminMixin, admin.ModelAdmin):
    admin_priority = 2
    list_display = (
        "product",
        "uom",
        "qty_in_base",
        "barcode",
        "is_sales_default",
        "is_pickable",
        "is_active",
    )
    list_filter = ("is_active", "is_sales_default", "is_pickable")
    search_fields = ("product__code", "product__name", "barcode", "uom__code", "uom__name")
    list_select_related = ("product", "uom")
    ordering = ("product", "sort_order", "id")


@admin.register(PosAvailableInventory)
class PosAvailableInventoryAdmin(PosReadonlyAdminMixin, admin.ModelAdmin):
    admin_priority = 3
    list_display = (
        "owner",
        "warehouse",
        "location",
        "product",
        "batch_no",
        "expiry_date",
        "available_qty",
        "base_unit",
    )
    list_filter = ("owner", "warehouse", "location", "product")
    search_fields = (
        "product__code",
        "product__sku",
        "product__name",
        "product__gtin",
        "batch_no",
        "location__code",
    )
    list_select_related = ("owner", "warehouse", "location", "product")
    ordering = ("owner", "warehouse", "product", "expiry_date")
    show_full_result_count = False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_active=True, available_qty__gt=0)


@admin.register(PosSaleOrder)
class PosSaleOrderAdmin(PosReadonlyAdminMixin, admin.ModelAdmin):
    admin_priority = 4
    inlines = [PosSaleOrderLineInline]
    list_display = (
        "order_no",
        "src_bill_no",
        "biz_date",
        "owner",
        "warehouse",
        "customer",
        "submit_status",
        "approval_status",
        "final_order_amount",
        "created_by",
        "created_at",
    )
    list_filter = (
        "submit_status",
        "approval_status",
        "owner",
        "warehouse",
        "customer",
        ("biz_date", admin.DateFieldListFilter),
    )
    search_fields = ("order_no", "src_bill_no", "customer__name", "customer__code")
    list_select_related = ("owner", "warehouse", "customer", "created_by")
    readonly_fields = (
        "order_no",
        "src_bill_no",
        "biz_date",
        "owner",
        "warehouse",
        "customer",
        "delivery_method",
        "submit_status",
        "approval_status",
        "pricing_status",
        "final_order_amount",
        "memo",
        "created_by",
        "created_at",
    )
    fields = readonly_fields
    date_hierarchy = "biz_date"
    ordering = ("-biz_date", "-id")
    show_full_result_count = False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(outbound_type="SALES", delivery_method="PICKUP")
        )


@admin.register(PosSaleOrderLine)
class PosSaleOrderLineAdmin(PosReadonlyAdminMixin, admin.ModelAdmin):
    admin_priority = 5
    list_display = (
        "order",
        "line_no",
        "product",
        "base_qty",
        "base_uom",
        "base_price",
        "final_line_amount",
    )
    list_filter = (
        "order__owner",
        "order__warehouse",
        "order__submit_status",
        "order__approval_status",
    )
    search_fields = (
        "order__order_no",
        "order__src_bill_no",
        "product__code",
        "product__sku",
        "product__name",
        "product__gtin",
    )
    list_select_related = ("order", "order__owner", "order__warehouse", "product", "base_uom")
    ordering = ("-order__biz_date", "-order_id", "line_no")
    show_full_result_count = False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .filter(order__outbound_type="SALES", order__delivery_method="PICKUP")
        )
