# allapp/billing/services.py
from allapp.outbound.models import OutboundOrderLine
import datetime
import time
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple, Dict, Set, Iterable
import importlib
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Sum, Q, F, Prefetch, ExpressionWrapper, DecimalField, Case, When
from django.utils import timezone
from allapp.billing.enums import (
    ChargeType, CalcMethod, AccrualStatus, PeriodStatus, BillStatus,
    MetricType, LadderMode, CapMode, BundleScope, BundleType
)
from allapp.billing.models import (
    BillingRule, BillingRuleTier, BillingEvent, BillingAccrual,
    BillingPeriod, Bill, BillLine, BillingMetricDaily, BillingJobRun
)
from allapp.core.data_accuracy import reconcile_data_accuracy

# ------------------------ 基础工具 ------------------------ #
def _q(val, q="0.01"):
    return (Decimal(val)).quantize(Decimal(q), rounding=ROUND_HALF_UP)

def _days_in_month(d: datetime.date) -> int:
    return monthrange(d.year, d.month)[1]

AUTO_METRIC_SOURCE_PREFIX = "AUTO:"
SCHEDULED_METRIC_JOB_NAME = BillingJobRun.JobName.DAILY_METRIC_GENERATION

class BillingAccuracyGateError(ValueError):
    def __init__(self, *, stage: str, issue_count: int, failed_checks: Iterable[str], details=None):
        self.stage = stage
        self.issue_count = int(issue_count or 0)
        self.failed_checks = list(dict.fromkeys(failed_checks))
        self.details = details or {}
        checks_text = ", ".join(self.failed_checks[:5])
        if len(self.failed_checks) > 5:
            checks_text += ", ..."
        message = f"数据对账未通过，已阻止{stage}：发现 {self.issue_count} 个问题"
        if checks_text:
            message += f"（{checks_text}）"
        super().__init__(message)

def _billing_accuracy_gate_enabled(setting_name: str) -> bool:
    if not getattr(settings, "BILLING_RECONCILIATION_GATE_ENABLED", True):
        return False
    return getattr(settings, setting_name, True)

def _date_range(start_date: datetime.date, end_date: datetime.date):
    day_count = (end_date - start_date).days
    for offset in range(day_count + 1):
        yield start_date + datetime.timedelta(days=offset)

def _failed_check_names(result):
    failed = []
    for section_name in ("inventory", "billing"):
        section = result.get(section_name)
        if not section:
            continue
        failed.extend(check["name"] for check in section["checks"] if not check["ok"])
    return failed


def _raise_if_reconciliation_failed(*, stage: str, results):
    issue_count = sum(result["issue_count"] for _, result in results)
    if issue_count <= 0:
        return

    failed_checks = []
    scoped_results = []
    for scope_label, result in results:
        failed_checks.extend(_failed_check_names(result))
        scoped_results.append(
            {
                "scope": scope_label,
                "issue_count": result["issue_count"],
                "result": result,
            }
        )

    raise BillingAccuracyGateError(
        stage=stage,
        issue_count=issue_count,
        failed_checks=failed_checks,
        details={"results": scoped_results},
    )


def _ensure_reconciliation_for_service_date(
    *,
    stage: str,
    owner_id,
    warehouse_id,
    service_date: datetime.date,
    include_inventory: bool = True,
    include_billing: bool = True,
):
    result = reconcile_data_accuracy(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date=service_date,
        include_inventory=include_inventory,
        include_billing=include_billing,
        limit=5,
    )
    _raise_if_reconciliation_failed(
        stage=stage,
        results=[(service_date.isoformat(), result)],
    )


def _ensure_reconciliation_for_date_range(
    *,
    stage: str,
    owner_id,
    warehouse_id,
    start_date: datetime.date,
    end_date: datetime.date,
):
    results = []

    inventory_result = reconcile_data_accuracy(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        include_inventory=True,
        include_billing=False,
        limit=5,
    )
    results.append(("inventory", inventory_result))

    for service_date in _date_range(start_date, end_date):
        billing_result = reconcile_data_accuracy(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            include_inventory=False,
            include_billing=True,
            limit=5,
        )
        results.append((service_date.isoformat(), billing_result))

    _raise_if_reconciliation_failed(stage=stage, results=results)


def _ensure_reconciliation_for_period(*, stage: str, period: BillingPeriod):
    result = reconcile_data_accuracy(
        owner_id=period.owner_id,
        warehouse_id=period.warehouse_id,
        period_id=period.id,
        include_inventory=False,
        include_billing=True,
        limit=5,
    )
    _raise_if_reconciliation_failed(
        stage=stage,
        results=[(period.label, result)],
    )
#
# def _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date) -> Optional[BillingRule]:
#     qs = (BillingRule.objects
#           .filter(active=True, charge_type=charge_type, calc_method=calc_method)
#           .filter(Q(owner_id=owner_id) | Q(owner__isnull=True))
#           .filter(Q(warehouse_id=warehouse_id) | Q(warehouse__isnull=True)))
#     if service_date:
#         qs = qs.filter(Q(effective_from__isnull=True) | Q(effective_from__lte=service_date),
#                        Q(effective_to__isnull=True) | Q(effective_to__gte=service_date))
#     # 优先匹配“更具体”的规则（owner/warehouse 非空在前），再按 priority
#     return qs.order_by("owner__isnull", "warehouse__isnull", "priority", "id").first()

def _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date) -> Optional[BillingRule]:
    qs = (BillingRule.objects
          .filter(active=True, charge_type=charge_type, calc_method=calc_method)
          .filter(Q(owner_id=owner_id) | Q(owner__isnull=True))
          .filter(Q(warehouse_id=warehouse_id) | Q(warehouse__isnull=True)))
    if service_date:
        qs = qs.filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=service_date),
            Q(effective_to__isnull=True) | Q(effective_to__gte=service_date),
        )

    # 优先匹配“更具体”的规则（owner/warehouse 非空在前），再按 priority：
    # - owner_id 非空的规则排在前面（NULL 在后）
    # - warehouse_id 非空的规则排在前面（NULL 在后）
    return qs.order_by(
        F("owner_id").asc(nulls_last=True),
        F("warehouse_id").asc(nulls_last=True),
        "priority",
        "id",
    ).first()


def _event_fp(task_id, scanlog_id, charge_type, calc_method, service_date, qty):
    return f"{task_id or '-'}|{scanlog_id or '-'}|{charge_type}|{calc_method}|{service_date}|{_q(qty,'0.0001')}"

def _acc_fp(owner_id, warehouse_id, rule_id, charge_type, service_date, qty, unit_price, currency, ev_fp):
    return f"{owner_id}|{warehouse_id}|{rule_id}|{charge_type}|{service_date}|{_q(qty,'0.0001')}|{_q(unit_price,'0.0001')}|{currency}|{ev_fp}"


def _save_adjusted_accrual(accrual: BillingAccrual, new_amount: Decimal) -> None:
    new_amount = max(Decimal("0.00"), _q(new_amount, "0.01"))
    if Decimal(accrual.amount) == new_amount:
        return

    accrual.amount = new_amount
    accrual.tax_amount = (
        _q(accrual.amount * (accrual.rule.tax_rate or 0), "0.01")
        if accrual.rule.taxable else Decimal("0.00")
    )
    if accrual.quantity and accrual.quantity > 0:
        accrual.unit_price = _q((accrual.amount / accrual.quantity), "0.0001")
    accrual.save(update_fields=["amount", "tax_amount", "unit_price"])


def _period_bundle_rule_queryset(period: BillingPeriod, bundle_key: str):
    return (
        BillingRule.objects
        .filter(
            active=True,
            bundle_key=bundle_key,
            bundle_scope=BundleScope.PER_PERIOD,
            bundle_price__isnull=False,
        )
        .filter(Q(owner_id=period.owner_id) | Q(owner__isnull=True))
        .filter(Q(warehouse_id=period.warehouse_id) | Q(warehouse__isnull=True))
        .filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=period.end_date),
            Q(effective_to__isnull=True) | Q(effective_to__gte=period.start_date),
        )
    )


