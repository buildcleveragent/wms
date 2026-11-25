"""Read-only pre-close review of daily scheduler coverage and contract evidence."""

from collections import defaultdict
from datetime import date
from itertools import groupby

from django.db.models import Q

from allapp.billing.enums import MetricType, PeriodStatus
from allapp.billing.models import (
    BillingJobRun,
    BillingMetricDaily,
    BillingPeriod,
    BillingServiceContract,
)

from .integrity import build_close_readiness
from .metrics import legacy_metric_action_count, metric_job_coverage


def _coverage_evidence(job):
    if job is None:
        return "无调度记录", None
    summary = job.summary
    coverage = metric_job_coverage(summary)
    if coverage is not None:
        if summary.get("had_single_metric_run") is True:
            reason = "曾执行单项"
        else:
            reason = "逐项执行记录"
        return reason, coverage

    if job.status in (BillingJobRun.Status.SUCCESS, BillingJobRun.Status.WARNING):
        count = legacy_metric_action_count(summary)
        if count == 1:
            return "旧记录最近一次只处理一项", None
        if count is not None and 1 < count < len(MetricType.values):
            return "旧记录最近一次只处理部分项", None
    return "历史执行范围待核", None


def _collect_day_evidence(scope, date_from, date_to):
    # Jobs alone omit direct metric generation; contracts can require dates with no rows at all.
    days = defaultdict(dict)
    jobs = BillingJobRun.objects.filter(
        **scope,
        job_name=BillingJobRun.JobName.DAILY_METRIC_GENERATION,
        service_date__range=(date_from, date_to),
    ).only("owner_id", "warehouse_id", "service_date", "status", "summary")
    for job in jobs.iterator():
        days[job.owner_id, job.warehouse_id][job.service_date] = {"job": job}
    metrics = BillingMetricDaily.objects.filter(**scope, service_date__range=(date_from, date_to))
    for metric in (
        metrics.order_by("metric_type")
        .values(
            "owner_id",
            "warehouse_id",
            "service_date",
            "metric_type",
            "value",
            "source",
            "source_quality",
        )
        .iterator()
    ):
        by_date = days[metric["owner_id"], metric["warehouse_id"]]
        evidence = by_date.setdefault(metric["service_date"], {})
        evidence.setdefault("metrics", []).append(
            {key: metric[key] for key in ("metric_type", "value", "source", "source_quality")}
        )

    contracts = BillingServiceContract.objects.filter(
        **scope, source_type="DAILY_METRIC", is_active=True, effective_from__lte=date_to
    ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=date_from))
    for owner, warehouse, start, end in contracts.values_list(
        "owner_id", "warehouse_id", "effective_from", "effective_to"
    ).iterator():
        for ordinal in range(
            max(start, date_from).toordinal(), min(end or date_to, date_to).toordinal() + 1
        ):
            day = days[owner, warehouse].setdefault(date.fromordinal(ordinal), {})
            day["contract_count"] = day.get("contract_count", 0) + 1
    return days


def _review_window(owner, warehouse, window, by_date):
    # A contract requires at most one metric or corresponding event per service day.
    sample_limit = max(10, sum(by_date[d].get("contract_count", 0) for d in window))
    start_date, end_date = min(window), max(window)
    readiness = build_close_readiness(
        owner_id=owner,
        warehouse_id=warehouse,
        start_date=start_date,
        end_date=end_date,
        sample_limit=sample_limit,
    )
    for blocker in readiness["blockers"]:
        if blocker["code"] != "BILLING_CONTRACT_METRIC_EVENT_MISSING":
            continue
        for sample in blocker["samples"]:
            window[sample["service_date"]]["contract_gaps"].append(
                {
                    "metric_type": sample["metric_type"],
                    "missing": "event" if "id" in sample else "metric",
                }
            )
    return {
        "owner_id": owner,
        "warehouse_id": warehouse,
        "date_from": start_date,
        "date_to": end_date,
        "blockers": readiness["blockers"],
    }


def audit_daily_metrics(date_from, date_to, *, owner_id=None, warehouse_id=None):
    """Review evidenced scope/days in the requested open or not-yet-created periods."""
    scope = {}
    if owner_id is not None:
        scope["owner_id"] = owner_id
    if warehouse_id is not None:
        scope["warehouse_id"] = warehouse_id
    days = _collect_day_evidence(scope, date_from, date_to)

    periods = BillingPeriod.objects.filter(
        **scope, start_date__lte=date_to, end_date__gte=date_from
    )
    periods_by_scope = defaultdict(list)
    for period in periods.only("owner_id", "warehouse_id", "start_date", "end_date", "status"):
        periods_by_scope[period.owner_id, period.warehouse_id].append(period)

    rows = []
    checks = []
    for (owner, warehouse), by_date in sorted(days.items()):
        open_days = []
        for service_date, evidence in sorted(by_date.items()):
            period = next(
                (
                    p
                    for p in periods_by_scope[owner, warehouse]
                    if p.start_date <= service_date <= p.end_date
                ),
                None,
            )
            if period and period.status != PeriodStatus.OPEN:
                continue
            job = evidence.get("job")
            reason, coverage = _coverage_evidence(job)
            row = {
                "owner_id": owner,
                "warehouse_id": warehouse,
                "service_date": service_date,
                "period_id": period.pk if period else None,
                "job_status": job.status if job else None,
                "evidence": reason,
                "metric_coverage": coverage,
                "missing_metric_types": (
                    sorted(set(MetricType.values) - coverage.keys())
                    if coverage is not None
                    else None
                ),
                "retry_metric_types": (
                    job.summary.get("retry_metric_types", []) if coverage is not None else None
                ),
                "stored_metrics": evidence.get("metrics", []),
                "contract_gaps": [],
            }
            rows.append(row)
            open_days.append(row)

        # Reuse the actual close checks, once per contiguous span, excluding closed days.
        for _, span in groupby(
            enumerate(open_days), key=lambda item: item[1]["service_date"].toordinal() - item[0]
        ):
            window = {row["service_date"]: row for _, row in span}
            checks.append(_review_window(owner, warehouse, window, by_date))

    priority = {
        "曾执行单项": 0,
        "旧记录最近一次只处理一项": 0,
        "旧记录最近一次只处理部分项": 1,
        "历史执行范围待核": 2,
        "无调度记录": 2,
    }
    rows.sort(
        key=lambda row: (
            priority.get(
                row["evidence"],
                1 if row["missing_metric_types"] or row["retry_metric_types"] else 3,
            ),
            row["owner_id"],
            row["warehouse_id"],
            row["service_date"],
        )
    )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "close_checks": checks,
        "note": "缺项为 null 表示执行范围无法还原。指标或事件缺失须结合合同费率和应计流水核实金额。",
    }
