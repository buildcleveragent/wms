from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from allapp.baseinfo.models import Owner
from allapp.products.models import Product

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
    SaleMiniProductReview,
    SaleMiniProductReviewImage,
    SaleMiniRefund,
    SaleProductConfig,
)
from .services_salemini_payments import (
    query_and_apply_payment,
    reconcile_refund,
)


def _format_validation_error(exc):
    if hasattr(exc, "message_dict"):
        parts = []
        for field, values in exc.message_dict.items():
            parts.append(f"{field}: {'; '.join(values)}")
        return "；".join(parts)
    messages_list = getattr(exc, "messages", None)
    if messages_list:
        return "；".join(str(item) for item in messages_list)
    return str(exc)


class SaleProductOwnerBulkForm(forms.Form):
    OPERATION_LIST = "list"
    OPERATION_CREATE = "create"
    OPERATION_UNLIST = "unlist"
    OPERATION_CHOICES = (
        (OPERATION_LIST, "创建缺失配置并上架该货主全部合格商品"),
        (OPERATION_CREATE, "仅创建缺失配置，不立即上架"),
        (OPERATION_UNLIST, "下架该货主全部已配置商品"),
    )

    owner = forms.ModelChoiceField(
        label="货主",
        queryset=Owner.objects.filter(is_active=True).order_by("code"),
        required=True,
    )
    operation = forms.ChoiceField(
        label="操作",
        choices=OPERATION_CHOICES,
        initial=OPERATION_LIST,
    )
    only_active_products = forms.BooleanField(
        label="只处理启用商品",
        required=False,
        initial=True,
    )
    skip_missing_price = forms.BooleanField(
        label="上架时跳过缺价格商品",
        required=False,
        initial=True,
    )
    sync_sale_price = forms.BooleanField(
        label="缺商城售价时使用商品基础价格",
        required=False,
        initial=True,
    )
    overwrite_existing = forms.BooleanField(
        label="覆盖已有配置的价格、库存展示和购买规则",
        required=False,
        initial=False,
    )
    stock_display = forms.ChoiceField(
        label="库存展示方式",
        choices=SaleProductConfig.StockDisplay.choices,
        initial=SaleProductConfig.StockDisplay.STATUS,
    )
    enable_qty_rules = forms.BooleanField(
        label="启用起购及递增限制",
        required=False,
        initial=False,
    )
    min_order_qty = forms.DecimalField(
        label="起购数量",
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        initial=Decimal("1.000"),
    )
    multiple_qty = forms.DecimalField(
        label="购买递增量",
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        initial=Decimal("1.000"),
    )
    max_order_qty = forms.DecimalField(
        label="最大购买量",
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        min_order_qty = cleaned.get("min_order_qty")
        max_order_qty = cleaned.get("max_order_qty")
        if (
            cleaned.get("enable_qty_rules")
            and min_order_qty is not None
            and max_order_qty is not None
            and max_order_qty < min_order_qty
        ):
            raise forms.ValidationError("最大购买量不能小于起购数量。")
        return cleaned


def _owner_bulk_product_queryset(owner, *, only_active_products):
    qs = Product.objects.filter(owner=owner).select_related(
        "owner", "category", "category__parent", "category__parent__parent"
    )
    if only_active_products:
        qs = qs.filter(is_active=True, owner__is_active=True)
    return qs.order_by("code", "id")


def _apply_owner_bulk_listing(form, user):
    owner = form.cleaned_data["owner"]
    products = list(
        _owner_bulk_product_queryset(
            owner,
            only_active_products=form.cleaned_data["only_active_products"],
        )
    )
    existing_configs = {
        config.product_id: config
        for config in SaleProductConfig.objects.filter(
            owner=owner, product_id__in=[product.id for product in products]
        )
    }
    result = {
        "owner": owner,
        "total": len(products),
        "created": 0,
        "updated": 0,
        "listed": 0,
        "unlisted": 0,
        "skipped": 0,
        "errors": [],
    }

    with transaction.atomic():
        for product in products:
            config = existing_configs.get(product.id)
            was_new = config is None
            try:
                changed = _apply_owner_bulk_to_product(
                    form,
                    user,
                    product,
                    config,
                )
            except ValidationError as exc:
                result["skipped"] += 1
                result["errors"].append(
                    f"{product.code} {product.name}: {_format_validation_error(exc)}"
                )
                continue
            if changed in {"created", "listed"} and was_new:
                result["created"] += 1
            if changed == "updated" or (changed == "listed" and not was_new):
                result["updated"] += 1
            if changed == "listed":
                result["listed"] += 1
            elif changed == "unlisted":
                result["unlisted"] += 1
                result["updated"] += 1
            elif changed == "skipped":
                result["skipped"] += 1
    return result


def _apply_owner_bulk_to_product(form, user, product, config):
    operation = form.cleaned_data["operation"]
    if operation == SaleProductOwnerBulkForm.OPERATION_UNLIST:
        if not config:
            return "skipped"
        if not config.is_listed:
            return "skipped"
        config.is_listed = False
        config.updated_by = user
        config.full_clean()
        config.save()
        return "unlisted"

    created = False
    if not config:
        config = SaleProductConfig(
            owner=product.owner,
            product=product,
            created_by=user,
        )
        created = True

    overwrite = form.cleaned_data["overwrite_existing"]
    sync_sale_price = form.cleaned_data["sync_sale_price"]
    if created or overwrite:
        config.stock_display = form.cleaned_data["stock_display"]
        config.enable_qty_rules = form.cleaned_data["enable_qty_rules"]
        config.min_order_qty = form.cleaned_data["min_order_qty"]
        config.multiple_qty = form.cleaned_data["multiple_qty"]
        config.max_order_qty = form.cleaned_data["max_order_qty"]
    if sync_sale_price and product.price is not None:
        if created or overwrite or config.sale_price is None:
            config.sale_price = product.price

    if operation == SaleProductOwnerBulkForm.OPERATION_LIST:
        if config.sale_price is None and product.price is None:
            if form.cleaned_data["skip_missing_price"]:
                raise ValidationError("商品基础价格和商城售价都为空，已跳过。")
            raise ValidationError("商品基础价格和商城售价都为空，不能上架。")
        config.is_active = True
        config.is_listed = True
    elif operation == SaleProductOwnerBulkForm.OPERATION_CREATE:
        if created:
            config.is_listed = False
        else:
            return "skipped"

    config.updated_by = user
    config.full_clean()
    config.save()
    if operation == SaleProductOwnerBulkForm.OPERATION_LIST:
        return "listed"
    return "created" if created else "updated"


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
    change_list_template = "admin/salesapp/saleproductconfig/change_list.html"
    actions = ("mark_listed", "mark_unlisted")
    fields = (
        "owner",
        "product",
        "sale_price",
        "market_price",
        "is_listed",
        "is_recommended",
        "is_hot",
        "is_new",
        "stock_display",
        "enable_qty_rules",
        "min_order_qty",
        "max_order_qty",
        "multiple_qty",
        "sort_order",
        "remark",
    )
    exclude = (
        "is_deleted",
        "deleted_at",
        "deleted_by",
        "created_by",
        "updated_by",
        "is_active",
    )
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
    autocomplete_fields = ("product",)

    class Media:
        js = ("admin/js/sale_product_config.js",)

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def get_urls(self):
        custom_urls = [
            path(
                "bulk-owner-list/",
                self.admin_site.admin_view(self.bulk_owner_list_view),
                name="salesapp_saleproductconfig_bulk_owner_list",
            ),
        ]
        return custom_urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["bulk_owner_list_url"] = reverse(
            "admin:salesapp_saleproductconfig_bulk_owner_list"
        )
        return super().changelist_view(request, extra_context=extra_context)

    def bulk_owner_list_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied("没有修改商城上架配置的权限。")
        if request.method == "POST":
            form = SaleProductOwnerBulkForm(request.POST)
            if form.is_valid():
                operation = form.cleaned_data["operation"]
                if operation != SaleProductOwnerBulkForm.OPERATION_UNLIST:
                    if not self.has_add_permission(request):
                        raise PermissionDenied("没有新增商城上架配置的权限。")
                result = _apply_owner_bulk_listing(form, request.user)
                level = messages.SUCCESS if not result["errors"] else messages.WARNING
                message = (
                    f"{result['owner'].code} 批量处理完成："
                    f"扫描 {result['total']} 个商品，"
                    f"创建 {result['created']} 个，更新 {result['updated']} 个，"
                    f"上架 {result['listed']} 个，下架 {result['unlisted']} 个，"
                    f"跳过 {result['skipped']} 个。"
                )
                if result["errors"]:
                    message += " 跳过原因：" + "；".join(result["errors"][:8])
                    if len(result["errors"]) > 8:
                        message += f"；还有 {len(result['errors']) - 8} 条已省略"
                self.message_user(request, message, level=level)
                return redirect("admin:salesapp_saleproductconfig_changelist")
        else:
            form = SaleProductOwnerBulkForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "按货主批量上架商城商品",
            "form": form,
            "changelist_url": reverse("admin:salesapp_saleproductconfig_changelist"),
        }
        return TemplateResponse(
            request,
            "admin/salesapp/saleproductconfig/bulk_owner_list.html",
            context,
        )

    @admin.action(description="上架选中的商城配置")
    def mark_listed(self, request, queryset):
        ok = 0
        errors = []
        for config in queryset.select_related("owner", "product", "product__category"):
            try:
                if config.sale_price is None and config.product.price is None:
                    raise ValidationError("商品基础价格和商城售价都为空，不能上架。")
                config.is_active = True
                config.is_listed = True
                config.updated_by = request.user
                config.full_clean()
                config.save()
                ok += 1
            except ValidationError as exc:
                errors.append(f"{config.product.code}: {_format_validation_error(exc)}")
        if ok:
            self.message_user(request, f"已上架 {ok} 个商品配置。", messages.SUCCESS)
        if errors:
            self.message_user(request, "；".join(errors[:10]), messages.WARNING)

    @admin.action(description="下架选中的商城配置")
    def mark_unlisted(self, request, queryset):
        updated = queryset.update(is_listed=False, updated_by_id=request.user.id)
        self.message_user(request, f"已下架 {updated} 个商品配置。", messages.SUCCESS)


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
    readonly_fields = (
        "request_payload",
        "client_pay_params",
        "prepay_response",
        "callback_payload",
    )
    actions = ("query_wechat_status",)

    @admin.action(description="查询微信支付状态")
    def query_wechat_status(self, request, queryset):
        success = 0
        failed = 0
        for payment_id in queryset.values_list("id", flat=True):
            try:
                payment = SaleMiniPayment.objects.get(pk=payment_id)
                query_and_apply_payment(payment, by_user=request.user)
                success += 1
            except Exception as exc:
                failed += 1
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(
            request,
            f"微信支付状态查询完成：成功 {success}，失败 {failed}。",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )


