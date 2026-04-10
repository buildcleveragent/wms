# allapp/billing/services/period.py
"""
账期生命周期管理 — 关账、试算、撤销。

本模块管理 BillingPeriod 的三个核心操作:

1. **lock_period（关账）**:
   将日期范围内的 OPEN accrual 锁定到 period，应用账期口径的封顶和打包。
   状态流转: Accrual OPEN→LOCKED, Period OPEN→CLOSED

2. **preview_lock_period（试算）**:
   模拟完整的 lock 流程但不写库。返回每条 accrual 的调整前/后金额和调整原因。
   用于在关账前预览最终金额，避免不可逆操作的风险。

3. **unlock_period（撤销）**:
   - CLOSED 期（未开票）: 直接回退 — 恢复封顶/打包前的金额，LOCKED→OPEN
   - INVOICED 期（已开票）: 红冲 — 创建负数冲销 accrual，作废 Bill

设计要点:
    - lock 和 preview 共用同一套封顶/打包逻辑（_apply_period_caps/_apply_period_bundles），
      通过 adjust_fn 回调区分「写库」和「仅修改内存」
    - unlock 利用 pre_adjustment_amount 字段恢复封顶前的原始金额
    - 红冲保留完整审计轨迹，原始记录不变，新建反向 accrual
"""
import dataclasses
import datetime
from decimal import Decimal
from itertools import groupby
from typing import Optional

from django.db import transaction
from django.db.models import F, Q
from django.db.utils import IntegrityError

from allapp.billing.enums import (
    AccrualStatus, BillStatus, BundleType, CapMode, PeriodStatus,
)
from allapp.billing.models import (
    Bill, BillingAccrual, BillingPeriod, BillingRule,
)

from ._common import (
    _apply_fixed_bundle_total, _q, _save_adjusted_accrual,
    _select_bundle_rule_for_period, logger,
)
from ._reconciliation import (
    _billing_accuracy_gate_enabled,
    _ensure_reconciliation_for_date_range,
)


# ============================================================================
# Period 获取/创建辅助
# ============================================================================

def _get_or_create_period_locked(*, owner_id, warehouse_id, label, start_date, end_date):
    """
    获取或创建 BillingPeriod，并加行锁。

    处理并发创建的竞态条件:
        1. 先尝试 get_or_create
        2. 如果 IntegrityError（另一个进程刚创建了同标签的 period），则查询已有的
        3. 最后对获取到的 period 加 select_for_update 行锁

    只捕获 IntegrityError（并发冲突），不捕获 ValidationError（数据校验失败应直接抛出）。
    """
    try:
        period, created = BillingPeriod.objects.get_or_create(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            label=label,
            defaults=dict(start_date=start_date, end_date=end_date, status=PeriodStatus.OPEN),
        )
    except IntegrityError:
        period = BillingPeriod.objects.filter(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            label=label,
        ).first()
        if period is None:
            raise
        created = False

    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    return period, created


# ============================================================================
# 共享封顶/打包逻辑（lock 和 preview 共用）
#
# 设计: 通过 adjust_fn 回调模式实现代码复用。
# lock_period 传入写库的 adjust_fn；preview_lock_period 传入仅修改内存的 adjust_fn。
# 这样封顶/打包的业务逻辑只维护一份，不会出现 lock 和 preview 结果不一致的问题。
# ============================================================================

def _scoped_cap_rules(owner_id, warehouse_id, start_date, end_date):
    """
    查询适用于指定 owner/warehouse/日期范围 的 PER_PERIOD 封顶规则。

    相比原先查询全部 PER_PERIOD 规则，增加了 owner/warehouse/日期范围过滤，
    避免无关 owner 的规则被扫描到（性能优化）。
    """
    return (
        BillingRule.objects
        .filter(active=True, cap_mode=CapMode.PER_PERIOD, cap_amount__isnull=False)
        .filter(Q(owner_id=owner_id) | Q(owner__isnull=True))
        .filter(Q(warehouse_id=warehouse_id) | Q(warehouse__isnull=True))
        .filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=end_date),
            Q(effective_to__isnull=True) | Q(effective_to__gte=start_date),
        )
        .values_list("id", "cap_amount")
    )


