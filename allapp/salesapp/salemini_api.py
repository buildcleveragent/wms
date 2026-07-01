import hashlib
import json
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib import error as url_error
from urllib import parse
from urllib import request as url_request

from django.conf import settings
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from allapp.baseinfo.models import Customer
from allapp.inventory.models import InventoryDetail
from allapp.outbound.models import OutboundOrder, OutboundOrderLine
from allapp.outbound.services import unallocate_for_order
from allapp.products.models import Brand, Product, ProductCategory

from .mobile_api import (
    MONEY_QUANT,
    PRICE_QUANT,
    QTY_QUANT,
    _channel_for_customer,
    _default_order_uom,
    _error_message,
    _image_url,
    _money,
    _owner_for_user,
    _price,
    _qty,
    _qty_in_base_for_uom,
    _str,
    _uom_options,
    _validate_order_uom,
)
from .models import (
    MiniCustomerAddress,
    MiniProgramUser,
    SaleMiniAfterSaleRequest,
    SaleMiniBanner,
    SaleMiniCart,
    SaleMiniCartItem,
    SaleMiniCoupon,
    SaleMiniOrderMapping,
    SaleMiniPayment,
    SaleMiniPaymentEvent,
    SaleMiniRefund,
    SaleProductConfig,
)
from .services_pricing import compute_price_for_line
from .services_salemini_adjustments import (
    build_adjustment_preview,
    confirm_adjustments,
    confirm_distribution,
    create_distribution_record,
    lock_adjustments,
    point_balance,
    release_adjustments,
    reverse_distribution,
)
from .services_validation import OrderRuleError, validate_order_line_rules
from .services_wechat_pay import (
    WechatPayConfigError,
    WechatPayRequestError,
    close_jsapi_payment,
    create_jsapi_prepay,
    decrypt_resource,
    money_to_cents,
    request_refund,
    sign_jsapi_pay_params,
    verify_callback_signature,
)


class SaleMiniPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class WechatLoginUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "微信登录服务暂不可用。"
    default_code = "wechat_login_unavailable"


class WechatPayUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "微信支付服务暂不可用。"
    default_code = "wechat_pay_unavailable"


def _business_date():
    now = timezone.now()
    if timezone.is_aware(now):
        return timezone.localtime(now).date()
    return now.date()


def _payment_deadline():
    minutes = max(int(getattr(settings, "SALE_MINI_PAY_TIMEOUT_MINUTES", 30)), 1)
    return timezone.now() + timedelta(minutes=minutes)


def _payment_code(prefix):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{stamp}{uuid.uuid4().hex[:10].upper()}"


def _handle_wechat_pay_error(exc):
    if isinstance(exc, WechatPayConfigError):
        raise WechatPayUnavailable(str(exc)) from exc
    if isinstance(exc, WechatPayRequestError):
        raise WechatPayUnavailable(str(exc)) from exc
    raise exc


def _payment_payload(payment):
    if not payment:
        return None
    return {
        "id": payment.id,
        "payment_no": payment.payment_no,
        "out_trade_no": payment.out_trade_no,
        "channel": payment.channel,
        "status": payment.status,
        "status_name": payment.get_status_display(),
        "amount": _str(payment.amount, MONEY_QUANT),
        "currency": payment.currency,
        "transaction_id": payment.transaction_id,
        "prepay_id": payment.prepay_id,
        "expires_at": payment.expires_at.isoformat() if payment.expires_at else "",
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else "",
    }


def _latest_payment(mapping):
    return mapping.payments.order_by("-created_at", "-id").first()


def _active_payment(mapping):
    return (
        mapping.payments.filter(
            channel=SaleMiniPayment.Channel.WECHAT_JSAPI,
            status__in=[
                SaleMiniPayment.Status.CREATED,
                SaleMiniPayment.Status.PREPAY,
            ],
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _refund_payload(refund):
    if not refund:
        return None
    return {
        "id": refund.id,
        "refund_no": refund.refund_no,
        "out_refund_no": refund.out_refund_no,
        "refund_id": refund.refund_id,
        "status": refund.status,
        "status_name": refund.get_status_display(),
        "amount": _str(refund.amount, MONEY_QUANT),
        "reason": refund.reason,
        "requested_at": refund.requested_at.isoformat() if refund.requested_at else "",
        "success_at": refund.success_at.isoformat() if refund.success_at else "",
    }


def _after_sale_payload(row):
    if not row:
        return None
    order = getattr(row.mapping, "outbound_order", None)
    return {
        "id": row.id,
        "request_no": row.request_no,
        "order_id": order.id if order else None,
        "order_no": getattr(order, "order_no", ""),
        "request_type": row.request_type,
        "request_type_name": row.get_request_type_display(),
        "status": row.status,
        "status_name": row.get_status_display(),
        "amount": _str(row.amount, MONEY_QUANT),
        "reason": row.reason,
        "requested_at": row.requested_at.isoformat() if row.requested_at else "",
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else "",
        "review_note": row.review_note,
    }


def _close_active_payment(mapping):
    payment = _active_payment(mapping)
    if not payment:
        return None
    try:
        close_jsapi_payment(payment)
    except (WechatPayConfigError, WechatPayRequestError) as exc:
        _handle_wechat_pay_error(exc)
    payment.status = SaleMiniPayment.Status.CLOSED
    payment.closed_at = timezone.now()
    payment.save(update_fields=["status", "closed_at", "updated_at"])
    return payment


def _cancel_unpaid_mapping(mapping, by_user=None, *, close_wechat=False):
    if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.PAID:
        raise ValidationError({"payment": "订单已支付，请走退款流程。"})
    if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.REFUNDING:
        raise ValidationError({"payment": "订单正在退款中，请勿重复取消。"})
    if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.REFUNDED:
        raise ValidationError({"payment": "订单已退款。"})
    if close_wechat:
        _close_active_payment(mapping)
    order = mapping.outbound_order
    if order.approval_status != "CANCELLED":
        unallocate_for_order(order)
        order.approval_status = "CANCELLED"
        order.updated_by = by_user
        order.save(update_fields=["approval_status", "updated_by", "updated_at"])
    release_adjustments(
        mapping,
        by_user=by_user,
        reverse_confirmed=(
            mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.OFFLINE
        ),
    )
    reverse_distribution(mapping)
    mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.CANCELLED
    mapping.updated_by = by_user
    mapping.save(update_fields=["payment_status", "updated_by", "updated_at"])
    return mapping


def _cancel_order_after_refund_request(mapping, by_user=None):
    order = mapping.outbound_order
    if order.approval_status != "CANCELLED":
        unallocate_for_order(order)
        order.approval_status = "CANCELLED"
        order.updated_by = by_user
        order.save(update_fields=["approval_status", "updated_by", "updated_at"])


def _finalize_successful_refund(mapping, payment, by_user=None):
    payment.status = SaleMiniPayment.Status.REFUNDED
    payment.save(update_fields=["status", "updated_at"])
    mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDED
    mapping.updated_by = by_user
    mapping.save(update_fields=["payment_status", "updated_by", "updated_at"])
    release_adjustments(mapping, by_user=by_user, reverse_confirmed=True)
    reverse_distribution(mapping)


class SaleMiniLineInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    qty = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    order_uom = serializers.CharField(required=False, allow_blank=True)


class SaleMiniPreviewSerializer(serializers.Serializer):
    PAYMENT_METHOD_CHOICES = [
        ("OFFLINE", "线下付款"),
        ("WECHAT", "微信支付"),
    ]

    owner_id = serializers.IntegerField(required=False)
    customer_id = serializers.IntegerField(required=False)
    cart_id = serializers.IntegerField(required=False, allow_null=True)
    cart_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True,
    )
    address_id = serializers.IntegerField(required=False, allow_null=True)
    payment_method = serializers.ChoiceField(
        choices=PAYMENT_METHOD_CHOICES,
        required=False,
        default="OFFLINE",
    )
    delivery_method = serializers.ChoiceField(
        choices=OutboundOrder.DELIVERY_METHOD_CHOICES,
        required=False,
        default="OWN_TRUCK",
    )
    contact = serializers.CharField(required=False, allow_blank=True, default="")
    contact_phone = serializers.CharField(required=False, allow_blank=True, default="")
    ship_to = serializers.CharField(required=False, allow_blank=True, default="")
    remark = serializers.CharField(required=False, allow_blank=True, default="")
    coupon_id = serializers.IntegerField(required=False, allow_null=True)
    points = serializers.IntegerField(required=False, min_value=0, default=0)
    referrer_buyer_id = serializers.IntegerField(required=False, allow_null=True)
    lines = SaleMiniLineInputSerializer(many=True)

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("至少需要一条商品明细。")
        product_keys = []
        for line in lines:
            product_keys.append(
                (line["product_id"], (line.get("order_uom") or "").strip())
            )
        if len(product_keys) != len(set(product_keys)):
            raise serializers.ValidationError("相同商品和订货单位请合并后再提交。")
        product_ids = [line["product_id"] for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise serializers.ValidationError(
                "同一商品请合并为一条，避免重复占用库存。"
            )
        return lines


class SaleMiniCartAddSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=False)
    customer_id = serializers.IntegerField(required=False)
    config_id = serializers.IntegerField(required=False)
    product_id = serializers.IntegerField()
    qty = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    order_uom = serializers.CharField(required=False, allow_blank=True)


class SaleMiniCartUpdateSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=False)
    customer_id = serializers.IntegerField(required=False)
    item_id = serializers.IntegerField(required=False)
    product_id = serializers.IntegerField(required=False)
    order_uom = serializers.CharField(required=False, allow_blank=True)
    qty = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0"),
    )

    def validate(self, attrs):
        if not attrs.get("item_id") and not attrs.get("product_id"):
            raise serializers.ValidationError("需要提供 item_id 或 product_id。")
        return attrs


class SaleMiniCartRemoveSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=False)
    customer_id = serializers.IntegerField(required=False)
    item_id = serializers.IntegerField(required=False)
    product_id = serializers.IntegerField(required=False)
    order_uom = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("item_id") and not attrs.get("product_id"):
            raise serializers.ValidationError("需要提供 item_id 或 product_id。")
        return attrs


class SaleMiniWechatLoginSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=256)
    owner_id = serializers.IntegerField(required=False)
    owner_code = serializers.CharField(required=False, allow_blank=True)
    nickname = serializers.CharField(required=False, allow_blank=True, default="")
    avatar_url = serializers.CharField(required=False, allow_blank=True, default="")


class SaleMiniWechatPrepaySerializer(serializers.Serializer):
    order_id = serializers.IntegerField()


class SaleMiniWechatRefundSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class SaleMiniAfterSaleCreateSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    request_type = serializers.ChoiceField(
        choices=SaleMiniAfterSaleRequest.RequestType.choices,
        required=False,
        default=SaleMiniAfterSaleRequest.RequestType.REFUND,
    )
    amount = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        allow_null=True,
        min_value=Decimal("0.01"),
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class MiniAddressInputSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=False)
    customer_id = serializers.IntegerField(required=False)
    contact = serializers.CharField(max_length=80)
    phone = serializers.CharField(max_length=40)
    province = serializers.CharField(
        max_length=30, required=False, allow_blank=True, default=""
    )
    city = serializers.CharField(
        max_length=30, required=False, allow_blank=True, default=""
    )
    district = serializers.CharField(
        max_length=30, required=False, allow_blank=True, default=""
    )
    detail = serializers.CharField(max_length=200)
    is_default = serializers.BooleanField(required=False, default=False)


def _buyer_for_user(owner, user):
    return (
        MiniProgramUser.objects.filter(owner=owner, user=user, is_active=True)
        .select_related("owner", "customer", "user", "user__warehouse")
        .first()
    )


def _buyer_record_for_user(owner, user):
    return (
        MiniProgramUser.objects.filter(owner=owner, user=user)
        .select_related("owner", "customer", "user", "user__warehouse")
        .first()
    )


def _buyer_bindings_for_user(user):
    return (
        MiniProgramUser.objects.filter(
            user=user,
            is_active=True,
            owner__is_active=True,
            customer__is_active=True,
        )
        .select_related("owner", "customer", "user", "user__warehouse")
        .order_by("owner__code", "id")
    )


def _default_owner_for_user(user):
    owner = getattr(user, "owner", None)
    if owner:
        return owner
    buyer = _buyer_bindings_for_user(user).first()
    if buyer:
        return buyer.owner
    return _owner_for_user(user)


def _mini_customer_code(owner, user):
    base = f"MINI-U{user.id or '0'}"
    for index in range(20):
        suffix = "" if index == 0 else f"-{index}"
        candidate = f"{base}{suffix}"[:30]
        if not Customer.objects.filter(owner=owner, code=candidate).exists():
            return candidate
    return f"MINI-{uuid.uuid4().hex[:24]}"[:30]