@admin.register(SaleMiniRefund)
class SaleMiniRefundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "refund_no",
        "out_refund_no",
        "owner",
        "customer",
        "source",
        "status",
        "amount",
        "refund_id",
        "retry_count",
        "requires_manual_action",
        "created_at",
    )
    list_filter = ("owner", "source", "status", "requires_manual_action")
    search_fields = (
        "refund_no",
        "out_refund_no",
        "refund_id",
        "payment__out_trade_no",
    )
    raw_id_fields = ("owner", "customer", "buyer_user", "payment")
    readonly_fields = ("request_payload", "response_payload", "callback_payload")
    actions = ("retry_wechat_refund",)

    @admin.action(description="查询微信状态或立即重试退款")
    def retry_wechat_refund(self, request, queryset):
        success = 0
        failed = 0
        for refund_id in queryset.values_list("id", flat=True):
            try:
                with transaction.atomic():
                    refund = (
                        SaleMiniRefund.objects.select_for_update()
                        .select_related("payment", "payment__mapping")
                        .get(pk=refund_id)
                    )
                    if refund.status == SaleMiniRefund.Status.SUCCESS:
                        continue
                    refund.requires_manual_action = False
                    refund.next_retry_at = timezone.now()
                    refund.updated_by = request.user
                    refund.save(
                        update_fields=[
                            "requires_manual_action",
                            "next_retry_at",
                            "updated_by",
                            "updated_at",
                        ]
                    )
                reconcile_refund(refund)
                success += 1
            except Exception as exc:
                failed += 1
                self.message_user(request, str(exc), level=messages.ERROR)
        self.message_user(
            request,
            f"退款处理完成：成功受理 {success}，失败 {failed}。",
            level=messages.SUCCESS if not failed else messages.WARNING,
        )


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