def _apply_period_caps(period, owner_id, warehouse_id, start_date, end_date, *, adjust_fn):
    """
    应用「按账期」封顶（PER_PERIOD cap）。

    对每条 PER_PERIOD 封顶规则:
        1. 查询该规则在本 period 内的所有 accrual（按时间排序）
        2. 累加 running 总额，超过 cap_amount 的部分截断为 0
        3. 被调整的 accrual 通过 adjust_fn 回调处理

    参数:
        adjust_fn: 回调函数 adjust_fn(accrual, new_amount, reason)
            - lock_period 中: 写库
            - preview 中: 仅修改内存副本并记录原因
    """
    cap_rules = _scoped_cap_rules(owner_id, warehouse_id, start_date, end_date)
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
            if new_amt != Decimal(a.amount):
                adjust_fn(a, new_amt, f"per_period_cap:rule={rid},cap={cap}")
            running += new_amt


def _apply_period_bundles(period, *, adjust_fn):
    """
    应用「按账期」打包（PER_PERIOD bundle）。

    流程:
        1. 找到本 period 内所有有 bundle_key 的 accrual，提取不同的 bundle_key
        2. 对每个 bundle_key，匹配最优的打包规则
        3. 根据打包类型执行调整:
           - CAP: 该打包组总额不超过 bundle_price（类似封顶）
           - FIXED: 该打包组总额强制调整为恰好等于 bundle_price（多退少补）
    """
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
                if new_amt != Decimal(a.amount):
                    adjust_fn(a, new_amt, f"per_period_bundle_cap:bundle_key={bk},cap={bprice}")
                running += new_amt
        else:
            # FIXED bundle — use dedicated total-redistribution function
            for a in accs:
                adjust_fn(a, Decimal(a.amount), f"per_period_bundle_fixed:bundle_key={bk},target={bprice}")
            _apply_fixed_bundle_total(accs, bprice)


# ============================================================================
# 关账时的 accrual 去重
#
# 问题场景:
#   Day 1: 规则价格=1.00, accrue_for_posting 创建 accrual A (指纹含 "1.0000")
#   Day 2: 管理员改价为 0.80
#   Day 3: 同一过账被重触发, accrue_for_posting 创建 accrual B (指纹含 "0.8000")
#   → A 和 B 的 acc_fingerprint 不同（因为含 unit_price），都是 OPEN
#   → lock_period 如果不去重，两条都会被锁定 → 重复收费
#
# 解决: 锁定后、封顶前，按 event_id 分组检测重复，保留最新、VOID 旧的。
# ============================================================================

def _dedup_locked_accruals(period: BillingPeriod) -> int:
    """
    对刚锁定到 period 的 accrual 执行 event 级去重。

    同一个 BillingEvent 对应多条 accrual（因规则改价导致指纹不同），
    只保留 created_at 最晚的那条（最新价格），其余标记为 VOID。

    被 VOID 的 accrual 设置 is_reversal=True, reversal_of 指向保留的那条，
    保留完整的审计轨迹。

    对于 event_id=NULL 的 accrual（理论上不应出现，因为所有计费路径都创建 event），
    跳过去重，不处理。

    参数:
        period: 刚锁定的 BillingPeriod

    返回:
        被 VOID 的 accrual 数量
    """
    # 查询该 period 下所有已锁定、非冲销、有 event 关联的 accrual
    # 按 event_id 分组，同组内按 created_at 倒序（最新在前）
    accruals = list(
        BillingAccrual.objects
        .filter(period=period, status=AccrualStatus.LOCKED, is_reversal=False)
        .exclude(event__isnull=True)
        .order_by("event_id", "-created_at")
        .select_related("rule")
    )

    voided = 0
    # groupby 要求输入已按 key 排序，上面的 order_by("event_id", ...) 满足此条件
    for event_id, group in groupby(accruals, key=lambda a: a.event_id):
        group_list = list(group)
        if len(group_list) <= 1:
            # 只有一条 → 无重复，跳过
            continue

        # 保留第一条（created_at 最晚 = 最新价格）
        keep = group_list[0]
        for dup in group_list[1:]:
            dup.status = AccrualStatus.VOID
            dup.is_reversal = True
            dup.reversal_of = keep
            dup.save(update_fields=["status", "is_reversal", "reversal_of"])
            voided += 1

    return voided


