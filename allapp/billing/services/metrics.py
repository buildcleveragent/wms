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
from decimal import Decimal
from typing import Iterable, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from allapp.billing.enums import MetricType
from allapp.billing.models import BillingJobRun

from ._common import AUTO_METRIC_SOURCE_PREFIX, SCHEDULED_METRIC_JOB_NAME, logger
from ._metrics import (
    _auto_metric_types, _default_metric_payload, _inventory_metric_rows,
    _normalize_metric_payload, _store_generated_metric,
)
from ._reconciliation import _billing_accuracy_gate_enabled, _ensure_reconciliation_for_service_date


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
        "created": 0, "updated": 0, "deleted_zero": 0,
        "skipped_zero": 0, "skipped_manual": 0, "unsupported": 0, "noop": 0,
    }

    for metric_type in selected_types:
        # 1) 构建指标 payload（调用 _build_pallet_metric 等具体构建器）
        payload = _default_metric_payload(
            metric_type, owner_id, warehouse_id, service_date,
            inventory_rows=inventory_rows, allow_area_fallback=allow_area_fallback,
        )
        # 2) 归一化为统一格式 {metric_type, value, source, note}
        normalized = _normalize_metric_payload(
            metric_type, payload,
            default_source=f"{AUTO_METRIC_SOURCE_PREFIX}{metric_type}",
        )
        if normalized is None:
            counters["unsupported"] += 1
            results.append({
                "metric_type": metric_type, "action": "unsupported",
                "value": None, "source": None,
                "note": "No metric resolver or default builder is available for this metric type.",
            })
            continue

        # 3) 写入 DB（含去重、手动保护、零值处理等逻辑）
        stored = _store_generated_metric(
            owner_id=owner_id, warehouse_id=warehouse_id,
            service_date=service_date, metric_payload=normalized, overwrite=overwrite,
        )
        counters[stored["action"]] = counters.get(stored["action"], 0) + 1
        results.append(stored)

    logger.info(
        "generate_metrics_for_date: owner=%s warehouse=%s date=%s created=%d updated=%d",
        owner_id, warehouse_id, service_date, counters["created"], counters["updated"],
    )
    return {
        "owner_id": owner_id, "warehouse_id": warehouse_id,
        "service_date": service_date, "results": results, **counters,
    }


def generate_metrics_for_range(
    owner_id, warehouse_id, start_date: datetime.date, end_date: datetime.date, *,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False, allow_area_fallback: bool = False,
):
    """
    为日期范围内的每一天生成指标（逐日调用 generate_metrics_for_date）。

    返回包含每日结果和汇总计数器的 summary dict。
    """
    summary = {
        "owner_id": owner_id, "warehouse_id": warehouse_id,
        "start_date": start_date, "end_date": end_date, "days": [],
        "created": 0, "updated": 0, "deleted_zero": 0,
        "skipped_zero": 0, "skipped_manual": 0, "unsupported": 0, "noop": 0,
    }
    current = start_date
    while current <= end_date:
        day_result = generate_metrics_for_date(
            owner_id, warehouse_id, current,
            metric_types=metric_types, overwrite=overwrite, allow_area_fallback=allow_area_fallback,
        )
        summary["days"].append(day_result)
        for key in ("created", "updated", "deleted_zero", "skipped_zero", "skipped_manual", "unsupported", "noop"):
            summary[key] += day_result.get(key, 0)
        current += datetime.timedelta(days=1)
    return summary


# ============================================================================
# 调度器辅助函数
# ============================================================================

def _metric_scheduler_stale_minutes():
    """获取作业超时阈值（分钟）。超过此时间的 RUNNING 作业被视为僵死，可以重新 claim。"""
    return max(1, int(getattr(settings, "BILLING_METRIC_SCHEDULER_STALE_MINUTES", 180)))


def _metric_job_run_payload(metric_summary):
    """将 generate_metrics_for_date 的返回结果转为适合存入 BillingJobRun.summary 的 dict。"""
    return {
        "service_date": metric_summary["service_date"].isoformat(),
        "created": metric_summary["created"],
        "updated": metric_summary["updated"],
        "deleted_zero": metric_summary["deleted_zero"],
        "skipped_zero": metric_summary["skipped_zero"],
        "skipped_manual": metric_summary["skipped_manual"],
        "unsupported": metric_summary["unsupported"],
        "noop": metric_summary["noop"],
    }