def _select_bundle_rule_for_period(period: BillingPeriod, bundle_key: str, preferred_rule_ids=None) -> Optional[BillingRule]:
    qs = _period_bundle_rule_queryset(period, bundle_key)
    ordering = (
        F("owner_id").asc(nulls_last=True),
        F("warehouse_id").asc(nulls_last=True),
        "priority",
        "id",
    )

    if preferred_rule_ids:
        preferred = qs.filter(id__in=preferred_rule_ids).order_by(*ordering).first()
        if preferred:
            return preferred
    return qs.order_by(*ordering).first()


def _apply_fixed_bundle_total(accs, target_total: Decimal) -> None:
    acc_list = list(accs)
    if not acc_list:
        return

    target_total = max(Decimal("0.00"), _q(target_total, "0.01"))
    current_total = sum((Decimal(a.amount) for a in acc_list), Decimal("0.00"))
    diff = _q(target_total - current_total, "0.01")
    if diff == 0:
        return

    if diff > 0:
        last = acc_list[-1]
        _save_adjusted_accrual(last, Decimal(last.amount) + diff)
        return

    remaining = -diff
    for accrual in reversed(acc_list):
        if remaining <= 0:
            break
        reducible = min(Decimal(accrual.amount), remaining)
        if reducible <= 0:
            continue
        _save_adjusted_accrual(accrual, Decimal(accrual.amount) - reducible)
        remaining -= reducible


# ------------------------ 阶梯计价（整档/累进；按量 or 按金额费率） ------------------------ #
def _compute_fee_with_rule(rule: BillingRule, base_value: Decimal) -> Tuple[Decimal, Decimal]:
    """
    返回: (amount, effective_price_or_rate)
      - 无阶梯：amount = base * rule.unit_price
      - WHOLE：落档全量
      - INCREMENTAL：分段累进
    对“费率阶梯（percent_rate）”：base_value 传“金额”，返回的 effective 表示有效费率
    """
    bv = Decimal(base_value or 0)
    if bv <= 0:
        return (Decimal("0.00"), Decimal("0.0000"))

    tiers = list(rule.tiers.all().order_by("threshold_from"))
    if not tiers:
        amt = _q(bv * Decimal(rule.unit_price), "0.01")
        eff = _q(Decimal(rule.unit_price), "0.0001")
        return (amt, eff)

    mode = rule.ladder_mode or LadderMode.WHOLE
    is_rate = any(t.percent_rate is not None for t in tiers)

    def t_price(t: BillingRuleTier) -> Decimal:
        return Decimal(t.percent_rate) if is_rate else Decimal(t.unit_price)

    if mode == LadderMode.WHOLE:
        chosen = None
        for t in tiers:
            start = Decimal(t.threshold_from)
            end = Decimal(t.threshold_to) if t.threshold_to is not None else None
            if (bv >= start) and (end is None or bv < end):
                chosen = t
        if chosen is None:
            chosen = tiers[-1]
        p = t_price(chosen)
        amt = _q(bv * p, "0.01")
        eff = _q(p, "0.0001")
        return (amt, eff)

    # INCREMENTAL
    amt = Decimal("0")
    last = Decimal("0")
    for t in tiers:
        start = Decimal(t.threshold_from)
        end = Decimal(t.threshold_to) if t.threshold_to is not None else None
        if bv <= start:
            break
        upto = bv if end is None else min(bv, end)
        seg = max(Decimal("0"), upto - max(start, last))
        if seg > 0:
            amt += seg * t_price(t)
            last = upto
    amt = _q(amt, "0.01")
    eff = _q((amt / bv) if bv > 0 else Decimal("0"), "0.0001")
    return (amt, eff)


# ------------------------ 封顶/打包（日口径：计提时即时限额） ------------------------ #
def _sum_amount_rule_day(rule_id, owner_id, warehouse_id, d):
    agg = (BillingAccrual.objects
           .filter(rule_id=rule_id, owner_id=owner_id, warehouse_id=warehouse_id, service_date=d)
           .aggregate(s=Sum("amount"))["s"])
    return Decimal(agg or 0)

def _sum_amount_bundle_day(bundle_key, owner_id, warehouse_id, d):
    if not bundle_key:
        return Decimal(0)
    agg = (BillingAccrual.objects
           .filter(bundle_key=bundle_key, owner_id=owner_id, warehouse_id=warehouse_id, service_date=d)
           .aggregate(s=Sum("amount"))["s"])
    return Decimal(agg or 0)

def _apply_caps_bundles_day(rule: BillingRule, owner_id, warehouse_id, service_date, draft_amount: Decimal) -> Decimal:
    """
    应用“按天”的封顶价与打包上限（打包 FIXED 只在账期阶段处理）。
    应用顺序（与我们讨论一致）：阶梯 → 封顶/打包(日) → 最低收费（注意：最低收费可能让总额超过当日上限，如需绝对不超，可将最低收费也挪到封顶前）。
    """
    amt = Decimal(draft_amount or 0)

    # 封顶（按天）
    if rule.cap_mode == CapMode.PER_DAY and rule.cap_amount:
        used = _sum_amount_rule_day(rule.id, owner_id, warehouse_id, service_date)
        remain = max(Decimal("0.00"), Decimal(rule.cap_amount) - used)
        amt = min(amt, remain)

    # 打包（按天，仅 CAP 行为）
    if rule.bundle_scope == BundleScope.PER_DAY and rule.bundle_price and rule.bundle_type == BundleType.CAP and rule.bundle_key:
        used_b = _sum_amount_bundle_day(rule.bundle_key, owner_id, warehouse_id, service_date)
        remain_b = max(Decimal("0.00"), Decimal(rule.bundle_price) - used_b)
        amt = min(amt, remain_b)

    return max(Decimal("0.00"), amt)


# ------------------------ 作业计费：来自已过账扫描日志（posting 成功） ------------------------ #
@transaction.atomic
def accrue_for_posting(task, posting_journal, by_user=None) -> Tuple[int, int]:
    from allapp.tasking.models import TaskScanLog

    logs = (TaskScanLog.objects
            .filter(task=task, status="OK", posted_at__isnull=False)
            .filter(Q(posting_journal=posting_journal))
            .select_related("task", "task_line", "owner", "warehouse"))

    mapping = {
        "RECEIVE":  (ChargeType.RECEIVE,  CalcMethod.PER_QTY_ABSDEL),
        "PUTAWAY":  (ChargeType.PUTAWAY,  CalcMethod.PER_QTY_ABSDEL),
        "RELOC":    (ChargeType.RELOC,    CalcMethod.PER_QTY_ABSDEL),
        "PICK":     (ChargeType.PICK,     CalcMethod.PER_QTY_ABSDEL),
        "REVIEW":   (ChargeType.REVIEW,   CalcMethod.PER_LINE),
        "PACK":     (ChargeType.PACK,     CalcMethod.PER_LINE),
        "LOAD":     (ChargeType.LOAD,     CalcMethod.PER_TASK),
        "DISPATCH": (ChargeType.DISPATCH, CalcMethod.PER_TASK),
        "COUNT":    (ChargeType.COUNT,    CalcMethod.PER_LINE),
    }

    created_events = created_accruals = 0
    for log in logs:
        ttype = task.task_type
        if ttype not in mapping:
            continue
        ctype, cm = mapping[ttype]

        qty = (log.qty_base_delta if log.qty_base_delta is not None else log.qty_base) or Decimal("0")
        qty_abs = abs(Decimal(qty))
        service_date = (log.posted_at or timezone.now()).date()

        ev_fp = _event_fp(task.id, log.id, ctype, cm, service_date, qty_abs)
        event, ev_created = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner=log.owner, warehouse=log.warehouse, charge_type=ctype, service_date=service_date,
                task=task, task_line=log.task_line, scan_log=log, posting_journal=posting_journal,
                quantity=_q(qty_abs, "0.0001"), quantity_uom="BASE"
            )
        )
        if ev_created:
            created_events += 1

        rule = _select_rule(log.owner_id, log.warehouse_id, ctype, cm, service_date)
        if not rule:
            continue

        qty_bill = Decimal("1") if cm in (CalcMethod.PER_TASK, CalcMethod.PER_LINE) else Decimal(event.quantity)
        amount, eff_price = _compute_fee_with_rule(rule, qty_bill)

        # 封顶/打包（日口径）
        amount = _apply_caps_bundles_day(rule, log.owner_id, log.warehouse_id, service_date, amount)

        # 最低收费（若你希望“绝对不超日上限”，可将此行移动到 _apply_caps_bundles_day 之前）
        if rule.min_charge and qty_bill > 0 and amount < rule.min_charge:
            amount = rule.min_charge

        if amount <= 0:
            continue

        eff_price = _q((amount / qty_bill) if qty_bill > 0 else eff_price, "0.0001")
        tax_amount = _q(amount * (rule.tax_rate or 0), "0.01") if rule.taxable else Decimal("0.00")

        acc_fp = _acc_fp(log.owner_id, log.warehouse_id, rule.id, ctype, service_date, qty_bill, eff_price, rule.currency, ev_fp)
        _, acc_created = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner=log.owner, warehouse=log.warehouse, period=None, charge_type=ctype, rule=rule,
                service_date=service_date, currency=rule.currency, quantity=_q(qty_bill, "0.0001"),
                unit_price=_q(eff_price, "0.0001"), amount=amount, tax_amount=tax_amount,
                status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=(rule.bundle_key or "")
            )
        )
        if acc_created:
            created_accruals += 1

    return created_events, created_accruals