def _ensure_buyer_for_owner(owner, user, customer=None):
    buyer = _buyer_for_user(owner, user)
    if buyer and buyer.customer_id:
        return buyer.customer, buyer

    existing = _buyer_record_for_user(owner, user)
    if existing:
        if not existing.is_active or not existing.customer.is_active:
            raise PermissionDenied("当前账号购买权限无效或已停用，请联系客服。")
        return existing.customer, existing

    primary = _buyer_bindings_for_user(user).first()
    if customer is None:
        primary_customer = primary.customer if primary else None
        customer = Customer.objects.create(
            owner=owner,
            salesperson=user,
            code=_mini_customer_code(owner, user),
            name=(
                getattr(primary_customer, "name", "")
                or getattr(user, "get_username", lambda: "")()
                or f"商城客户{user.id}"
            )[:50],
            contact_person=getattr(primary_customer, "contact_person", None),
            phone=getattr(primary_customer, "phone", None),
            mobile=getattr(primary_customer, "mobile", None),
        )
    buyer = MiniProgramUser.objects.create(
        owner=owner,
        user=user,
        customer=customer,
        nickname=(getattr(primary, "nickname", "") if primary else "")[:80],
        avatar_url=(getattr(primary, "avatar_url", "") if primary else ""),
        phone=(getattr(primary, "phone", "") if primary else ""),
        created_by=user,
        updated_by=user,
    )
    return customer, buyer


def _buyer_binding_payload(buyer):
    return {
        "owner": {
            "id": buyer.owner_id,
        },
        "buyer": {
            "id": buyer.id,
            "nickname": buyer.nickname or "",
            "phone": buyer.phone or "",
            "openid_bound": bool(buyer.openid),
            "unionid_bound": bool(buyer.unionid),
        },
        "customer": {
            "id": buyer.customer_id,
        },
    }


def _customer_for_context(owner, user, data=None, *, required=True, auto_create=False):
    data = data or {}
    buyer = _buyer_for_user(owner, user)
    if buyer and buyer.customer_id:
        return buyer.customer, buyer

    customer_id = data.get("customer_id")
    if customer_id:
        customer = get_object_or_404(
            Customer.objects.filter(owner=owner, is_active=True),
            pk=customer_id,
        )
        if auto_create:
            return _ensure_buyer_for_owner(owner, user, customer=customer)
        return customer, buyer

    if auto_create:
        return _ensure_buyer_for_owner(owner, user)

    if required:
        raise PermissionDenied(
            "当前商品暂未对你的账号开通购买权限，可先浏览或联系客服。"
        )
    return None, buyer


def _warehouse_for_user(user):
    warehouse = getattr(user, "warehouse", None)
    if not warehouse:
        raise PermissionDenied("当前账号暂未完成商城履约配置，请联系客服处理。")
    return warehouse


def _wechat_code_to_session(code):
    appid = settings.WECHAT_MINI_APPID
    secret = settings.WECHAT_MINI_SECRET
    if not appid or not secret:
        raise WechatLoginUnavailable("微信小程序 appid/secret 未配置。")
    query = parse.urlencode(
        {
            "appid": appid,
            "secret": secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
    )
    url = f"{settings.WECHAT_JSCODE2SESSION_URL}?{query}"
    try:
        with url_request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, url_error.URLError) as exc:
        raise WechatLoginUnavailable("微信登录服务请求失败，请稍后重试。") from exc

    errcode = payload.get("errcode")
    if errcode:
        raise ValidationError(
            {
                "code": (
                    f"微信登录 code 无效或已过期：{payload.get('errmsg') or errcode}"
                )
            }
        )
    if not payload.get("openid"):
        raise WechatLoginUnavailable("微信登录未返回 openid。")
    return payload


def _token_payload_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "user": {"id": user.id, "username": user.username},
    }


def _sale_mini_context_payload(buyer):
    user = buyer.user
    customer = buyer.customer
    bindings = [_buyer_binding_payload(row) for row in _buyer_bindings_for_user(user)]
    return {
        "buyer": {
            "id": buyer.id,
            "nickname": buyer.nickname or "",
            "phone": buyer.phone or "",
            "openid_bound": bool(buyer.openid),
            "unionid_bound": bool(buyer.unionid),
        },
        "customer": {
            "id": customer.id,
        },
        "bindings": bindings,
    }


def _validate_wechat_buyer(buyer, openid, unionid):
    if not buyer:
        raise PermissionDenied("当前微信账号暂未开通购买权限，请联系客服。")
    if not buyer.user_id:
        raise PermissionDenied("当前账号暂未完成商城服务配置，请联系客服处理。")
    if not buyer.user.is_active:
        raise PermissionDenied("当前绑定账号已停用。")
    if not buyer.customer_id or not buyer.customer.is_active:
        raise PermissionDenied("当前账号购买权限无效或已停用，请联系客服。")
    if buyer.customer.owner_id != buyer.owner_id:
        raise PermissionDenied("当前账号购买权限配置异常，请联系客服处理。")
    if not buyer.user.warehouse_id:
        raise PermissionDenied("当前账号暂未完成商城履约配置，请联系客服处理。")
    if buyer.openid and buyer.openid != openid:
        raise PermissionDenied("当前微信用户 openid 与已绑定记录不一致。")
    if unionid and buyer.unionid and buyer.unionid != unionid:
        raise PermissionDenied("当前微信用户 unionid 与已绑定记录不一致。")


def _bound_wechat_buyer(data, session_data):
    openid = session_data["openid"]
    unionid = session_data.get("unionid") or ""
    owner_id = data.get("owner_id")
    owner_code = (data.get("owner_code") or "").strip()

    def scoped(qs):
        if owner_id:
            qs = qs.filter(owner_id=owner_id)
        if owner_code:
            qs = qs.filter(owner__code=owner_code)
        return qs

    base_qs = (
        MiniProgramUser.objects.select_for_update()
        .select_related("owner", "customer", "user", "user__warehouse")
        .filter(is_active=True)
    )
    duplicate = scoped(base_qs.filter(openid=openid))
    if unionid:
        duplicate = duplicate | scoped(base_qs.filter(openid="", unionid=unionid))
    buyers = list(duplicate.distinct())
    if not buyers:
        raise PermissionDenied("当前微信账号暂未开通购买权限，请联系客服。")
    user_ids = {buyer.user_id for buyer in buyers if buyer.user_id}
    if len(user_ids) > 1:
        raise ValidationError(
            {"openid": "当前微信账号存在多个购买权限记录，请联系客服处理。"}
        )

    buyer = buyers[0]
    for candidate in buyers:
        _validate_wechat_buyer(candidate, openid, unionid)
    conflict_qs = MiniProgramUser.objects.filter(
        openid=openid,
        is_active=True,
    ).exclude(user_id=buyer.user_id)
    if conflict_qs.exists():
        raise ValidationError(
            {"openid": "当前 openid 已绑定到其他小程序用户，请联系管理员。"}
        )

    update_fields = []
    if not buyer.openid:
        buyer.openid = openid
        update_fields.append("openid")
    if unionid and not buyer.unionid:
        buyer.unionid = unionid
        update_fields.append("unionid")
    nickname = (data.get("nickname") or "").strip()
    if nickname and buyer.nickname != nickname:
        buyer.nickname = nickname
        update_fields.append("nickname")
    avatar_url = (data.get("avatar_url") or "").strip()
    if avatar_url and buyer.avatar_url != avatar_url:
        buyer.avatar_url = avatar_url
        update_fields.append("avatar_url")
    if update_fields:
        update_fields.append("updated_at")
        buyer.save(update_fields=update_fields)
    return buyer


def _saleable_inventory_detail_filter():
    batch_ready = Q(product__batch_control=False) | (
        Q(product__batch_control=True) & ~Q(batch_no="")
    )
    expiry_ready = Q(product__expiry_control=False) | (
        Q(product__expiry_control=True)
        & Q(expiry_date__isnull=False)
        & (
            Q(product__expiry_basis=Product.ExpiryBasis.INBOUND)
            | (
                Q(product__expiry_basis=Product.ExpiryBasis.MFG)
                & Q(production_date__isnull=False)
            )
        )
    )
    serial_ready = Q(product__serial_control=False) | (
        Q(product__serial_control=True) & Q(serial_no_norm__isnull=False)
    )
    return batch_ready & expiry_ready & serial_ready


def _available_map(owner, product_ids, warehouse=None):
    if not product_ids:
        return {}
    qs = InventoryDetail.objects.filter(
        owner=owner,
        product_id__in=product_ids,
        is_active=True,
    ).filter(_saleable_inventory_detail_filter())
    if warehouse:
        qs = qs.filter(warehouse=warehouse)
    rows = qs.values("product_id").annotate(available_qty=Sum("available_qty"))
    return {row["product_id"]: Decimal(row["available_qty"] or 0) for row in rows}


def _config_qs(owner):
    return (
        SaleProductConfig.objects.filter(
            owner=owner,
            product__owner=owner,
            is_active=True,
            is_listed=True,
            product__is_active=True,
        )
        .select_related(
            "product", "product__base_uom", "product__category", "product__brand"
        )
        .prefetch_related("product__packages", "product__packages__uom")
    )


def _public_config_qs():
    return (
        SaleProductConfig.objects.filter(
            owner__is_active=True,
            product__owner=F("owner"),
            product__owner__is_active=True,
            is_active=True,
            is_listed=True,
            product__is_active=True,
        )
        .select_related(
            "owner",
            "product",
            "product__base_uom",
            "product__category",
            "product__brand",
        )
        .prefetch_related("product__packages", "product__packages__uom")
        .order_by("sort_order", "owner__code", "product__code")
    )


def _available_map_for_configs(configs):
    configs = list(configs)
    if not configs:
        return {}
    owner_ids = sorted({config.owner_id for config in configs})
    product_ids = [config.product_id for config in configs]
    rows = (
        InventoryDetail.objects.filter(
            owner_id__in=owner_ids,
            product_id__in=product_ids,
            is_active=True,
        )
        .filter(_saleable_inventory_detail_filter())
        .values("owner_id", "product_id")
        .annotate(available_qty=Sum("available_qty"))
    )
    return {
        (row["owner_id"], row["product_id"]): Decimal(row["available_qty"] or 0)
        for row in rows
    }


def _base_price(owner, customer, product, order_date, channel, config):
    if config.sale_price is not None:
        return _price(config.sale_price)
    return _price(compute_price_for_line(owner, customer, product, order_date, channel))


def _stock_payload(config, product, available_qty):
    available_qty = Decimal(available_qty or 0)
    base_code = getattr(product.base_uom, "code", "")
    if available_qty <= 0:
        status_code = "OUT"
        status_text = "缺货"
    elif available_qty <= Decimal(product.min_pick_multiple or 1) * Decimal("2"):
        status_code = "LOW"
        status_text = "库存紧张"
    else:
        status_code = "IN"
        status_text = "有货"

    if config.stock_display == SaleProductConfig.StockDisplay.EXACT:
        display = f"{_str(available_qty, QTY_QUANT)} {base_code}"
    elif config.stock_display == SaleProductConfig.StockDisplay.HIDDEN:
        display = ""
    else:
        display = status_text

    return {
        "status": status_code,
        "text": status_text,
        "display": display,
        "available_qty": _str(available_qty, QTY_QUANT),
        "base_uom": base_code,
    }


def _package_for_uom(product, order_uom):
    order_uom = (order_uom or "").strip()
    for package in product.packages.all():
        uom = getattr(package, "uom", None)
        values = {
            (getattr(uom, "code", "") or "").strip(),
            (getattr(uom, "name", "") or "").strip(),
        }
        if order_uom in values:
            return package
    return None


def _effective_rules(product, config, policy, qty_in_base):
    min_qty = max(
        Decimal(config.min_order_qty or 0),
        Decimal(policy.get("min_order_qty") or 0),
        Decimal("1"),
    )
    multiple_qty = max(
        Decimal(config.multiple_qty or 0),
        Decimal(policy.get("multiple_qty") or 0),
        Decimal("1"),
    )
    base_multiple = Decimal(product.min_pick_multiple or 1)
    if base_multiple > 1 and qty_in_base > 0:
        converted = (base_multiple / qty_in_base).quantize(
            QTY_QUANT, rounding=ROUND_HALF_UP
        )
        if converted > multiple_qty:
            multiple_qty = converted
    return min_qty, multiple_qty


def _is_multiple(qty, multiple):
    if not multiple:
        return True
    quotient = qty / multiple
    return quotient == quotient.to_integral_value()


def _product_payload(
    request, owner, customer, config, available_qty, order_date, *, detail=False
):
    product = config.product
    channel = _channel_for_customer(owner, customer) if customer else None
    policy = {
        "source": "",
        "order_uom": "",
        "min_order_qty": Decimal("0"),
        "multiple_qty": Decimal("0"),
    }
    if customer:
        from .mobile_api import _policy_for

        policy = _policy_for(owner, customer, product, channel)
    base_unit_price = _base_price(owner, customer, product, order_date, channel, config)
    uom_options = _uom_options(product, base_unit_price=base_unit_price)
    order_uom = _default_order_uom(product, policy)
    qty_in_base = _qty_in_base_for_uom(product, order_uom) or Decimal("1")
    unit_price = _price(base_unit_price * qty_in_base)
    min_order_qty, multiple_qty = _effective_rules(product, config, policy, qty_in_base)

    payload = {
        "id": product.id,
        "config_id": config.id,
        "name": product.name,
        "spec": product.spec or "",
        "brand": getattr(product.brand, "name", "") if product.brand_id else "",
        "category_id": product.category_id,
        "category_name": (
            getattr(product.category, "name", "") if product.category_id else ""
        ),
        "image_url": _image_url(request, product),
        "price": _str(unit_price, PRICE_QUANT),
        "market_price": (
            _str(config.market_price, PRICE_QUANT) if config.market_price else ""
        ),
        "order_uom": order_uom,
        "uom_options": uom_options,
        "stock": _stock_payload(config, product, available_qty),
        "badges": {
            "recommended": config.is_recommended,
            "hot": config.is_hot,
            "new": config.is_new,
        },
        "rules": {
            "min_order_qty": _str(min_order_qty, QTY_QUANT),
            "multiple_qty": _str(multiple_qty, QTY_QUANT),
            "max_order_qty": (
                _str(config.max_order_qty, QTY_QUANT) if config.max_order_qty else ""
            ),
            "break_box_allowed": product.break_box_allowed,
            "min_pick_multiple": product.min_pick_multiple,
        },
    }
    if detail:
        payload.update(
            {
                "description": product.description or "",
                "pack_requirement": product.get_pack_requirement_display(),
                "pack_note": product.pack_note or "",
            }
        )
    return payload