# ============================================================================
# lock_period（关账）
# ============================================================================

@transaction.atomic
def lock_period(owner_id, warehouse_id, label, start_date, end_date) -> BillingPeriod:
    """
    关账：将日期范围内的 OPEN accrual 锁定到 period，并应用账期口径的封顶和打包。

    完整步骤:
        1. [可选] 数据对账门控
        2. 获取或创建 Period（并加行锁防并发）
        3. 批量将 OPEN + period=NULL 的 accrual 标记为 LOCKED，挂靠到 period
        4. 应用 PER_PERIOD 封顶（超过上限的金额截断为 0）
        5. 应用 PER_PERIOD 打包（CAP: 组总额不超上限；FIXED: 组总额强制等于打包价）
        6. Period 状态 → CLOSED

    不可逆性:
        关账后 accrual 变为 LOCKED，金额可能已被封顶/打包调整。
        如需撤销，使用 unlock_period（直接回退 或 红冲）。
        封顶前的原始金额保存在 pre_adjustment_amount 字段中。

    参数:
        label: 账期标签（如 "2026-03"），与 owner/warehouse 组成唯一约束
        start_date/end_date: 账期日期范围

    返回:
        已关账的 BillingPeriod（status=CLOSED）
    """
    if _billing_accuracy_gate_enabled("BILLING_RECONCILIATION_GATE_LOCK_ENABLED"):
        _ensure_reconciliation_for_date_range(
            stage="锁账",
            owner_id=owner_id, warehouse_id=warehouse_id,
            start_date=start_date, end_date=end_date,
        )

    period, created = _get_or_create_period_locked(
        owner_id=owner_id, warehouse_id=warehouse_id,
        label=label, start_date=start_date, end_date=end_date,
    )
    if not created and period.status != PeriodStatus.OPEN:
        raise ValueError(f"Period {period.label} is already {period.status}.")
    if not created and (period.start_date != start_date or period.end_date != end_date):
        raise ValueError(
            f"Period {period.label} already exists with range "
            f"{period.start_date}~{period.end_date}, expected {start_date}~{end_date}."
        )

    # 步骤 3: 批量将 OPEN + 尚未挂靠任何 period 的 accrual 锁定
    # 使用 QuerySet.update 而非逐条 save，因为只是做状态迁移，不需要 full_clean
    # period__isnull=True 防止误将已属于其他 period 的 accrual 抢占
    (BillingAccrual.objects
     .filter(owner_id=owner_id, warehouse_id=warehouse_id, status=AccrualStatus.OPEN,
             period__isnull=True)
     .filter(service_date__gte=start_date, service_date__lte=end_date)
     .update(status=AccrualStatus.LOCKED, period=period))

    # 步骤 4: 去重 — 同一 event 因改价产生的多条 accrual，只保留最新价格的那条
    voided_count = _dedup_locked_accruals(period)
    if voided_count:
        logger.info(
            "lock_period: voided %d duplicate accruals (repricing) for period %s",
            voided_count, label,
        )

    # 关账时的调整回调: 先保存调整前金额（用于 unlock 恢复），再执行实际调整
    def _lock_adjust_fn(accrual, new_amount, reason):
        # 首次被调整时记录原始金额，后续 unlock 时可恢复
        if accrual.pre_adjustment_amount is None:
            accrual.pre_adjustment_amount = accrual.amount
            accrual.save(update_fields=["pre_adjustment_amount"])
        _save_adjusted_accrual(accrual, new_amount)

    # 步骤 5 & 6: 应用账期封顶和打包
    _apply_period_caps(period, owner_id, warehouse_id, start_date, end_date, adjust_fn=_lock_adjust_fn)
    _apply_period_bundles(period, adjust_fn=_lock_adjust_fn)

    period.status = PeriodStatus.CLOSED
    period.save(update_fields=["status"])

    locked_count = BillingAccrual.objects.filter(period=period).count()
    logger.info(
        "lock_period: label=%s owner=%s warehouse=%s accruals_locked=%d",
        label, owner_id, warehouse_id, locked_count,
    )
    return period