# ------------------------ 仓储（日在库） ------------------------ #
@transaction.atomic
def accrue_storage_for_date(owner_id, warehouse_id, service_date: datetime.date, by_user=None) -> Tuple[int, int]:
    from allapp.inventory.models import InventoryDetail

    rule = _select_rule(owner_id, warehouse_id, ChargeType.STORAGE, CalcMethod.PER_DAY_ONHAND_BASE, service_date)
    if not rule:
        return (0, 0)

    qs = (InventoryDetail.objects
          .filter(owner_id=owner_id, warehouse_id=warehouse_id, is_active=True)
          .values("owner_id", "warehouse_id")
          .annotate(onhand=Sum("onhand_qty")))

    created_events = created_accruals = 0
    for row in qs:
        qty = Decimal(row["onhand"] or 0)
        if qty <= 0:
            continue

        ev_fp = _event_fp(None, None, ChargeType.STORAGE, CalcMethod.PER_DAY_ONHAND_BASE, service_date, qty)
        event, ev_created = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.STORAGE,
                service_date=service_date, quantity=_q(qty, "0.0001"), quantity_uom="BASE"
            )
        )
        if ev_created:
            created_events += 1

        qty_bill = qty
        amount, eff_price = _compute_fee_with_rule(rule, qty_bill)
        amount = _apply_caps_bundles_day(rule, owner_id, warehouse_id, service_date, amount)
        if rule.min_charge and qty_bill > 0 and amount < rule.min_charge:
            amount = rule.min_charge
        if amount <= 0:
            continue

        eff_price = _q((amount / qty_bill) if qty_bill > 0 else eff_price, "0.0001")
        tax_amount = _q(amount * (rule.tax_rate or 0), "0.01") if rule.taxable else Decimal("0.00")

        acc_fp = _acc_fp(owner_id, warehouse_id, rule.id, ChargeType.STORAGE, service_date, qty_bill, eff_price, rule.currency, ev_fp)
        _, acc_created = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.STORAGE, rule=rule,
                service_date=service_date, currency=rule.currency, quantity=_q(qty_bill, "0.0001"),
                unit_price=_q(eff_price, "0.0001"), amount=amount, tax_amount=tax_amount,
                status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=(rule.bundle_key or "")
            )
        )
        if acc_created:
            created_accruals += 1

    return created_events, created_accruals


# ------------------------ 日指标：面积/CBM/托盘位/订单金额 等 ------------------------ #
def _load_metric_resolver(metric_type: str):
    path = getattr(settings, f"BILLING_{metric_type}_METRIC_RESOLVER", None)
    if not path:
        return None
    mod, func = path.split(":")
    return getattr(importlib.import_module(mod), func)


def _normalize_metric_payload(metric_type: str, payload, default_source: str, default_note: str = ""):
    if payload is None:
        return None

    if isinstance(payload, dict):
        value = payload.get("value")
        source = payload.get("source", default_source)
        note = payload.get("note", default_note)
    elif isinstance(payload, tuple):
        if len(payload) == 3:
            value, source, note = payload
        elif len(payload) == 2:
            value, source = payload
            note = default_note
        elif len(payload) == 1:
            value = payload[0]
            source = default_source
            note = default_note
        else:
            raise ValueError(f"Unsupported metric payload tuple for {metric_type}: {payload!r}")
    else:
        value = payload
        source = default_source
        note = default_note

    if value is None:
        return None

    return {
        "metric_type": metric_type,
        "value": _q(Decimal(value), "0.0001"),
        "source": source,
        "note": note or "",
    }


def _current_inventory_metric_rows(owner_id, warehouse_id):
    from allapp.inventory.models import InventoryDetail
    from allapp.products.models import ProductPackage

    return list(
        InventoryDetail.objects
        .filter(owner_id=owner_id, warehouse_id=warehouse_id, is_active=True, onhand_qty__gt=0)
        .select_related("product", "location")
        .prefetch_related(
            Prefetch(
                "product__packages",
                queryset=ProductPackage.objects.order_by("-is_sales_default", "-is_pickable", "sort_order", "id"),
            )
        )
    )


def _snapshot_inventory_metric_rows(owner_id, warehouse_id, service_date):
    from allapp.inventory.models import InventorySnapshotDaily

    return list(
        InventorySnapshotDaily.objects
        .filter(
            snapshot_date=service_date,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            onhand_qty__gt=0,
        )
        .select_related("product", "location")
    )


def _ensure_inventory_snapshot_for_date(owner_id, warehouse_id, service_date):
    from allapp.inventory.snapshot_services import generate_inventory_snapshot_for_date

    return generate_inventory_snapshot_for_date(
        service_date,
        owner_id=owner_id,
        warehouse_id=warehouse_id,
    )


def _inventory_rows_use_snapshot(service_date, rows=None) -> bool:
    del rows
    return service_date < timezone.now().date()


def _inventory_metric_rows(owner_id, warehouse_id, service_date):
    today = timezone.now().date()
    if service_date < today:
        rows = _snapshot_inventory_metric_rows(owner_id, warehouse_id, service_date)
        if rows:
            return rows
        _ensure_inventory_snapshot_for_date(owner_id, warehouse_id, service_date)
        return _snapshot_inventory_metric_rows(owner_id, warehouse_id, service_date)

    return _current_inventory_metric_rows(owner_id, warehouse_id)


def _resolve_product_unit_volume(product, cache: Dict[int, Optional[Decimal]]) -> Optional[Decimal]:
    if product.id in cache:
        return cache[product.id]

    unit_volume = Decimal(product.volume) if getattr(product, "volume", None) is not None else None
    if unit_volume is None:
        for pkg in product.packages.all():
            pkg_volume = getattr(pkg, "volume_m3", None)
            qty_in_base = getattr(pkg, "qty_in_base", None)
            if pkg_volume is None or not qty_in_base:
                continue
            unit_volume = Decimal(pkg_volume) / Decimal(qty_in_base)
            break

    cache[product.id] = unit_volume
    return unit_volume