def _metric_job_run_message(metric_summary):
    """生成用于 BillingJobRun.message 的人类可读摘要字符串。"""
    payload = _metric_job_run_payload(metric_summary)
    return (
        f"created={payload['created']}, updated={payload['updated']}, "
        f"deleted_zero={payload['deleted_zero']}, skipped_zero={payload['skipped_zero']}, "
        f"skipped_manual={payload['skipped_manual']}, unsupported={payload['unsupported']}, "
        f"noop={payload['noop']}"
    )


@transaction.atomic
def _claim_scheduled_metric_job(owner_id, warehouse_id, service_date: datetime.date, *, force: bool = False):
    """
    尝试「认领」一个调度作业（分布式锁）。

    通过 BillingJobRun + select_for_update 实现:
        - 新建: claimed → 开始执行
        - 已成功: skipped_success（不重复跑）
        - 运行中但未超时: skipped_running（另一个进程在跑）
        - 运行中已超时（默认 180 分钟）: 重新 claim（视为僵死进程）
        - force=True: 强制重跑，无视已有状态

    返回:
        (job_run, claim_status) 元组
        claim_status: "claimed" / "skipped_success" / "skipped_running"
    """
    now = timezone.now()
    stale_before = now - datetime.timedelta(minutes=_metric_scheduler_stale_minutes())
    job_run, created = (
        BillingJobRun.objects
        .select_for_update()
        .get_or_create(
            job_name=SCHEDULED_METRIC_JOB_NAME,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            defaults={
                "status": BillingJobRun.Status.RUNNING,
                "attempts": 1,
                "started_at": now,
                "message": "",
                "summary": {},
            },
        )
    )

    if created:
        return job_run, "claimed"

    # 已成功 → 跳过（除非 force）
    if job_run.status == BillingJobRun.Status.SUCCESS and not force:
        return job_run, "skipped_success"

    # 运行中且未超时 → 跳过（除非 force）
    if (
        job_run.status == BillingJobRun.Status.RUNNING
        and job_run.started_at
        and job_run.started_at >= stale_before
        and not force
    ):
        return job_run, "skipped_running"

    # 其他情况（失败/超时/force）→ 重新 claim
    job_run.status = BillingJobRun.Status.RUNNING
    job_run.attempts = (job_run.attempts or 0) + 1
    job_run.started_at = now
    job_run.finished_at = None
    job_run.message = ""
    job_run.summary = {}
    job_run.save(update_fields=["status", "attempts", "started_at", "finished_at", "message", "summary", "updated_at"])
    return job_run, "claimed"


def _finish_scheduled_metric_job(job_run: BillingJobRun, *, status: str, message: str, summary=None):
    """记录调度作业的完成状态（成功/失败）。"""
    job_run.status = status
    job_run.finished_at = timezone.now()
    job_run.message = message[:200]
    job_run.summary = summary or {}
    job_run.save(update_fields=["status", "finished_at", "message", "summary", "updated_at"])


