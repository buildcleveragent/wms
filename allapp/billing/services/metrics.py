# allapp/billing/services/metrics.py
"""
指标生成与调度模块。

本模块负责两件事：
1. **指标生成**: 按日/按日期范围计算 PALLET/CBM/AREA_M2/ORDER_AMT 四类指标
2. **调度器**: 后台定时执行指标生成，包含分布式作业锁、快照管理、对账门控

模块层级关系:
    metrics.py (本模块) — 公共入口和调度编排
        └→ _metrics.py — 具体的指标构建器和存储逻辑（内部模块）

调度模型:
    由管理命令 billing_run_scheduler 在后台持续运行。
    默认 UTC 1:05 触发，回看最近 3 天（可配置）。
    通过 BillingJobRun 模型 + select_for_update 实现分布式锁。

调用方:
    - views.py: BillingMetricDailyViewSet.generate / BillingPeriodViewSet.generate-metrics
    - admin.py: BillingPeriodAdmin.accrue_storage_view
    - management commands: billing_generate_metrics, billing_run_scheduler
"""

import datetime
from typing import Iterable, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from allapp.billing.enums import MetricType, PeriodStatus, SourceQuality
from allapp.billing.models import BillingJobRun, BillingPeriod

from ._common import AUTO_METRIC_SOURCE_PREFIX, SCHEDULED_METRIC_JOB_NAME, logger
from ._metrics import (
    _auto_metric_types,
    _default_metric_payload,
    _ensure_inventory_snapshot_for_date,
    _inventory_metric_rows,
    _normalize_metric_payload,
    _store_generated_metric,
)
from ._reconciliation import (
    _billing_accuracy_gate_enabled,
    _ensure_reconciliation_for_service_date,
)

# ============================================================================
# 单日 / 日期范围 指标生成
# ============================================================================


@transaction.atomic
def generate_metrics_for_date(
    owner_id,
    warehouse_id,
    service_date: datetime.date,
    *,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False,
    allow_area_fallback: bool = False,
):
    """
    为指定 owner/warehouse/date 生成四类日指标。

    流程:
        1. 确定要生成的指标类型（默认全部 4 类）
        2. 如果包含库存类指标，预加载库存数据（复用同一份数据，避免重复查询）
        3. 遍历每种指标类型:
           a. 调用对应的构建器计算指标值
           b. 归一化 payload 格式
           c. 写入 BillingMetricDaily（处理已存在/手动录入/零值等情况）
        4. 返回包含各操作计数器的 summary dict

    参数:
        metric_types: 要生成的指标类型列表，None=全部。可选值: PALLET/CBM/AREA_M2/ORDER_AMT
        overwrite: True=覆盖手动录入的指标；False=手动指标不动
        allow_area_fallback: True=面积数据缺失时用库位数替代

    返回:
        dict，包含 owner_id, warehouse_id, service_date, results(逐条明细),
        以及 created/updated/deleted_zero/skipped_* 等计数器
    """
    selected_types = _auto_metric_types(metric_types)

    # 库存类指标（PALLET/CBM/AREA）共享同一份库存数据，避免重复查询
    inventory_rows = None
    if any(mt in {MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2} for mt in selected_types):
        inventory_rows = _inventory_metric_rows(owner_id, warehouse_id, service_date)

    results = []
    counters = {
        "created": 0,
        "updated": 0,
        "deleted_zero": 0,
        "skipped_zero": 0,
        "skipped_manual": 0,
        "unsupported": 0,
        "noop": 0,
        "approximate": 0,
    }

    for metric_type in selected_types:
        # 1) 构建指标 payload（调用 _build_pallet_metric 等具体构建器）
        payload = _default_metric_payload(
            metric_type,
            owner_id,
            warehouse_id,
            service_date,
            inventory_rows=inventory_rows,
            allow_area_fallback=allow_area_fallback,
        )
        # 2) 归一化为统一格式 {metric_type, value, source, note}
        normalized = _normalize_metric_payload(
            metric_type,
            payload,
            default_source=f"{AUTO_METRIC_SOURCE_PREFIX}{metric_type}",
        )
        if normalized is None:
            counters["unsupported"] += 1
            results.append(
                {
                    "metric_type": metric_type,
                    "action": "unsupported",
                    "value": None,
                    "source": None,
                    "note": (
                        "No metric resolver or default builder is available "
                        "for this metric type."
                    ),
                }
            )
            continue

        # 3) 写入 DB（含去重、手动保护、零值处理等逻辑）
        stored = _store_generated_metric(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            metric_payload=normalized,
            overwrite=overwrite,
        )
        counters[stored["action"]] = counters.get(stored["action"], 0) + 1
        if stored.get("source_quality") == SourceQuality.APPROXIMATE:
            counters["approximate"] += 1
        results.append(stored)

    logger.info(
        "generate_metrics_for_date: owner=%s warehouse=%s date=%s created=%d updated=%d",
        owner_id,
        warehouse_id,
        service_date,
        counters["created"],
        counters["updated"],
    )
    return {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "results": results,
        **counters,
    }