def _build_pallet_metric(owner_id, warehouse_id, service_date, *, inventory_rows=None, **kwargs):
    rows = inventory_rows if inventory_rows is not None else _inventory_metric_rows(owner_id, warehouse_id, service_date)
    occupied_locations = {row.location_id for row in rows if Decimal(row.onhand_qty or 0) > 0}
    use_snapshot = _inventory_rows_use_snapshot(service_date, rows)
    return {
        "value": Decimal(len(occupied_locations)),
        "source": (
            f"{AUTO_METRIC_SOURCE_PREFIX}SNAPSHOT_PALLET_LOC"
            if use_snapshot
            else f"{AUTO_METRIC_SOURCE_PREFIX}PALLET_OCCUPIED_LOCATION_COUNT"
        ),
        "note": "Distinct occupied inventory locations with onhand_qty > 0.",
    }


def _build_cbm_metric(owner_id, warehouse_id, service_date, *, inventory_rows=None, **kwargs):
    rows = inventory_rows if inventory_rows is not None else _inventory_metric_rows(owner_id, warehouse_id, service_date)
    if _inventory_rows_use_snapshot(service_date, rows):
        total = Decimal("0.0000")
        missing_product_ids = set()

        for row in rows:
            unit_volume = row.unit_volume_m3_snapshot
            if unit_volume is None:
                missing_product_ids.add(row.product_id)
                continue
            total += Decimal(row.onhand_qty or 0) * Decimal(unit_volume)

        notes = ["Sum(onhand_qty * unit_volume_m3_snapshot)."]
        if missing_product_ids:
            notes.append(f"{len(missing_product_ids)} snapshot products missing volume and were skipped.")
        return {
            "value": total,
            "source": f"{AUTO_METRIC_SOURCE_PREFIX}INVENTORY_SNAPSHOT_ONHAND_VOLUME",
            "note": " ".join(notes),
        }

    volume_cache: Dict[int, Optional[Decimal]] = {}
    total = Decimal("0.0000")
    missing_product_ids = set()
    package_fallback_hits = 0

    for row in rows:
        unit_volume = _resolve_product_unit_volume(row.product, volume_cache)
        if unit_volume is None:
            missing_product_ids.add(row.product_id)
            continue
        if getattr(row.product, "volume", None) is None:
            package_fallback_hits += 1
        total += Decimal(row.onhand_qty or 0) * unit_volume

    notes = ["Sum(onhand_qty * unit_volume_m3)."]
    if package_fallback_hits:
        notes.append(f"{package_fallback_hits} detail rows used package-level volume fallback.")
    if missing_product_ids:
        notes.append(f"{len(missing_product_ids)} products missing volume and were skipped.")

    return {
        "value": total,
        "source": f"{AUTO_METRIC_SOURCE_PREFIX}INVENTORY_ONHAND_VOLUME",
        "note": " ".join(notes),
    }


def _build_area_metric(owner_id, warehouse_id, service_date, *, inventory_rows=None, allow_area_fallback=False, **kwargs):
    rows = inventory_rows if inventory_rows is not None else _inventory_metric_rows(owner_id, warehouse_id, service_date)
    if _inventory_rows_use_snapshot(service_date, rows):
        occupied_locations = {row.location_id for row in rows if Decimal(row.onhand_qty or 0) > 0}
        location_areas = {}
        missing_area_locations = set()

        for row in rows:
            if Decimal(row.onhand_qty or 0) <= 0:
                continue
            if row.location_area_m2_snapshot is None:
                missing_area_locations.add(row.location_id)
                continue
            location_areas.setdefault(row.location_id, Decimal(row.location_area_m2_snapshot))

        if location_areas:
            notes = ["Sum(distinct occupied location_area_m2_snapshot)."]
            if missing_area_locations:
                notes.append(f"{len(missing_area_locations)} occupied locations missing area snapshot.")
            return {
                "value": sum(location_areas.values(), Decimal("0.0000")),
                "source": f"{AUTO_METRIC_SOURCE_PREFIX}SNAPSHOT_AREA_M2",
                "note": " ".join(notes),
            }

        if not allow_area_fallback:
            return None

        return {
            "value": Decimal(len(occupied_locations)),
            "source": f"{AUTO_METRIC_SOURCE_PREFIX}AREA_FALLBACK_LOC_COUNT",
            "note": "No snapshot location area data found; using occupied location count as area proxy.",
        }

    resolver = _load_metric_resolver(MetricType.AREA_M2)
    if resolver:
        return resolver(owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date, inventory_rows=rows)

    if not allow_area_fallback:
        return None

    occupied_locations = {row.location_id for row in rows if Decimal(row.onhand_qty or 0) > 0}
    return {
        "value": Decimal(len(occupied_locations)),
        "source": f"{AUTO_METRIC_SOURCE_PREFIX}AREA_FALLBACK_LOC_COUNT",
        "note": "No explicit area master data found; using occupied location count as area proxy.",
    }


# def _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs):
#     from allapp.outbound.models import OutboundOrderLine
#
#     amount_expr = ExpressionWrapper(
#         F("base_qty") * F("base_price"),
#         output_field=DecimalField(max_digits=18, decimal_places=2),
#     )
#     total = (
#         OutboundOrderLine.objects
#         .filter(
#             order__owner_id=owner_id,
#             order__warehouse_id=warehouse_id,
#             order__biz_date=service_date,
#             order__submit_status="SUBMITTED",
#             order__is_deleted=False,
#             is_deleted=False,
#         )
#         .exclude(order__approval_status="CANCELLED")
#         .aggregate(total=Sum(amount_expr))["total"]
#         or Decimal("0.00")
#     )
#     return {
#         "value": total,
#         "source": f"{AUTO_METRIC_SOURCE_PREFIX}OUTBOUND_ORDER_LINE_AMOUNT",
#         "note": "Sum(base_qty * base_price) for submitted outbound order lines, excluding cancelled orders.",
#     }


# 替换 allapp/billing/services.py 中负责生成 ORDER_AMT 指标的函数
# 如果你当前函数名不同，请把这段逻辑并入实际的 ORDER_AMT 构建器。




# def _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs):
#     """
#     订单金额日指标（ORDER_AMT）
#
#     当前口径：
#     - 归属日期：OutboundOrder.biz_date
#     - 仅统计已提交、未取消、价格已确认的订单
#     - 金额来源：OutboundOrder.final_order_amount
#     """
#     total = (
#         OutboundOrder.objects
#         .filter(
#             owner_id=owner_id,
#             warehouse_id=warehouse_id,
#             biz_date=service_date,
#             submit_status="SUBMITTED",
#             pricing_status=PricingStatus.CONFIRMED,
#             is_deleted=False,
#         )
#         .exclude(approval_status="CANCELLED")
#         .aggregate(total=Sum("final_order_amount"))["total"]
#         or Decimal("0.00")
#     )
#
#     return {
#         "value": total,
#         "source": f"{AUTO_METRIC_SOURCE_PREFIX}OUTBOUND_ORDER_AMOUNT",
#         "note": "Sum(final_order_amount) for submitted & pricing-confirmed outbound orders.",
#     }

# def _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs):
#     """
#     订单金额日指标：
#     - 优先使用冻结后的 final_line_amount
#     - 未冻结时回退 base_qty * base_price
#     - 仅统计已提交、未取消的订单
#     """
#     line_amount_expr = Case(
#         When(
#             final_line_amount__gt=0,
#             then=F("final_line_amount"),
#         ),
#         default=ExpressionWrapper(
#             F("base_qty") * F("base_price"),
#             output_field=DecimalField(max_digits=18, decimal_places=2),
#         ),
#         output_field=DecimalField(max_digits=18, decimal_places=2),
#     )
#     total = (
#         OutboundOrderLine.objects
#         .filter(
#             order__owner_id=owner_id,
#             order__warehouse_id=warehouse_id,
#             order__biz_date=service_date,
#             order__submit_status="SUBMITTED",
#             order__is_deleted=False,
#             is_deleted=False,
#         )
#         .exclude(order__approval_status="CANCELLED")
#         .aggregate(total=Sum(line_amount_expr))["total"]
#         or Decimal("0.00")
#     )
#
#     return {
#         "value": total,
#         "source": f"{AUTO_METRIC_SOURCE_PREFIX}OUTBOUND_ORDER_AMOUNT",
#         "note": "Sum(final_line_amount) with fallback to base_qty * base_price for submitted outbound lines, excluding cancelled orders.",
#     }




