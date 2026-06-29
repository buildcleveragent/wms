from django.contrib import admin

from .models import (
    MiniCustomerAddress,
    MiniProgramUser,
    SaleMiniAfterSaleRequest,
    SaleMiniBanner,
    SaleMiniCart,
    SaleMiniCartItem,
    SaleMiniCoupon,
    SaleMiniCouponTemplate,
    SaleMiniDistributionRecord,
    SaleMiniOrderAdjustment,
    SaleMiniOrderMapping,
    SaleMiniPayment,
    SaleMiniPaymentEvent,
    SaleMiniPointLedger,
    SaleMiniRefund,
    SaleProductConfig,
)


@admin.register(MiniProgramUser)
class MiniProgramUserAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "customer", "user", "nickname", "phone", "is_active")
    list_filter = ("owner", "is_active")
    search_fields = (
        "nickname",
        "phone",
        "openid",
        "customer__code",
        "customer__name",
        "user__username",
    )
    raw_id_fields = ("owner", "customer", "user")


@admin.register(MiniCustomerAddress)
class MiniCustomerAddressAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner",
        "customer",
        "contact",
        "phone",
        "full_address",
        "is_default",
        "is_active",
    )
    list_filter = ("owner", "is_default", "is_active")
    search_fields = ("contact", "phone", "detail", "customer__code", "customer__name")
    raw_id_fields = ("owner", "customer", "buyer_user")


@admin.register(SaleMiniBanner)
class SaleMiniBannerAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "title", "sort_order", "is_active")
    list_filter = ("owner", "is_active")
    search_fields = ("title", "link_value")
    raw_id_fields = ("owner",)


@admin.register(SaleProductConfig)
class SaleProductConfigAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner",
        "product",
        "is_listed",
        "sale_price",
        "market_price",
        "is_recommended",
        "is_hot",
        "is_new",
        "sort_order",
    )
    list_filter = (
        "owner",
        "is_listed",
        "is_recommended",
        "is_hot",
        "is_new",
        "stock_display",
    )
    search_fields = ("product__code", "product__sku", "product__name")
    raw_id_fields = ("owner", "product")


class SaleMiniCartItemInline(admin.TabularInline):
    model = SaleMiniCartItem
    extra = 0
    raw_id_fields = ("product",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(SaleMiniCart)
class SaleMiniCartAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "customer", "buyer_user", "is_active", "updated_at")
    list_filter = ("owner", "is_active")
    search_fields = ("customer__code", "customer__name", "buyer_user__nickname")
    raw_id_fields = ("owner", "customer", "buyer_user")
    inlines = [SaleMiniCartItemInline]


@admin.register(SaleMiniCartItem)
class SaleMiniCartItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cart", "product", "order_uom", "qty", "updated_at")
    search_fields = ("product__code", "product__sku", "product__name")
    raw_id_fields = ("cart", "product")


@admin.register(SaleMiniOrderMapping)
class SaleMiniOrderMappingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner",
        "customer",
        "outbound_order",
        "payment_status",
        "source",
        "created_at",
    )
    list_filter = ("owner", "payment_status", "source")
    search_fields = ("outbound_order__order_no", "customer__code", "customer__name")
    raw_id_fields = ("owner", "customer", "buyer_user", "outbound_order")


@admin.register(SaleMiniPayment)
class SaleMiniPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "payment_no",
        "out_trade_no",
        "owner",
        "customer",
        "status",
        "amount",
        "transaction_id",
        "created_at",
    )
    list_filter = ("owner", "channel", "status")
    search_fields = (
        "payment_no",
        "out_trade_no",
        "transaction_id",
        "mapping__outbound_order__order_no",
    )
    raw_id_fields = ("owner", "customer", "buyer_user", "mapping")
    readonly_fields = ("client_pay_params", "prepay_response", "callback_payload")


@admin.register(SaleMiniRefund)
class SaleMiniRefundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "refund_no",
        "out_refund_no",
        "owner",
        "customer",
        "status",
        "amount",
        "refund_id",
        "created_at",
    )
    list_filter = ("owner", "status")
    search_fields = (
        "refund_no",
        "out_refund_no",
        "refund_id",
        "payment__out_trade_no",
    )
    raw_id_fields = ("owner", "customer", "buyer_user", "payment")
    readonly_fields = ("request_payload", "response_payload", "callback_payload")


@admin.register(SaleMiniAfterSaleRequest)
class SaleMiniAfterSaleRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "request_no",
        "owner",
        "customer",
        "mapping",
        "request_type",
        "status",
        "amount",
        "requested_at",
    )
    list_filter = ("owner", "request_type", "status")
    search_fields = (
        "request_no",
        "mapping__outbound_order__order_no",
        "customer__code",
        "customer__name",
    )
    raw_id_fields = ("owner", "customer", "buyer_user", "mapping")


@admin.register(SaleMiniPaymentEvent)
class SaleMiniPaymentEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event_id",
        "event_type",
        "process_status",
        "out_trade_no",
        "out_refund_no",
        "processed_at",
    )
    list_filter = ("event_type", "process_status")
    search_fields = ("event_id", "out_trade_no", "out_refund_no")
    raw_id_fields = ("payment", "refund")
    readonly_fields = ("payload", "decrypted_payload")


@admin.register(SaleMiniCouponTemplate)
class SaleMiniCouponTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner",
        "code",
        "title",
        "threshold_amount",
        "discount_amount",
        "effective_from",
        "effective_to",
        "is_active",
    )
    list_filter = ("owner", "coupon_type", "is_active")
    search_fields = ("code", "title")
    raw_id_fields = ("owner",)


@admin.register(SaleMiniCoupon)
class SaleMiniCouponAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "coupon_no",
        "owner",
        "customer",
        "buyer_user",
        "template",
        "status",
        "expires_at",
    )
    list_filter = ("owner", "status", "template")
    search_fields = ("coupon_no", "customer__code", "customer__name")
    raw_id_fields = (
        "owner",
        "customer",
        "buyer_user",
        "template",
        "locked_mapping",
        "used_mapping",
    )


@admin.register(SaleMiniOrderAdjustment)
class SaleMiniOrderAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "adjustment_no",
        "owner",
        "customer",
        "mapping",
        "adjustment_type",
        "status",
        "amount",
        "source_code",
    )
    list_filter = ("owner", "adjustment_type", "status")
    search_fields = (
        "adjustment_no",
        "source_code",
        "mapping__outbound_order__order_no",
    )
    raw_id_fields = ("owner", "customer", "buyer_user", "mapping")


@admin.register(SaleMiniPointLedger)
class SaleMiniPointLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tx_no",
        "owner",
        "customer",
        "buyer_user",
        "mapping",
        "tx_type",
        "points_delta",
        "frozen_delta",
        "amount",
    )
    list_filter = ("owner", "tx_type")
    search_fields = ("tx_no", "customer__code", "customer__name")
    raw_id_fields = ("owner", "customer", "buyer_user", "mapping")


@admin.register(SaleMiniDistributionRecord)
class SaleMiniDistributionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner",
        "customer",
        "buyer_user",
        "referrer",
        "mapping",
        "status",
        "commission_rate",
        "commission_amount",
    )
    list_filter = ("owner", "status")
    search_fields = (
        "customer__code",
        "customer__name",
        "mapping__outbound_order__order_no",
    )
    raw_id_fields = ("owner", "customer", "buyer_user", "referrer", "mapping")
