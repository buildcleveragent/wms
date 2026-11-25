"""Find overdue daily billing obligations from contracts, including dates without a job."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db.models import Q

from allapp.billing.enums import DAILY_CONTRACT_METRICS, MetricType, PeriodStatus
from allapp.billing.models import BillingJobRun, BillingPeriod, BillingServiceContract
from allapp.billing.services.metrics import daily_metric_scheduler_time, metric_job_coverage

from .models import AlertCase

ALERT_TYPE = "BILLING_DAILY_METRIC_OVERDUE"
DAY = timedelta(days=1)


def _last_due_day(now, start_time, grace):
    # Subtract the grace first so a deadline can fall after midnight on the following day.
    start = now - grace
    return start.date() - (DAY if start.time() >= start_time else 2 * DAY)


def _scope_filter(keys):
    """Restrict scans to each warehouse's own contract owners."""
    owners_by_warehouse = defaultdict(set)
    for owner, warehouse, *_ in keys:
        owners_by_warehouse[warehouse].add(owner)
    scopes = Q()
    for warehouse, owners in owners_by_warehouse.items():
        scopes |= Q(warehouse_id=warehouse, owner_id__in=owners)
    return scopes


def _window_sources(first, last, start_time, grace, tzinfo, outstanding, *, tracked=None):
    contracts = BillingServiceContract.objects.filter(
        _scope_filter(tracked) if tracked is not None else Q(),
        is_active=True,
        source_type="DAILY_METRIC",
        calc_method__in=DAILY_CONTRACT_METRICS,
        effective_from__lte=last,
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=first))
    required = defaultdict(set)
    for owner, warehouse, method, start, end in contracts.values_list(
        "owner_id", "warehouse_id", "calc_method", "effective_from", "effective_to"
    ).iterator():
        start, end = max(start, first), min(end or last, last)
        if tracked is None:
            days = (date.fromordinal(n) for n in range(start.toordinal(), end.toordinal() + 1))
        else:
            days = (day for day in tracked.get((owner, warehouse), ()) if start <= day <= end)
        for day in days:
            required[owner, warehouse, day].add(DAILY_CONTRACT_METRICS[method])
    if not required:
        return

    scope = _scope_filter(required)
    closed = defaultdict(list)
    periods = BillingPeriod.objects.filter(scope, start_date__lte=last, end_date__gte=first)
    for owner, warehouse, start, end in periods.exclude(status=PeriodStatus.OPEN).values_list(
        "owner_id", "warehouse_id", "start_date", "end_date"
    ):
        closed[owner, warehouse].append((start, end))
    jobs = BillingJobRun.objects.filter(
        scope,
        job_name=BillingJobRun.JobName.DAILY_METRIC_GENERATION,
        service_date__range=(first, last),
    ).only("id", "owner_id", "warehouse_id", "service_date", "status", "summary")
    by_scope = {(row.owner_id, row.warehouse_id, row.service_date): row for row in jobs.iterator()}
    for (owner, warehouse, day), metrics in sorted(required.items()):
        if (owner, warehouse, day) not in outstanding and any(
            start <= day <= end for start, end in closed[owner, warehouse]
        ):
            continue
        job = by_scope.get((owner, warehouse, day))
        detail, reason = _metric_gap(metrics, job)
        if not reason:
            continue
        source_id = f"{owner}:{warehouse}:{day}"
        deadline = datetime.combine(day + DAY, start_time, tzinfo=tzinfo) + grace
        yield {
            "dedup_key": f"{ALERT_TYPE}:{source_id}",
            "alert_type": ALERT_TYPE,
            "source_type": "DailyBillingScope",
            "source_id": source_id,
            "owner_id": owner,
            "warehouse_id": warehouse,
            "severity": AlertCase.Severity.HIGH,
            "title": f"日计费未完成：{reason}",
            "detail": {
                "record_no": day.isoformat(),
                "service_date": day.isoformat(),
                "business_due_at": deadline.isoformat(),
                "job_id": job.pk if job else None,
                "job_status": job.status if job else None,
                **detail,
            },
        }


def _metric_gap(required, job):
    evidence = metric_job_coverage(job.summary) if job else None
    completed = set()
    retry = set()
    unsupported = set()
    if evidence is not None:
        retry = required.intersection(job.summary.get("retry_metric_types", []))
        unsupported = {
            key
            for key in required.intersection(evidence)
            if evidence[key]["action"] == "unsupported"
        }
        completed = required.intersection(evidence) - retry - unsupported
    gaps = sorted(required - completed)
    detail = {
        "required_metric_types": sorted(required),
        "completed_metric_types": sorted(completed),
        "missing_metric_types": gaps,
        "unsupported_metric_types": sorted(unsupported),
        "retry_metric_types": sorted(retry),
    }
    if not gaps:
        return detail, None
    if job is None:
        return detail, "无调度记录"
    if evidence is None:
        return detail, "缺少有效的逐项执行记录"
    labels = "、".join(str(MetricType(key).label) for key in gaps)
    if set(gaps) == unsupported:
        return detail, f"缺少计算来源（{labels}）"
    if set(gaps) == retry:
        return detail, f"待重试（{labels}）"
    return detail, f"缺少有效结果（{labels}）"


def overdue_daily_metric_sources(now, last_check):
    """Discover recent and missed deadlines, and recheck older outstanding cases by scope."""
    start_time = daily_metric_scheduler_time()
    grace = timedelta(minutes=settings.BILLING_METRIC_ALERT_GRACE_MINUTES)
    last = _last_due_day(now, start_time, grace)
    first = last - (max(1, settings.BILLING_METRIC_SCHEDULER_LOOKBACK_DAYS) - 1) * DAY
    if last_check:
        first = min(first, _last_due_day(last_check, start_time, grace) + DAY)

    # Old open cases stay under review even after leaving the scheduler catch-up window.
    outstanding = set()
    old = defaultdict(lambda: defaultdict(set))
    cases = AlertCase.objects.filter(alert_type=ALERT_TYPE).exclude(
        status__in=[AlertCase.Status.RESOLVED, AlertCase.Status.CLOSED]
    )
    for owner, warehouse, raw_day in cases.values_list(
        "owner_id", "warehouse_id", "detail__service_date"
    ):
        day = date.fromisoformat(raw_day)
        outstanding.add((owner, warehouse, day))
        if day < first:
            old[day.replace(day=1)][owner, warehouse].add(day)
    for month, tracked in sorted(old.items()):
        latest = max(max(days) for days in tracked.values())
        yield from _window_sources(
            month, latest, start_time, grace, now.tzinfo, outstanding, tracked=tracked
        )
    while first <= last:
        end = min(first + 30 * DAY, last)
        yield from _window_sources(first, end, start_time, grace, now.tzinfo, outstanding)
        first = end + DAY
