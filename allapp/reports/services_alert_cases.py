"""Materialize dynamic data-quality alerts into a small workflow ledger."""

from django.db import transaction
from django.utils import timezone

from allapp.billing.enums import AccrualStatus, PricingStatus, SourceQuality
from allapp.billing.models import BillingAccrual, BillingEvent, BillingJobRun

from .models import AlertCase, AlertCaseHistory


def _sources():
    for event in BillingEvent.objects.filter(
        pricing_status__in=[PricingStatus.PENDING, PricingStatus.UNPRICED]
    ).iterator():
        yield {
            "dedup_key": f"UNPRICED_BILLING_EVENT:{event.pk}",
            "alert_type": "UNPRICED_BILLING_EVENT",
            "source_type": "BillingEvent",
            "source_id": str(event.pk),
            "owner_id": event.owner_id,
            "warehouse_id": event.warehouse_id,
            "severity": AlertCase.Severity.HIGH,
            "title": "计费事件尚未定价",
            "detail": {
                "service_date": str(event.service_date),
                "reason": event.pricing_reason,
            },
        }
    for accrual in (
        BillingAccrual.objects.filter(source_quality=SourceQuality.APPROXIMATE)
        .exclude(status=AccrualStatus.VOID)
        .iterator()
    ):
        yield {
            "dedup_key": f"APPROXIMATE_BILLING_ACCRUAL:{accrual.pk}",
            "alert_type": "APPROXIMATE_BILLING_DATA",
            "source_type": "BillingAccrual",
            "source_id": str(accrual.pk),
            "owner_id": accrual.owner_id,
            "warehouse_id": accrual.warehouse_id,
            "severity": AlertCase.Severity.CRITICAL,
            "title": "应计使用近似库存来源",
            "detail": {
                "service_date": str(accrual.service_date),
                "note": accrual.source_note,
            },
        }
    for job in BillingJobRun.objects.filter(
        status__in=["FAILED", "WARNING"]
    ).iterator():
        yield {
            "dedup_key": f"BILLING_JOB:{job.pk}",
            "alert_type": "BILLING_JOB_FAILURE",
            "source_type": "BillingJobRun",
            "source_id": str(job.pk),
            "owner_id": job.owner_id,
            "warehouse_id": job.warehouse_id,
            "severity": (
                AlertCase.Severity.HIGH
                if job.status == "FAILED"
                else AlertCase.Severity.WARNING
            ),
            "title": "计费作业失败" if job.status == "FAILED" else "计费作业存在风险",
            "detail": {"service_date": str(job.service_date), "message": job.message},
        }


@transaction.atomic
def sync_alert_cases():
    now = timezone.now()
    seen = set()
    created = reopened = resolved = 0
    managed_types = {
        "UNPRICED_BILLING_EVENT",
        "APPROXIMATE_BILLING_DATA",
        "BILLING_JOB_FAILURE",
    }
    for source in _sources():
        key = source.pop("dedup_key")
        seen.add(key)
        case, was_created = AlertCase.objects.select_for_update().get_or_create(
            dedup_key=key,
            defaults={**source, "first_seen_at": now, "last_seen_at": now},
        )
        if was_created:
            created += 1
            AlertCaseHistory.objects.create(
                case=case, action="DISCOVER", to_status=case.status
            )
            continue
        before = case.status
        case.last_seen_at = now
        case.detail = source["detail"]
        case.title = source["title"]
        case.severity = source["severity"]
        if case.status in [AlertCase.Status.RESOLVED, AlertCase.Status.CLOSED]:
            case.status = AlertCase.Status.OPEN
            case.resolved_at = None
            case.closed_at = None
            reopened += 1
            AlertCaseHistory.objects.create(
                case=case, action="REOPEN", from_status=before, to_status=case.status
            )
        case.save()
    stale = (
        AlertCase.objects.select_for_update()
        .filter(alert_type__in=managed_types)
        .exclude(dedup_key__in=seen)
        .exclude(status__in=[AlertCase.Status.RESOLVED, AlertCase.Status.CLOSED])
    )
    for case in stale:
        before = case.status
        case.status = AlertCase.Status.RESOLVED
        case.resolved_at = now
        case.save(update_fields=["status", "resolved_at", "updated_at"])
        AlertCaseHistory.objects.create(
            case=case, action="AUTO_RESOLVE", from_status=before, to_status=case.status
        )
        resolved += 1
    return {
        "seen": len(seen),
        "created": created,
        "reopened": reopened,
        "resolved": resolved,
    }