def _line_quote_payload(
    product, config, qty, order_uom, base_price, unit_price, available_qty
):
    qty_in_base = _qty_in_base_for_uom(product, order_uom) or Decimal("1")
    base_qty = _qty(qty * qty_in_base)
    owner = config.owner
    return {
        "owner_id": owner.id,
        "config_id": config.id,
        "product_id": product.id,
        "product_code": product.code,
        "product_name": product.name,
        "product_spec": product.spec or "",
        "image_url": "",
        "order_uom": order_uom,
        "qty": _str(qty, QTY_QUANT),
        "qty_in_base": _str(qty_in_base, QTY_QUANT),
        "base_qty": _str(base_qty, QTY_QUANT),
        "base_uom": getattr(product.base_uom, "code", ""),
        "unit_price": _str(unit_price, PRICE_QUANT),
        "base_unit_price": _str(base_price, PRICE_QUANT),
        "line_amount": _str(_money(unit_price * qty), MONEY_QUANT),
        "available_qty": _str(available_qty, QTY_QUANT),
        "ok": True,
        "message": "",
    }


def _buyer_product_name(product):
    return product.name or "该商品"


def _adjustment_spec_payload(spec):
    return {
        "type": spec["type"],
        "title": spec["title"],
        "amount": _str(spec["amount"], MONEY_QUANT),
        "source_code": spec.get("source_code", ""),
    }


def _build_order_preview(
    request,
    owner,
    customer,
    lines_data,
    *,
    strict=False,
    buyer=None,
    adjustment_data=None,
):
    adjustment_data = adjustment_data or {}
    warehouse = _warehouse_for_user(request.user)
    order_date = _business_date()
    channel = _channel_for_customer(owner, customer)
    product_ids = [line["product_id"] for line in lines_data]
    configs = {
        cfg.product_id: cfg
        for cfg in _config_qs(owner).filter(product_id__in=product_ids)
    }
    missing = [pid for pid in product_ids if pid not in configs]
    if missing:
        raise ValidationError({"products": f"商品未上架或无权购买：{missing}"})

    available = _available_map(owner, product_ids, warehouse=warehouse)
    preview_lines = []
    contexts = []
    total = Decimal("0.00")
    ok = True

    for line_data in lines_data:
        config = configs[line_data["product_id"]]
        product = config.product
        policy = {
            "source": "",
            "order_uom": "",
            "min_order_qty": Decimal("0"),
            "multiple_qty": Decimal("0"),
        }
        from .mobile_api import _policy_for

        policy = _policy_for(owner, customer, product, channel)
        order_uom = (
            line_data.get("order_uom") or _default_order_uom(product, policy)
        ).strip()
        qty = _qty(line_data["qty"])
        line_ok = True
        message = ""

        try:
            _validate_order_uom(product, order_uom)
            validate_order_line_rules(owner, customer.id, product, order_uom, qty)
        except (OrderRuleError, ValidationError) as exc:
            line_ok = False
            message = _error_message(exc)

        qty_in_base = _qty_in_base_for_uom(product, order_uom) or Decimal("1")
        min_qty, multiple_qty = _effective_rules(product, config, policy, qty_in_base)
        product_name = _buyer_product_name(product)
        if qty < min_qty:
            line_ok = False
            message = (
                f"{product_name} 起订量为 {_str(min_qty, QTY_QUANT)} {order_uom}。"
            )
        if config.max_order_qty and qty > config.max_order_qty:
            line_ok = False
            message = f"{product_name} 超过限购数量 {_str(config.max_order_qty, QTY_QUANT)} {order_uom}。"
        if not _is_multiple(qty, multiple_qty):
            line_ok = False
            message = f"{product_name} 购买数量需按 {_str(multiple_qty, QTY_QUANT)} {order_uom} 递增。"
        if (
            not product.break_box_allowed
            and qty_in_base == Decimal("1")
            and product.packages.exists()
        ):
            line_ok = False
            message = f"{product_name} 不支持当前单位购买，请选择整箱单位。"

        base_qty = _qty(qty * qty_in_base)
        available_qty = available.get(product.id, Decimal("0"))
        if base_qty > available_qty:
            line_ok = False
            message = (
                f"{product_name} 库存不足：需要 {_str(base_qty, QTY_QUANT)}，"
                f"当前 {_str(available_qty, QTY_QUANT)}。"
            )

        base_price = _base_price(owner, customer, product, order_date, channel, config)
        unit_price = _price(base_price * qty_in_base)
        line_amount = _money(unit_price * qty)
        total += line_amount
        ok = ok and line_ok

        payload = _line_quote_payload(
            product,
            config,
            qty,
            order_uom,
            base_price,
            unit_price,
            available_qty,
        )
        payload["ok"] = line_ok
        payload["message"] = message
        payload["image_url"] = _image_url(request, product)
        preview_lines.append(payload)
        contexts.append(
            {
                "config": config,
                "product": product,
                "package": _package_for_uom(product, order_uom),
                "order_uom": order_uom,
                "qty": qty,
                "qty_in_base": qty_in_base,
                "base_qty": base_qty,
                "base_price": base_price,
                "unit_price": unit_price,
                "line_amount": line_amount,
            }
        )

    if strict and not ok:
        failed = next((line for line in preview_lines if not line["ok"]), None)
        raise ValidationError(
            {"detail": failed["message"] if failed else "订单校验未通过。"}
        )

    adjustment_preview = build_adjustment_preview(
        owner=owner,
        customer=customer,
        buyer=buyer,
        goods_amount=total,
        order_date=order_date,
        channel=channel,
        coupon_id=adjustment_data.get("coupon_id"),
        points=adjustment_data.get("points") or 0,
    )
    goods_amount = adjustment_preview["goods_amount"]
    adjustment_amount = adjustment_preview["adjustment_amount"]
    payable_amount = adjustment_preview["payable_amount"]
    adjustment_specs = adjustment_preview["adjustments"]

    return (
        {
            "ok": ok,
            "warehouse": {
                "id": warehouse.id,
            },
            "customer": {
                "id": customer.id,
            },
            "goods_amount": _str(goods_amount, MONEY_QUANT),
            "adjustment_amount": _str(adjustment_amount, MONEY_QUANT),
            "payable_amount": _str(payable_amount, MONEY_QUANT),
            "total_amount": _str(payable_amount, MONEY_QUANT),
            "adjustments": [
                _adjustment_spec_payload(spec) for spec in adjustment_specs
            ],
            "line_count": len(preview_lines),
            "lines": preview_lines,
        },
        contexts,
        adjustment_specs,
    )


def _order_has_owner_scoped_adjustments(data):
    return bool(data.get("coupon_id")) or int(data.get("points") or 0) > 0


def _cart_ids_by_owner_for_payload(request, data):
    cart_ids = data.get("cart_ids") or []
    if not cart_ids:
        return {}
    carts = SaleMiniCart.objects.filter(
        id__in=cart_ids,
        buyer_user__user=request.user,
        is_active=True,
    ).select_related("owner", "customer", "buyer_user")
    return {cart.owner_id: cart.id for cart in carts}


def _order_contexts_for_payload(request, data, *, auto_create=False):
    owner_id = data.get("owner_id")
    if owner_id:
        binding = get_object_or_404(
            _buyer_bindings_for_user(request.user),
            owner_id=owner_id,
        )
        customer, buyer = _customer_for_context(
            binding.owner,
            request.user,
            data,
            auto_create=auto_create,
        )
        return [
            {
                "owner": binding.owner,
                "customer": customer,
                "buyer": buyer,
                "lines": data.get("lines", []),
                "cart_id": data.get("cart_id"),
            }
        ]

    product_ids = [line["product_id"] for line in data.get("lines", [])]
    configs = list(_public_config_qs().filter(product_id__in=product_ids))
    configs_by_product = {config.product_id: config for config in configs}
    missing = [pid for pid in product_ids if pid not in configs_by_product]
    if missing:
        raise ValidationError({"products": f"商品未上架或无权购买：{missing}"})

    cart_ids_by_owner = _cart_ids_by_owner_for_payload(request, data)
    grouped = {}
    for line in data.get("lines", []):
        config = configs_by_product[line["product_id"]]
        bucket = grouped.setdefault(
            config.owner_id,
            {
                "owner": config.owner,
                "lines": [],
                "cart_id": cart_ids_by_owner.get(config.owner_id),
            },
        )
        bucket["lines"].append(line)
    if len(grouped) == 1 and data.get("cart_id"):
        next(iter(grouped.values()))["cart_id"] = data.get("cart_id")

    contexts = []
    for group in grouped.values():
        customer, buyer = _customer_for_context(
            group["owner"],
            request.user,
            data,
            auto_create=True if auto_create or not owner_id else auto_create,
        )
        contexts.append(
            {
                "owner": group["owner"],
                "customer": customer,
                "buyer": buyer,
                "lines": group["lines"],
                "cart_id": group["cart_id"],
            }
        )
    return contexts


def _combined_preview_payload(group_previews):
    goods_amount = Decimal("0.00")
    adjustment_amount = Decimal("0.00")
    payable_amount = Decimal("0.00")
    lines = []
    groups = []
    ok = True
    warehouse = None
    for group in group_previews:
        preview = dict(group["preview"])
        preview["owner_id"] = group["owner"].id
        groups.append(preview)
        lines.extend(preview["lines"])
        ok = ok and preview["ok"]
        goods_amount += Decimal(preview["goods_amount"])
        adjustment_amount += Decimal(preview["adjustment_amount"])
        payable_amount += Decimal(preview["payable_amount"])
        warehouse = warehouse or preview.get("warehouse")

    return {
        "ok": ok,
        "is_combined": True,
        "warehouse": warehouse or {},
        "customer": {"id": None},
        "goods_amount": _str(goods_amount, MONEY_QUANT),
        "adjustment_amount": _str(adjustment_amount, MONEY_QUANT),
        "payable_amount": _str(payable_amount, MONEY_QUANT),
        "total_amount": _str(payable_amount, MONEY_QUANT),
        "adjustments": [],
        "line_count": len(lines),
        "lines": lines,
        "groups": groups,
    }


def _create_sale_mini_order_mapping(
    request,
    owner,
    customer,
    buyer,
    data,
    lines_data,
    *,
    cart_id=None,
    adjustment_data=None,
    source="sale-mini",
):
    preview, contexts, adjustment_specs = _build_order_preview(
        request,
        owner,
        customer,
        lines_data,
        strict=True,
        buyer=buyer,
        adjustment_data=adjustment_data or data,
    )
    warehouse = _warehouse_for_user(request.user)
    delivery_method = _delivery_method(data)
    contact, contact_phone, ship_to, _address = _fulfillment_for_order_payload(
        owner, customer, data
    )

    order = OutboundOrder.objects.create(
        owner=owner,
        customer=customer,
        warehouse=warehouse,
        outbound_type="SALES",
        delivery_method=delivery_method,
        submit_status="SUBMITTED",
        approval_status="OWNER_PENDING",
        ship_to=ship_to,
        contact=contact,
        contact_phone=contact_phone,
        memo=data.get("remark", ""),
        biz_date=_business_date(),
        created_by=request.user,
        updated_by=request.user,
    )
    order.src_bill_no = f"SALE-MINI-{order.id}"
    order.save(update_fields=["src_bill_no", "updated_at"])
    for ctx in contexts:
        line_kwargs = {
            "order": order,
            "product": ctx["product"],
            "base_qty": ctx["base_qty"],
            "base_price": ctx["base_price"],
            "final_line_amount": ctx["line_amount"],
            "pack_requirement": ctx["product"].pack_requirement,
            "pack_note": ctx["product"].pack_note or "",
            "created_by": request.user,
            "updated_by": request.user,
        }
        if ctx["package"]:
            line_kwargs.update(
                {
                    "aux_qty": ctx["qty"],
                    "aux_uom": ctx["package"],
                    "aux_price": ctx["unit_price"],
                }
            )
        OutboundOrderLine.objects.create(**line_kwargs)

    order.final_order_amount = Decimal(preview["goods_amount"])
    order.save(update_fields=["final_order_amount", "updated_at"])
    order.owner_approve(by_user=request.user, allow_backorder=False)
    payment_method = data.get("payment_method") or "OFFLINE"
    mapping = SaleMiniOrderMapping.objects.create(
        owner=owner,
        customer=customer,
        buyer_user=buyer,
        outbound_order=order,
        payment_status=(
            SaleMiniOrderMapping.PaymentStatus.UNPAID
            if payment_method == "WECHAT"
            else SaleMiniOrderMapping.PaymentStatus.OFFLINE
        ),
        goods_amount=Decimal(preview["goods_amount"]),
        adjustment_amount=Decimal(preview["adjustment_amount"]),
        payable_amount=Decimal(preview["payable_amount"]),
        pay_deadline_at=_payment_deadline() if payment_method == "WECHAT" else None,
        source=source,
        created_by=request.user,
        updated_by=request.user,
    )
    lock_adjustments(
        owner=owner,
        customer=customer,
        buyer=buyer,
        mapping=mapping,
        specs=adjustment_specs,
        payment_method=payment_method,
        by_user=request.user,
    )
    create_distribution_record(
        mapping,
        referrer_id=data.get("referrer_buyer_id"),
        by_user=request.user,
    )
    _clear_source_cart(
        request,
        owner,
        customer,
        buyer,
        cart_id,
        contexts,
    )
    return mapping