def generate_metrics_for_range(
    owner_id,
    warehouse_id,
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False,
    allow_area_fallback: bool = False,
):
    """
    为日期范围内的每一天生成指标（逐日调用 generate_metrics_for_date）。

    返回包含每日结果和汇总计数器的 summary dict。
    """
    summary = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "start_date": start_date,
        "end_date": end_date,
        "days": [],
        "created": 0,
        "updated": 0,
        "deleted_zero": 0,
        "skipped_zero": 0,
        "skipped_manual": 0,
        "unsupported": 0,
        "noop": 0,
        "approximate": 0,
    }
    current = start_date
    while current <= end_date:
        day_result = generate_metrics_for_date(
            owner_id,
            warehouse_id,
            current,
            metric_types=metric_types,
            overwrite=overwrite,
            allow_area_fallback=allow_area_fallback,
        )
        summary["days"].append(day_result)
        for key in (
            "created",
            "updated",
            "deleted_zero",
            "skipped_zero",
            "skipped_manual",
            "unsupported",
            "noop",
            "approximate",
        ):
            summary[key] += day_result.get(key, 0)
        current += datetime.timedelta(days=1)
    return summary


# ============================================================================
# 调度器辅助函数
# ============================================================================


def daily_metric_scheduler_time():
    """Scheduled start in the billing service clock."""
    return datetime.time(
        hour=max(0, min(23, int(settings.BILLING_METRIC_SCHEDULER_HOUR))),
        minute=max(0, min(59, int(settings.BILLING_METRIC_SCHEDULER_MINUTE))),
    )


def _metric_scheduler_stale_minutes():
    """获取作业超时阈值（分钟）。超过此时间的 RUNNING 作业被视为僵死，可以重新 claim。"""
    return max(1, int(getattr(settings, "BILLING_METRIC_SCHEDULER_STALE_MINUTES", 180)))


_METRIC_ACTIONS = (
    "created",
    "updated",
    "deleted_zero",
    "skipped_zero",
    "skipped_manual",
    "unsupported",
    "noop",
)
_DAILY_METRIC_TYPES = tuple(_auto_metric_types())


def legacy_metric_action_count(summary):
    """Count mutually exclusive outcomes in a pre-coverage summary, if known."""
    if not isinstance(summary, dict) or "coverage_version" in summary:
        return None
    counts = [summary.get(action) for action in _METRIC_ACTIONS]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    return sum(counts)


def metric_job_coverage(summary):
    """Read per-type execution evidence; legacy or malformed summaries have unknown coverage."""
    if (
        not isinstance(summary, dict)
        or type(summary.get("coverage_version")) is not int
        or summary["coverage_version"] != 1
    ):
        return None
    coverage = summary.get("metric_coverage")
    retry_types = summary.get("retry_metric_types", [])
    if not isinstance(retry_types, list) or any(
        mt not in _DAILY_METRIC_TYPES for mt in retry_types
    ):
        return None
    if not isinstance(coverage, dict):
        return None
    for metric_type, result in coverage.items():
        if (
            metric_type not in _DAILY_METRIC_TYPES
            or not isinstance(result, dict)
            or result.get("action") not in _METRIC_ACTIONS
            or not isinstance(result.get("overwrite"), bool)
            or (
                metric_type == MetricType.AREA_M2
                and not isinstance(result.get("allow_area_fallback"), bool)
            )
        ):
            return None
    return coverage