# ============================================================================
# preview_lock_period（试算/干运行）
#
# 设计思路:
#   完整模拟 lock_period 的封顶/打包流程，但所有调整都在内存中进行，不写库。
#   使用轻量级 dataclass (_PreviewAccrual) 代替真实的 BillingAccrual ORM 对象，
#   记录每条 accrual 的调整前/后金额和调整原因。
#
#   与 lock_period 共用 _scoped_cap_rules 和 _select_bundle_rule_for_period，
#   确保试算结果与实际关账一致。
# ============================================================================

@dataclasses.dataclass
class _PreviewAccrual:
    """
    试算时的内存 accrual 副本。

    不使用真实的 BillingAccrual ORM 对象，因为:
    1. 不需要 DB 操作（save/update）
    2. 需要额外的 adjustment_reason 字段记录调整原因
    3. 需要同时保留 original 和 adjusted 两个版本的金额
    """
    accrual_id: int
    event_id: Optional[int]
    created_at: datetime.datetime
    charge_type: str
    service_date: datetime.date
    rule_id: int
    bundle_key: str
    quantity: Decimal
    original_amount: Decimal
    adjusted_amount: Decimal
    original_tax_amount: Decimal
    adjusted_tax_amount: Decimal
    adjustment_reason: str
    _voided_by_dedup: bool = False
    # internal refs for adjustment logic
    _rule_taxable: bool = False
    _rule_tax_rate: Decimal = Decimal("0")