def _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs):
    """
    订单金额日指标：
    - 优先使用冻结后的 final_line_amount
    - 未冻结时回退 base_qty * base_price
    - 仅统计已提交、未取消的订单
    """
    line_amount_expr = Case(
        When(
            final_line_amount__gt=0,
            then=F("final_line_amount"),
        ),
        default=ExpressionWrapper(
            F("base_qty") * F("base_price"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    total = (
        OutboundOrderLine.objects
        .filter(
            order__owner_id=owner_id,
            order__warehouse_id=warehouse_id,
            order__biz_date=service_date,
            order__submit_status="SUBMITTED",
            order__is_deleted=False,
            is_deleted=False,
        )
        .exclude(order__approval_status="CANCELLED")
        .aggregate(total=Sum(line_amount_expr))["total"]
        or Decimal("0.00")
    )

    return {
        "value": total,
        "source": f"{AUTO_METRIC_SOURCE_PREFIX}OUTBOUND_ORDER_AMOUNT",
        "note": "Sum(final_line_amount) with fallback to base_qty * base_price for submitted outbound lines, excluding cancelled orders.",
    }

def _default_metric_payload(metric_type: str, owner_id, warehouse_id, service_date, **kwargs):
    resolver = _load_metric_resolver(metric_type)
    if resolver:
        return resolver(owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date, **kwargs)

    if metric_type == MetricType.PALLET:
        return _build_pallet_metric(owner_id, warehouse_id, service_date, **kwargs)
    if metric_type == MetricType.CBM:
        return _build_cbm_metric(owner_id, warehouse_id, service_date, **kwargs)
    if metric_type == MetricType.AREA_M2:
        return _build_area_metric(owner_id, warehouse_id, service_date, **kwargs)
    if metric_type == MetricType.ORDER_AMT:
        return _build_order_amount_metric(owner_id, warehouse_id, service_date, **kwargs)
    return None


def _auto_metric_types(metric_types: Optional[Iterable[str]] = None):
    base_types = [MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2, MetricType.ORDER_AMT]
    if metric_types is None:
        return base_types
    allowed = set(base_types)
    return [metric_type for metric_type in metric_types if metric_type in allowed]


def _is_auto_metric_row(metric: BillingMetricDaily) -> bool:
    return (metric.source or "").startswith(AUTO_METRIC_SOURCE_PREFIX)


def _recover_existing_metric_after_create_race(metric_filter, *, attempts: int = 20, delay: float = 0.05):
    for _ in range(attempts):
        existing = BillingMetricDaily.objects.select_for_update().filter(**metric_filter).first()
        if existing is not None:
            return existing
        time.sleep(delay)
    return None


def _store_generated_metric(
    *,
    owner_id,
    warehouse_id,
    service_date,
    metric_payload,
    overwrite: bool = False,
):
    metric_type = metric_payload["metric_type"]
    value = Decimal(metric_payload["value"])
    source = metric_payload["source"]
    note = metric_payload["note"]
    create_kwargs = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "metric_type": metric_type,
        "value": value,
        "source": source,
        "note": note,
    }
    metric_filter = {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "metric_type": metric_type,
    }

    existing = BillingMetricDaily.objects.select_for_update().filter(**metric_filter).first()

    if existing and not overwrite and not _is_auto_metric_row(existing):
        return {
            "metric_type": metric_type,
            "action": "skipped_manual",
            "value": existing.value,
            "source": existing.source,
            "note": existing.note,
        }

    if value <= 0:
        if existing and (_is_auto_metric_row(existing) or overwrite):
            existing.delete()
            return {
                "metric_type": metric_type,
                "action": "deleted_zero",
                "value": Decimal("0.0000"),
                "source": source,
                "note": note,
            }
        return {
            "metric_type": metric_type,
            "action": "skipped_zero",
            "value": value,
            "source": source,
            "note": note,
        }

    if not existing:
        try:
            with transaction.atomic():
                BillingMetricDaily.objects.create(**create_kwargs)
            return {
                "metric_type": metric_type,
                "action": "created",
                "value": value,
                "source": source,
                "note": note,
            }
        except (IntegrityError, ValidationError) as exc:
            existing = _recover_existing_metric_after_create_race(metric_filter)
            if existing is None and isinstance(exc, IntegrityError) and exc.__cause__ is None:
                try:
                    BillingMetricDaily(**create_kwargs).save(force_insert=True)
                except IntegrityError:
                    existing = _recover_existing_metric_after_create_race(metric_filter)
                else:
                    existing = BillingMetricDaily.objects.select_for_update().filter(**metric_filter).first()
            if existing is None:
                raise exc

    if not overwrite and not _is_auto_metric_row(existing):
        return {
            "metric_type": metric_type,
            "action": "skipped_manual",
            "value": existing.value,
            "source": existing.source,
            "note": existing.note,
        }

    if value <= 0:
        if _is_auto_metric_row(existing) or overwrite:
            existing.delete()
            return {
                "metric_type": metric_type,
                "action": "deleted_zero",
                "value": Decimal("0.0000"),
                "source": source,
                "note": note,
            }
        return {
            "metric_type": metric_type,
            "action": "skipped_zero",
            "value": value,
            "source": source,
            "note": note,
        }

    changed = (
        Decimal(existing.value) != value
        or (existing.source or "") != source
        or (existing.note or "") != note
    )
    if changed:
        existing.value = value
        existing.source = source
        existing.note = note
        existing.save(update_fields=["value", "source", "note"])
        action = "updated"
    else:
        action = "noop"

    return {
        "metric_type": metric_type,
        "action": action,
        "value": value,
        "source": source,
        "note": note,
    }


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
    selected_types = _auto_metric_types(metric_types)
    inventory_rows = None
    if any(metric_type in {MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2} for metric_type in selected_types):
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
    }

    for metric_type in selected_types:
        payload = _default_metric_payload(
            metric_type,
            owner_id,
            warehouse_id,
            service_date,
            inventory_rows=inventory_rows,
            allow_area_fallback=allow_area_fallback,
        )
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
                    "note": "No metric resolver or default builder is available for this metric type.",
                }
            )
            continue

        stored = _store_generated_metric(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            metric_payload=normalized,
            overwrite=overwrite,
        )
        counters[stored["action"]] = counters.get(stored["action"], 0) + 1
        results.append(stored)

    return {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "results": results,
        **counters,
    }


@transaction.atomic
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
        for key in ("created", "updated", "deleted_zero", "skipped_zero", "skipped_manual", "unsupported", "noop"):
            summary[key] += day_result.get(key, 0)
        current += datetime.timedelta(days=1)

    return summary


def _metric_job_run_payload(metric_summary):
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
    payload = _metric_job_run_payload(metric_summary)
    return (
        f"created={payload['created']}, updated={payload['updated']}, "
        f"deleted_zero={payload['deleted_zero']}, skipped_zero={payload['skipped_zero']}, "
        f"skipped_manual={payload['skipped_manual']}, unsupported={payload['unsupported']}, "
        f"noop={payload['noop']}"
    )


def _metric_scheduler_stale_minutes():
    return max(1, int(getattr(settings, "BILLING_METRIC_SCHEDULER_STALE_MINUTES", 180)))