def _metric_job_run_message(payload):
    return (
        f"created={payload['created']}, updated={payload['updated']}, "
        f"deleted_zero={payload['deleted_zero']}, skipped_zero={payload['skipped_zero']}, "
        f"skipped_manual={payload['skipped_manual']}, unsupported={payload['unsupported']}, "
        f"noop={payload['noop']}, approximate={payload['approximate']}"
    )


@transaction.atomic
def _claim_scheduled_metric_job(
    owner_id, warehouse_id, service_date, *, requested_types, overwrite, allow_area_fallback, force
):
    """Claim only the requested types without compatible, successful execution evidence."""
    now = timezone.now()
    stale_before = now - datetime.timedelta(minutes=_metric_scheduler_stale_minutes())
    scope = dict(
        job_name=SCHEDULED_METRIC_JOB_NAME,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date=service_date,
    )
    jobs = BillingJobRun.objects.select_for_update()
    try:
        job_run, created = jobs.get_or_create(**scope, defaults={"started_at": now})
    except ValidationError:
        # Model.full_clean() can observe the concurrent winner before INSERT.
        # get_or_create only recovers from the database-level uniqueness race.
        job_run = jobs.filter(**scope).first()
        if job_run is None:
            raise
        created = False
    if (
        not created
        and job_run.status == BillingJobRun.Status.RUNNING
        and job_run.started_at
        and job_run.started_at >= stale_before
    ):
        return job_run, "skipped_running", []

    previous = job_run.summary
    coverage = metric_job_coverage(previous)
    if coverage is None:
        had_single = (
            job_run.status in (BillingJobRun.Status.SUCCESS, BillingJobRun.Status.WARNING)
            and legacy_metric_action_count(previous) == 1
        )
        coverage = {}
        pending = set()
    else:
        had_single = previous.get("had_single_metric_run") is True
        pending = set(previous.get("retry_metric_types", []))

    to_run = []
    for metric_type in requested_types:
        evidence = coverage.get(metric_type, {})
        if (
            force
            or metric_type in pending
            or evidence.get("overwrite") != overwrite
            or (
                metric_type == MetricType.AREA_M2
                and evidence.get("allow_area_fallback") != allow_area_fallback
            )
        ):
            to_run.append(metric_type)
    if not created and not to_run:
        return job_run, "skipped_success", []

    # Keep previous evidence visible. Uncommitted work stays retryable after a
    # failure or takeover, even if another subset is requested in the meantime.
    pending.update(to_run)
    job_run.summary = {
        "coverage_version": 1,
        "metric_coverage": coverage,
        "requested_metric_types": requested_types,
        "missing_metric_types": [mt for mt in _DAILY_METRIC_TYPES if mt not in coverage],
        "retry_metric_types": [mt for mt in _DAILY_METRIC_TYPES if mt in pending],
        "had_single_metric_run": had_single,
    }
    job_run.status = BillingJobRun.Status.RUNNING
    if not created:
        job_run.attempts += 1
    job_run.started_at = now
    job_run.finished_at = None
    job_run.message = ""
    job_run.save(
        update_fields=[
            "status",
            "attempts",
            "started_at",
            "finished_at",
            "message",
            "summary",
            "updated_at",
        ]
    )
    return job_run, "claimed", to_run


def _finish_scheduled_metric_job(job_run, *, status, message, summary):
    """Only the worker owning the current attempt may publish an outcome."""
    now = timezone.now()
    message = message[:200]
    updated = BillingJobRun.objects.filter(
        pk=job_run.pk, status=BillingJobRun.Status.RUNNING, attempts=job_run.attempts
    ).update(status=status, finished_at=now, message=message, summary=summary, updated_at=now)
    if updated:
        job_run.status = status
        job_run.message = message
        job_run.summary = summary
    return bool(updated)


class _MetricJobClaimLost(Exception):
    """Roll back metric writes when the attempt has been superseded."""