def preview_lock_period(owner_id, warehouse_id, label, start_date, end_date) -> dict:
    """
    Simulate the full lock_period flow in memory without writing to DB.
    Returns per-accrual detail with original and adjusted amounts.
    """
    accruals_qs = (
        BillingAccrual.objects
        .filter(owner_id=owner_id, warehouse_id=warehouse_id,
                status=AccrualStatus.OPEN, period__isnull=True)
        .filter(service_date__gte=start_date, service_date__lte=end_date)
        .select_related("rule")
        .order_by("service_date", "id")
    )

    # Build in-memory preview copies
    previews = {}
    for a in accruals_qs:
        previews[a.id] = _PreviewAccrual(
            accrual_id=a.id,
            event_id=a.event_id,
            created_at=a.created_at,
            charge_type=a.charge_type,
            service_date=a.service_date,
            rule_id=a.rule_id,
            bundle_key=a.bundle_key,
            quantity=Decimal(a.quantity),
            original_amount=Decimal(a.amount),
            adjusted_amount=Decimal(a.amount),
            original_tax_amount=Decimal(a.tax_amount),
            adjusted_tax_amount=Decimal(a.tax_amount),
            adjustment_reason="",
            _rule_taxable=a.rule.taxable if a.rule else False,
            _rule_tax_rate=Decimal(a.rule.tax_rate or 0) if a.rule else Decimal("0"),
        )

    # --- 模拟去重（与 lock_period 中的 _dedup_locked_accruals 逻辑一致） ---
    # 按 event_id 分组，同组内只保留 created_at 最晚的，其余标记为 voided
    event_groups = {}
    for p in previews.values():
        if p.event_id is not None:
            event_groups.setdefault(p.event_id, []).append(p)
    for event_id, group in event_groups.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda x: x.created_at, reverse=True)
        for dup in group[1:]:
            dup._voided_by_dedup = True
            dup.adjusted_amount = Decimal("0.00")
            dup.adjustment_reason = f"voided_by_dedup:kept_accrual={group[0].accrual_id}"

    # 从 previews 中移除被去重的条目（它们不参与后续封顶/打包计算）
    active_previews = {k: v for k, v in previews.items() if not v._voided_by_dedup}
    voided_previews = {k: v for k, v in previews.items() if v._voided_by_dedup}

    if not active_previews and not voided_previews:
        return {
            "accrual_count": 0,
            "original_subtotal": Decimal("0.00"),
            "adjusted_subtotal": Decimal("0.00"),
            "original_tax_total": Decimal("0.00"),
            "adjusted_tax_total": Decimal("0.00"),
            "adjustments_applied": 0,
            "accruals": [],
        }

    # We need a temporary "fake" period object for bundle rule selection.
    # Try to find existing or build a transient one (not saved).
    period = BillingPeriod.objects.filter(
        owner_id=owner_id, warehouse_id=warehouse_id, label=label,
    ).first()
    if period is None:
        period = BillingPeriod(
            owner_id=owner_id, warehouse_id=warehouse_id,
            label=label, start_date=start_date, end_date=end_date,
            status=PeriodStatus.OPEN,
        )

    # Monkey-patch accrual objects for in-memory adjustment
    # We need the actual DB accrual objects for the queryset-based cap/bundle logic
    # So we use a different approach: simulate by iterating previews

    # --- 对 active_previews 模拟 PER_PERIOD 封顶 ---
    cap_rules = _scoped_cap_rules(owner_id, warehouse_id, start_date, end_date)
    for rid, cap in cap_rules:
        cap = Decimal(cap or 0)
        if cap <= 0:
            continue
        rule_previews = sorted(
            [p for p in active_previews.values() if p.rule_id == rid],
            key=lambda p: (p.service_date, p.accrual_id),
        )
        running = Decimal("0")
        for p in rule_previews:
            allowed = max(Decimal("0"), cap - running)
            new_amt = min(p.adjusted_amount, allowed)
            if new_amt != p.adjusted_amount:
                p.adjusted_amount = new_amt
                p.adjustment_reason = f"per_period_cap:rule={rid},cap={cap}"
            running += new_amt

    # --- 对 active_previews 模拟 PER_PERIOD 打包 ---
    bundle_keys_set = {p.bundle_key for p in active_previews.values() if p.bundle_key}
    for bk in bundle_keys_set:
        involved_rule_ids = list({p.rule_id for p in active_previews.values() if p.bundle_key == bk})
        r = _select_bundle_rule_for_period(period, bk, preferred_rule_ids=involved_rule_ids)
        if not r:
            continue
        bprice = Decimal(r.bundle_price or 0)
        if bprice <= 0:
            continue

        bk_previews = sorted(
            [p for p in active_previews.values() if p.bundle_key == bk],
            key=lambda p: (p.service_date, p.accrual_id),
        )
        total = sum(p.adjusted_amount for p in bk_previews)
        if total <= 0:
            continue

        if r.bundle_type == BundleType.CAP:
            running = Decimal("0")
            for p in bk_previews:
                allowed = max(Decimal("0"), bprice - running)
                new_amt = min(p.adjusted_amount, allowed)
                if new_amt != p.adjusted_amount:
                    p.adjusted_amount = new_amt
                    p.adjustment_reason = f"per_period_bundle_cap:bundle_key={bk},cap={bprice}"
                running += new_amt
        else:
            target = max(Decimal("0.00"), _q(bprice, "0.01"))
            current_total = sum(p.adjusted_amount for p in bk_previews)
            diff = _q(target - current_total, "0.01")
            if diff != 0:
                if diff > 0:
                    bk_previews[-1].adjusted_amount += diff
                    bk_previews[-1].adjustment_reason = f"per_period_bundle_fixed:bundle_key={bk},target={bprice}"
                else:
                    remaining = -diff
                    for p in reversed(bk_previews):
                        if remaining <= 0:
                            break
                        reducible = min(p.adjusted_amount, remaining)
                        if reducible > 0:
                            p.adjusted_amount -= reducible
                            p.adjustment_reason = f"per_period_bundle_fixed:bundle_key={bk},target={bprice}"
                            remaining -= reducible

    # 重算税额（active + voided 都要算）
    all_previews = {**active_previews, **voided_previews}
    for p in all_previews.values():
        if p._rule_taxable:
            p.adjusted_tax_amount = _q(p.adjusted_amount * p._rule_tax_rate, "0.01")
        else:
            p.adjusted_tax_amount = Decimal("0.00")

    accrual_list = sorted(all_previews.values(), key=lambda p: (p.service_date, p.accrual_id))
    adjustments_applied = sum(1 for p in accrual_list if p.adjustment_reason)

    logger.info(
        "preview_lock_period: label=%s owner=%s warehouse=%s accruals=%d adjustments=%d",
        label, owner_id, warehouse_id, len(accrual_list), adjustments_applied,
    )

    return {
        "accrual_count": len(accrual_list),
        "original_subtotal": _q(sum(p.original_amount for p in accrual_list), "0.01"),
        "adjusted_subtotal": _q(sum(p.adjusted_amount for p in accrual_list), "0.01"),
        "original_tax_total": _q(sum(p.original_tax_amount for p in accrual_list), "0.01"),
        "adjusted_tax_total": _q(sum(p.adjusted_tax_amount for p in accrual_list), "0.01"),
        "adjustments_applied": adjustments_applied,
        "accruals": [
            {
                "accrual_id": p.accrual_id,
                "charge_type": p.charge_type,
                "service_date": p.service_date.isoformat(),
                "rule_id": p.rule_id,
                "bundle_key": p.bundle_key,
                "quantity": p.quantity,
                "original_amount": p.original_amount,
                "adjusted_amount": p.adjusted_amount,
                "original_tax_amount": p.original_tax_amount,
                "adjusted_tax_amount": p.adjusted_tax_amount,
                "adjustment_reason": p.adjustment_reason,
            }
            for p in accrual_list
        ],
    }


