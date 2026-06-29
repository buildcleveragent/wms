import uuid
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    MiniProgramUser,
    Promotion,
    PromotionDiscountStep,
    SaleMiniCoupon,
    SaleMiniDistributionRecord,
    SaleMiniOrderAdjustment,
    SaleMiniPointLedger,
)

MONEY_QUANT = Decimal("0.01")


def _money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT)


def _code(prefix):
    return f"{prefix}{timezone.now():%Y%m%d%H%M%S%f}{uuid.uuid4().hex[:6].upper()}"


def _point_rate():
    rate = Decimal(str(getattr(settings, "SALE_MINI_POINT_EXCHANGE_RATE", "100")))
    if rate <= 0:
        raise ValidationError({"points": "积分兑换比例必须大于 0。"})
    return rate


def point_balance(owner, customer, buyer=None):
    qs = SaleMiniPointLedger.objects.filter(owner=owner, customer=customer)
    if buyer:
        qs = qs.filter(buyer_user=buyer)
    return _point_balance_from_queryset(qs)


def _point_balance_from_queryset(qs):
    totals = qs.aggregate(
        points=Sum("points_delta"),
        frozen=Sum("frozen_delta"),
    )
    return int(totals["points"] or 0), int(totals["frozen"] or 0)


def _valid_discount_steps(owner, customer, channel, order_date):
    qs = PromotionDiscountStep.objects.filter(
        owner=owner,
        is_active=True,
        promotion__owner=owner,
        promotion__is_active=True,
        promotion__promo_type=Promotion.PromoType.DISCOUNT_STEP,
        promotion__effective_from__lte=order_date,
    ).filter(
        Q(promotion__effective_to__isnull=True)
        | Q(promotion__effective_to__gte=order_date)
    )
    qs = qs.filter(
        Q(promotion__customer__isnull=True) | Q(promotion__customer=customer)
    )
    if channel:
        qs = qs.filter(
            Q(promotion__channel__isnull=True) | Q(promotion__channel=channel)
        )
    else:
        qs = qs.filter(promotion__channel__isnull=True)
    return qs.select_related("promotion").order_by(
        "-discount_amount", "-threshold_amount", "id"
    )


def _discount_step_spec(owner, customer, channel, order_date, goods_amount):
    for step in _valid_discount_steps(owner, customer, channel, order_date):
        if goods_amount >= step.threshold_amount:
            amount = min(_money(step.discount_amount), goods_amount)
            if amount <= 0:
                return None
            return {
                "type": SaleMiniOrderAdjustment.AdjustmentType.DISCOUNT_STEP,
                "title": step.promotion.name,
                "amount": -amount,
                "source_model": "PromotionDiscountStep",
                "source_id": str(step.id),
                "source_code": step.promotion.code,
            }
    return None


def _coupon_for_preview(owner, customer, buyer, coupon_id, order_date):
    if not coupon_id:
        return None
    qs = (
        SaleMiniCoupon.objects.select_related("template")
        .filter(
            owner=owner,
            customer=customer,
            is_active=True,
            status=SaleMiniCoupon.Status.AVAILABLE,
            template__is_active=True,
            template__effective_from__lte=order_date,
        )
        .filter(
            Q(template__effective_to__isnull=True)
            | Q(template__effective_to__gte=order_date)
        )
    )
    if buyer:
        qs = qs.filter(Q(buyer_user=buyer) | Q(buyer_user__isnull=True))
    coupon = qs.filter(pk=coupon_id).first()
    if not coupon:
        raise ValidationError({"coupon_id": "优惠券不可用或已过期。"})
    if coupon.expires_at and coupon.expires_at <= timezone.now():
        raise ValidationError({"coupon_id": "优惠券已过期。"})
    return coupon


def _coupon_spec(owner, customer, buyer, coupon_id, order_date, amount_before_coupon):
    coupon = _coupon_for_preview(owner, customer, buyer, coupon_id, order_date)
    if not coupon:
        return None
    template = coupon.template
    if amount_before_coupon < template.threshold_amount:
        raise ValidationError({"coupon_id": "未达到优惠券使用门槛。"})
    amount = min(_money(template.discount_amount), amount_before_coupon)
    if amount <= 0:
        return None
    return {
        "type": SaleMiniOrderAdjustment.AdjustmentType.COUPON,
        "title": template.title,
        "amount": -amount,
        "source_model": "SaleMiniCoupon",
        "source_id": str(coupon.id),
        "source_code": coupon.coupon_no,
        "coupon_id": coupon.id,
    }