@transaction.atomic
def _generate_claimed_metrics(job_run, to_run, *, overwrite, allow_area_fallback, periods):
    scope = {
        "owner_id": job_run.owner_id,
        "warehouse_id": job_run.warehouse_id,
        "service_date": job_run.service_date,
    }
    check_accuracy = _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_DAILY_ENABLED")
    if check_accuracy:
        _ensure_reconciliation_for_service_date(stage="日调度前的计费数据生成", **scope)
    if job_run.service_date < timezone.now().date() and any(
        mt in {MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2} for mt in to_run
    ):
        _ensure_inventory_snapshot_for_date(**scope)

    metric_summary = generate_metrics_for_date(
        job_run.owner_id,
        job_run.warehouse_id,
        job_run.service_date,
        metric_types=to_run,
        overwrite=overwrite,
        allow_area_fallback=allow_area_fallback,
    )
    if check_accuracy:
        _ensure_reconciliation_for_service_date(stage="日调度后的计费数据生成", **scope)

    # Recheck the date at commit. A period may have closed since this worker
    # claimed its job. Hold the existing period row through the metric commit.
    if periods is not None:
        period_status = periods.select_for_update().values_list("status", flat=True).first()
        if period_status is not None and period_status != PeriodStatus.OPEN:
            raise ValueError("计算期间业务日期所属账期已关账，本次指标写入已撤回。")

    coverage = job_run.summary["metric_coverage"].copy()
    results = {row["metric_type"]: row for row in metric_summary["results"]}
    if set(results) != set(to_run):
        raise ValueError("日指标生成结果与本次处理范围不符。")
    for metric_type, row in results.items():
        if row["action"] not in _METRIC_ACTIONS:
            raise ValueError(f"日指标 {metric_type} 的执行结果无法识别。")
        result = {
            "action": row["action"],
            "source": row.get("source"),
            "source_quality": row.get("source_quality"),
            "overwrite": overwrite,
        }
        if metric_type == MetricType.AREA_M2:
            result["allow_area_fallback"] = allow_area_fallback
        coverage[metric_type] = result

    payload = {
        **job_run.summary,
        **{action: metric_summary[action] for action in _METRIC_ACTIONS},
        "service_date": job_run.service_date.isoformat(),
        "metric_coverage": coverage,
        "missing_metric_types": [mt for mt in _DAILY_METRIC_TYPES if mt not in coverage],
        "retry_metric_types": [
            mt for mt in job_run.summary["retry_metric_types"] if mt not in results
        ],
        "had_single_metric_run": (
            job_run.summary["had_single_metric_run"]
            or len(job_run.summary["requested_metric_types"]) == 1
        ),
        "approximate": metric_summary.get("approximate", 0),
        "approximate_metrics": [
            {"metric_type": mt, "source": row.get("source")}
            for mt, row in results.items()
            if row.get("source_quality") == SourceQuality.APPROXIMATE
        ],
    }
    has_approximate = any(
        row.get("source_quality") == SourceQuality.APPROXIMATE for row in coverage.values()
    )
    status = BillingJobRun.Status.WARNING if has_approximate else BillingJobRun.Status.SUCCESS
    if not _finish_scheduled_metric_job(
        job_run, status=status, message=_metric_job_run_message(payload), summary=payload
    ):
        raise _MetricJobClaimLost()


def _run_scheduled_metric_generation_for_scope(
    owner_id, warehouse_id, service_date, *, metric_types, overwrite, allow_area_fallback, force
):
    periods = BillingPeriod.objects.filter(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        start_date__lte=service_date,
        end_date__gte=service_date,
    )
    if not force and periods.exclude(status=PeriodStatus.OPEN).exists():
        return {
            "owner_id": owner_id,
            "warehouse_id": warehouse_id,
            "service_date": service_date,
            "job_run_id": None,
            "status": "skipped_closed_period",
            "summary": {},
            "message": "业务日期所属账期已关账或已开票，普通日任务已跳过。",
        }

    job_run, status, to_run = _claim_scheduled_metric_job(
        owner_id,
        warehouse_id,
        service_date,
        requested_types=metric_types,
        overwrite=overwrite,
        allow_area_fallback=allow_area_fallback,
        force=force,
    )
    if status == "claimed":
        claimed_summary = job_run.summary
        try:
            _generate_claimed_metrics(
                job_run,
                to_run,
                overwrite=overwrite,
                allow_area_fallback=allow_area_fallback,
                periods=None if force else periods,
            )
            status = job_run.status.lower()
        except _MetricJobClaimLost:
            status = "skipped_running"
        except Exception as exc:
            summary = {**(getattr(exc, "details", None) or {}), **claimed_summary}
            if _finish_scheduled_metric_job(
                job_run, status=BillingJobRun.Status.FAILED, message=str(exc), summary=summary
            ):
                status = "failed"
                logger.warning(
                    "Scheduled metric generation failed: owner=%s warehouse=%s date=%s error=%s",
                    owner_id,
                    warehouse_id,
                    service_date,
                    exc,
                )
            else:
                status = "skipped_running"
        if status == "skipped_running":
            job_run.refresh_from_db(fields=["message", "summary"])
    return {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "job_run_id": job_run.id,
        "status": status,
        "summary": job_run.summary,
        "message": job_run.message,
    }


