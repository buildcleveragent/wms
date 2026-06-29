from __future__ import annotations

import json

from django.contrib import admin
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join
from django.utils.text import Truncator

from .models import (
    PosAuditLog,
    PosPayment,
    PosPaymentLine,
    PosPrintLog,
    PosRefund,
    PosReturn,
    PosReturnLine,
    PosSale,
    PosSaleLine,
    PosSaleOrder,
    PosShift,
    PosShiftPaymentSummary,
)


def _all_model_field_names(model):
    return tuple(field.name for field in model._meta.fields)


def _admin_change_link(obj, label=None):
    if not obj:
        return "-"
    try:
        url = reverse(
            f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk]
        )
    except NoReverseMatch:
        return str(label or obj)
    return format_html('<a href="{}">{}</a>', url, label or obj)


class ReadOnlyAdminMixin:
    actions = None
    list_per_page = 50
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        readonly.extend(_all_model_field_names(self.model))
        return tuple(dict.fromkeys(readonly))


class ReadOnlyInlineMixin:
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        readonly.extend(_all_model_field_names(self.model))
        return tuple(dict.fromkeys(readonly))


class PosSaleLineInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosSaleLine
    fields = (
        "line_no",
        "owner",
        "product",
        "qty",
        "price",
        "amount",
        "outbound_order_line",
        "created_at",
    )
    ordering = ("line_no", "id")


class PosPaymentLineInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosPaymentLine
    fields = (
        "method",
        "amount",
        "amount_received",
        "change_amount",
        "reference_no",
        "status",
        "created_at",
    )
    ordering = ("id",)


class PosSaleOrderInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosSaleOrder
    fields = ("owner", "outbound_order_link", "amount", "created_at")
    readonly_fields = ("outbound_order_link",)
    ordering = ("owner_id", "id")

    @admin.display(description="出库单")
    def outbound_order_link(self, obj):
        return _admin_change_link(obj.outbound_order, obj.outbound_order)


class PosReturnInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosReturn
    fields = ("return_no", "status", "shift", "cashier", "total_amount", "created_at")
    ordering = ("-created_at", "-id")


class PosReturnLineInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosReturnLine
    fields = (
        "line_no",
        "sale_line",
        "owner",
        "product",
        "qty",
        "price",
        "amount",
        "created_at",
    )
    ordering = ("line_no", "id")


class PosRefundInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosRefund
    fields = (
        "method",
        "amount",
        "reference_no",
        "status",
        "processed_by",
        "processed_at",
        "created_at",
    )
    ordering = ("id",)


class PosShiftPaymentSummaryInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosShiftPaymentSummary
    fields = (
        "method",
        "sale_count",
        "refund_count",
        "expected_amount",
        "refund_amount",
        "actual_amount",
        "difference",
    )
    ordering = ("method",)


class PosPrintLogForSaleInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosPrintLog
    fk_name = "sale"
    fields = (
        "print_type",
        "source",
        "printed_by",
        "printed_at",
        "copy_no",
        "payload_hash",
        "remark",
    )
    ordering = ("-printed_at", "-id")


class PosPrintLogForShiftInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosPrintLog
    fk_name = "shift"
    fields = (
        "print_type",
        "source",
        "printed_by",
        "printed_at",
        "copy_no",
        "payload_hash",
        "remark",
    )
    ordering = ("-printed_at", "-id")


class PosAuditLogForSaleInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosAuditLog
    fk_name = "sale"
    fields = ("action", "actor", "reason", "metadata_preview", "created_at")
    readonly_fields = ("metadata_preview",)
    ordering = ("-created_at", "-id")

    @admin.display(description="元数据")
    def metadata_preview(self, obj):
        return _metadata_preview(obj.metadata)


class PosAuditLogForReturnInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosAuditLog
    fk_name = "return_order"
    fields = ("action", "actor", "reason", "metadata_preview", "created_at")
    readonly_fields = ("metadata_preview",)
    ordering = ("-created_at", "-id")

    @admin.display(description="元数据")
    def metadata_preview(self, obj):
        return _metadata_preview(obj.metadata)


class PosAuditLogForShiftInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = PosAuditLog
    fk_name = "shift"
    fields = ("action", "actor", "reason", "metadata_preview", "created_at")
    readonly_fields = ("metadata_preview",)
    ordering = ("-created_at", "-id")

    @admin.display(description="元数据")
    def metadata_preview(self, obj):
        return _metadata_preview(obj.metadata)


def _metadata_preview(metadata):
    if not metadata:
        return "-"
    text = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return Truncator(text).chars(160)