def _points_spec(owner, customer, buyer, requested_points, amount_before_points):
    points = int(requested_points or 0)
    if points <= 0:
        return None
    available, _frozen = point_balance(owner, customer, buyer)
    if points > available:
        raise ValidationError({"points": "可用积分不足。"})
    rate = _point_rate()
    max_points = int(
        (amount_before_points * rate).to_integral_value(rounding=ROUND_DOWN)
    )
    if points > max_points:
        raise ValidationError({"points": "积分抵扣超过订单可抵扣金额。"})
    amount = _money(Decimal(points) / rate)
    if amount <= 0:
        raise ValidationError({"points": "积分抵扣金额必须大于 0。"})
    return {
        "type": SaleMiniOrderAdjustment.AdjustmentType.POINTS,
        "title": "积分抵扣",
        "amount": -amount,
        "source_model": "SaleMiniPointLedger",
        "source_id": str(points),
        "source_code": str(points),
        "points": points,
    }


def build_adjustment_preview(
    *,
    owner,
    customer,
    buyer,
    goods_amount,
    order_date,
    channel=None,
    coupon_id=None,
    points=0,
):
    goods_amount = _money(goods_amount)
    specs = []
    step = _discount_step_spec(owner, customer, channel, order_date, goods_amount)
    if step:
        specs.append(step)
    subtotal = max(
        Decimal("0.00"), _money(goods_amount + sum(s["amount"] for s in specs))
    )
    coupon = _coupon_spec(owner, customer, buyer, coupon_id, order_date, subtotal)
    if coupon:
        specs.append(coupon)
    subtotal = max(
        Decimal("0.00"), _money(goods_amount + sum(s["amount"] for s in specs))
    )
    point = _points_spec(owner, customer, buyer, points, subtotal)
    if point:
        specs.append(point)
    adjustment_amount = _money(sum((spec["amount"] for spec in specs), Decimal("0.00")))
    payable_amount = max(Decimal("0.00"), _money(goods_amount + adjustment_amount))
    return {
        "goods_amount": goods_amount,
        "adjustment_amount": adjustment_amount,
        "payable_amount": payable_amount,
        "adjustments": specs,
    }


def _create_adjustment(owner, customer, buyer, mapping, spec, status, by_user=None):
    now = timezone.now()
    return SaleMiniOrderAdjustment.objects.create(
        owner=owner,
        customer=customer,
        buyer_user=buyer,
        mapping=mapping,
        adjustment_no=_code("SMA"),
        adjustment_type=spec["type"],
        status=status,
        title=spec["title"],
        amount=spec["amount"],
        source_model=spec.get("source_model", ""),
        source_id=spec.get("source_id", ""),
        source_code=spec.get("source_code", ""),
        locked_at=now if status == SaleMiniOrderAdjustment.Status.LOCKED else None,
        confirmed_at=(
            now if status == SaleMiniOrderAdjustment.Status.CONFIRMED else None
        ),
        created_by=by_user,
        updated_by=by_user,
    )


def _lock_coupon(spec, mapping, status, by_user=None):
    coupon_id = spec.get("coupon_id")
    if not coupon_id:
        return
    coupon = SaleMiniCoupon.objects.select_for_update().get(pk=coupon_id)
    if coupon.status != SaleMiniCoupon.Status.AVAILABLE:
        raise ValidationError({"coupon_id": "优惠券已被使用或锁定。"})
    now = timezone.now()
    if coupon.expires_at and coupon.expires_at <= now:
        coupon.status = SaleMiniCoupon.Status.EXPIRED
        coupon.save(update_fields=["status", "updated_at"])
        raise ValidationError({"coupon_id": "优惠券已过期。"})
    if status == SaleMiniOrderAdjustment.Status.CONFIRMED:
        coupon.status = SaleMiniCoupon.Status.USED
        coupon.used_mapping = mapping
        coupon.used_at = now
        coupon.locked_mapping = None
        coupon.locked_at = None
        fields = [
            "status",
            "used_mapping",
            "used_at",
            "locked_mapping",
            "locked_at",
            "updated_at",
        ]
    else:
        coupon.status = SaleMiniCoupon.Status.LOCKED
        coupon.locked_mapping = mapping
        coupon.locked_at = now
        fields = ["status", "locked_mapping", "locked_at", "updated_at"]
    coupon.updated_by = by_user
    fields.append("updated_by")
    coupon.save(update_fields=fields)