SALE_MINI_BATCH_SOURCE_PREFIX = "sale-mini-batch-"


def _new_sale_mini_batch_source():
    return f"{SALE_MINI_BATCH_SOURCE_PREFIX}{uuid.uuid4().hex[:10]}"


def _is_sale_mini_batch_source(source):
    return str(source or "").startswith(SALE_MINI_BATCH_SOURCE_PREFIX)


def _public_batch_order_no(source):
    suffix = str(source or "").removeprefix(SALE_MINI_BATCH_SOURCE_PREFIX)
    return f"SC{suffix.upper()}" if suffix else "SC"


def _combined_display_status(orders):
    statuses = {order["status"] for order in orders}
    if len(statuses) == 1:
        return orders[0]["status"], orders[0]["status_name"]
    if "WAIT_PAY" in statuses:
        return "WAIT_PAY", "待付款"
    if "REFUNDING" in statuses:
        return "REFUNDING", "退款中"
    if statuses <= {"COMPLETED", "REFUNDED"}:
        return "COMPLETED", "已完成"
    if statuses <= {"CANCELLED", "REFUNDED"}:
        return "CANCELLED", "已取消"
    return "WAIT_SHIP", "待发货"


def _combined_order_payload(request, mappings):
    orders = [_order_payload(request, mapping) for mapping in mappings]
    if len(orders) == 1:
        return orders[0]

    goods_amount = sum(Decimal(order["goods_amount"]) for order in orders)
    adjustment_amount = sum(Decimal(order["adjustment_amount"]) for order in orders)
    payable_amount = sum(Decimal(order["payable_amount"]) for order in orders)
    first = orders[0]
    display_status, display_status_name = _combined_display_status(orders)
    payment_statuses = {order["payment_status"] for order in orders}
    deadline_values = [
        order["pay_deadline_at"] for order in orders if order.get("pay_deadline_at")
    ]
    source = mappings[0].source if mappings else ""
    return {
        "id": first["id"],
        "mapping_id": first["mapping_id"],
        "is_combined": True,
        "order_count": len(orders),
        "orders": orders,
        "order_no": _public_batch_order_no(source),
        "created_at": first.get("created_at", ""),
        "biz_date": first.get("biz_date", ""),
        "status": display_status,
        "status_name": display_status_name,
        "submit_status": first.get("submit_status", ""),
        "approval_status": first.get("approval_status", ""),
        "payment_status": (
            first["payment_status"] if len(payment_statuses) == 1 else "MIXED"
        ),
        "payment_status_name": (
            first["payment_status_name"] if len(payment_statuses) == 1 else "混合"
        ),
        "pay_deadline_at": min(deadline_values) if deadline_values else "",
        "paid_at": first.get("paid_at", ""),
        "payment": first.get("payment", {}),
        "refund": first.get("refund", {}),
        "after_sale": None,
        "customer": first.get("customer", {}),
        "warehouse": first.get("warehouse", {}),
        "delivery_method": first["delivery_method"],
        "delivery_method_name": first["delivery_method_name"],
        "contact": first.get("contact", ""),
        "contact_phone": first.get("contact_phone", ""),
        "ship_to": first.get("ship_to", ""),
        "remark": first.get("remark", ""),
        "goods_amount": _str(goods_amount, MONEY_QUANT),
        "adjustment_amount": _str(adjustment_amount, MONEY_QUANT),
        "payable_amount": _str(payable_amount, MONEY_QUANT),
        "total_amount": _str(payable_amount, MONEY_QUANT),
        "adjustments": [
            adjustment
            for order in orders
            for adjustment in order.get("adjustments", [])
        ],
        "line_count": sum(order["line_count"] for order in orders),
        "lines": [
            line
            for order in orders
            for line in order.get("lines", [])
        ],
    }


def _order_mapping_groups(mappings):
    groups = []
    index = {}
    for mapping in mappings:
        key = (
            mapping.source
            if _is_sale_mini_batch_source(mapping.source)
            else f"single:{mapping.id}"
        )
        if key not in index:
            index[key] = []
            groups.append(index[key])
        index[key].append(mapping)
    return groups


def _order_group_payload(request, mappings):
    mappings = list(mappings)
    if len(mappings) == 1:
        return _order_payload(request, mappings[0])
    return _combined_order_payload(request, mappings)


def _cart_for_owner(request, owner, data=None, *, create=True, for_update=False):
    customer, buyer = _customer_for_context(
        owner,
        request.user,
        data or {},
        auto_create=True,
    )
    qs = SaleMiniCart.objects.filter(
        owner=owner,
        customer=customer,
        buyer_user=buyer,
        is_active=True,
    )
    if for_update:
        qs = qs.select_for_update()
    cart = qs.first()
    if cart or not create:
        return cart, owner, customer, buyer
    cart = SaleMiniCart.objects.create(
        owner=owner,
        customer=customer,
        buyer_user=buyer,
        created_by=request.user,
        updated_by=request.user,
    )
    return cart, owner, customer, buyer


def _cart_key(product_id, order_uom):
    return f"{product_id}:{order_uom or ''}"


def _cart_invalid_line(request, item, message):
    product = item.product
    owner = item.cart.owner
    qty = _qty(item.qty)
    qty_in_base = _qty_in_base_for_uom(product, item.order_uom) or Decimal("1")
    base_qty = _qty(qty * qty_in_base)
    return {
        "cart_id": item.cart_id,
        "item_id": item.id,
        "key": _cart_key(product.id, item.order_uom),
        "owner_id": owner.id,
        "product_id": product.id,
        "product_code": product.code,
        "product_name": product.name,
        "product_spec": product.spec or "",
        "image_url": _image_url(request, product),
        "order_uom": item.order_uom,
        "qty": _str(qty, QTY_QUANT),
        "qty_in_base": _str(qty_in_base, QTY_QUANT),
        "base_qty": _str(base_qty, QTY_QUANT),
        "base_uom": getattr(product.base_uom, "code", ""),
        "unit_price": _str(Decimal("0"), PRICE_QUANT),
        "base_unit_price": _str(Decimal("0"), PRICE_QUANT),
        "line_amount": _str(Decimal("0"), MONEY_QUANT),
        "available_qty": _str(Decimal("0"), QTY_QUANT),
        "ok": False,
        "message": message,
    }


def _cart_payload(request, cart, owner, customer):
    warehouse = _warehouse_for_user(request.user)
    items = list(
        cart.items.filter(is_active=True)
        .select_related("product", "product__base_uom")
        .prefetch_related("product__packages", "product__packages__uom")
        .order_by("id")
    )
    if not items:
        return {
            "id": cart.id,
            "cart_id": cart.id,
            "owner_id": owner.id,
            "ok": True,
            "warehouse": {
                "id": warehouse.id,
            },
            "customer": {
                "id": customer.id,
            },
            "goods_amount": _str(Decimal("0"), MONEY_QUANT),
            "adjustment_amount": _str(Decimal("0"), MONEY_QUANT),
            "payable_amount": _str(Decimal("0"), MONEY_QUANT),
            "total_amount": _str(Decimal("0"), MONEY_QUANT),
            "adjustments": [],
            "line_count": 0,
            "items": [],
            "lines": [],
        }

    product_ids = [item.product_id for item in items]
    configs = {
        cfg.product_id: cfg
        for cfg in _config_qs(owner).filter(product_id__in=product_ids)
    }
    valid_inputs = [
        {
            "product_id": item.product_id,
            "qty": item.qty,
            "order_uom": item.order_uom,
        }
        for item in items
        if item.product_id in configs
    ]
    valid_by_key = {}
    total = Decimal("0.00")
    goods_amount = Decimal("0.00")
    adjustment_amount = Decimal("0.00")
    payable_amount = Decimal("0.00")
    adjustments = []
    ok = True
    if valid_inputs:
        preview, _contexts, _adjustment_specs = _build_order_preview(
            request,
            owner,
            customer,
            valid_inputs,
            buyer=cart.buyer_user,
        )
        ok = preview["ok"]
        total = Decimal(preview["total_amount"])
        goods_amount = Decimal(preview["goods_amount"])
        adjustment_amount = Decimal(preview["adjustment_amount"])
        payable_amount = Decimal(preview["payable_amount"])
        adjustments = preview["adjustments"]
        valid_by_key = {
            (line["product_id"], line["order_uom"]): line for line in preview["lines"]
        }

    lines = []
    for item in items:
        key = (item.product_id, item.order_uom)
        if key in valid_by_key:
            line = dict(valid_by_key[key])
            line["cart_id"] = item.cart_id
            line["item_id"] = item.id
            line["key"] = _cart_key(item.product_id, item.order_uom)
        else:
            line = _cart_invalid_line(
                request,
                item,
                "商品已下架、停用或无权购买，请从购物车删除。",
            )
            ok = False
        lines.append(line)

    return {
        "id": cart.id,
        "cart_id": cart.id,
        "owner_id": owner.id,
        "ok": ok,
        "warehouse": {
            "id": warehouse.id,
        },
        "customer": {"id": customer.id},
        "goods_amount": _str(goods_amount, MONEY_QUANT),
        "adjustment_amount": _str(adjustment_amount, MONEY_QUANT),
        "payable_amount": _str(payable_amount, MONEY_QUANT),
        "total_amount": _str(total, MONEY_QUANT),
        "adjustments": adjustments,
        "line_count": len(lines),
        "items": lines,
        "lines": lines,
    }


def _empty_combined_cart_payload(request):
    warehouse = _warehouse_for_user(request.user)
    return {
        "id": None,
        "cart_id": None,
        "ok": True,
        "warehouse": {
            "id": warehouse.id,
        },
        "goods_amount": _str(Decimal("0"), MONEY_QUANT),
        "adjustment_amount": _str(Decimal("0"), MONEY_QUANT),
        "payable_amount": _str(Decimal("0"), MONEY_QUANT),
        "total_amount": _str(Decimal("0"), MONEY_QUANT),
        "adjustments": [],
        "line_count": 0,
        "items": [],
        "lines": [],
        "groups": [],
    }


def _combined_cart_payload(request):
    bindings = list(_buyer_bindings_for_user(request.user))
    if not bindings:
        return _empty_combined_cart_payload(request)
    carts = list(
        SaleMiniCart.objects.filter(
            buyer_user__in=bindings,
            is_active=True,
        )
        .select_related("owner", "customer", "buyer_user")
        .order_by("owner__code", "id")
    )
    if not carts:
        return _empty_combined_cart_payload(request)

    groups = []
    items = []
    ok = True
    goods_amount = Decimal("0.00")
    adjustment_amount = Decimal("0.00")
    payable_amount = Decimal("0.00")
    total_amount = Decimal("0.00")
    for cart in carts:
        group = _cart_payload(request, cart, cart.owner, cart.customer)
        if group["line_count"] <= 0:
            continue
        groups.append(group)
        items.extend(group["items"])
        ok = ok and group["ok"]
        goods_amount += Decimal(group["goods_amount"])
        adjustment_amount += Decimal(group["adjustment_amount"])
        payable_amount += Decimal(group["payable_amount"])
        total_amount += Decimal(group["total_amount"])

    if not groups:
        return _empty_combined_cart_payload(request)
    warehouse = _warehouse_for_user(request.user)
    return {
        "id": None,
        "cart_id": None,
        "ok": ok,
        "warehouse": {
            "id": warehouse.id,
        },
        "goods_amount": _str(goods_amount, MONEY_QUANT),
        "adjustment_amount": _str(adjustment_amount, MONEY_QUANT),
        "payable_amount": _str(payable_amount, MONEY_QUANT),
        "total_amount": _str(total_amount, MONEY_QUANT),
        "adjustments": [],
        "line_count": len(items),
        "items": items,
        "lines": items,
        "groups": groups,
    }


def _cart_item_for_payload(cart, data):
    qs = cart.items.filter(is_active=True)
    item_id = data.get("item_id")
    if item_id:
        return get_object_or_404(qs, pk=item_id)
    product_id = data.get("product_id")
    order_uom = (data.get("order_uom") or "").strip()
    if order_uom:
        qs = qs.filter(order_uom=order_uom)
    return get_object_or_404(qs, product_id=product_id)