class SaleMiniProductReviewImageInline(admin.TabularInline):
    model = SaleMiniProductReviewImage
    extra = 0
    can_delete = False
    fields = ("sort_order", "image_preview", "width", "height", "size_bytes")
    readonly_fields = fields

    @admin.display(description="图片")
    def image_preview(self, obj):
        if not obj.pk or not obj.image:
            return "-"
        return format_html(
            '<a href="{}" target="_blank"><img src="{}" style="max-width:120px;max-height:120px"></a>',
            obj.image.url,
            obj.image.url,
        )


@admin.register(SaleMiniProductReview)
class SaleMiniProductReviewAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "product",
        "buyer_user",
        "quality_score",
        "delivery_score",
        "overall_score",
        "status",
        "is_anonymous",
        "submitted_at",
    )
    list_filter = ("owner", "status", "overall_score", "is_anonymous")
    search_fields = (
        "product__code",
        "product__name",
        "buyer_user__nickname",
        "mapping__outbound_order__order_no",
        "content",
    )
    raw_id_fields = (
        "owner",
        "customer",
        "buyer_user",
        "mapping",
        "order_line",
        "product",
        "product_config",
        "reviewed_by",
    )
    readonly_fields = (
        "owner",
        "customer",
        "buyer_user",
        "mapping",
        "order_line",
        "product",
        "product_config",
        "quality_score",
        "delivery_score",
        "overall_score",
        "content",
        "is_anonymous",
        "status",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "published_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "owner",
        "customer",
        "buyer_user",
        "mapping",
        "order_line",
        "product",
        "product_config",
        "quality_score",
        "delivery_score",
        "overall_score",
        "content",
        "is_anonymous",
        "status",
        "rejection_reason",
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "published_at",
        "created_at",
        "updated_at",
    )
    inlines = [SaleMiniProductReviewImageInline]
    actions = ("publish_reviews", "reject_reviews", "hide_reviews")

    def has_add_permission(self, request):
        return False

    @admin.action(description="审核通过并发布选中的评价")
    def publish_reviews(self, request, queryset):
        now = timezone.now()
        count = 0
        with transaction.atomic():
            for review in queryset.select_for_update().filter(
                status=SaleMiniProductReview.Status.PENDING
            ):
                review.status = SaleMiniProductReview.Status.PUBLISHED
                review.reviewed_at = now
                review.reviewed_by = request.user
                review.published_at = now
                review.rejection_reason = ""
                review.updated_by = request.user
                review.save()
                count += 1
        self.message_user(request, f"已发布 {count} 条评价。", messages.SUCCESS)

    @admin.action(description="驳回选中的待审核评价")
    def reject_reviews(self, request, queryset):
        now = timezone.now()
        count = 0
        with transaction.atomic():
            for review in queryset.select_for_update().filter(
                status=SaleMiniProductReview.Status.PENDING
            ):
                review.status = SaleMiniProductReview.Status.REJECTED
                review.reviewed_at = now
                review.reviewed_by = request.user
                review.published_at = None
                if not review.rejection_reason:
                    review.rejection_reason = "评价未通过审核，请修改后重新提交。"
                review.updated_by = request.user
                review.save()
                count += 1
        self.message_user(request, f"已驳回 {count} 条评价。", messages.SUCCESS)

    @admin.action(description="隐藏选中的已发布评价")
    def hide_reviews(self, request, queryset):
        now = timezone.now()
        count = 0
        with transaction.atomic():
            for review in queryset.select_for_update().filter(
                status=SaleMiniProductReview.Status.PUBLISHED
            ):
                review.status = SaleMiniProductReview.Status.HIDDEN
                review.reviewed_at = now
                review.reviewed_by = request.user
                review.updated_by = request.user
                review.save()
                count += 1
        self.message_user(request, f"已隐藏 {count} 条评价。", messages.SUCCESS)


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