def _lock_points(owner, customer, buyer, mapping, spec, status, by_user=None):
    points = int(spec.get("points") or 0)
    if points <= 0:
        return
    balance_qs = SaleMiniPointLedger.objects.select_for_update().filter(
        owner=owner,
        customer=customer,
    )
    if buyer:
        balance_qs = balance_qs.filter(buyer_user=buyer)
    available, _frozen = _point_balance_from_queryset(balance_qs)
    if points > available:
        raise ValidationError({"points": "可用积分不足。"})
    if status == SaleMiniOrderAdjustment.Status.CONFIRMED:
        tx_type = SaleMiniPointLedger.TxType.CONSUME
        points_delta = -points
        frozen_delta = 0
    else:
        tx_type = SaleMiniPointLedger.TxType.FREEZE
        points_delta = -points
        frozen_delta = points
    SaleMiniPointLedger.objects.create(
        owner=owner,
        customer=customer,
        buyer_user=buyer,
        mapping=mapping,
        tx_no=_code("SMP"),
        tx_type=tx_type,
        points_delta=points_delta,
        frozen_delta=frozen_delta,
        amount=abs(spec["amount"]),
        note=spec["title"],
        created_by=by_user,
        updated_by=by_user,
    )


@transaction.atomic
def lock_adjustments(
    *,
    owner,
    customer,
    buyer,
    mapping,
    specs,
    payment_method,
    by_user=None,
):
    status = (
        SaleMiniOrderAdjustment.Status.LOCKED
        if payment_method == "WECHAT"
        else SaleMiniOrderAdjustment.Status.CONFIRMED
    )
    rows = []
    for spec in specs:
        if spec["type"] == SaleMiniOrderAdjustment.AdjustmentType.COUPON:
            _lock_coupon(spec, mapping, status, by_user=by_user)
        if spec["type"] == SaleMiniOrderAdjustment.AdjustmentType.POINTS:
            _lock_points(owner, customer, buyer, mapping, spec, status, by_user=by_user)
        rows.append(
            _create_adjustment(owner, customer, buyer, mapping, spec, status, by_user)
        )
    return rows


@transaction.atomic
def confirm_adjustments(mapping, by_user=None):
    now = timezone.now()
    rows = list(
        mapping.adjustments.select_for_update().filter(
            status=SaleMiniOrderAdjustment.Status.LOCKED
        )
    )
    for row in rows:
        if row.adjustment_type == SaleMiniOrderAdjustment.AdjustmentType.COUPON:
            coupon = (
                SaleMiniCoupon.objects.select_for_update()
                .filter(
                    pk=row.source_id,
                    status=SaleMiniCoupon.Status.LOCKED,
                    locked_mapping=mapping,
                )
                .first()
            )
            if coupon:
                coupon.status = SaleMiniCoupon.Status.USED
                coupon.used_mapping = mapping
                coupon.used_at = now
                coupon.locked_mapping = None
                coupon.locked_at = None
                coupon.updated_by = by_user
                coupon.save(
                    update_fields=[
                        "status",
                        "used_mapping",
                        "used_at",
                        "locked_mapping",
                        "locked_at",
                        "updated_by",
                        "updated_at",
                    ]
                )
        if row.adjustment_type == SaleMiniOrderAdjustment.AdjustmentType.POINTS:
            points = int(row.source_id or 0)
            if points > 0:
                SaleMiniPointLedger.objects.create(
                    owner=mapping.owner,
                    customer=mapping.customer,
                    buyer_user=mapping.buyer_user,
                    mapping=mapping,
                    tx_no=_code("SMP"),
                    tx_type=SaleMiniPointLedger.TxType.CONSUME,
                    points_delta=0,
                    frozen_delta=-points,
                    amount=abs(row.amount),
                    note="支付成功确认积分抵扣",
                    created_by=by_user,
                    updated_by=by_user,
                )
        row.status = SaleMiniOrderAdjustment.Status.CONFIRMED
        row.confirmed_at = now
        row.updated_by = by_user
        row.save(update_fields=["status", "confirmed_at", "updated_by", "updated_at"])