def _cart_item_context_for_payload(request, data, *, for_update=False):
    item_id = data.get("item_id")
    if item_id:
        qs = SaleMiniCartItem.objects.filter(
            cart__buyer_user__user=request.user,
            cart__is_active=True,
            is_active=True,
        ).select_related("cart", "cart__owner", "cart__customer", "cart__buyer_user")
        if for_update:
            qs = qs.select_for_update()
        item = get_object_or_404(qs, pk=item_id)
        return (
            item,
            item.cart,
            item.cart.owner,
            item.cart.customer,
            item.cart.buyer_user,
        )

    product_id = data.get("product_id")
    config = get_object_or_404(_public_config_qs(), product_id=product_id)
    cart, owner, customer, buyer = _cart_for_owner(
        request,
        config.owner,
        data,
        for_update=for_update,
    )
    item = _cart_item_for_payload(cart, data)
    return item, cart, owner, customer, buyer


def _clear_source_cart(request, owner, customer, buyer, cart_id, contexts):
    if not cart_id:
        return
    qs = SaleMiniCart.objects.filter(
        id=cart_id,
        owner=owner,
        customer=customer,
        is_active=True,
    )
    if buyer:
        qs = qs.filter(buyer_user=buyer)
    cart = qs.select_for_update().first()
    if not cart:
        return
    for ctx in contexts:
        cart.items.filter(
            product=ctx["product"],
            order_uom=ctx["order_uom"],
        ).delete()
    cart.updated_by = request.user
    cart.save(update_fields=["updated_by", "updated_at"])


def _address_payload(address):
    return {
        "id": address.id,
        "owner_id": address.owner_id,
        "contact": address.contact,
        "phone": address.phone,
        "province": address.province,
        "city": address.city,
        "district": address.district,
        "detail": address.detail,
        "full_address": address.full_address,
        "is_default": address.is_default,
    }


def _fulfillment_status(order):
    if order.approval_status == "CANCELLED":
        return "CANCELLED", "已取消"
    if order.approval_status in {"OWNER_REJECTED", "WHS_REJECTED"}:
        return "REJECTED", "已关闭"
    if order.is_closed:
        return "COMPLETED", "已完成"
    if order.submit_status == "DRAFT":
        return "DRAFT", "待提交"
    if order.approval_status == "OWNER_PENDING":
        return "PENDING_REVIEW", "平台处理中"
    if order.approval_status == "OWNER_APPROVED":
        return "WAIT_WAREHOUSE", "平台处理中"
    if order.approval_status == "WHS_PENDING":
        return "WAIT_WAREHOUSE", "平台处理中"
    if order.approval_status == "WHS_APPROVED":
        return "WAIT_PICK", "备货中"
    return "PROCESSING", "处理中"


def _display_status(order, mapping=None):
    fulfillment_status, fulfillment_name = _fulfillment_status(order)
    if fulfillment_status == "CANCELLED" or (
        mapping
        and mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.CANCELLED
    ):
        return "CANCELLED", "已取消"
    if fulfillment_status == "REJECTED":
        return "CANCELLED", "已关闭"
    if mapping:
        if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.REFUNDING:
            return "REFUNDING", "退款中"
        if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.REFUNDED:
            return "REFUNDED", "已退款"
        if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.UNPAID:
            return "WAIT_PAY", "待付款"
    if fulfillment_status == "COMPLETED":
        return "COMPLETED", "已完成"
    if fulfillment_status == "DRAFT":
        return "WAIT_SHIP", "待发货"
    if fulfillment_status in {
        "PENDING_REVIEW",
        "WAIT_WAREHOUSE",
        "WAIT_PICK",
        "PROCESSING",
    }:
        return "WAIT_SHIP", "待发货"
    return fulfillment_status, fulfillment_name


def _display_status_q(status_code):
    if status_code == "WAIT_PAY":
        return Q(
            payment_status=SaleMiniOrderMapping.PaymentStatus.UNPAID,
            outbound_order__is_closed=False,
        ) & ~Q(
            outbound_order__approval_status__in=[
                "CANCELLED",
                "OWNER_REJECTED",
                "WHS_REJECTED",
            ]
        )
    if status_code == "WAIT_SHIP":
        return Q(
            payment_status__in=[
                SaleMiniOrderMapping.PaymentStatus.PAID,
                SaleMiniOrderMapping.PaymentStatus.OFFLINE,
            ],
            outbound_order__is_closed=False,
            outbound_order__submit_status="SUBMITTED",
        ) & ~Q(
            outbound_order__approval_status__in=[
                "CANCELLED",
                "OWNER_REJECTED",
                "WHS_REJECTED",
            ]
        )
    if status_code == "REFUNDING":
        return Q(payment_status=SaleMiniOrderMapping.PaymentStatus.REFUNDING)
    if status_code == "REFUNDED":
        return Q(payment_status=SaleMiniOrderMapping.PaymentStatus.REFUNDED)
    if status_code == "CANCELLED":
        return Q(outbound_order__approval_status="CANCELLED") | Q(
            payment_status=SaleMiniOrderMapping.PaymentStatus.CANCELLED
        )
    if status_code == "REJECTED":
        return Q(outbound_order__approval_status__in=["OWNER_REJECTED", "WHS_REJECTED"])
    if status_code == "COMPLETED":
        return Q(
            outbound_order__is_closed=True,
            payment_status__in=[
                SaleMiniOrderMapping.PaymentStatus.PAID,
                SaleMiniOrderMapping.PaymentStatus.OFFLINE,
            ],
        ) & ~Q(
            outbound_order__approval_status__in=[
                "CANCELLED",
                "OWNER_REJECTED",
                "WHS_REJECTED",
            ]
        )
    if status_code == "DRAFT":
        return Q(outbound_order__submit_status="DRAFT") & ~Q(
            outbound_order__approval_status__in=[
                "CANCELLED",
                "OWNER_REJECTED",
                "WHS_REJECTED",
            ]
        )
    if status_code == "PENDING_REVIEW":
        return Q(
            outbound_order__is_closed=False,
            outbound_order__submit_status="SUBMITTED",
            outbound_order__approval_status="OWNER_PENDING",
        )
    if status_code == "WAIT_WAREHOUSE":
        return Q(
            outbound_order__is_closed=False,
            outbound_order__submit_status="SUBMITTED",
            outbound_order__approval_status__in=["OWNER_APPROVED", "WHS_PENDING"],
        )
    if status_code == "WAIT_PICK":
        return Q(
            outbound_order__is_closed=False,
            outbound_order__submit_status="SUBMITTED",
            outbound_order__approval_status="WHS_APPROVED",
        )
    return Q()


def _order_line_payload(request, line, config_id=None):
    product = line.product
    if line.aux_uom_id:
        order_uom = getattr(line.aux_uom.uom, "code", "")
        qty = Decimal(line.aux_qty or 0)
        unit_price = Decimal(line.aux_price or 0)
    else:
        order_uom = getattr(product.base_uom, "code", "")
        qty = Decimal(line.base_qty or 0)
        unit_price = Decimal(line.base_price or 0)
    return {
        "id": line.id,
        "owner_id": line.order.owner_id,
        "config_id": config_id,
        "product_id": product.id,
        "product_code": product.code,
        "product_name": product.name,
        "product_spec": product.spec or "",
        "image_url": _image_url(request, product),
        "order_uom": order_uom,
        "qty": _str(qty, QTY_QUANT),
        "base_qty": _str(line.base_qty, QTY_QUANT),
        "base_uom": getattr(product.base_uom, "code", ""),
        "unit_price": _str(unit_price, PRICE_QUANT),
        "base_unit_price": _str(line.base_price, PRICE_QUANT),
        "line_amount": _str(line.final_line_amount, MONEY_QUANT),
    }


def _order_adjustment_payload(adjustment):
    return {
        "id": adjustment.id,
        "adjustment_no": adjustment.adjustment_no,
        "type": adjustment.adjustment_type,
        "type_name": adjustment.get_adjustment_type_display(),
        "status": adjustment.status,
        "status_name": adjustment.get_status_display(),
        "title": adjustment.title,
        "amount": _str(adjustment.amount, MONEY_QUANT),
        "source_code": adjustment.source_code,
    }


def _order_payload(request, mapping):
    order = mapping.outbound_order
    display_status, display_status_name = _display_status(order, mapping)
    latest_payment = _latest_payment(mapping)
    latest_refund = (
        latest_payment.refunds.order_by("-created_at", "-id").first()
        if latest_payment
        else None
    )
    latest_after_sale = mapping.after_sale_requests.order_by(
        "-created_at", "-id"
    ).first()
    lines = list(
        order.lines.filter(is_deleted=False)
        .select_related("product", "product__base_uom", "aux_uom", "aux_uom__uom")
        .prefetch_related("product__packages", "product__packages__uom")
    )
    config_ids = {
        product_id: config_id
        for product_id, config_id in SaleProductConfig.objects.filter(
            owner_id=order.owner_id,
            product_id__in=[line.product_id for line in lines],
        ).values_list("product_id", "id")
    }
    return {
        "id": order.id,
        "mapping_id": mapping.id,
        "order_no": order.order_no,
        "owner_id": order.owner_id,
        "created_at": order.created_at.isoformat() if order.created_at else "",
        "biz_date": order.biz_date.isoformat() if order.biz_date else "",
        "status": display_status,
        "status_name": display_status_name,
        "submit_status": order.submit_status,
        "approval_status": order.approval_status,
        "payment_status": mapping.payment_status,
        "payment_status_name": mapping.get_payment_status_display(),
        "pay_deadline_at": (
            mapping.pay_deadline_at.isoformat() if mapping.pay_deadline_at else ""
        ),
        "paid_at": mapping.paid_at.isoformat() if mapping.paid_at else "",
        "payment": _payment_payload(latest_payment),
        "refund": _refund_payload(latest_refund),
        "after_sale": _after_sale_payload(latest_after_sale),
        "customer": {
            "id": order.customer_id,
        },
        "warehouse": {
            "id": order.warehouse_id,
        },
        "delivery_method": order.delivery_method,
        "delivery_method_name": (
            order.get_delivery_method_display() if order.delivery_method else ""
        ),
        "contact": order.contact or "",
        "contact_phone": order.contact_phone or "",
        "ship_to": order.ship_to or "",
        "remark": order.memo or "",
        "goods_amount": _str(
            mapping.goods_amount or order.final_order_amount,
            MONEY_QUANT,
        ),
        "adjustment_amount": _str(mapping.adjustment_amount, MONEY_QUANT),
        "payable_amount": _str(
            mapping.payable_amount or order.final_order_amount,
            MONEY_QUANT,
        ),
        "total_amount": _str(
            mapping.payable_amount or order.final_order_amount,
            MONEY_QUANT,
        ),
        "adjustments": [
            _order_adjustment_payload(adjustment)
            for adjustment in mapping.adjustments.order_by("id")
        ],
        "line_count": len(lines),
        "lines": [
            _order_line_payload(request, line, config_ids.get(line.product_id))
            for line in lines
        ],
    }


class SaleMiniProfileApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bindings = list(_buyer_bindings_for_user(request.user))
        primary = None
        if request.user.owner_id:
            primary = next(
                (row for row in bindings if row.owner_id == request.user.owner_id),
                None,
            )
        if not primary and bindings:
            primary = bindings[0]
        customer = primary.customer if primary else None
        buyer = primary
        return Response(
            {
                "buyer": {
                    "id": buyer.id if buyer else None,
                    "nickname": buyer.nickname if buyer else "",
                    "phone": buyer.phone if buyer else "",
                },
                "customer": ({"id": customer.id} if customer else None),
                "bindings": [_buyer_binding_payload(row) for row in bindings],
            }
        )