@transaction.atomic
def _claim_scheduled_metric_job(owner_id, warehouse_id, service_date: datetime.date, *, force: bool = False):
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

    if job_run.status == BillingJobRun.Status.SUCCESS and not force:
        return job_run, "skipped_success"

    if (
        job_run.status == BillingJobRun.Status.RUNNING
        and job_run.started_at
        and job_run.started_at >= stale_before
        and not force
    ):
        return job_run, "skipped_running"

    job_run.status = BillingJobRun.Status.RUNNING
    job_run.attempts = (job_run.attempts or 0) + 1
    job_run.started_at = now
    job_run.finished_at = None
    job_run.message = ""
    job_run.summary = {}
    job_run.save(update_fields=["status", "attempts", "started_at", "finished_at", "message", "summary", "updated_at"])
    return job_run, "claimed"


def _finish_scheduled_metric_job(job_run: BillingJobRun, *, status: str, message: str, summary=None):
    job_run.status = status
    job_run.finished_at = timezone.now()
    job_run.message = message[:200]
    job_run.summary = summary or {}
    job_run.save(update_fields=["status", "finished_at", "message", "summary", "updated_at"])


def _run_scheduled_metric_generation_for_scope(
    owner_id,
    warehouse_id,
    service_date: datetime.date,
    *,
    metric_types: Optional[Iterable[str]] = None,
    overwrite: bool = False,
    allow_area_fallback: bool = False,
    force: bool = False,
):
    selected_types = _auto_metric_types(metric_types)
    job_run, claim_status = _claim_scheduled_metric_job(
        owner_id,
        warehouse_id,
        service_date,
        force=force,
    )
    if claim_status != "claimed":
        return {
            "owner_id": owner_id,
            "warehouse_id": warehouse_id,
            "service_date": service_date,
            "job_run_id": job_run.id,
            "status": claim_status,
            "summary": job_run.summary,
            "message": job_run.message,
        }

    try:
        if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_DAILY_ENABLED"):
            _ensure_reconciliation_for_service_date(
                stage="日调度前的计费数据生成",
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                service_date=service_date,
            )

        snapshot_summary = None
        if (
            service_date < timezone.now().date()
            and any(metric_type in {MetricType.PALLET, MetricType.CBM, MetricType.AREA_M2} for metric_type in selected_types)
        ):
            from allapp.inventory.snapshot_services import generate_inventory_snapshots_for_dates

            snapshot_summary = generate_inventory_snapshots_for_dates(
                [service_date],
                owner_id=owner_id,
                warehouse_id=warehouse_id,
            )

        metric_summary = generate_metrics_for_date(
            owner_id,
            warehouse_id,
            service_date,
            metric_types=selected_types,
            overwrite=overwrite,
            allow_area_fallback=allow_area_fallback,
        )
        if snapshot_summary is not None:
            metric_summary["inventory_snapshot"] = snapshot_summary["days"][0]
        if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_DAILY_ENABLED"):
            _ensure_reconciliation_for_service_date(
                stage="日调度后的计费数据生成",
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                service_date=service_date,
            )
    except Exception as exc:
        failure_summary = getattr(exc, "details", None) or {}
        _finish_scheduled_metric_job(
            job_run,
            status=BillingJobRun.Status.FAILED,
            message=str(exc),
            summary=failure_summary,
        )
        return {
            "owner_id": owner_id,
            "warehouse_id": warehouse_id,
            "service_date": service_date,
            "job_run_id": job_run.id,
            "status": "failed",
            "summary": failure_summary,
            "message": str(exc),
        }

    payload = _metric_job_run_payload(metric_summary)
    _finish_scheduled_metric_job(
        job_run,
        status=BillingJobRun.Status.SUCCESS,
        message=_metric_job_run_message(metric_summary),
        summary=payload,
    )
    return {
        "owner_id": owner_id,
        "warehouse_id": warehouse_id,
        "service_date": service_date,
        "job_run_id": job_run.id,
        "status": "success",
        "summary": metric_summary,
        "message": _metric_job_run_message(metric_summary),
    }


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
    dates = sorted({service_date for service_date in service_dates})

    summary = {
        "service_dates": dates,
        "runs": [],
        "scopes_total": len(dates) * len(owners) * len(warehouses),
        "success": 0,
        "failed": 0,
        "skipped_success": 0,
        "skipped_running": 0,
        "created": 0,
        "updated": 0,
        "deleted_zero": 0,
        "skipped_zero": 0,
        "skipped_manual": 0,
        "unsupported": 0,
        "noop": 0,
    }

    for service_date in dates:
        for owner in owners:
            for warehouse in warehouses:
                run_result = _run_scheduled_metric_generation_for_scope(
                    owner.id,
                    warehouse.id,
                    service_date,
                    metric_types=metric_types,
                    overwrite=overwrite,
                    allow_area_fallback=allow_area_fallback,
                    force=force,
                )
                summary["runs"].append(run_result)
                status = run_result["status"]
                if status in {"success", "failed", "skipped_success", "skipped_running"}:
                    summary[status] += 1
                if status == "success":
                    metric_summary = run_result["summary"]
                    for key in ("created", "updated", "deleted_zero", "skipped_zero", "skipped_manual", "unsupported", "noop"):
                        summary[key] += metric_summary.get(key, 0)

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
    return run_scheduled_metric_generation_for_dates(
        [service_date],
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        metric_types=metric_types,
        overwrite=overwrite,
        allow_area_fallback=allow_area_fallback,
        force=force,
    )


@transaction.atomic
def accrue_metrics_for_date(owner_id, warehouse_id, service_date: datetime.date, by_user=None) -> Tuple[int, int]:
    created_events = created_accruals = 0

    ms = BillingMetricDaily.objects.filter(owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date)
    for m in ms:
        if m.metric_type == MetricType.PALLET:
            cm, ctype = CalcMethod.PER_PALLET_DAY, ChargeType.STORAGE
        elif m.metric_type == MetricType.CBM:
            cm, ctype = CalcMethod.PER_CBM_DAY, ChargeType.STORAGE
        elif m.metric_type == MetricType.AREA_M2:
            cm, ctype = CalcMethod.PER_AREA_MONTH, ChargeType.STORAGE
        elif m.metric_type == MetricType.ORDER_AMT:
            cm, ctype = CalcMethod.PERCENT_OF_ORDER_AMOUNT, ChargeType.DISPATCH
        else:
            continue

        rule = _select_rule(owner_id, warehouse_id, ctype, cm, service_date)
        if not rule:
            continue

        qty_bill = Decimal(m.value)

        # 面积月价：先按月价阶梯求整月，再按日分摊
        if cm == CalcMethod.PER_AREA_MONTH:
            monthly_amount, monthly_eff = _compute_fee_with_rule(rule, qty_bill)
            amount = _q(Decimal(monthly_amount) / Decimal(_days_in_month(service_date)), "0.01")
            eff_price = _q(Decimal(monthly_eff) / Decimal(_days_in_month(service_date)), "0.0001")  # 记录为“日有效价”
        else:
            amount, eff_price = _compute_fee_with_rule(rule, qty_bill)

        amount = _apply_caps_bundles_day(rule, owner_id, warehouse_id, service_date, amount)
        if rule.min_charge and qty_bill > 0 and amount < rule.min_charge:
            amount = rule.min_charge
        if amount <= 0:
            continue

        eff_price = _q((amount / qty_bill) if qty_bill > 0 else eff_price, "0.0001")
        tax_amount = _q(amount * (rule.tax_rate or 0), "0.01") if rule.taxable else Decimal("0.00")

        ev_fp = _event_fp(None, None, ctype, cm, service_date, qty_bill)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ctype, service_date=service_date,
                quantity=_q(qty_bill, "0.0001"), quantity_uom="BASE"
            )
        )

        acc_fp = _acc_fp(owner_id, warehouse_id, rule.id, ctype, service_date, qty_bill, eff_price, rule.currency, ev_fp)
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ctype, rule=rule,
                service_date=service_date, currency=rule.currency, quantity=_q(qty_bill, "0.0001"),
                unit_price=_q(eff_price, "0.0001"), amount=amount, tax_amount=tax_amount,
                status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=(rule.bundle_key or "")
            )
        )
        created_events += int(ev_new)
        created_accruals += 1 if acc_new else 0

    return created_events, created_accruals