@transaction.atomic
def release_adjustments(mapping, by_user=None, *, reverse_confirmed=False):
    now = timezone.now()
    allowed_statuses = [SaleMiniOrderAdjustment.Status.LOCKED]
    if reverse_confirmed:
        allowed_statuses.append(SaleMiniOrderAdjustment.Status.CONFIRMED)
    rows = list(
        mapping.adjustments.select_for_update().filter(status__in=allowed_statuses)
    )
    for row in rows:
        if row.adjustment_type == SaleMiniOrderAdjustment.AdjustmentType.COUPON:
            coupon = (
                SaleMiniCoupon.objects.select_for_update()
                .filter(pk=row.source_id)
                .first()
            )
            if coupon and coupon.status in {
                SaleMiniCoupon.Status.LOCKED,
                SaleMiniCoupon.Status.USED,
            }:
                coupon.status = (
                    SaleMiniCoupon.Status.EXPIRED
                    if coupon.expires_at and coupon.expires_at <= now
                    else SaleMiniCoupon.Status.AVAILABLE
                )
                coupon.locked_mapping = None
                coupon.used_mapping = None
                coupon.locked_at = None
                coupon.used_at = None
                coupon.updated_by = by_user
                coupon.save(
                    update_fields=[
                        "status",
                        "locked_mapping",
                        "used_mapping",
                        "locked_at",
                        "used_at",
                        "updated_by",
                        "updated_at",
                    ]
                )
        if row.adjustment_type == SaleMiniOrderAdjustment.AdjustmentType.POINTS:
            points = int(row.source_id or 0)
            if points > 0:
                if row.status == SaleMiniOrderAdjustment.Status.LOCKED:
                    tx_type = SaleMiniPointLedger.TxType.RELEASE
                    points_delta = points
                    frozen_delta = -points
                    note = "订单取消释放冻结积分"
                else:
                    tx_type = SaleMiniPointLedger.TxType.REFUND
                    points_delta = points
                    frozen_delta = 0
                    note = "订单退款退回积分"
                SaleMiniPointLedger.objects.create(
                    owner=mapping.owner,
                    customer=mapping.customer,
                    buyer_user=mapping.buyer_user,
                    mapping=mapping,
                    tx_no=_code("SMP"),
                    tx_type=tx_type,
                    points_delta=points_delta,
                    frozen_delta=frozen_delta,
                    amount=abs(row.amount),
                    note=note,
                    created_by=by_user,
                    updated_by=by_user,
                )
        row.status = (
            SaleMiniOrderAdjustment.Status.REVERSED
            if row.status == SaleMiniOrderAdjustment.Status.CONFIRMED
            else SaleMiniOrderAdjustment.Status.RELEASED
        )
        row.released_at = now
        row.updated_by = by_user
        row.save(update_fields=["status", "released_at", "updated_by", "updated_at"])


def create_distribution_record(mapping, referrer_id=None, by_user=None):
    if not referrer_id:
        return None
    if mapping.buyer_user_id and int(referrer_id) == mapping.buyer_user_id:
        raise ValidationError({"referrer_buyer_id": "分销推荐人不能是下单人自己。"})
    referrer = MiniProgramUser.objects.filter(
        owner=mapping.owner,
        pk=referrer_id,
        is_active=True,
    ).first()
    if not referrer:
        raise ValidationError({"referrer_buyer_id": "分销推荐人无效。"})
    rate = Decimal(
        str(getattr(settings, "SALE_MINI_DISTRIBUTION_COMMISSION_RATE", "0"))
    )
    if rate <= 0:
        return None
    base_amount = _money(mapping.payable_amount)
    commission_amount = _money(base_amount * rate)
    if commission_amount <= 0:
        return None
    return SaleMiniDistributionRecord.objects.create(
        owner=mapping.owner,
        customer=mapping.customer,
        buyer_user=mapping.buyer_user,
        referrer=referrer,
        mapping=mapping,
        commission_rate=rate,
        base_amount=base_amount,
        commission_amount=commission_amount,
        created_by=by_user,
        updated_by=by_user,
    )


def confirm_distribution(mapping):
    record = getattr(mapping, "distribution_record", None)
    if record and not record.confirmed_at:
        record.confirmed_at = timezone.now()
        record.save(update_fields=["confirmed_at", "updated_at"])


def reverse_distribution(mapping):
    record = getattr(mapping, "distribution_record", None)
    if record and record.status != SaleMiniDistributionRecord.Status.REVERSED:
        record.status = SaleMiniDistributionRecord.Status.REVERSED
        record.reversed_at = timezone.now()
        record.save(update_fields=["status", "reversed_at", "updated_at"])