class SaleMiniWechatLoginApi(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniWechatLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        session_data = _wechat_code_to_session(data["code"])
        buyer = _bound_wechat_buyer(data, session_data)
        payload = _token_payload_for_user(buyer.user)
        payload.update(_sale_mini_context_payload(buyer))
        return Response(payload)


class SaleMiniHomeApi(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        order_date = _business_date()
        banners = [
            {
                "id": banner.id,
                "title": banner.title,
                "image_url": banner.image_url,
                "link_type": banner.link_type,
                "link_value": banner.link_value,
            }
            for banner in SaleMiniBanner.objects.filter(
                owner__is_active=True, is_active=True
            )
            .select_related("owner")
            .order_by("sort_order", "id")[:8]
        ]
        categories = _category_rows()[:12]
        products_qs = _public_config_qs()

        def take(flag_name, limit, *, fallback=False):
            rows = list(products_qs.filter(**{flag_name: True})[:limit])
            if fallback and not rows:
                rows = list(products_qs[:limit])
            available = _available_map_for_configs(rows)
            return [
                _product_payload(
                    request,
                    row.owner,
                    None,
                    row,
                    available.get((row.owner_id, row.product_id), Decimal("0")),
                    order_date,
                )
                for row in rows
            ]

        return Response(
            {
                "banners": banners,
                "categories": categories,
                "recommend_products": take("is_recommended", 12, fallback=True),
                "hot_products": take("is_hot", 12, fallback=True),
                "new_products": take("is_new", 8),
            }
        )


def _category_rows(owner=None, owner_id=None):
    filters = {
        "products__sale_mini_configs__is_active": True,
        "products__sale_mini_configs__is_listed": True,
        "products__sale_mini_configs__owner__is_active": True,
        "products__owner__is_active": True,
        "products__is_active": True,
        "is_active": True,
    }
    if owner is not None:
        owner_id = owner.id
    if owner_id:
        filters["products__owner_id"] = owner_id
        filters["products__sale_mini_configs__owner_id"] = owner_id
    else:
        filters["products__sale_mini_configs__owner_id"] = F("products__owner_id")
    qs = (
        ProductCategory.objects.filter(**filters)
        .annotate(product_count=Count("products__sale_mini_configs", distinct=True))
        .distinct()
        .order_by("code")
    )
    return [
        {
            "id": category.id,
            "name": category.name,
            "parent_id": category.parent_id,
            "product_count": category.product_count or 0,
        }
        for category in qs
    ]


def _brand_rows(owner_id=None, category_id=None):
    filters = {
        "products__sale_mini_configs__is_active": True,
        "products__sale_mini_configs__is_listed": True,
        "products__sale_mini_configs__owner__is_active": True,
        "products__owner__is_active": True,
        "products__is_active": True,
        "products__brand__isnull": False,
        "is_active": True,
    }
    if owner_id:
        filters["products__owner_id"] = owner_id
        filters["products__sale_mini_configs__owner_id"] = owner_id
    else:
        filters["products__sale_mini_configs__owner_id"] = F("products__owner_id")
    if category_id:
        filters["products__category_id"] = category_id
    qs = (
        Brand.objects.filter(**filters)
        .annotate(product_count=Count("products__sale_mini_configs", distinct=True))
        .distinct()
        .order_by("code")
    )
    return [
        {
            "id": brand.id,
            "name": brand.name,
            "product_count": brand.product_count or 0,
        }
        for brand in qs
    ]


def _sale_mini_coupon_payload(coupon, order_amount=None):
    template = coupon.template
    try:
        order_amount = Decimal(order_amount) if order_amount is not None else None
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"order_amount": "订单金额格式无效。"}) from exc
    usable = order_amount is None or order_amount >= template.threshold_amount
    return {
        "id": coupon.id,
        "owner_id": coupon.owner_id,
        "coupon_no": coupon.coupon_no,
        "title": template.title,
        "coupon_type": template.coupon_type,
        "threshold_amount": _str(template.threshold_amount, MONEY_QUANT),
        "discount_amount": _str(template.discount_amount, MONEY_QUANT),
        "status": coupon.status,
        "expires_at": coupon.expires_at.isoformat() if coupon.expires_at else "",
        "effective_from": template.effective_from.isoformat(),
        "effective_to": (
            template.effective_to.isoformat() if template.effective_to else ""
        ),
        "usable": usable,
    }


class SaleMiniCategoryApi(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(_category_rows())


class SaleMiniBrandApi(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            _brand_rows(
                category_id=request.query_params.get("category_id"),
            )
        )


class SaleMiniCouponListApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner_id = request.query_params.get("owner_id")
        if owner_id:
            binding = get_object_or_404(
                _buyer_bindings_for_user(request.user),
                owner_id=owner_id,
            )
            contexts = [(binding.owner, binding.customer, binding)]
        else:
            contexts = [
                (binding.owner, binding.customer, binding)
                for binding in _buyer_bindings_for_user(request.user)
            ]
            if not contexts:
                owner = _default_owner_for_user(request.user)
                customer, buyer = _customer_for_context(
                    owner, request.user, request.query_params
                )
                contexts = [(owner, customer, buyer)]
        today = _business_date()
        now = timezone.now()
        order_amount = request.query_params.get("order_amount")
        rows = []
        for owner, customer, buyer in contexts:
            qs = (
                SaleMiniCoupon.objects.select_related("template")
                .filter(
                    owner=owner,
                    customer=customer,
                    is_active=True,
                    status=SaleMiniCoupon.Status.AVAILABLE,
                    template__is_active=True,
                    template__effective_from__lte=today,
                )
                .filter(
                    Q(template__effective_to__isnull=True)
                    | Q(template__effective_to__gte=today)
                )
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                .order_by(
                    "template__threshold_amount", "-template__discount_amount", "id"
                )
            )
            if buyer:
                qs = qs.filter(Q(buyer_user=buyer) | Q(buyer_user__isnull=True))
            rows.extend(
                _sale_mini_coupon_payload(coupon, order_amount=order_amount)
                for coupon in qs
            )
        rows.sort(
            key=lambda row: (
                Decimal(row["threshold_amount"]),
                -Decimal(row["discount_amount"]),
                row["id"],
            )
        )
        return Response(rows)


class SaleMiniPointBalanceApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner_id = request.query_params.get("owner_id")
        if owner_id:
            binding = get_object_or_404(
                _buyer_bindings_for_user(request.user),
                owner_id=owner_id,
            )
            contexts = [(binding.owner, binding.customer, binding)]
        else:
            contexts = [
                (binding.owner, binding.customer, binding)
                for binding in _buyer_bindings_for_user(request.user)
            ]
            if not contexts:
                owner = _default_owner_for_user(request.user)
                customer, buyer = _customer_for_context(
                    owner, request.user, request.query_params
                )
                contexts = [(owner, customer, buyer)]
        points = 0
        frozen = 0
        for owner, customer, buyer in contexts:
            context_points, context_frozen = point_balance(owner, customer, buyer)
            points += context_points
            frozen += context_frozen
        try:
            rate = Decimal(
                str(getattr(settings, "SALE_MINI_POINT_EXCHANGE_RATE", "100"))
            )
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError({"points": "积分兑换比例配置无效。"}) from exc
        if rate <= 0:
            raise ValidationError({"points": "积分兑换比例必须大于 0。"})
        return Response(
            {
                "points": points,
                "frozen": frozen,
                "exchange_rate": _str(rate, MONEY_QUANT),
            }
        )


class SaleMiniProductListApi(APIView):
    permission_classes = [AllowAny]
    pagination_class = SaleMiniPagination

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        category_id = request.query_params.get("category_id")
        brand_id = request.query_params.get("brand_id")
        only_stock = request.query_params.get("only_stock") in {"1", "true", "True"}
        ordering = request.query_params.get("ordering") or "sort"

        qs = _public_config_qs()
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__spec__icontains=search)
                | Q(product__brand__name__icontains=search)
                | Q(product__category__name__icontains=search)
            )
        if category_id:
            qs = qs.filter(product__category_id=category_id)
        if brand_id:
            qs = qs.filter(product__brand_id=brand_id)
        if only_stock:
            stock_configs = list(qs)
            stocked_qty = _available_map_for_configs(stock_configs)
            qs = qs.filter(
                id__in=[
                    config.id
                    for config in stock_configs
                    if stocked_qty.get((config.owner_id, config.product_id), 0) > 0
                ]
            )
        if ordering in {"price_asc", "price_desc"}:
            qs = qs.annotate(
                display_price=Coalesce(
                    "sale_price",
                    "product__price",
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=4),
                )
            ).order_by(
                "-display_price" if ordering == "price_desc" else "display_price"
            )
        elif ordering == "hot":
            qs = qs.order_by("-is_hot", "sort_order", "product__code")
        else:
            qs = qs.order_by("sort_order", "product__code")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        configs = page if page is not None else qs
        available = _available_map_for_configs(configs)
        order_date = _business_date()
        data = [
            _product_payload(
                request,
                config.owner,
                None,
                config,
                available.get((config.owner_id, config.product_id), Decimal("0")),
                order_date,
            )
            for config in configs
        ]
        if page is not None:
            return paginator.get_paginated_response(data)
        return Response(data)