# ------------------------ 订单处理费（事实）：基于已过账的 TaskScanLog ------------------------ #
def _load_taskline_order_resolver():
    path = getattr(settings, "BILLING_TASKLINE_ORDER_RESOLVER", None)
    if not path:
        return None
    mod, func = path.split(":")
    return getattr(importlib.import_module(mod), func)

@transaction.atomic
def accrue_order_processing_from_posted(owner_id, warehouse_id, start_date, end_date, by_user=None) -> Tuple[int, int]:
    from allapp.tasking.models import TaskScanLog
    resolver = _load_taskline_order_resolver()
    rule_cache: Dict[Tuple[str, str, datetime.date], Optional[BillingRule]] = {}

    logs = (TaskScanLog.objects
            .filter(owner_id=owner_id, warehouse_id=warehouse_id, status="OK", posted_at__isnull=False)
            .filter(posted_at__date__gte=start_date, posted_at__date__lte=end_date)
            .filter(task__task_type__in=["DISPATCH", "PACK", "REVIEW", "PICK"])
            .select_related("task", "task_line"))

    order_ids: Set[Tuple[int, datetime.date]] = set()
    order_lines: Set[Tuple[int, int, datetime.date]] = set()
    parcels_by_date: Dict[datetime.date, int] = {}
    order_amount_by_date: Dict[datetime.date, Decimal] = {}
    bundle_by_date: Dict[datetime.date, str] = {}  # 可选：从 resolver 动态传入 bundle_key

    for log in logs:
        if not log.task_line or resolver is None:
            continue
        mapping = resolver(log.task_line) or {}
        svc_date = (log.posted_at or timezone.now()).date()

        for oid in mapping.get("order_ids", set()):
            order_ids.add((oid, svc_date))
        for olid in mapping.get("order_line_ids", set()):
            order_lines.add((olid[0], olid[1], svc_date))
        if mapping.get("parcels"):
            parcels_by_date[svc_date] = parcels_by_date.get(svc_date, 0) + int(mapping["parcels"])
        if mapping.get("order_amount"):
            order_amount_by_date[svc_date] = order_amount_by_date.get(svc_date, Decimal("0")) + Decimal(mapping["order_amount"])
        if mapping.get("bundle_key"):
            bundle_by_date[svc_date] = mapping["bundle_key"]

    created_events = created_accruals = 0

    def _rule_for(charge_type: str, calc_method: str, service_date: datetime.date) -> Optional[BillingRule]:
        key = (charge_type, calc_method, service_date)
        if key not in rule_cache:
            rule_cache[key] = _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date)
        return rule_cache[key]

    # PER_ORDER
    for (_oid, svc_date) in sorted(order_ids):
        rule_order = _rule_for(ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date)
        if not rule_order:
            continue
        ev_fp = _event_fp(_oid, None, ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date, 1)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.DISPATCH,
                service_date=svc_date, quantity=1, quantity_uom="ORDER"
            )
        )
        amount, eff_price = _compute_fee_with_rule(rule_order, Decimal(1))
        amount = _apply_caps_bundles_day(rule_order, owner_id, warehouse_id, svc_date, amount)
        if rule_order.min_charge and amount < rule_order.min_charge:
            amount = rule_order.min_charge
        if amount <= 0:
            continue
        eff_price = _q(amount, "0.0001")
        tax_amount = _q(amount * (rule_order.tax_rate or 0), "0.01") if rule_order.taxable else Decimal("0.00")
        bk = bundle_by_date.get(svc_date) or (rule_order.bundle_key or "")
        acc_fp = _acc_fp(owner_id, warehouse_id, rule_order.id, ChargeType.DISPATCH, svc_date, 1, eff_price, rule_order.currency, ev_fp)
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.DISPATCH, rule=rule_order,
                service_date=svc_date, currency=rule_order.currency, quantity=1, unit_price=_q(eff_price, "0.0001"),
                amount=amount, tax_amount=tax_amount, status=AccrualStatus.OPEN, event=event, created_by=by_user,
                bundle_key=bk
            )
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    # PER_ORDER_LINE
    for (_oid, _olid, svc_date) in sorted(order_lines):
        rule_line = _rule_for(ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date)
        if not rule_line:
            continue
        ev_fp = _event_fp(_oid, _olid, ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date, 1)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.DISPATCH,
                service_date=svc_date, quantity=1, quantity_uom="ORDER_LINE"
            )
        )
        amount, eff_price = _compute_fee_with_rule(rule_line, Decimal(1))
        amount = _apply_caps_bundles_day(rule_line, owner_id, warehouse_id, svc_date, amount)
        if rule_line.min_charge and amount < rule_line.min_charge:
            amount = rule_line.min_charge
        if amount <= 0:
            continue
        eff_price = _q(amount, "0.0001")
        tax_amount = _q(amount * (rule_line.tax_rate or 0), "0.01") if rule_line.taxable else Decimal("0.00")
        bk = bundle_by_date.get(svc_date) or (rule_line.bundle_key or "")
        acc_fp = _acc_fp(owner_id, warehouse_id, rule_line.id, ChargeType.DISPATCH, svc_date, 1, eff_price, rule_line.currency, ev_fp)
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.DISPATCH, rule=rule_line,
                service_date=svc_date, currency=rule_line.currency, quantity=1, unit_price=_q(eff_price, "0.0001"),
                amount=amount, tax_amount=tax_amount, status=AccrualStatus.OPEN, event=event, created_by=by_user,
                bundle_key=bk
            )
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    # PER_PARCEL
    for svc_date, cnt in sorted(parcels_by_date.items()):
        rule_parcel = _rule_for(ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date)
        if not rule_parcel or cnt <= 0:
            continue
        ev_fp = _event_fp(None, None, ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date, cnt)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.PACK,
                service_date=svc_date, quantity=_q(cnt, "0.0001"), quantity_uom="PARCEL"
            )
        )
        amount, eff_price = _compute_fee_with_rule(rule_parcel, Decimal(cnt))
        amount = _apply_caps_bundles_day(rule_parcel, owner_id, warehouse_id, svc_date, amount)
        if rule_parcel.min_charge and cnt > 0 and amount < rule_parcel.min_charge:
            amount = rule_parcel.min_charge
        if amount <= 0:
            continue
        eff_price = _q((amount / Decimal(cnt)) if cnt > 0 else eff_price, "0.0001")
        tax_amount = _q(amount * (rule_parcel.tax_rate or 0), "0.01") if rule_parcel.taxable else Decimal("0.00")
        bk = bundle_by_date.get(svc_date) or (rule_parcel.bundle_key or "")
        acc_fp = _acc_fp(owner_id, warehouse_id, rule_parcel.id, ChargeType.PACK, svc_date, cnt, eff_price, rule_parcel.currency, ev_fp)
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.PACK, rule=rule_parcel,
                service_date=svc_date, currency=rule_parcel.currency, quantity=_q(cnt, "0.0001"),
                unit_price=_q(eff_price, "0.0001"), amount=amount, tax_amount=tax_amount,
                status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=bk
            )
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    # PERCENT_OF_ORDER_AMOUNT
    by_date_amounts = dict(order_amount_by_date)
    missing_dates = []
    for n in range((end_date - start_date).days + 1):
        d = start_date + datetime.timedelta(days=n)
        if d not in by_date_amounts:
            missing_dates.append(d)
    if missing_dates:
        ms = (BillingMetricDaily.objects
              .filter(owner_id=owner_id, warehouse_id=warehouse_id, service_date__in=missing_dates, metric_type=MetricType.ORDER_AMT))
        for m in ms:
            by_date_amounts[m.service_date] = by_date_amounts.get(m.service_date, Decimal("0")) + Decimal(m.value)

    for svc_date, amt in sorted(by_date_amounts.items()):
        rule_pct = _rule_for(ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date)
        if not rule_pct:
            continue
        amt = Decimal(amt or 0)
        if amt <= 0:
            continue
        amount, eff_rate = _compute_fee_with_rule(rule_pct, amt)  # base=金额
        amount = _apply_caps_bundles_day(rule_pct, owner_id, warehouse_id, svc_date, amount)
        if rule_pct.min_charge and amount < rule_pct.min_charge:
            amount = rule_pct.min_charge
        if amount <= 0:
            continue
        eff_rate = _q((amount / amt) if amt > 0 else eff_rate, "0.0001")
        tax_amount = _q(amount * (rule_pct.tax_rate or 0), "0.01") if rule_pct.taxable else Decimal("0.00")
        ev_fp = _event_fp(None, None, ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date, amt)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.DISPATCH,
                service_date=svc_date, quantity=_q(amt, "0.01"), quantity_uom="CURRENCY"
            )
        )
        bk = bundle_by_date.get(svc_date) or (rule_pct.bundle_key or "")
        acc_fp = _acc_fp(owner_id, warehouse_id, rule_pct.id, ChargeType.DISPATCH, svc_date, amt, eff_rate, rule_pct.currency, ev_fp)
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.DISPATCH, rule=rule_pct,
                service_date=svc_date, currency=rule_pct.currency, quantity=_q(amt, "0.01"),
                unit_price=_q(eff_rate, "0.0001"), amount=amount, tax_amount=tax_amount,
                status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=bk
            )
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    return created_events, created_accruals


