"""Non-configurable billing integrity gates and safe event repricing."""

import datetime

from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from allapp.billing.enums import AccrualStatus, PricingStatus, SourceQuality
from allapp.billing.models import (
    BillingAccrual,
    BillingEvent,
    BillingMetricDaily,
    BillingPeriod,
    BillingServiceContract,
)
from allapp.tasking.models import WmsTask

from ._common import (
    _acc_fp,
    _compute_fee_with_rule,
    _finalize_daily_price,
    _q,
    _select_rule,
)


class BillingCloseBlocked(ValueError):
    def __init__(self, readiness):
        self.readiness = readiness
        super().__init__("Billing period is not ready to close or invoice.")


def _blocker(code, queryset, fields, detail):
    count = queryset.count()
    if not count:
        return None
    return {
        "code": code,
        "count": count,
        "detail": detail,
        "samples": list(queryset.values(*fields)[:10]),
    }


def build_close_readiness(
    *, owner_id, warehouse_id, start_date, end_date, for_invoice=False
):
    event_scope = BillingEvent.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date__range=(start_date, end_date),
    )
    accrual_scope = BillingAccrual.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date__range=(start_date, end_date),
    )
    metric_scope = BillingMetricDaily.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date__range=(start_date, end_date),
    )
    blockers = [
        _blocker(
            "BILLING_EVENT_PRICING_INCOMPLETE",
            event_scope.filter(
                pricing_status__in=[PricingStatus.PENDING, PricingStatus.UNPRICED]
            ),
            (
                "id",
                "service_date",
                "charge_type",
                "calc_method",
                "pricing_status",
                "pricing_reason",
            ),
            "存在待处理或未定价计费事件。",
        ),
        _blocker(
            "BILLING_EVENT_ACCRUAL_MISSING",
            event_scope.filter(pricing_status=PricingStatus.ACCRUED).exclude(
                billingaccrual__status__in=[
                    AccrualStatus.OPEN,
                    AccrualStatus.LOCKED,
                    AccrualStatus.INVOICED,
                ]
            ),
            ("id", "service_date", "charge_type", "calc_method"),
            "已计费事件没有有效非 VOID 应计。",
        ),
        _blocker(
            "BILLING_NO_CHARGE_EVIDENCE_MISSING",
            event_scope.filter(pricing_status=PricingStatus.NO_CHARGE).filter(
                Q(pricing_rule__isnull=True)
                | Q(pricing_reason="")
                | Q(pricing_detail={})
            ),
            ("id", "service_date", "charge_type", "calc_method"),
            "零费用事件缺少规则、原因或计价明细。",
        ),
        _blocker(
            "APPROXIMATE_BILLING_DATA",
            accrual_scope.filter(source_quality=SourceQuality.APPROXIMATE).exclude(
                status=AccrualStatus.VOID
            ),
            ("id", "service_date", "charge_type", "source_note"),
            "存在近似来源应计，必须先使用可信快照重建。",
        ),
        _blocker(
            "APPROXIMATE_BILLING_METRIC",
            metric_scope.filter(source_quality=SourceQuality.APPROXIMATE),
            ("id", "service_date", "metric_type", "source"),
            "存在近似来源计费指标。",
        ),
        _blocker(
            "LOCKED_ACCRUAL_WITHOUT_PERIOD",
            accrual_scope.filter(status=AccrualStatus.LOCKED, period__isnull=True),
            ("id", "owner_id", "warehouse_id", "service_date"),
            "存在历史遗留的无账期 LOCKED 应计。",
        ),
    ]
    contracts = BillingServiceContract.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        is_active=True,
        effective_from__lte=end_date,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start_date))
    missing_task_samples = []
    missing_task_count = 0
    for contract in contracts.filter(source_type="TASK"):
        task_scope = WmsTask.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            task_type=contract.charge_type,
            posting_status=WmsTask.PostingStatus.POSTED,
            posted_at__date__range=(
                max(start_date, contract.effective_from),
                min(end_date, contract.effective_to or end_date),
            ),
        ).exclude(
            billingevent__charge_type=contract.charge_type,
            billingevent__calc_method=contract.calc_method,
        )
        missing_task_count += task_scope.count()
        if len(missing_task_samples) < 10:
            missing_task_samples.extend(
                list(
                    task_scope.values("id", "task_no", "task_type", "posted_at")[
                        : 10 - len(missing_task_samples)
                    ]
                )
            )
    if missing_task_count:
        blockers.append(
            {
                "code": "BILLING_CONTRACT_EVENT_MISSING",
                "count": missing_task_count,
                "detail": "服务合同要求计费的已过账任务缺少计费事件。",
                "samples": missing_task_samples,
            }
        )
    metric_map = {
        "PER_PALLET_DAY": "PALLET",
        "PER_CBM_DAY": "CBM",
        "PER_AREA_MONTH": "AREA_M2",
        "PERCENT_OF_ORDER_AMOUNT": "ORDER_AMT",
    }
    missing_metric_count = 0
    missing_metric_samples = []
    for contract in contracts.filter(source_type="DAILY_METRIC"):
        metric_type = metric_map.get(contract.calc_method)
        if not metric_type:
            continue
        range_start = max(start_date, contract.effective_from)
        range_end = min(end_date, contract.effective_to or end_date)
        metric_rows = BillingMetricDaily.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            metric_type=metric_type,
            service_date__range=(range_start, range_end),
        )
        existing_dates = set(metric_rows.values_list("service_date", flat=True))
        cursor = range_start
        while cursor <= range_end:
            if cursor not in existing_dates:
                missing_metric_count += 1
                if len(missing_metric_samples) < 10:
                    missing_metric_samples.append(
                        {"service_date": cursor, "metric_type": metric_type}
                    )
            cursor += datetime.timedelta(days=1)
        no_event = metric_rows.exclude(
            billing_events__charge_type=contract.charge_type,
            billing_events__calc_method=contract.calc_method,
        )
        missing_metric_count += no_event.count()
        if len(missing_metric_samples) < 10:
            missing_metric_samples.extend(
                list(
                    no_event.values("id", "service_date", "metric_type")[
                        : 10 - len(missing_metric_samples)
                    ]
                )
            )
    if missing_metric_count:
        blockers.append(
            {
                "code": "BILLING_CONTRACT_METRIC_EVENT_MISSING",
                "count": missing_metric_count,
                "detail": "服务合同要求的日指标或对应计费事件缺失。",
                "samples": missing_metric_samples,
            }
        )
    if for_invoice:
        blockers.append(
            _blocker(
                "OPEN_ACCRUAL_IN_PERIOD_RANGE",
                accrual_scope.filter(status=AccrualStatus.OPEN, period__isnull=True),
                ("id", "service_date", "charge_type", "amount"),
                "开票范围内仍有未锁定 OPEN 应计。",
            )
        )
        period = BillingPeriod.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            start_date=start_date,
            end_date=end_date,
        ).first()
        if period and period.closed_at:
            blockers.append(
                _blocker(
                    "LATE_ARRIVING_BILLING_EVENT",
                    event_scope.filter(created_at__gt=period.closed_at),
                    ("id", "service_date", "charge_type", "created_at"),
                    "账期关闭后出现服务日期落在账期内的计费事件。",
                )
            )
    blockers = [item for item in blockers if item]
    return {
        "ready": not blockers,
        "blocker_count": sum(item["count"] for item in blockers),
        "blockers": blockers,
        "by_code": {item["code"]: item["count"] for item in blockers},
    }