@admin.register(PosSale)
class PosSaleAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    inlines = [
        PosSaleLineInline,
        PosPaymentLineInline,
        PosSaleOrderInline,
        PosReturnInline,
        PosPrintLogForSaleInline,
        PosAuditLogForSaleInline,
    ]
    list_display = (
        "sale_no",
        "src_bill_no",
        "status",
        "warehouse",
        "cashier",
        "shift",
        "total_amount",
        "payment_method",
        "created_at",
        "voided_at",
    )
    list_select_related = (
        "warehouse",
        "cashier",
        "shift",
        "selected_customer",
        "payment",
        "voided_by",
    )
    list_filter = (
        "status",
        "warehouse",
        "shift",
        "payment__method",
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "sale_no",
        "src_bill_no",
        "payment__reference_no",
        "selected_customer__name",
        "selected_customer__code",
        "cashier__username",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    readonly_fields = ("payment_summary", "outbound_orders_links")
    fieldsets = (
        (
            "基础信息",
            {
                "fields": (
                    "sale_no",
                    "src_bill_no",
                    "status",
                    "warehouse",
                    "cashier",
                    "shift",
                    "selected_customer",
                )
            },
        ),
        ("金额与收款", {"fields": ("total_amount", "payment_summary")}),
        ("出库关联", {"fields": ("outbound_orders_links",)}),
        ("作废信息", {"fields": ("voided_at", "voided_by", "void_reason")}),
        ("幂等信息", {"fields": ("idempotency_key", "idempotency_fingerprint")}),
        ("时间", {"fields": ("created_at", "updated_at")}),
        ("备注", {"fields": ("remark",)}),
    )

    @admin.display(description="支付方式")
    def payment_method(self, obj):
        payment = getattr(obj, "payment", None)
        return payment.get_method_display() if payment else "-"

    @admin.display(description="收款摘要")
    def payment_summary(self, obj):
        payment = getattr(obj, "payment", None)
        if not payment:
            return "-"
        return (
            f"{payment.get_method_display()} / {payment.get_status_display()} / "
            f"应收 {payment.amount_due} / 实收 {payment.amount_received} / 找零 {payment.change_amount}"
        )

    @admin.display(description="出库单")
    def outbound_orders_links(self, obj):
        links = [
            _admin_change_link(link.outbound_order, link.outbound_order)
            for link in obj.sale_orders.select_related("outbound_order").all()
        ]
        if not links:
            return "-"
        return format_html_join(format_html("<br>"), "{}", ((link,) for link in links))


@admin.register(PosSaleLine)
class PosSaleLineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "sale",
        "line_no",
        "owner",
        "product",
        "qty",
        "price",
        "amount",
        "outbound_order_line",
        "created_at",
    )
    list_select_related = ("sale", "owner", "product", "outbound_order_line")
    list_filter = ("owner", ("created_at", admin.DateFieldListFilter))
    search_fields = (
        "sale__sale_no",
        "sale__src_bill_no",
        "product__code",
        "product__sku",
        "product__name",
        "owner__code",
        "owner__name",
    )
    ordering = ("-created_at", "-id")


@admin.register(PosPayment)
class PosPaymentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "sale",
        "method",
        "status",
        "amount_due",
        "amount_received",
        "change_amount",
        "reference_no",
        "created_at",
    )
    list_select_related = ("sale",)
    list_filter = ("method", "status", ("created_at", admin.DateFieldListFilter))
    search_fields = ("sale__sale_no", "sale__src_bill_no", "reference_no")
    ordering = ("-created_at", "-id")


@admin.register(PosPaymentLine)
class PosPaymentLineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "sale",
        "method",
        "status",
        "amount",
        "amount_received",
        "change_amount",
        "reference_no",
        "created_at",
    )
    list_select_related = ("sale",)
    list_filter = ("method", "status", ("created_at", admin.DateFieldListFilter))
    search_fields = ("sale__sale_no", "sale__src_bill_no", "reference_no")
    ordering = ("-created_at", "-id")


@admin.register(PosShift)
class PosShiftAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    inlines = [
        PosShiftPaymentSummaryInline,
        PosPrintLogForShiftInline,
        PosAuditLogForShiftInline,
    ]
    list_display = (
        "shift_no",
        "status",
        "warehouse",
        "cashier",
        "opened_at",
        "closed_at",
        "total_sales_amount",
        "sale_count",
        "return_count",
        "cash_difference",
    )
    list_select_related = ("warehouse", "cashier", "opened_by", "closed_by")
    list_filter = (
        "status",
        "warehouse",
        "cashier",
        ("opened_at", admin.DateFieldListFilter),
    )
    search_fields = ("shift_no", "cashier__username")
    date_hierarchy = "opened_at"
    ordering = ("-opened_at", "-id")
    fieldsets = (
        (
            "基础信息",
            {"fields": ("shift_no", "status", "warehouse", "cashier", "remark")},
        ),
        (
            "开班/交班",
            {
                "fields": (
                    "opened_at",
                    "opened_by",
                    "closed_at",
                    "closed_by",
                    "reopened_at",
                    "reopened_by",
                    "reopen_count",
                    "reopen_reason",
                )
            },
        ),
        (
            "现金",
            {
                "fields": (
                    "opening_cash_amount",
                    "expected_cash_amount",
                    "actual_cash_amount",
                    "cash_difference",
                )
            },
        ),
        (
            "汇总",
            {
                "fields": (
                    "total_sales_amount",
                    "total_voided_amount",
                    "total_return_amount",
                    "sale_count",
                    "completed_count",
                    "voided_count",
                    "return_count",
                )
            },
        ),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(PosShiftPaymentSummary)
class PosShiftPaymentSummaryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "shift",
        "method",
        "sale_count",
        "refund_count",
        "expected_amount",
        "refund_amount",
        "actual_amount",
        "difference",
    )
    list_select_related = ("shift",)
    list_filter = ("method", "shift__status")
    search_fields = ("shift__shift_no",)
    ordering = ("-shift__opened_at", "method")