# ============================================================================
# unlock_period（撤销关账）
#
# 两种模式:
#   1. CLOSED（未开票）→ 直接回退: 恢复金额、LOCKED→OPEN、Period→OPEN
#   2. INVOICED（已开票）→ 红冲: 保留原始记录，创建负数冲销 accrual，作废 Bill
#
# 红冲 vs 直接回退的选择依据:
#   已开票的数据可能已同步到外部财务系统或交付给客户，不能修改原始记录。
#   红冲通过创建「镜像反向记录」实现逻辑撤销，保留完整的审计轨迹。
# ============================================================================

@transaction.atomic
def unlock_period(period: BillingPeriod, *, by_user=None, reason: str = "") -> dict:
    """
    撤销关账 — 根据 period 状态自动选择直接回退或红冲。

    参数:
        period: 要撤销的 BillingPeriod 实例
        by_user: 操作人（记录到冲销 accrual 的 created_by）
        reason: 撤销原因（记录到日志）

    返回:
        dict，包含 action ("direct_rollback" / "red_reversal") 和操作统计

    异常:
        ValueError: period 状态为 OPEN（无需撤销）
    """
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)

    if period.status == PeriodStatus.OPEN:
        raise ValueError("Period is already OPEN, nothing to unlock.")

    if period.status == PeriodStatus.CLOSED:
        return _unlock_closed_period(period, by_user=by_user, reason=reason)

    if period.status == PeriodStatus.INVOICED:
        return _unlock_invoiced_period(period, by_user=by_user, reason=reason)

    raise ValueError(f"Cannot unlock period with status {period.status}.")