# ============================================================================
# 调度器批量入口（由管理命令 billing_run_scheduler 调用）
# ============================================================================


def run_scheduled_metric_generation_for_dates(
    service_dates: Iterable[datetime.date],
    *,
    owner_id=None,
    warehouse_id=None,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False,
    allow_area_fallback: bool = False,
    force: bool = False,
):
    """
    批量调度入口：遍历 dates × owners × warehouses 三重循环执行指标生成。

    调用方:
        - billing_run_scheduler 管理命令（后台持续运行）

    参数:
        service_dates: 要处理的日期列表
        owner_id/warehouse_id: 可选过滤，None=处理全部
        force: True=强制重跑已成功的作业

    返回:
        汇总 dict，包含 runs(逐条结果) 和全局计数器
    """
    from allapp.baseinfo.models import Owner
    from allapp.locations.models import Warehouse

    owners_qs = Owner.objects.order_by("id")
    warehouses_qs = Warehouse.objects.order_by("id")
    if owner_id:
        owners_qs = owners_qs.filter(id=owner_id)
    if warehouse_id:
        warehouses_qs = warehouses_qs.filter(id=warehouse_id)

    owners = list(owners_qs.only("id"))
    warehouses = list(warehouses_qs.only("id"))
    dates = sorted({d for d in service_dates})
    requested_types = list(dict.fromkeys(_auto_metric_types(metric_types)))

    summary = {
        "service_dates": dates,
        "runs": [],
        "scopes_total": len(dates) * len(owners) * len(warehouses),
        "success": 0,
        "warning": 0,
        "failed": 0,
        "skipped_success": 0,
        "skipped_running": 0,
        "skipped_closed_period": 0,
        "created": 0,
        "updated": 0,
        "deleted_zero": 0,
        "skipped_zero": 0,
        "skipped_manual": 0,
        "unsupported": 0,
        "noop": 0,
    }

    # 三重循环：日期 × 货主 × 仓库，每个组合独立执行
    for service_date in dates:
        for owner in owners:
            for warehouse in warehouses:
                run_result = _run_scheduled_metric_generation_for_scope(
                    owner.id,
                    warehouse.id,
                    service_date,
                    metric_types=requested_types,
                    overwrite=overwrite,
                    allow_area_fallback=allow_area_fallback,
                    force=force,
                )
                summary["runs"].append(run_result)
                status = run_result["status"]
                if status in {
                    "success",
                    "warning",
                    "failed",
                    "skipped_success",
                    "skipped_running",
                    "skipped_closed_period",
                }:
                    summary[status] += 1
                if status in {"success", "warning"}:
                    ms = run_result["summary"]
                    for key in (
                        "created",
                        "updated",
                        "deleted_zero",
                        "skipped_zero",
                        "skipped_manual",
                        "unsupported",
                        "noop",
                    ):
                        summary[key] += ms.get(key, 0)

    return summary


def run_scheduled_metric_generation_for_date(
    service_date: datetime.date,
    *,
    owner_id=None,
    warehouse_id=None,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False,
    allow_area_fallback: bool = False,
    force: bool = False,
):
    """单日便捷入口，委托给 run_scheduled_metric_generation_for_dates。"""
    return run_scheduled_metric_generation_for_dates(
        [service_date],
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        metric_types=metric_types,
        overwrite=overwrite,
        allow_area_fallback=allow_area_fallback,
        force=force,
    )