@admin.register(PosReturn)
class PosReturnAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    inlines = [PosReturnLineInline, PosRefundInline, PosAuditLogForReturnInline]
    list_display = (
        "return_no",
        "sale_link",
        "status",
        "warehouse",
        "cashier",
        "shift",
        "total_amount",
        "created_at",
    )
    list_select_related = ("sale", "warehouse", "shift", "cashier")
    list_filter = (
        "status",
        "warehouse",
        "shift",
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "return_no",
        "sale__sale_no",
        "sale__src_bill_no",
        "reason",
        "refunds__reference_no",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    readonly_fields = ("sale_link",)
    fieldsets = (
        (
            "基础信息",
            {
                "fields": (
                    "return_no",
                    "sale_link",
                    "status",
                    "warehouse",
                    "shift",
                    "cashier",
                    "total_amount",
                    "reason",
                )
            },
        ),
        ("幂等信息", {"fields": ("idempotency_key", "idempotency_fingerprint")}),
        ("时间", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="原销售单")
    def sale_link(self, obj):
        return _admin_change_link(obj.sale, obj.sale)


@admin.register(PosReturnLine)
class PosReturnLineAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "return_order",
        "line_no",
        "sale_line",
        "owner",
        "product",
        "qty",
        "price",
        "amount",
        "created_at",
    )
    list_select_related = ("return_order", "sale_line", "owner", "product")
    list_filter = ("owner", ("created_at", admin.DateFieldListFilter))
    search_fields = (
        "return_order__return_no",
        "return_order__sale__sale_no",
        "product__code",
        "product__sku",
        "product__name",
        "owner__code",
        "owner__name",
    )
    ordering = ("-created_at", "-id")


@admin.register(PosRefund)
class PosRefundAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "return_order",
        "sale",
        "shift",
        "method",
        "amount",
        "reference_no",
        "status",
        "processed_by",
        "processed_at",
        "created_at",
    )
    list_select_related = ("return_order", "sale", "shift", "processed_by")
    list_filter = (
        "method",
        "status",
        "shift",
        ("processed_at", admin.DateFieldListFilter),
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "return_order__return_no",
        "sale__sale_no",
        "sale__src_bill_no",
        "reference_no",
    )
    ordering = ("-created_at", "-id")


@admin.register(PosSaleOrder)
class PosSaleOrderAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("sale", "owner", "outbound_order_link", "amount", "created_at")
    list_select_related = ("sale", "owner", "outbound_order")
    list_filter = ("owner", ("created_at", admin.DateFieldListFilter))
    search_fields = (
        "sale__sale_no",
        "sale__src_bill_no",
        "owner__code",
        "owner__name",
        "outbound_order__order_no",
    )
    ordering = ("-created_at", "-id")
    readonly_fields = ("outbound_order_link",)

    @admin.display(description="出库单")
    def outbound_order_link(self, obj):
        return _admin_change_link(obj.outbound_order, obj.outbound_order)


@admin.register(PosPrintLog)
class PosPrintLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "print_type",
        "source",
        "sale",
        "shift",
        "printed_by",
        "printed_at",
        "copy_no",
        "payload_hash",
    )
    list_select_related = ("sale", "shift", "printed_by")
    list_filter = (
        "print_type",
        "source",
        ("printed_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "sale__sale_no",
        "sale__src_bill_no",
        "shift__shift_no",
        "printed_by__username",
        "payload_hash",
        "remark",
    )
    date_hierarchy = "printed_at"
    ordering = ("-printed_at", "-id")


@admin.register(PosAuditLog)
class PosAuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "action",
        "sale",
        "return_order",
        "shift",
        "actor",
        "reason_short",
        "created_at",
    )
    list_select_related = ("sale", "return_order", "shift", "actor")
    list_filter = ("action", "actor", ("created_at", admin.DateFieldListFilter))
    search_fields = (
        "sale__sale_no",
        "sale__src_bill_no",
        "return_order__return_no",
        "shift__shift_no",
        "actor__username",
        "reason",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at", "-id")
    readonly_fields = ("metadata_pretty",)
    fieldsets = (
        (
            "基础信息",
            {"fields": ("action", "sale", "return_order", "shift", "actor", "reason")},
        ),
        ("元数据", {"fields": ("metadata_pretty",)}),
        ("时间", {"fields": ("created_at",)}),
    )

    @admin.display(description="原因")
    def reason_short(self, obj):
        return Truncator(obj.reason or "-").chars(40)

    @admin.display(description="元数据")
    def metadata_pretty(self, obj):
        if not obj.metadata:
            return "-"
        text = json.dumps(obj.metadata, ensure_ascii=False, indent=2, sort_keys=True)
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", text)