# ------------------------ 关账/开票 ------------------------ #
def _get_or_create_period_locked(*, owner_id, warehouse_id, label, start_date, end_date):
    try:
        period, created = BillingPeriod.objects.get_or_create(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            label=label,
            defaults=dict(start_date=start_date, end_date=end_date, status=PeriodStatus.OPEN),
        )
    except (IntegrityError, ValidationError):
        period = None
        for _ in range(20):
            period = BillingPeriod.objects.filter(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                label=label,
            ).first()
            if period is not None:
                break
            time.sleep(0.05)
        if period is None:
            raise
        created = False

    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    return period, created


@transaction.atomic
def lock_period(owner_id, warehouse_id, label, start_date, end_date) -> BillingPeriod:
    """
    关账时：
      1) 将区间内 OPEN 的应计全部挂到该 period 并置为 LOCKED；
      2) 应用“按账期口径”的封顶与打包（CAP & FIXED）。
    """
    if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_LOCK_ENABLED"):
        _ensure_reconciliation_for_date_range(
            stage="锁账",
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            start_date=start_date,
            end_date=end_date,
        )

    period, created = _get_or_create_period_locked(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        label=label,
        start_date=start_date,
        end_date=end_date,
    )
    if not created and period.status != PeriodStatus.OPEN:
        raise ValueError(f"Period {period.label} is already {period.status}.")
    if not created and (period.start_date != start_date or period.end_date != end_date):
        raise ValueError(
            f"Period {period.label} already exists with range "
            f"{period.start_date}~{period.end_date}, expected {start_date}~{end_date}."
        )

    # 这里使用批量 update 是有意为之：这批 accrual 已经按 owner/warehouse/date 范围收口，
    # 仅做 status/period 的批量状态迁移，不依赖 save()->full_clean()。
    (BillingAccrual.objects
     .filter(owner_id=owner_id, warehouse_id=warehouse_id, status=AccrualStatus.OPEN,
             period__isnull=True)
     .filter(service_date__gte=start_date, service_date__lte=end_date)
     .update(status=AccrualStatus.LOCKED, period=period))

    # --- 按账期：封顶（CapMode.PER_PERIOD） ---
    cap_rules = (BillingRule.objects
                 .filter(active=True, cap_mode=CapMode.PER_PERIOD, cap_amount__isnull=False)
                 .values_list("id", "cap_amount"))
    for rid, cap in cap_rules:
        cap = Decimal(cap or 0)
        if cap <= 0:
            continue
        accs = (BillingAccrual.objects
                .filter(period=period, rule_id=rid)
                .order_by("service_date", "id"))
        running = Decimal("0")
        for a in accs:
            allowed = max(Decimal("0"), cap - running)
            new_amt = min(Decimal(a.amount), allowed)
            _save_adjusted_accrual(a, new_amt)
            running += a.amount

    # --- 按账期：打包（BundleScope.PER_PERIOD） ---
    bundle_keys = list(
        BillingAccrual.objects
        .filter(period=period)
        .exclude(bundle_key="")
        .values_list("bundle_key", flat=True)
        .distinct()
    )

    for bk in bundle_keys:
        involved_rule_ids = list(
            BillingAccrual.objects
            .filter(period=period, bundle_key=bk)
            .values_list("rule_id", flat=True)
            .distinct()
        )
        r = _select_bundle_rule_for_period(period, bk, preferred_rule_ids=involved_rule_ids)
        if not r:
            continue
        bprice = Decimal(r.bundle_price or 0)
        if bprice <= 0:
            continue

        accs = list(
            BillingAccrual.objects
            .filter(period=period, bundle_key=bk)
            .select_related("rule")
            .order_by("service_date", "id")
        )
        total = sum((Decimal(a.amount) for a in accs), Decimal("0"))
        if total <= 0:
            continue

        if r.bundle_type == BundleType.CAP:
            running = Decimal("0")
            for a in accs:
                allowed = max(Decimal("0"), bprice - running)
                new_amt = min(Decimal(a.amount), allowed)
                _save_adjusted_accrual(a, new_amt)
                running += a.amount
        else:
            _apply_fixed_bundle_total(accs, bprice)

    period.status = PeriodStatus.CLOSED
    period.save(update_fields=["status"])
    return period


@transaction.atomic
def generate_invoice_for_period(period: BillingPeriod, invoice_no: str, issue_date=None, due_date=None) -> Bill:
    original_period = period
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status != PeriodStatus.CLOSED:
        raise ValueError("Only closed periods can be invoiced.")
    if Bill.objects.filter(period=period).exists():
        raise ValueError("Invoice already exists for this period.")
    if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_INVOICE_ENABLED"):
        _ensure_reconciliation_for_period(stage="开票", period=period)

    accs = (BillingAccrual.objects
            .filter(period=period, status=AccrualStatus.LOCKED)
            .select_related("rule")
            .order_by("service_date", "charge_type", "id"))
    if not accs.exists():
        raise ValueError("No locked accruals to invoice.")

    bill = Bill.objects.create(
        owner=period.owner, warehouse=period.warehouse, period=period,
        invoice_no=invoice_no, issue_date=issue_date or timezone.now().date(),
        due_date=due_date, currency=period.currency
    )

    subtotal = tax_total = Decimal("0.00")
    for a in accs:
        BillLine.objects.create(
            bill=bill, accrual=a, charge_type=a.charge_type, service_date=a.service_date,
            quantity=a.quantity, unit_price=a.unit_price, amount=a.amount, tax_amount=a.tax_amount,
            description=f"{a.charge_type} {a.service_date}"
        )
        subtotal += a.amount
        tax_total += a.tax_amount
        a.status = AccrualStatus.INVOICED
        a.save(update_fields=["status"])

    bill.subtotal = _q(subtotal, "0.01")
    bill.tax_total = _q(tax_total, "0.01")
    bill.total = _q(subtotal + tax_total, "0.01")
    bill.status = BillStatus.ISSUED
    bill.save(update_fields=["subtotal", "tax_total", "total", "status"])

    period.status = PeriodStatus.INVOICED
    period.save(update_fields=["status"])
    if original_period is not period:
        original_period.status = period.status
    return bill
