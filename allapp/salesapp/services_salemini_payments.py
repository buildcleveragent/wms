import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from allapp.inventory.locking import lock_warehouses_for_inventory_write
from allapp.outbound.services import unallocate_for_order

from .models import SaleMiniOrderMapping, SaleMiniPayment, SaleMiniRefund
from .services_salemini_adjustments import (
    confirm_adjustments,
    confirm_distribution,
    release_adjustments,
    reverse_distribution,
)
from .services_wechat_pay import (
    WechatPayConfigError,
    WechatPayRequestError,
    build_refund_request_payload,
    close_jsapi_payment,
    query_jsapi_payment,
    query_refund,
    request_refund,
)

MONEY_QUANT = Decimal("0.01")
REFUND_RETRY_DELAYS_MINUTES = (1, 5, 15, 60, 180, 360)
TERMINAL_REFUND_STATUSES = {
    SaleMiniRefund.Status.ABNORMAL,
    SaleMiniRefund.Status.CLOSED,
}
TRANSIENT_WECHAT_CODES = {
    "SYSTEMERROR",
    "FREQUENCY_LIMITED",
    "RATE_LIMITED",
}
ORDER_NOT_FOUND_CODES = {"ORDER_NOT_EXIST", "ORDERNOTEXIST"}
ORDER_PAID_CODES = {"ORDERPAID", "ORDER_PAID"}


def _code(prefix):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{stamp}{uuid.uuid4().hex[:10].upper()}"


def payable_amount(mapping):
    """Return the authoritative payable amount without truthy fallbacks."""

    try:
        amount = Decimal(mapping.payable_amount).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"payable_amount": "订单应付金额无效。"}) from exc
    if amount < 0:
        raise ValidationError({"payable_amount": "订单应付金额不能小于零。"})
    return amount


def _local_cancel_locked(mapping, by_user=None):
    order = mapping.outbound_order
    if order.approval_status != "CANCELLED":
        unallocate_for_order(order)
        order.approval_status = "CANCELLED"
        order.updated_by = by_user
        order.save(update_fields=["approval_status", "updated_by", "updated_at"])
    release_adjustments(
        mapping,
        by_user=by_user,
        reverse_confirmed=(mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.OFFLINE),
    )
    reverse_distribution(mapping)
    mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.CANCELLED
    mapping.updated_by = by_user
    mapping.save(update_fields=["payment_status", "updated_by", "updated_at"])
    return mapping


@transaction.atomic
def cancel_mapping_if_unpaid(mapping_id, by_user=None):
    warehouse_id = (
        SaleMiniOrderMapping.objects.filter(pk=mapping_id)
        .values_list("outbound_order__warehouse_id", flat=True)
        .get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    mapping = (
        SaleMiniOrderMapping.objects.select_for_update()
        .select_related("outbound_order")
        .get(pk=mapping_id)
    )
    if mapping.outbound_order.warehouse_id != warehouse_id:
        raise ValidationError("商城订单仓库在取消期间发生变化，请重试。")
    if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.PAID:
        return {"result": "paid", "mapping": mapping}
    if mapping.payment_status in {
        SaleMiniOrderMapping.PaymentStatus.REFUNDING,
        SaleMiniOrderMapping.PaymentStatus.REFUNDED,
    }:
        return {"result": "refund_in_progress", "mapping": mapping}
    if mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.CANCELLED:
        return {"result": "cancelled", "mapping": mapping}
    return {"result": "cancelled", "mapping": _local_cancel_locked(mapping, by_user)}


def cancel_order_for_refund(mapping, by_user=None):
    order = mapping.outbound_order
    if order.approval_status != "CANCELLED":
        unallocate_for_order(order)
        order.approval_status = "CANCELLED"
        order.updated_by = by_user
        order.save(update_fields=["approval_status", "updated_by", "updated_at"])


def validate_payment_result(payment, payload):
    amount = payload.get("amount") or {}
    checks = {
        "out_trade_no": (
            payload.get("out_trade_no"),
            payment.out_trade_no,
            "微信支付商户订单号与本地支付流水不一致。",
        ),
        "appid": (
            payload.get("appid"),
            settings.WECHAT_MINI_APPID,
            "微信支付 AppID 与本地配置不一致。",
        ),
        "mchid": (
            payload.get("mchid"),
            settings.WECHAT_PAY_MCH_ID,
            "微信支付商户号与本地配置不一致。",
        ),
        "currency": (
            amount.get("currency"),
            payment.currency,
            "微信支付币种与本地支付流水不一致。",
        ),
    }
    for field, (actual, expected, message) in checks.items():
        if not actual or str(actual) != str(expected):
            raise ValidationError({field: message})
    try:
        total = int(amount.get("total"))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"amount": "微信支付回调金额无效。"}) from exc
    if total != payment.amount_cents:
        raise ValidationError({"amount": "微信回调金额与本地支付流水金额不一致。"})