def _unlock_closed_period(period: BillingPeriod, *, by_user=None, reason: str = "") -> dict:
    """
    直接回退: 将 CLOSED（未开票）的 period 恢复为 OPEN。

    步骤:
        1. 确认没有活跃的 Bill（已 VOID 的 Bill 不阻止回退）
        2. 遍历所有 LOCKED accrual:
           - 如果有 pre_adjustment_amount（封顶/打包前的原始金额），恢复之
           - 重算 tax_amount 和 unit_price
           - 状态 LOCKED → OPEN，移除 period 关联
        3. Period 状态 CLOSED → OPEN
    """
    if Bill.objects.filter(period=period).exclude(status=BillStatus.VOID).exists():
        raise ValueError("Period has an active bill. Use red-reversal via INVOICED status instead.")

    accruals = BillingAccrual.objects.filter(period=period, status=AccrualStatus.LOCKED).select_related("rule")
    reverted = 0
    for a in accruals:
        if a.pre_adjustment_amount is not None and Decimal(a.pre_adjustment_amount) != Decimal(a.amount):
            a.amount = a.pre_adjustment_amount
            a.tax_amount = (
                _q(a.amount * (a.rule.tax_rate or 0), "0.01")
                if a.rule.taxable else Decimal("0.00")
            )
            if a.quantity and a.quantity > 0:
                a.unit_price = _q(a.amount / a.quantity, "0.0001")
        a.status = AccrualStatus.OPEN
        a.period = None
        a.pre_adjustment_amount = None
        a.save(update_fields=["amount", "tax_amount", "unit_price", "status", "period", "pre_adjustment_amount"])
        reverted += 1

    period.status = PeriodStatus.OPEN
    period.save(update_fields=["status"])

    logger.info(
        "unlock_period (rollback): period=%s accruals_reverted=%d reason=%s",
        period.label, reverted, reason,
    )
    return {
        "action": "direct_rollback",
        "period_id": period.id,
        "period_status": period.status,
        "accruals_reverted": reverted,
        "reversal_accruals_created": 0,
        "bill_voided": None,
    }


def _unlock_invoiced_period(period: BillingPeriod, *, by_user=None, reason: str = "") -> dict:
    """
    红冲: 对已开票的 period 创建反向冲销记录。

    原则: 原始记录完全不动，通过创建「负数镜像」实现逻辑撤销。

    步骤:
        1. 找到关联的 Bill，状态设为 VOID
        2. 对每条 INVOICED accrual 创建一条冲销记录:
           - amount = -原金额, tax_amount = -原税额, unit_price = -原单价
           - is_reversal=True, reversal_of=原记录
           - status=VOID, acc_fingerprint=原指纹+"|REV"
        3. 原始 accrual 和 period 状态保持不变（审计轨迹完整）

    使用 bulk_create(ignore_conflicts=True) 防止重复红冲时指纹冲突报错。
    """
    bill = Bill.objects.filter(period=period).exclude(status=BillStatus.VOID).first()
    bill_voided = None
    if bill:
        bill.status = BillStatus.VOID
        bill.save(update_fields=["status"])
        bill_voided = bill.invoice_no

    accruals = (
        BillingAccrual.objects
        .filter(period=period, status=AccrualStatus.INVOICED)
        .select_related("rule")
    )

    reversal_accruals = []
    for a in accruals:
        reversal_accruals.append(BillingAccrual(
            owner_id=a.owner_id,
            warehouse_id=a.warehouse_id,
            period=period,
            charge_type=a.charge_type,
            rule=a.rule,
            service_date=a.service_date,
            currency=a.currency,
            quantity=a.quantity,
            unit_price=-Decimal(a.unit_price),
            amount=-Decimal(a.amount),
            tax_amount=-Decimal(a.tax_amount),
            status=AccrualStatus.VOID,
            event=a.event,
            bundle_key=a.bundle_key,
            acc_fingerprint=f"{a.acc_fingerprint}|REV",
            created_by=by_user,
            is_reversal=True,
            reversal_of=a,
        ))

    created = BillingAccrual.objects.bulk_create(reversal_accruals, ignore_conflicts=True)
    created_count = len(created)

    logger.info(
        "unlock_period (red-reversal): period=%s reversals_created=%d bill_voided=%s reason=%s",
        period.label, created_count, bill_voided, reason,
    )
    return {
        "action": "red_reversal",
        "period_id": period.id,
        "period_status": period.status,
        "accruals_reverted": 0,
        "reversal_accruals_created": created_count,
        "bill_voided": bill_voided,
    }