class SaleMiniProductDetailApi(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        qs = _public_config_qs().filter(product_id=pk)
        config_id = request.query_params.get("config_id")
        if config_id:
            qs = qs.filter(id=config_id)
        config = qs.order_by("sort_order", "id").first()
        if not config:
            raise Http404
        available = _available_map_for_configs([config])
        return Response(
            _product_payload(
                request,
                config.owner,
                None,
                config,
                available.get((config.owner_id, config.product_id), Decimal("0")),
                _business_date(),
                detail=True,
            )
        )


class SaleMiniAddressListCreateApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        owner_id = request.query_params.get("owner_id")
        if owner_id:
            binding = get_object_or_404(
                _buyer_bindings_for_user(request.user),
                owner_id=owner_id,
            )
            contexts = [(binding.owner, binding.customer, binding)]
        else:
            contexts = [
                (binding.owner, binding.customer, binding)
                for binding in _buyer_bindings_for_user(request.user)
            ]
            if not contexts:
                owner = _default_owner_for_user(request.user)
                customer, buyer = _customer_for_context(
                    owner, request.user, request.query_params
                )
                contexts = [(owner, customer, buyer)]
        rows = []
        seen = set()
        for owner, customer, buyer in contexts:
            qs = MiniCustomerAddress.objects.filter(
                owner=owner, customer=customer, is_active=True
            )
            if buyer:
                qs = qs.filter(Q(buyer_user=buyer) | Q(buyer_user__isnull=True))
            for address in qs.order_by("-is_default", "-id"):
                if address.id in seen:
                    continue
                seen.add(address.id)
                rows.append(_address_payload(address))
        return Response(rows)

    @transaction.atomic
    def post(self, request):
        serializer = MiniAddressInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        owner_id = (
            request.data.get("owner_id") if hasattr(request.data, "get") else None
        )
        if owner_id:
            binding = get_object_or_404(
                _buyer_bindings_for_user(request.user),
                owner_id=owner_id,
            )
            owner = binding.owner
        else:
            owner = _default_owner_for_user(request.user)
        customer, buyer = _customer_for_context(owner, request.user, data)
        if data.get("is_default"):
            MiniCustomerAddress.objects.filter(owner=owner, customer=customer).update(
                is_default=False
            )
        address = MiniCustomerAddress.objects.create(
            owner=owner,
            customer=customer,
            buyer_user=buyer,
            contact=data["contact"],
            phone=data["phone"],
            province=data.get("province", ""),
            city=data.get("city", ""),
            district=data.get("district", ""),
            detail=data["detail"],
            is_default=data.get("is_default", False),
            created_by=request.user,
            updated_by=request.user,
        )
        return Response(_address_payload(address), status=status.HTTP_201_CREATED)


class SaleMiniAddressDetailApi(APIView):
    permission_classes = [IsAuthenticated]

    def _get_address(self, request, pk):
        context = (
            request.data if request.method in {"PUT", "PATCH"} else request.query_params
        )
        owner_id = context.get("owner_id") if hasattr(context, "get") else None
        if owner_id:
            binding = get_object_or_404(
                _buyer_bindings_for_user(request.user),
                owner_id=owner_id,
            )
            owner = binding.owner
        else:
            owner = _default_owner_for_user(request.user)
        customer, buyer = _customer_for_context(owner, request.user, context)
        qs = MiniCustomerAddress.objects.filter(
            owner=owner, customer=customer, is_active=True
        )
        if buyer:
            qs = qs.filter(Q(buyer_user=buyer) | Q(buyer_user__isnull=True))
        return get_object_or_404(qs, pk=pk)

    @transaction.atomic
    def put(self, request, pk):
        address = self._get_address(request, pk)
        owner = address.owner
        serializer = MiniAddressInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("is_default"):
            MiniCustomerAddress.objects.filter(
                owner=owner, customer=address.customer
            ).exclude(pk=address.pk).update(is_default=False)
        for field in [
            "contact",
            "phone",
            "province",
            "city",
            "district",
            "detail",
            "is_default",
        ]:
            setattr(address, field, data.get(field, getattr(address, field)))
        address.updated_by = request.user
        address.save()
        return Response(_address_payload(address))

    @transaction.atomic
    def delete(self, request, pk):
        address = self._get_address(request, pk)
        address.is_active = False
        address.is_deleted = True
        address.deleted_by = request.user
        address.save(
            update_fields=["is_active", "is_deleted", "deleted_by", "updated_at"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SaleMiniCartApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cart_id = request.query_params.get("cart_id")
        if cart_id:
            cart = get_object_or_404(
                SaleMiniCart.objects.select_related("owner", "customer", "buyer_user")
                .filter(
                    id=cart_id,
                    buyer_user__user=request.user,
                    is_active=True,
                )
            )
            return Response(_cart_payload(request, cart, cart.owner, cart.customer))

        owner_id = request.query_params.get("owner_id")
        if not owner_id:
            return Response(_combined_cart_payload(request))
        binding = get_object_or_404(
            _buyer_bindings_for_user(request.user),
            owner_id=owner_id,
        )
        cart, owner, customer, _buyer = _cart_for_owner(
            request,
            binding.owner,
            request.query_params,
        )
        return Response(_cart_payload(request, cart, owner, customer))


class SaleMiniCartAddApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniCartAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config_qs = _public_config_qs().filter(product_id=data["product_id"])
        if data.get("owner_id"):
            config_qs = config_qs.filter(owner_id=data["owner_id"])
        if data.get("config_id"):
            config_qs = config_qs.filter(id=data["config_id"])
        config = get_object_or_404(config_qs)
        cart, owner, customer, _buyer = _cart_for_owner(
            request,
            config.owner,
            data,
            for_update=True,
        )
        product = config.product

        from .mobile_api import _policy_for

        channel = _channel_for_customer(owner, customer)
        policy = _policy_for(owner, customer, product, channel)
        order_uom = (
            data.get("order_uom") or _default_order_uom(product, policy)
        ).strip()
        _validate_order_uom(product, order_uom)

        other_uom_item = (
            cart.items.select_for_update()
            .filter(product=product, is_active=True)
            .exclude(order_uom=order_uom)
            .first()
        )
        if other_uom_item:
            raise ValidationError(
                {
                    "product": (
                        f"该商品已按 {other_uom_item.order_uom} 加入购物车，"
                        "请先删除后再切换订货单位。"
                    )
                }
            )

        item, created = SaleMiniCartItem.objects.get_or_create(
            cart=cart,
            product=product,
            order_uom=order_uom,
            defaults={
                "qty": _qty(data["qty"]),
                "created_by": request.user,
                "updated_by": request.user,
            },
        )
        if not created:
            item.qty = _qty(Decimal(item.qty or 0) + data["qty"])
            item.updated_by = request.user
            item.save(update_fields=["qty", "updated_by", "updated_at"])
        cart.updated_by = request.user
        cart.save(update_fields=["updated_by", "updated_at"])
        return Response(_cart_payload(request, cart, owner, customer))


class SaleMiniCartUpdateApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniCartUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item, cart, owner, customer, _buyer = _cart_item_context_for_payload(
            request, data, for_update=True
        )
        qty = _qty(data["qty"])
        if qty <= 0:
            item.delete()
        else:
            item.qty = qty
            item.updated_by = request.user
            item.save(update_fields=["qty", "updated_by", "updated_at"])
        cart.updated_by = request.user
        cart.save(update_fields=["updated_by", "updated_at"])
        return Response(_cart_payload(request, cart, owner, customer))


class SaleMiniCartRemoveApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniCartRemoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item, cart, owner, customer, _buyer = _cart_item_context_for_payload(
            request, data, for_update=True
        )
        item.delete()
        cart.updated_by = request.user
        cart.save(update_fields=["updated_by", "updated_at"])
        return Response(_cart_payload(request, cart, owner, customer))


class SaleMiniCartClearApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        owner_id = (
            request.data.get("owner_id") if isinstance(request.data, dict) else None
        )
        if not owner_id:
            carts = SaleMiniCart.objects.select_for_update().filter(
                buyer_user__user=request.user,
                is_active=True,
            )
            for cart in carts:
                cart.items.all().delete()
                cart.updated_by = request.user
                cart.save(update_fields=["updated_by", "updated_at"])
            return Response(_combined_cart_payload(request))
        binding = get_object_or_404(
            _buyer_bindings_for_user(request.user),
            owner_id=owner_id,
        )
        cart, owner, customer, _buyer = _cart_for_owner(
            request,
            binding.owner,
            request.data,
            for_update=True,
        )
        cart.items.all().delete()
        cart.updated_by = request.user
        cart.save(update_fields=["updated_by", "updated_at"])
        return Response(_cart_payload(request, cart, owner, customer))


class SaleMiniOrderPreviewApi(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SaleMiniPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order_contexts = _order_contexts_for_payload(request, data, auto_create=True)
        if len(order_contexts) > 1 and _order_has_owner_scoped_adjustments(data):
            raise ValidationError(
                {
                    "adjustments": (
                        "多包裹订单暂不支持优惠券或积分抵扣，请先不使用优惠后再提交。"
                    )
                }
            )

        group_previews = []
        for context in order_contexts:
            owner = context["owner"]
            customer = context["customer"]
            buyer = context["buyer"]
            preview, _contexts, _adjustment_specs = _build_order_preview(
                request,
                owner,
                customer,
                context["lines"],
                buyer=buyer,
                adjustment_data=data,
            )
            preview["address"] = _fulfillment_preview_payload(owner, customer, data)
            group_previews.append({"owner": owner, "preview": preview})

        if len(group_previews) > 1:
            return Response(_combined_preview_payload(group_previews))
        preview = group_previews[0]["preview"]
        return Response(preview)


def _address_from_payload(owner, customer, data):
    address_id = data.get("address_id")
    if not address_id:
        return None
    return get_object_or_404(
        MiniCustomerAddress.objects.filter(
            owner=owner, customer=customer, is_active=True
        ),
        pk=address_id,
    )


def _inline_address(data):
    contact = data.get("contact", "")
    phone = data.get("contact_phone", "")
    ship_to = data.get("ship_to", "")
    return {
        "id": None,
        "contact": contact,
        "phone": phone,
        "province": "",
        "city": "",
        "district": "",
        "detail": ship_to,
        "full_address": ship_to,
        "is_default": False,
    }


def _delivery_method(data):
    return data.get("delivery_method") or "OWN_TRUCK"


def _pickup_ship_to(owner, data):
    return (data.get("ship_to") or "").strip() or "客户自提"


def _fulfillment_preview_payload(owner, customer, data):
    address = _address_from_payload(owner, customer, data)
    if address:
        return _address_payload(address)
    if _delivery_method(data) == "PICKUP":
        return _inline_address({**data, "ship_to": _pickup_ship_to(owner, data)})
    return _inline_address(data)


def _fulfillment_for_order_payload(owner, customer, data):
    delivery_method = _delivery_method(data)
    address = _address_from_payload(owner, customer, data)
    if delivery_method == "PICKUP":
        contact = (data.get("contact") or "").strip()
        contact_phone = (data.get("contact_phone") or "").strip()
        if address:
            contact = contact or address.contact
            contact_phone = contact_phone or address.phone
        if not contact or not contact_phone:
            raise ValidationError({"address": "请填写自提联系人和电话。"})
        return contact, contact_phone, _pickup_ship_to(owner, data), address

    contact = address.contact if address else (data.get("contact") or "").strip()
    contact_phone = (
        address.phone if address else (data.get("contact_phone") or "").strip()
    )
    ship_to = address.full_address if address else (data.get("ship_to") or "").strip()
    if not contact or not contact_phone or not ship_to:
        raise ValidationError({"address": "请填写完整收货联系人、电话和地址。"})
    return contact, contact_phone, ship_to, address


def _owner_for_order_payload(request, data):
    owner_id = data.get("owner_id")
    if owner_id:
        binding = get_object_or_404(
            _buyer_bindings_for_user(request.user),
            owner_id=owner_id,
        )
        return binding.owner

    product_ids = [line["product_id"] for line in data.get("lines", [])]
    configs = list(_public_config_qs().filter(product_id__in=product_ids))
    owner_ids = {config.owner_id for config in configs}
    if len(owner_ids) != 1:
        raise ValidationError({"owner_id": "购物车已按配送包裹拆分，可统一提交。"})
    owner = configs[0].owner
    _customer_for_context(owner, request.user, data, auto_create=True)
    return owner


class SaleMiniOrderListCreateApi(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = SaleMiniPagination

    def get(self, request):
        status_param = (request.query_params.get("status") or "").strip()
        search = (request.query_params.get("search") or "").strip()
        bindings = list(_buyer_bindings_for_user(request.user))
        if not bindings:
            return Response([])
        qs = (
            SaleMiniOrderMapping.objects.filter(buyer_user__in=bindings)
            .select_related(
                "outbound_order",
                "outbound_order__owner",
                "outbound_order__customer",
                "outbound_order__warehouse",
            )
            .order_by("-created_at", "-id")
        )
        if status_param:
            qs = qs.filter(_display_status_q(status_param))
        if search:
            qs = qs.filter(
                Q(outbound_order__order_no__icontains=search)
                | Q(outbound_order__src_bill_no__icontains=search)
            )

        groups = _order_mapping_groups(list(qs))
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(groups, request, view=self)
        mapping_groups = page if page is not None else groups
        data = [_order_group_payload(request, group) for group in mapping_groups]
        if page is not None:
            return paginator.get_paginated_response(data)
        return Response(data)

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order_contexts = _order_contexts_for_payload(request, data, auto_create=True)
        if len(order_contexts) > 1 and _order_has_owner_scoped_adjustments(data):
            raise ValidationError(
                {
                    "adjustments": (
                        "多包裹订单暂不支持优惠券或积分抵扣，请先不使用优惠后再提交。"
                    )
                }
            )

        batch_source = (
            _new_sale_mini_batch_source() if len(order_contexts) > 1 else "sale-mini"
        )
        mappings = []
        for context in order_contexts:
            mappings.append(
                _create_sale_mini_order_mapping(
                    request,
                    context["owner"],
                    context["customer"],
                    context["buyer"],
                    data,
                    context["lines"],
                    cart_id=context.get("cart_id"),
                    adjustment_data=data,
                    source=batch_source,
                )
            )
        return Response(
            _combined_order_payload(request, mappings),
            status=status.HTTP_201_CREATED,
        )


class SaleMiniWechatPrepayApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniWechatPrepaySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mapping = _mapping_for_request(
            request,
            serializer.validated_data["order_id"],
            for_update=True,
        )
        order = mapping.outbound_order
        if order.approval_status == "CANCELLED":
            mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.CANCELLED
            mapping.save(update_fields=["payment_status", "updated_at"])
            raise ValidationError({"order": "订单已取消，不能支付。"})
        if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.PAID:
            return Response({"paid": True, "order": _order_payload(request, mapping)})
        if mapping.payment_status in {
            SaleMiniOrderMapping.PaymentStatus.REFUNDING,
            SaleMiniOrderMapping.PaymentStatus.REFUNDED,
            SaleMiniOrderMapping.PaymentStatus.CANCELLED,
        }:
            raise ValidationError({"payment": "当前订单状态不能发起支付。"})

        now = timezone.now()
        if mapping.pay_deadline_at and mapping.pay_deadline_at <= now:
            _cancel_unpaid_mapping(mapping, request.user, close_wechat=True)
            raise ValidationError({"payment": "订单已超时取消，请重新下单。"})

        buyer = mapping.buyer_user or _buyer_for_user(mapping.owner, request.user)
        if not buyer or not buyer.openid:
            raise ValidationError(
                {"openid": "当前小程序用户未绑定 openid，不能微信支付。"}
            )

        if not mapping.pay_deadline_at:
            mapping.pay_deadline_at = _payment_deadline()
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.UNPAID
        mapping.updated_by = request.user
        mapping.save(
            update_fields=[
                "payment_status",
                "pay_deadline_at",
                "updated_by",
                "updated_at",
            ]
        )

        payment = _active_payment(mapping)
        if (
            payment
            and payment.prepay_id
            and payment.expires_at
            and payment.expires_at > now
        ):
            try:
                params = sign_jsapi_pay_params(payment.prepay_id)
            except (WechatPayConfigError, WechatPayRequestError) as exc:
                _handle_wechat_pay_error(exc)
            payment.client_pay_params = params
            payment.save(update_fields=["client_pay_params", "updated_at"])
            return Response(
                {
                    "paid": False,
                    "payment": _payment_payload(payment),
                    "pay_params": params,
                    "order": _order_payload(request, mapping),
                }
            )

        amount = Decimal(
            mapping.payable_amount or order.final_order_amount or 0
        ).quantize(MONEY_QUANT)
        amount_cents = money_to_cents(amount)
        if amount_cents <= 0:
            raise ValidationError({"amount": "订单金额必须大于 0 才能微信支付。"})
        payment = SaleMiniPayment.objects.create(
            owner=mapping.owner,
            customer=mapping.customer,
            buyer_user=buyer,
            mapping=mapping,
            payment_no=_payment_code("SMP"),
            out_trade_no=_payment_code("SMT"),
            amount=amount,
            amount_cents=amount_cents,
            expires_at=mapping.pay_deadline_at,
            created_by=request.user,
            updated_by=request.user,
        )
        try:
            prepay_id, raw_response, params = create_jsapi_prepay(
                payment,
                buyer.openid,
                f"销售订单 {order.order_no}",
            )
        except (WechatPayConfigError, WechatPayRequestError) as exc:
            payment.status = SaleMiniPayment.Status.FAILED
            payment.trade_state_desc = str(exc)[:200]
            payment.save(update_fields=["status", "trade_state_desc", "updated_at"])
            _handle_wechat_pay_error(exc)

        payment.status = SaleMiniPayment.Status.PREPAY
        payment.prepay_id = prepay_id
        payment.prepay_response = raw_response
        payment.client_pay_params = params
        payment.save(
            update_fields=[
                "status",
                "prepay_id",
                "prepay_response",
                "client_pay_params",
                "updated_at",
            ]
        )
        return Response(
            {
                "paid": False,
                "payment": _payment_payload(payment),
                "pay_params": params,
                "order": _order_payload(request, mapping),
            }
        )


def _callback_event(payload, raw_body, decrypted):
    event_id = payload.get("id") or hashlib.sha256(raw_body).hexdigest()
    event, created = SaleMiniPaymentEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "event_type": payload.get("event_type", ""),
            "resource_type": payload.get("resource_type", ""),
            "payload": payload,
            "decrypted_payload": decrypted,
            "out_trade_no": decrypted.get("out_trade_no", ""),
            "out_refund_no": decrypted.get("out_refund_no", ""),
        },
    )
    return event, created


def _wechat_callback_ok():
    return Response({"code": "SUCCESS", "message": "成功"})


def _wechat_callback_fail(message, *, http_status=status.HTTP_400_BAD_REQUEST):
    return Response({"code": "FAIL", "message": message}, status=http_status)


class SaleMiniWechatPaymentCallbackApi(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @transaction.atomic
    def post(self, request):
        raw_body = request.body
        try:
            verify_callback_signature(request.headers, raw_body)
            payload = json.loads(raw_body.decode("utf-8"))
            decrypted = decrypt_resource(payload.get("resource") or {})
        except (ValueError, WechatPayConfigError, WechatPayRequestError) as exc:
            return _wechat_callback_fail(str(exc))

        event, created = _callback_event(payload, raw_body, decrypted)
        if (
            not created
            and event.process_status == SaleMiniPaymentEvent.ProcessStatus.PROCESSED
        ):
            return _wechat_callback_ok()

        try:
            payment = (
                SaleMiniPayment.objects.select_for_update()
                .select_related("mapping", "mapping__outbound_order")
                .get(out_trade_no=decrypted.get("out_trade_no", ""))
            )
            amount = decrypted.get("amount") or {}
            total_cents = int(amount.get("total") or 0)
            if total_cents != payment.amount_cents:
                raise ValidationError(
                    {"amount": "微信回调金额与本地支付流水金额不一致。"}
                )
            trade_state = decrypted.get("trade_state") or ""
            mapping = (
                SaleMiniOrderMapping.objects.select_for_update()
                .select_related("outbound_order")
                .get(pk=payment.mapping_id)
            )
            payment.callback_payload = decrypted
            payment.trade_state = trade_state
            payment.trade_state_desc = decrypted.get("trade_state_desc") or ""
            payment.transaction_id = (
                decrypted.get("transaction_id") or payment.transaction_id
            )
            if trade_state == "SUCCESS":
                paid_at = timezone.now()
                payment.status = SaleMiniPayment.Status.PAID
                payment.paid_at = paid_at
                if mapping.outbound_order.approval_status == "CANCELLED":
                    mapping.payment_status = (
                        SaleMiniOrderMapping.PaymentStatus.REFUNDING
                    )
                    mapping.paid_at = paid_at
                else:
                    mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
                    mapping.paid_at = paid_at
                    confirm_adjustments(mapping)
                    confirm_distribution(mapping)
                mapping.save(update_fields=["payment_status", "paid_at", "updated_at"])
            elif trade_state in {"CLOSED", "REVOKED"}:
                payment.status = SaleMiniPayment.Status.CLOSED
                payment.closed_at = timezone.now()
            elif trade_state in {"PAYERROR", "NOTPAY"}:
                payment.status = SaleMiniPayment.Status.FAILED
            payment.save(
                update_fields=[
                    "status",
                    "trade_state",
                    "trade_state_desc",
                    "transaction_id",
                    "callback_payload",
                    "paid_at",
                    "closed_at",
                    "updated_at",
                ]
            )
            event.payment = payment
            event.process_status = SaleMiniPaymentEvent.ProcessStatus.PROCESSED
            event.processed_at = timezone.now()
            event.save(
                update_fields=[
                    "payment",
                    "process_status",
                    "processed_at",
                    "updated_at",
                ]
            )
        except Exception as exc:
            event.process_status = SaleMiniPaymentEvent.ProcessStatus.FAILED
            event.error_message = _error_message(exc)[:300]
            event.processed_at = timezone.now()
            event.save(
                update_fields=[
                    "process_status",
                    "error_message",
                    "processed_at",
                    "updated_at",
                ]
            )
            return _wechat_callback_fail(event.error_message)
        return _wechat_callback_ok()


class SaleMiniWechatRefundApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniWechatRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mapping = _mapping_for_request(
            request,
            serializer.validated_data["order_id"],
            for_update=True,
        )
        order = mapping.outbound_order
        fulfillment_status, _name = _fulfillment_status(order)
        if fulfillment_status in {"WAIT_PICK", "COMPLETED"}:
            raise ValidationError({"status": "订单已进入备货阶段，请申请售后。"})
        if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.UNPAID:
            _cancel_unpaid_mapping(mapping, request.user, close_wechat=True)
            return Response(_order_payload(request, mapping))
        if mapping.payment_status not in {
            SaleMiniOrderMapping.PaymentStatus.PAID,
            SaleMiniOrderMapping.PaymentStatus.REFUNDING,
        }:
            raise ValidationError({"payment": "当前订单状态不能退款。"})

        payment = (
            mapping.payments.select_for_update()
            .filter(status=SaleMiniPayment.Status.PAID)
            .order_by("-paid_at", "-id")
            .first()
        )
        if not payment:
            raise ValidationError({"payment": "未找到可退款的成功支付流水。"})
        if payment.refunds.filter(status=SaleMiniRefund.Status.SUCCESS).exists():
            raise ValidationError({"refund": "该支付流水已退款。"})

        refund = SaleMiniRefund.objects.create(
            owner=payment.owner,
            customer=payment.customer,
            buyer_user=payment.buyer_user,
            payment=payment,
            refund_no=_payment_code("SMR"),
            out_refund_no=_payment_code("SMRF"),
            amount=payment.amount,
            amount_cents=payment.amount_cents,
            total_amount_cents=payment.amount_cents,
            reason=(serializer.validated_data.get("reason") or "用户申请退款"),
            requested_at=timezone.now(),
            created_by=request.user,
            updated_by=request.user,
        )
        try:
            request_payload, raw_response = request_refund(refund)
        except (WechatPayConfigError, WechatPayRequestError) as exc:
            refund.status = SaleMiniRefund.Status.FAILED
            refund.response_payload = {"error": str(exc)}
            refund.save(update_fields=["status", "response_payload", "updated_at"])
            _handle_wechat_pay_error(exc)

        refund.request_payload = request_payload
        refund.response_payload = raw_response
        refund.refund_id = raw_response.get("refund_id", "")
        refund_status = raw_response.get("status") or SaleMiniRefund.Status.PROCESSING
        if refund_status not in SaleMiniRefund.Status.values:
            refund_status = SaleMiniRefund.Status.PROCESSING
        refund.status = refund_status
        if refund.status == SaleMiniRefund.Status.SUCCESS:
            refund.success_at = timezone.now()
        refund.save(
            update_fields=[
                "request_payload",
                "response_payload",
                "refund_id",
                "status",
                "success_at",
                "updated_at",
            ]
        )
        _cancel_order_after_refund_request(mapping, request.user)
        if refund.status == SaleMiniRefund.Status.SUCCESS:
            _finalize_successful_refund(mapping, payment, by_user=request.user)
        else:
            payment.status = SaleMiniPayment.Status.REFUNDING
            payment.save(update_fields=["status", "updated_at"])
            mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDING
            mapping.updated_by = request.user
            mapping.save(update_fields=["payment_status", "updated_by", "updated_at"])
        return Response(
            {
                "order": _order_payload(request, mapping),
                "refund": _refund_payload(refund),
            }
        )


class SaleMiniWechatRefundCallbackApi(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @transaction.atomic
    def post(self, request):
        raw_body = request.body
        try:
            verify_callback_signature(request.headers, raw_body)
            payload = json.loads(raw_body.decode("utf-8"))
            decrypted = decrypt_resource(payload.get("resource") or {})
        except (ValueError, WechatPayConfigError, WechatPayRequestError) as exc:
            return _wechat_callback_fail(str(exc))

        event, created = _callback_event(payload, raw_body, decrypted)
        if (
            not created
            and event.process_status == SaleMiniPaymentEvent.ProcessStatus.PROCESSED
        ):
            return _wechat_callback_ok()

        try:
            refund = (
                SaleMiniRefund.objects.select_for_update()
                .select_related("payment", "payment__mapping")
                .get(out_refund_no=decrypted.get("out_refund_no", ""))
            )
            payment = refund.payment
            mapping = SaleMiniOrderMapping.objects.select_for_update().get(
                pk=payment.mapping_id
            )
            refund.callback_payload = decrypted
            refund.refund_id = decrypted.get("refund_id") or refund.refund_id
            status_value = (
                decrypted.get("refund_status") or decrypted.get("status") or ""
            )
            if status_value in SaleMiniRefund.Status.values:
                refund.status = status_value
            if refund.status == SaleMiniRefund.Status.SUCCESS:
                refund.success_at = timezone.now()
                _finalize_successful_refund(mapping, payment)
            elif refund.status in {
                SaleMiniRefund.Status.ABNORMAL,
                SaleMiniRefund.Status.CLOSED,
                SaleMiniRefund.Status.FAILED,
            }:
                payment.status = SaleMiniPayment.Status.PAID
                mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
                payment.save(update_fields=["status", "updated_at"])
                mapping.save(update_fields=["payment_status", "updated_at"])
            refund.save(
                update_fields=[
                    "callback_payload",
                    "refund_id",
                    "status",
                    "success_at",
                    "updated_at",
                ]
            )
            event.refund = refund
            event.payment = payment
            event.process_status = SaleMiniPaymentEvent.ProcessStatus.PROCESSED
            event.processed_at = timezone.now()
            event.save(
                update_fields=[
                    "refund",
                    "payment",
                    "process_status",
                    "processed_at",
                    "updated_at",
                ]
            )
        except Exception as exc:
            event.process_status = SaleMiniPaymentEvent.ProcessStatus.FAILED
            event.error_message = _error_message(exc)[:300]
            event.processed_at = timezone.now()
            event.save(
                update_fields=[
                    "process_status",
                    "error_message",
                    "processed_at",
                    "updated_at",
                ]
            )
            return _wechat_callback_fail(event.error_message)
        return _wechat_callback_ok()


class SaleMiniAfterSaleListCreateApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        bindings = list(_buyer_bindings_for_user(request.user))
        qs = (
            SaleMiniAfterSaleRequest.objects.filter(buyer_user__in=bindings)
            .select_related("mapping", "mapping__outbound_order")
            .order_by("-created_at", "-id")
        )
        order_id = request.query_params.get("order_id")
        if order_id:
            qs = qs.filter(mapping__outbound_order_id=order_id)
        return Response([_after_sale_payload(row) for row in qs])

    @transaction.atomic
    def post(self, request):
        serializer = SaleMiniAfterSaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mapping = _mapping_for_request(
            request,
            data["order_id"],
            for_update=True,
        )
        owner = mapping.owner
        customer = mapping.customer
        buyer = mapping.buyer_user
        fulfillment_status, _name = _fulfillment_status(mapping.outbound_order)
        if fulfillment_status not in {"WAIT_PICK", "PROCESSING", "COMPLETED"}:
            raise ValidationError(
                {"status": "当前订单仍可取消或退款，请优先使用原订单流程。"}
            )
        if mapping.payment_status not in {
            SaleMiniOrderMapping.PaymentStatus.PAID,
            SaleMiniOrderMapping.PaymentStatus.OFFLINE,
        }:
            raise ValidationError({"payment": "当前支付状态不能申请售后。"})
        if mapping.after_sale_requests.filter(
            status=SaleMiniAfterSaleRequest.Status.PENDING
        ).exists():
            raise ValidationError({"after_sale": "该订单已有待处理售后申请。"})
        amount = data.get("amount")
        if amount is None:
            amount = mapping.payable_amount or mapping.outbound_order.final_order_amount
        row = SaleMiniAfterSaleRequest.objects.create(
            owner=owner,
            customer=customer,
            buyer_user=buyer,
            mapping=mapping,
            request_no=_payment_code("SMAS"),
            request_type=data["request_type"],
            amount=amount,
            reason=data.get("reason", ""),
            requested_at=timezone.now(),
            created_by=request.user,
            updated_by=request.user,
        )
        return Response(_after_sale_payload(row), status=status.HTTP_201_CREATED)


class SaleMiniOrderDetailApi(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        mappings = _mapping_group_for_request(request, pk)
        return Response(_order_group_payload(request, mappings))


class SaleMiniOrderCancelApi(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        mappings = _mapping_group_for_request(request, pk, for_update=True)
        for mapping in mappings:
            order = mapping.outbound_order
            if mapping.payment_status in {
                SaleMiniOrderMapping.PaymentStatus.PAID,
                SaleMiniOrderMapping.PaymentStatus.REFUNDING,
                SaleMiniOrderMapping.PaymentStatus.REFUNDED,
            }:
                raise ValidationError({"payment": "已支付订单请走退款流程。"})
            fulfillment_status, _name = _fulfillment_status(order)
            if fulfillment_status in {"WAIT_PICK", "COMPLETED"}:
                raise ValidationError({"status": "订单已进入备货阶段，暂不能取消。"})
        for mapping in mappings:
            if mapping.outbound_order.approval_status != "CANCELLED":
                _cancel_unpaid_mapping(mapping, request.user, close_wechat=True)
        return Response(_order_group_payload(request, mappings))


def _mapping_for_request(request, pk, *, for_update=False):
    bindings = list(_buyer_bindings_for_user(request.user))
    qs = SaleMiniOrderMapping.objects.filter(buyer_user__in=bindings)
    if for_update:
        qs = qs.select_for_update()
    qs = qs.select_related(
        "outbound_order",
        "outbound_order__owner",
        "outbound_order__customer",
        "outbound_order__warehouse",
    )
    return get_object_or_404(qs, outbound_order_id=pk)


def _mapping_group_for_request(request, pk, *, for_update=False):
    mapping = _mapping_for_request(request, pk, for_update=for_update)
    if not _is_sale_mini_batch_source(mapping.source):
        return [mapping]
    bindings = list(_buyer_bindings_for_user(request.user))
    qs = SaleMiniOrderMapping.objects.filter(
        buyer_user__in=bindings,
        source=mapping.source,
    )
    if for_update:
        qs = qs.select_for_update()
    qs = qs.select_related(
        "outbound_order",
        "outbound_order__owner",
        "outbound_order__customer",
        "outbound_order__warehouse",
    ).order_by("id")
    return list(qs)