def validate_refund_result(refund, payload, *, require_merchant=False):
    if require_merchant and str(payload.get("mchid") or "") != str(settings.WECHAT_PAY_MCH_ID):
        raise ValidationError({"mchid": "微信退款商户号与本地配置不一致。"})
    if str(payload.get("out_refund_no") or "") != refund.out_refund_no:
        raise ValidationError({"out_refund_no": "微信退款单号与本地退款单不一致。"})
    amount = payload.get("amount") or {}
    if str(amount.get("currency") or "") != refund.currency:
        raise ValidationError({"currency": "微信退款币种与本地退款单不一致。"})
    try:
        refund_cents = int(amount.get("refund"))
        total_cents = int(amount.get("total"))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"amount": "微信退款回调金额无效。"}) from exc
    if refund_cents != refund.amount_cents or total_cents != refund.total_amount_cents:
        raise ValidationError({"amount": "微信退款回调金额与本地退款单不一致。"})


@transaction.atomic
def get_or_create_full_refund(
    payment,
    *,
    source=SaleMiniRefund.Source.USER_REQUEST,
    reason="用户申请退款",
    by_user=None,
):
    payment = (
        SaleMiniPayment.objects.select_for_update().select_related("mapping").get(pk=payment.pk)
    )
    idempotency_key = f"FULL_PAYMENT:{payment.pk}"
    refund = SaleMiniRefund.objects.filter(idempotency_key=idempotency_key).first()
    if refund:
        if source == SaleMiniRefund.Source.LATE_PAYMENT and refund.source != source:
            refund.source = source
            refund.save(update_fields=["source", "updated_at"])
        return refund, False
    refund = SaleMiniRefund.objects.create(
        owner=payment.owner,
        customer=payment.customer,
        buyer_user=payment.buyer_user,
        payment=payment,
        source=source,
        idempotency_key=idempotency_key,
        refund_no=_code("SMR"),
        out_refund_no=_code("SMRF"),
        amount=payment.amount,
        amount_cents=payment.amount_cents,
        total_amount_cents=payment.amount_cents,
        reason=reason,
        next_retry_at=timezone.now(),
        created_by=by_user,
        updated_by=by_user,
    )
    return refund, True


def mark_refunding(
    mapping,
    payment,
    refund,
    by_user=None,
    *,
    cancel_order=True,
    update_mapping=True,
):
    if cancel_order:
        cancel_order_for_refund(mapping, by_user)
    payment.status = SaleMiniPayment.Status.REFUNDING
    payment.save(update_fields=["status", "updated_at"])
    if update_mapping:
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDING
        mapping.updated_by = by_user
        mapping.save(update_fields=["payment_status", "updated_by", "updated_at"])
    if (
        refund.status
        not in {
            SaleMiniRefund.Status.SUCCESS,
            SaleMiniRefund.Status.PROCESSING,
        }
        and not refund.requires_manual_action
    ):
        refund.next_retry_at = refund.next_retry_at or timezone.now()
        refund.save(update_fields=["next_retry_at", "updated_at"])


def _finalize_successful_refund_locked(
    refund,
    payload=None,
    by_user=None,
    *,
    from_callback=False,
    preserve_paid_mapping=False,
):
    payment = refund.payment
    mapping = payment.mapping
    now = timezone.now()
    refund.status = SaleMiniRefund.Status.SUCCESS
    refund.success_at = refund.success_at or now
    refund.next_retry_at = None
    refund.last_error = ""
    refund.requires_manual_action = False
    if payload is not None:
        if from_callback:
            refund.callback_payload = payload
        else:
            refund.response_payload = payload
        refund.refund_id = payload.get("refund_id") or refund.refund_id
    fields = [
        "status",
        "success_at",
        "next_retry_at",
        "last_error",
        "requires_manual_action",
        "refund_id",
        "updated_at",
    ]
    if payload is not None:
        fields.append("callback_payload" if from_callback else "response_payload")
    refund.save(update_fields=fields)
    payment.status = SaleMiniPayment.Status.REFUNDED
    payment.save(update_fields=["status", "updated_at"])
    if not preserve_paid_mapping:
        mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.REFUNDED
        mapping.updated_by = by_user
        mapping.save(update_fields=["payment_status", "updated_by", "updated_at"])
        release_adjustments(mapping, by_user=by_user, reverse_confirmed=True)
        reverse_distribution(mapping)
    return refund