def _run_scheduled_metric_generation_for_scope(
    owner_id, warehouse_id, service_date: datetime.date, *,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False, allow_area_fallback: bool = False, force: bool = False,
):
    """
    单个 owner × warehouse × date 的调度执行流程。

    完整步骤:
        1. 认领作业锁（如果已成功/正在运行则跳过）
        2. [可选] 数据对账门控（日调度前）
        3. [如果是历史日期] 自动生成库存快照（确保历史指标有数据来源）
        4. 调用 generate_metrics_for_date 生成四类指标
        5. [可选] 数据对账门控（日调度后）
        6. 记录成功/失败到 BillingJobRun

    任何异常都会被捕获并记录为 FAILED 状态，不会中断整个调度批次。
    """
    selected_types = _auto_metric_types(metric_types)
    job_run, claim_status = _claim_scheduled_metric_job(owner_id, warehouse_id, service_date, force=force)
    if claim_status != "claimed":
        return {
            "owner_id": owner_id, "warehouse_id": warehouse_id,
            "service_date": service_date, "job_run_id": job_run.id,
            "status": claim_status, "summary": job_run.summary, "message": job_run.message,
        }

    try:
        # 调度前对账（可通过 settings 关闭）
        if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_DAILY_ENABLED"):
            _ensure_reconciliation_for_service_date(
                stage="日调度前的计费数据生成",
                owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date,
            )

        # 历史日期 → 先确保有库存快照
        snapshot_summary = None
        if (
            service_date < timezone.now().date()
            and any(mt in {MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2} for mt in selected_types)
        ):
            from allapp.inventory.snapshot_services import generate_inventory_snapshots_for_dates
            snapshot_summary = generate_inventory_snapshots_for_dates(
                [service_date], owner_id=owner_id, warehouse_id=warehouse_id,
            )

        # 生成指标
        metric_summary = generate_metrics_for_date(
            owner_id, warehouse_id, service_date,
            metric_types=selected_types, overwrite=overwrite, allow_area_fallback=allow_area_fallback,
        )
        if snapshot_summary is not None:
            metric_summary["inventory_snapshot"] = snapshot_summary["days"][0]

        # 调度后对账
        if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_DAILY_ENABLED"):
            _ensure_reconciliation_for_service_date(
                stage="日调度后的计费数据生成",
                owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date,
            )
    except Exception as exc:
        # 异常不抛出，记录到 BillingJobRun 后继续下一个 scope
        failure_summary = getattr(exc, "details", None) or {}
        _finish_scheduled_metric_job(
            job_run, status=BillingJobRun.Status.FAILED, message=str(exc), summary=failure_summary,
        )
        logger.warning(
            "Scheduled metric generation failed: owner=%s warehouse=%s date=%s error=%s",
            owner_id, warehouse_id, service_date, exc,
        )
        return {
            "owner_id": owner_id, "warehouse_id": warehouse_id,
            "service_date": service_date, "job_run_id": job_run.id,
            "status": "failed", "summary": failure_summary, "message": str(exc),
        }

    payload = _metric_job_run_payload(metric_summary)
    _finish_scheduled_metric_job(
        job_run, status=BillingJobRun.Status.SUCCESS,
        message=_metric_job_run_message(metric_summary), summary=payload,
    )
    return {
        "owner_id": owner_id, "warehouse_id": warehouse_id,
        "service_date": service_date, "job_run_id": job_run.id,
        "status": "success", "summary": metric_summary,
        "message": _metric_job_run_message(metric_summary),
    }


# ============================================================================
# 调度器批量入口（由管理命令 billing_run_scheduler 调用）
# ============================================================================

def run_scheduled_metric_generation_for_dates(
    service_dates: Iterable[datetime.date], *,
    owner_id=None, warehouse_id=None,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False, allow_area_fallback: bool = False, force: bool = False,
):
    """
    批量调度入口：遍历 dates × owners × warehouses 三重循环执行指标生成。

    调用方:
        - billing_run_scheduler 管理命令（后台持续运行）
        - billing_generate_metrics 管理命令（手动触发）

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

    summary = {
        "service_dates": dates, "runs": [],
        "scopes_total": len(dates) * len(owners) * len(warehouses),
        "success": 0, "failed": 0, "skipped_success": 0, "skipped_running": 0,
        "created": 0, "updated": 0, "deleted_zero": 0,
        "skipped_zero": 0, "skipped_manual": 0, "unsupported": 0, "noop": 0,
    }

    # 三重循环：日期 × 货主 × 仓库，每个组合独立执行
    for service_date in dates:
        for owner in owners:
            for warehouse in warehouses:
                run_result = _run_scheduled_metric_generation_for_scope(
                    owner.id, warehouse.id, service_date,
                    metric_types=metric_types, overwrite=overwrite,
                    allow_area_fallback=allow_area_fallback, force=force,
                )
                summary["runs"].append(run_result)
                status = run_result["status"]
                if status in {"success", "failed", "skipped_success", "skipped_running"}:
                    summary[status] += 1
                if status == "success":
                    ms = run_result["summary"]
                    for key in ("created", "updated", "deleted_zero", "skipped_zero", "skipped_manual", "unsupported", "noop"):
                        summary[key] += ms.get(key, 0)

    return summary


def run_scheduled_metric_generation_for_date(
    service_date: datetime.date, *,
    owner_id=None, warehouse_id=None,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False, allow_area_fallback: bool = False, force: bool = False,
):
    """单日便捷入口，委托给 run_scheduled_metric_generation_for_dates。"""
    return run_scheduled_metric_generation_for_dates(
        [service_date], owner_id=owner_id, warehouse_id=warehouse_id,
        metric_types=metric_types, overwrite=overwrite,
        allow_area_fallback=allow_area_fallback, force=force,
    )