def ensure_close_readiness(**kwargs):
    readiness = build_close_readiness(**kwargs)
    if not readiness["ready"]:
        raise BillingCloseBlocked(readiness)
    return readiness


@transaction.atomic
def reprice_unpriced_events(
    *, owner_id, warehouse_id, date_from, date_to, dry_run=True, by_user=None
):
    events = list(
        BillingEvent.objects.select_for_update()
        .filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date__range=(date_from, date_to),
            pricing_status__in=[PricingStatus.UNPRICED, PricingStatus.PENDING],
        )
        .order_by("service_date", "id")
    )
    result = {
        "scanned": len(events),
        "repriced": 0,
        "no_charge": 0,
        "still_unpriced": 0,
        "errors": [],
    }
    for event in events:
        if not event.calc_method:
            result["errors"].append({"event": event.id, "code": "CALC_METHOD_MISSING"})
            continue
        rule = _select_rule(
            event.owner_id,
            event.warehouse_id,
            event.charge_type,
            event.calc_method,
            event.service_date,
        )
        if not rule:
            result["still_unpriced"] += 1
            if not dry_run:
                event.pricing_status = PricingStatus.UNPRICED
                event.pricing_reason = "NO_ACTIVE_RULE"
                event.save(update_fields=["pricing_status", "pricing_reason"])
            continue
        raw_amount, _ = _compute_fee_with_rule(rule, Decimal(event.quantity))
        pricing = _finalize_daily_price(
            rule,
            event.owner_id,
            event.warehouse_id,
            event.service_date,
            Decimal(event.quantity),
            raw_amount,
        )
        if pricing.final_amount <= 0:
            result["no_charge"] += 1
            if not dry_run:
                event.pricing_status = PricingStatus.NO_CHARGE
                event.pricing_rule = rule
                event.pricing_reason = (
                    pricing.limit_reasons[-1] if pricing.limit_reasons else "ZERO_RATE"
                )
                event.pricing_detail = pricing.as_detail()
                from django.utils import timezone

                event.priced_at = timezone.now()
                event.save(
                    update_fields=[
                        "pricing_status",
                        "pricing_rule",
                        "pricing_reason",
                        "pricing_detail",
                        "priced_at",
                    ]
                )
            continue
        result["repriced"] += 1
        if dry_run:
            continue
        effective_price = pricing.effective_price
        fingerprint = _acc_fp(
            event.owner_id,
            event.warehouse_id,
            rule.id,
            event.charge_type,
            event.service_date,
            event.quantity,
            effective_price,
            rule.currency,
            event.event_fp,
        )
        accrual, _created = BillingAccrual.objects.get_or_create(
            acc_fingerprint=fingerprint,
            defaults={
                "owner_id": event.owner_id,
                "warehouse_id": event.warehouse_id,
                "charge_type": event.charge_type,
                "rule": rule,
                "service_date": event.service_date,
                "currency": rule.currency,
                "quantity": _q(event.quantity, "0.0001"),
                "unit_price": effective_price,
                "amount": pricing.final_amount,
                "tax_amount": (
                    _q(pricing.final_amount * (rule.tax_rate or 0), "0.01")
                    if rule.taxable
                    else Decimal("0.00")
                ),
                "status": AccrualStatus.OPEN,
                "event": event,
                "created_by": by_user,
                "bundle_key": event.bundle_key or rule.bundle_key or "",
                "source_quality": (
                    event.metric.source_quality
                    if event.metric_id
                    else SourceQuality.VERIFIED
                ),
                "source_note": (
                    f"Inherited from metric {event.metric_id}"
                    if event.metric_id
                    and event.metric.source_quality == SourceQuality.APPROXIMATE
                    else ""
                ),
            },
        )
        if accrual.status == AccrualStatus.VOID:
            result["repriced"] -= 1
            result["errors"].append(
                {"event": event.id, "code": "VOID_ACCRUAL_REQUIRES_REPAIR"}
            )
            continue
        from django.utils import timezone

        event.pricing_status = PricingStatus.ACCRUED
        event.pricing_rule = rule
        event.pricing_reason = "PRICED"
        event.pricing_detail = pricing.as_detail()
        event.priced_at = timezone.now()
        event.save(
            update_fields=[
                "pricing_status",
                "pricing_rule",
                "pricing_reason",
                "pricing_detail",
                "priced_at",
            ]
        )
    return result