@transaction.atomic
def apply_refund_result(refund, payload, *, from_callback=False, by_user=None):
    warehouse_id = (
        SaleMiniRefund.objects.filter(pk=refund.pk)
        .values_list("payment__mapping__outbound_order__warehouse_id", flat=True)
        .get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    refund = (
        SaleMiniRefund.objects.select_for_update()
        .select_related("payment", "payment__mapping", "payment__mapping__outbound_order")
        .get(pk=refund.pk)
    )
    payment = SaleMiniPayment.objects.select_for_update().get(pk=refund.payment_id)
    mapping = SaleMiniOrderMapping.objects.select_for_update().get(pk=payment.mapping_id)
    if mapping.outbound_order.warehouse_id != warehouse_id:
        raise ValidationError("商城订单仓库在退款期间发生变化，请重试。")
    refund.payment = payment
    payment.mapping = mapping
    validate_refund_result(refund, payload, require_merchant=from_callback)
    status_value = payload.get("refund_status") or payload.get("status") or ""
    preserve_paid_mapping = (
        mapping.payment_status == SaleMiniOrderMapping.PaymentStatus.PAID
        and mapping.outbound_order.approval_status != "CANCELLED"
        and payment.status == SaleMiniPayment.Status.REFUNDING
        and mapping.payments.exclude(pk=payment.pk)
        .filter(
            status__in=[
                SaleMiniPayment.Status.PAID,
                SaleMiniPayment.Status.REFUNDING,
                SaleMiniPayment.Status.REFUNDED,
            ]
        )
        .exists()
    )
    if status_value == SaleMiniRefund.Status.SUCCESS:
        return _finalize_successful_refund_locked(
            refund,
            payload,
            by_user=by_user,
            from_callback=from_callback,
            preserve_paid_mapping=preserve_paid_mapping,
        )

    refund.refund_id = payload.get("refund_id") or refund.refund_id
    if from_callback:
        refund.callback_payload = payload
    else:
        refund.response_payload = payload
    if status_value in TERMINAL_REFUND_STATUSES:
        refund.status = status_value
        refund.next_retry_at = None
        refund.last_error = payload.get("user_received_account") or "微信退款异常，需人工处理。"
        refund.requires_manual_action = True
    else:
        refund.status = SaleMiniRefund.Status.PROCESSING
        refund.next_retry_at = timezone.now() + timedelta(
            minutes=max(
                int(getattr(settings, "SALE_MINI_REFUND_QUERY_INTERVAL_MINUTES", 5)),
                1,
            )
        )
        refund.last_error = ""
        refund.requires_manual_action = False
    fields = [
        "refund_id",
        "status",
        "next_retry_at",
        "last_error",
        "requires_manual_action",
        "updated_at",
        "callback_payload" if from_callback else "response_payload",
    ]
    refund.save(update_fields=fields)
    mark_refunding(
        mapping,
        payment,
        refund,
        by_user,
        cancel_order=not preserve_paid_mapping,
        update_mapping=not preserve_paid_mapping,
    )
    return refund


def _max_refund_retries():
    return min(
        max(int(getattr(settings, "SALE_MINI_REFUND_MAX_RETRIES", 6)), 1),
        len(REFUND_RETRY_DELAYS_MINUTES),
    )


def _max_refund_attempts():
    return _max_refund_retries() + 1


@transaction.atomic
def _schedule_refund_failure(refund_id, exc, *, keep_processing=False):
    refund = (
        SaleMiniRefund.objects.select_for_update()
        .select_related("payment", "payment__mapping")
        .get(pk=refund_id)
    )
    refund.status = (
        SaleMiniRefund.Status.PROCESSING if keep_processing else SaleMiniRefund.Status.FAILED
    )
    refund.last_error = str(exc)[:300]
    refund.response_payload = getattr(exc, "response", None) or {"error": str(exc)}
    retryable = isinstance(exc, WechatPayRequestError) and (
        exc.is_network_error
        or exc.http_status in {429, 500, 502, 503, 504}
        or exc.code in TRANSIENT_WECHAT_CODES
    )
    if retryable and refund.retry_count <= _max_refund_retries():
        attempt = max(refund.retry_count, 1)
        refund.next_retry_at = timezone.now() + timedelta(
            minutes=REFUND_RETRY_DELAYS_MINUTES[attempt - 1]
        )
        refund.requires_manual_action = False
    else:
        refund.next_retry_at = None
        refund.requires_manual_action = True
    refund.save(
        update_fields=[
            "status",
            "last_error",
            "response_payload",
            "next_retry_at",
            "requires_manual_action",
            "updated_at",
        ]
    )
    return refund


@transaction.atomic
def _prepare_refund_request(refund_id):
    refund = (
        SaleMiniRefund.objects.select_for_update()
        .select_related("payment", "payment__mapping")
        .get(pk=refund_id)
    )
    if refund.status == SaleMiniRefund.Status.SUCCESS or refund.requires_manual_action:
        return refund, None
    if refund.retry_count >= _max_refund_attempts():
        refund.next_retry_at = None
        refund.requires_manual_action = True
        refund.last_error = refund.last_error or "退款重试次数已用尽，需人工处理。"
        refund.save(
            update_fields=[
                "next_retry_at",
                "requires_manual_action",
                "last_error",
                "updated_at",
            ]
        )
        return refund, None
    refund.retry_count += 1
    refund.requested_at = timezone.now()
    refund.next_retry_at = timezone.now() + timedelta(
        minutes=REFUND_RETRY_DELAYS_MINUTES[
            min(refund.retry_count, len(REFUND_RETRY_DELAYS_MINUTES)) - 1
        ]
    )
    payload = build_refund_request_payload(refund)
    refund.request_payload = payload
    refund.save(
        update_fields=[
            "retry_count",
            "requested_at",
            "next_retry_at",
            "request_payload",
            "updated_at",
        ]
    )
    return refund, payload


def submit_refund(refund):
    refund, payload = _prepare_refund_request(refund.pk)
    if payload is None:
        return refund
    try:
        _request_payload, response = request_refund(refund, payload=payload)
    except (WechatPayConfigError, WechatPayRequestError) as exc:
        return _schedule_refund_failure(refund.pk, exc)
    return apply_refund_result(refund, response)


@transaction.atomic
def _prepare_refund_query(refund_id):
    refund = (
        SaleMiniRefund.objects.select_for_update()
        .select_related("payment", "payment__mapping")
        .get(pk=refund_id)
    )
    if refund.status == SaleMiniRefund.Status.SUCCESS or refund.requires_manual_action:
        return refund, False
    if refund.retry_count >= _max_refund_attempts():
        refund.next_retry_at = None
        refund.requires_manual_action = True
        refund.last_error = refund.last_error or "退款查询次数已用尽，需人工处理。"
        refund.save(
            update_fields=[
                "next_retry_at",
                "requires_manual_action",
                "last_error",
                "updated_at",
            ]
        )
        return refund, False
    refund.retry_count += 1
    refund.next_retry_at = timezone.now() + timedelta(
        minutes=REFUND_RETRY_DELAYS_MINUTES[
            min(refund.retry_count, len(REFUND_RETRY_DELAYS_MINUTES)) - 1
        ]
    )
    refund.save(update_fields=["retry_count", "next_retry_at", "updated_at"])
    return refund, True


def reconcile_refund(refund):
    current = SaleMiniRefund.objects.select_related("payment").get(pk=refund.pk)
    if current.status == SaleMiniRefund.Status.SUCCESS or current.requires_manual_action:
        return current
    if current.status != SaleMiniRefund.Status.PROCESSING:
        return submit_refund(current)
    current, should_query = _prepare_refund_query(current.pk)
    if not should_query:
        return current
    try:
        response = query_refund(current)
    except (WechatPayConfigError, WechatPayRequestError) as exc:
        return _schedule_refund_failure(current.pk, exc, keep_processing=True)
    return apply_refund_result(current, response)


def _is_duplicate_success_payment(mapping, payment):
    return (
        mapping.payments.exclude(pk=payment.pk)
        .filter(
            status__in=[
                SaleMiniPayment.Status.PAID,
                SaleMiniPayment.Status.REFUNDING,
                SaleMiniPayment.Status.REFUNDED,
            ],
            transaction_id__isnull=False,
        )
        .exists()
    )


@transaction.atomic
def apply_payment_success(payment, payload, by_user=None):
    warehouse_id = (
        SaleMiniPayment.objects.filter(pk=payment.pk)
        .values_list("mapping__outbound_order__warehouse_id", flat=True)
        .get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    payment = (
        SaleMiniPayment.objects.select_for_update()
        .select_related("mapping", "mapping__outbound_order")
        .get(pk=payment.pk)
    )
    mapping = (
        SaleMiniOrderMapping.objects.select_for_update()
        .select_related("outbound_order")
        .get(pk=payment.mapping_id)
    )
    if mapping.outbound_order.warehouse_id != warehouse_id:
        raise ValidationError("商城订单仓库在支付确认期间发生变化，请重试。")
    payment.mapping = mapping
    validate_payment_result(payment, payload)
    transaction_id = payload.get("transaction_id")
    if (
        payment.status
        in {
            SaleMiniPayment.Status.PAID,
            SaleMiniPayment.Status.REFUNDING,
            SaleMiniPayment.Status.REFUNDED,
        }
        and payment.transaction_id == transaction_id
    ):
        return {"result": "paid", "refund": payment.refunds.order_by("-id").first()}

    now = timezone.now()
    payment.callback_payload = payload
    payment.trade_state = "SUCCESS"
    payment.trade_state_desc = payload.get("trade_state_desc") or ""
    payment.transaction_id = transaction_id or payment.transaction_id
    payment.paid_at = payment.paid_at or now
    payment.status = SaleMiniPayment.Status.PAID
    payment.next_reconcile_at = None
    payment.last_error = ""
    payment.requires_manual_action = False
    payment.save(
        update_fields=[
            "callback_payload",
            "trade_state",
            "trade_state_desc",
            "transaction_id",
            "paid_at",
            "status",
            "next_reconcile_at",
            "last_error",
            "requires_manual_action",
            "updated_at",
        ]
    )

    duplicate_payment = _is_duplicate_success_payment(mapping, payment)
    cancelled_order = (
        mapping.outbound_order.approval_status == "CANCELLED"
        or mapping.payment_status
        in {
            SaleMiniOrderMapping.PaymentStatus.CANCELLED,
            SaleMiniOrderMapping.PaymentStatus.REFUNDED,
            SaleMiniOrderMapping.PaymentStatus.REFUNDING,
        }
    )
    if duplicate_payment or cancelled_order:
        refund, _created = get_or_create_full_refund(
            payment,
            source=SaleMiniRefund.Source.LATE_PAYMENT,
            reason=(
                "重复到账，自动原路退款"
                if duplicate_payment and not cancelled_order
                else "订单取消后延迟到账，自动原路退款"
            ),
            by_user=by_user,
        )
        mark_refunding(
            mapping,
            payment,
            refund,
            by_user,
            cancel_order=cancelled_order,
            update_mapping=cancelled_order,
        )
        mapping.paid_at = mapping.paid_at or payment.paid_at
        mapping.save(update_fields=["paid_at", "updated_at"])
        return {"result": "late_payment_refund_queued", "refund": refund}

    mapping.paid_at = mapping.paid_at or payment.paid_at
    mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
    mapping.updated_by = by_user
    mapping.save(update_fields=["payment_status", "paid_at", "updated_by", "updated_at"])
    confirm_adjustments(mapping, by_user=by_user)
    confirm_distribution(mapping)
    return {"result": "paid", "refund": None}


@transaction.atomic
def settle_internal_zero(mapping, by_user=None):
    mapping = (
        SaleMiniOrderMapping.objects.select_for_update()
        .select_related("outbound_order")
        .get(pk=mapping.pk)
    )
    if payable_amount(mapping) != Decimal("0.00"):
        raise ValidationError({"payable_amount": "仅零元订单可以内部结算。"})
    if mapping.payment_status in {
        SaleMiniOrderMapping.PaymentStatus.CANCELLED,
        SaleMiniOrderMapping.PaymentStatus.REFUNDING,
        SaleMiniOrderMapping.PaymentStatus.REFUNDED,
    }:
        raise ValidationError({"payment": "当前订单状态不能执行零元结算。"})
    payment = (
        mapping.payments.select_for_update()
        .filter(channel=SaleMiniPayment.Channel.INTERNAL_ZERO)
        .order_by("-id")
        .first()
    )
    now = timezone.now()
    if not payment:
        payment = SaleMiniPayment.objects.create(
            owner=mapping.owner,
            customer=mapping.customer,
            buyer_user=mapping.buyer_user,
            mapping=mapping,
            payment_no=_code("SMPZ"),
            out_trade_no=_code("SMTZ"),
            channel=SaleMiniPayment.Channel.INTERNAL_ZERO,
            status=SaleMiniPayment.Status.PAID,
            amount=Decimal("0.00"),
            amount_cents=0,
            trade_state="SUCCESS",
            trade_state_desc="内部零元结算",
            paid_at=now,
            created_by=by_user,
            updated_by=by_user,
        )
    mapping.payment_status = SaleMiniOrderMapping.PaymentStatus.PAID
    mapping.paid_at = mapping.paid_at or payment.paid_at or now
    mapping.updated_by = by_user
    mapping.save(update_fields=["payment_status", "paid_at", "updated_by", "updated_at"])
    confirm_adjustments(mapping, by_user=by_user)
    confirm_distribution(mapping)
    return payment


@transaction.atomic
def refund_internal_zero_payment(payment, by_user=None, reason="用户申请退款"):
    warehouse_id = (
        SaleMiniPayment.objects.filter(pk=payment.pk)
        .values_list("mapping__outbound_order__warehouse_id", flat=True)
        .get()
    )
    lock_warehouses_for_inventory_write(warehouse_id)
    payment = (
        SaleMiniPayment.objects.select_for_update()
        .select_related("mapping", "mapping__outbound_order")
        .get(pk=payment.pk)
    )
    if payment.channel != SaleMiniPayment.Channel.INTERNAL_ZERO:
        raise ValidationError({"payment": "该支付流水不是内部零元结算。"})
    mapping = SaleMiniOrderMapping.objects.select_for_update().get(pk=payment.mapping_id)
    if mapping.outbound_order.warehouse_id != warehouse_id:
        raise ValidationError("商城订单仓库在零元退款期间发生变化，请重试。")
    payment.mapping = mapping
    refund, _created = get_or_create_full_refund(
        payment,
        reason=reason,
        by_user=by_user,
    )
    mark_refunding(mapping, payment, refund, by_user)
    return _finalize_successful_refund_locked(refund, by_user=by_user)


@transaction.atomic
def _record_payment_state(payment_id, response):
    payment = SaleMiniPayment.objects.select_for_update().get(pk=payment_id)
    payment.trade_state = response.get("trade_state") or ""
    payment.trade_state_desc = response.get("trade_state_desc") or ""
    payment.last_error = ""
    payment.next_reconcile_at = (
        timezone.now() + timedelta(minutes=1)
        if payment.trade_state in {"NOTPAY", "USERPAYING", ""}
        else None
    )
    if payment.trade_state in {"CLOSED", "REVOKED"}:
        payment.status = SaleMiniPayment.Status.CLOSED
        payment.closed_at = payment.closed_at or timezone.now()
    elif payment.trade_state == "PAYERROR":
        payment.status = SaleMiniPayment.Status.FAILED
    payment.save(
        update_fields=[
            "trade_state",
            "trade_state_desc",
            "last_error",
            "next_reconcile_at",
            "status",
            "closed_at",
            "updated_at",
        ]
    )
    return payment


@transaction.atomic
def _record_payment_query_error(payment_id, exc):
    payment = SaleMiniPayment.objects.select_for_update().get(pk=payment_id)
    payment.retry_count += 1
    payment.last_error = str(exc)[:300]
    payment.next_reconcile_at = timezone.now() + timedelta(
        minutes=min(max(payment.retry_count, 1), 15)
    )
    if payment.retry_count >= 6:
        payment.requires_manual_action = True
        payment.next_reconcile_at = None
    payment.save(
        update_fields=[
            "retry_count",
            "last_error",
            "next_reconcile_at",
            "requires_manual_action",
            "updated_at",
        ]
    )
    return payment


def query_and_apply_payment(payment, by_user=None):
    current = SaleMiniPayment.objects.select_related("mapping").get(pk=payment.pk)
    if current.channel == SaleMiniPayment.Channel.INTERNAL_ZERO:
        return {
            "trade_state": current.trade_state or "SUCCESS",
            "result": ("paid" if current.status == SaleMiniPayment.Status.PAID else "pending"),
            "refund": current.refunds.order_by("-id").first(),
        }
    try:
        response = query_jsapi_payment(current)
        validate_payment_result(current, response)
    except (WechatPayConfigError, WechatPayRequestError, ValidationError) as exc:
        _record_payment_query_error(current.pk, exc)
        raise
    trade_state = response.get("trade_state") or ""
    if trade_state == "SUCCESS":
        result = apply_payment_success(current, response, by_user=by_user)
        return {"trade_state": trade_state, **result}
    _record_payment_state(current.pk, response)
    return {"trade_state": trade_state, "result": "pending", "refund": None}


def _latest_active_payment(mapping):
    return (
        mapping.payments.filter(
            channel=SaleMiniPayment.Channel.WECHAT_JSAPI,
            status__in=[
                SaleMiniPayment.Status.CREATED,
                SaleMiniPayment.Status.PREPAY,
                SaleMiniPayment.Status.PAID,
            ],
        )
        .order_by("-created_at", "-id")
        .first()
    )


def safely_cancel_unpaid_mapping(mapping, by_user=None):
    current = SaleMiniOrderMapping.objects.select_related("outbound_order").get(pk=mapping.pk)
    if current.payment_status == SaleMiniOrderMapping.PaymentStatus.PAID:
        return {"result": "paid", "mapping": current}
    if current.payment_status in {
        SaleMiniOrderMapping.PaymentStatus.REFUNDING,
        SaleMiniOrderMapping.PaymentStatus.REFUNDED,
    }:
        return {"result": "refund_in_progress", "mapping": current}
    if current.payment_status == SaleMiniOrderMapping.PaymentStatus.CANCELLED:
        return {"result": "cancelled", "mapping": current}
    payment = _latest_active_payment(current)
    if not payment or not payment.out_trade_no:
        return cancel_mapping_if_unpaid(current.pk, by_user)

    try:
        response = query_jsapi_payment(payment)
    except WechatPayRequestError as exc:
        if exc.code in ORDER_NOT_FOUND_CODES:
            return cancel_mapping_if_unpaid(current.pk, by_user)
        _record_payment_query_error(payment.pk, exc)
        return {"result": "unknown", "mapping": current, "error": str(exc)}
    except WechatPayConfigError as exc:
        _record_payment_query_error(payment.pk, exc)
        return {"result": "unknown", "mapping": current, "error": str(exc)}

    try:
        validate_payment_result(payment, response)
    except ValidationError as exc:
        _record_payment_query_error(payment.pk, exc)
        return {"result": "unknown", "mapping": current, "error": str(exc)}
    trade_state = response.get("trade_state") or ""
    _record_payment_state(payment.pk, response)
    if trade_state == "SUCCESS":
        result = apply_payment_success(payment, response, by_user=by_user)
        current.refresh_from_db()
        return {"result": result["result"], "mapping": current}
    if trade_state == "USERPAYING":
        return {"result": "pending", "mapping": current}
    if trade_state == "NOTPAY":
        try:
            close_jsapi_payment(payment)
        except WechatPayRequestError as exc:
            if exc.code in ORDER_PAID_CODES:
                try:
                    result = query_and_apply_payment(payment, by_user=by_user)
                except (
                    WechatPayConfigError,
                    WechatPayRequestError,
                    ValidationError,
                ) as query_exc:
                    return {
                        "result": "unknown",
                        "mapping": current,
                        "error": str(query_exc),
                    }
                current.refresh_from_db()
                return {"result": result["result"], "mapping": current}
            _record_payment_query_error(payment.pk, exc)
            return {"result": "unknown", "mapping": current, "error": str(exc)}
        except WechatPayConfigError as exc:
            _record_payment_query_error(payment.pk, exc)
            return {"result": "unknown", "mapping": current, "error": str(exc)}
        with transaction.atomic():
            locked_payment = SaleMiniPayment.objects.select_for_update().get(pk=payment.pk)
            locked_payment.status = SaleMiniPayment.Status.CLOSED
            locked_payment.closed_at = timezone.now()
            locked_payment.next_reconcile_at = None
            locked_payment.save(
                update_fields=[
                    "status",
                    "closed_at",
                    "next_reconcile_at",
                    "updated_at",
                ]
            )
        return cancel_mapping_if_unpaid(current.pk, by_user)
    if trade_state in {"CLOSED", "REVOKED", "PAYERROR"}:
        return cancel_mapping_if_unpaid(current.pk, by_user)
    return {"result": "unknown", "mapping": current}
