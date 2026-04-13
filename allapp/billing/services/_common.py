# allapp/billing/services/_common.py
"""
计费系统共享基础模块。

本模块包含所有计费服务子模块共用的工具函数、常量和核心算法：

1. **精度工具** (_q, _days_in_month) — 统一的 Decimal 四舍五入，防止浮点漂移
2. **数据对账异常** (BillingAccuracyGateError) — 在关键节点拦截数据不一致
3. **规则选择引擎** (_select_rule) — 按 owner/warehouse 层级优先匹配计费规则
4. **指纹防重** (_event_fp, _acc_fp) — 生成唯一指纹，配合 get_or_create 实现幂等
5. **金额调整** (_save_adjusted_accrual, _apply_fixed_bundle_total) — 封顶/打包时修改 accrual
6. **阶梯计价** (_compute_fee_with_rule) — 支持 WHOLE（落档）和 INCREMENTAL（累进）两种模式
7. **日封顶/打包** (_apply_caps_bundles_day) — 每笔 accrual 生成时即时限额

其他子模块通过 from ._common import ... 引用这些函数。
"""
import datetime
import logging
from calendar import monthrange
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple, Iterable

from django.db.models import F, Q, Sum

from allapp.billing.enums import (
    AccrualStatus, BundleScope, BundleType, CalcMethod, CapMode, LadderMode,
)
from allapp.billing.models import (
    BillingAccrual, BillingJobRun, BillingRule, BillingRuleTier, BillingPeriod,
)

# 全局 logger，所有计费子模块共用此 logger 名称
logger = logging.getLogger("allapp.billing")

# 自动生成的指标 source 字段统一前缀，用于区分「系统自动」与「人工录入」的指标
AUTO_METRIC_SOURCE_PREFIX = "AUTO:"

# 调度器任务名称常量，对应 BillingJobRun 中的任务类型
SCHEDULED_METRIC_JOB_NAME = BillingJobRun.JobName.DAILY_METRIC_GENERATION


# ============================================================================
# 精度工具
# ============================================================================

def _q(val, q="0.01"):
    """
    统一的 Decimal 四舍五入。

    全文件所有金额 / 单价 / 税额计算最终都经过此函数对齐精度，避免浮点漂移。

    参数:
        val: 要量化的数值（int / float / Decimal / str 均可）
        q: 精度模板，如 "0.01"（分）、"0.0001"（万分位，用于单价/费率）

    用法:
        _q(123.456)          → Decimal("123.46")
        _q(0.12345, "0.0001") → Decimal("0.1235")
    """
    return (Decimal(val)).quantize(Decimal(q), rounding=ROUND_HALF_UP)


def _days_in_month(d: datetime.date) -> int:
    """
    返回指定日期所在月份的天数。

    用于面积月租按日分摊：先用整月面积走阶梯算出月总价，再除以本月天数得到日金额。
    例如 2 月 28 天和 1 月 31 天，每日单价不同，但月总价由阶梯决定。
    """
    return monthrange(d.year, d.month)[1]


# ============================================================================
# 数据对账异常
# ============================================================================

class BillingAccuracyGateError(ValueError):
    """
    数据对账门控异常。

    在关键节点（日调度前/后、锁账、开票）调用 core.data_accuracy.reconcile_data_accuracy
    进行库存/计费数据一致性检查。如果发现问题，抛出此异常阻止后续操作。

    这是一道安全闸门：发现问题就停下来，由人工排查后再继续。

    属性:
        stage: 被阻止的操作阶段（如 "锁账"、"开票"）
        issue_count: 发现的问题总数
        failed_checks: 失败的检查项名称列表
        details: 包含完整检查结果的字典
    """
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


# ============================================================================
# 规则选择引擎
# ============================================================================

def _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date) -> Optional[BillingRule]:
    """
    根据「谁、在哪、什么费、怎么算、哪天」匹配最优的一条计费规则。

    匹配策略（四级 fallback）:
        1. owner 指定 + warehouse 指定  （最具体）
        2. owner 指定 + warehouse 通配(NULL)
        3. owner 通配(NULL) + warehouse 指定
        4. owner 通配 + warehouse 通配  （兜底默认价）

    同一层级内按 priority 升序（小数优先），再按 id 确定性排序。
    只返回第一条（.first()），即最优匹配。

    这样设计允许：先配一个「全仓默认价」，再为特定客户/仓库覆盖自定义价格。

    参数:
        owner_id: 货主 ID
        warehouse_id: 仓库 ID
        charge_type: 计费类型（RECEIVE, PICK, STORAGE 等）
        calc_method: 计量方式（PER_QTY_ABSDEL, PER_TASK, PER_LINE 等）
        service_date: 服务日期，用于过滤生效区间

    返回:
        匹配到的 BillingRule 或 None（未配置规则时跳过计费，不报错）
    """
    qs = (BillingRule.objects
          .filter(active=True, charge_type=charge_type, calc_method=calc_method)
          .filter(Q(owner_id=owner_id) | Q(owner__isnull=True))
          .filter(Q(warehouse_id=warehouse_id) | Q(warehouse__isnull=True)))
    if service_date:
        qs = qs.filter(
            Q(effective_from__isnull=True) | Q(effective_from__lte=service_date),
            Q(effective_to__isnull=True) | Q(effective_to__gte=service_date),
        )

    # nulls_last=True 确保「指定了 owner/warehouse 的规则」排在「通配 NULL 的规则」之前
    rule = qs.order_by(
        F("owner_id").asc(nulls_last=True),
        F("warehouse_id").asc(nulls_last=True),
        "priority",
        "id",
    ).first()

    if rule is None:
        logger.debug(
            "No billing rule matched: owner=%s warehouse=%s charge=%s calc=%s date=%s",
            owner_id, warehouse_id, charge_type, calc_method, service_date,
        )
    return rule


# ============================================================================
# 指纹防重
# ============================================================================

def _event_fp(task_id, scanlog_id, charge_type, calc_method, service_date, qty, scope_key=None):
    """
    生成 BillingEvent 的唯一指纹。

    指纹由「任务ID|扫描日志ID|费用类型|计量方式|日期|数量」拼接而成。
    配合 BillingEvent.event_fp 的 unique 约束 + get_or_create，实现幂等写入：
    同一笔过账被重复处理时，不会产生重复的 Event。

    对于非任务来源的事件（如仓储/指标），task_id 和 scanlog_id 为 None，用 '-' 代替。

    scope_key: 可选前缀（如 "{owner_id}:{warehouse_id}"），用于区分不同
    owner/warehouse 在 task_id 为 None 时的指纹碰撞。
    """
    base = f"{task_id or '-'}|{scanlog_id or '-'}|{charge_type}|{calc_method}|{service_date}|{_q(qty,'0.0001')}"
    if scope_key:
        return f"{scope_key}|{base}"
    return base


def _acc_fp(owner_id, warehouse_id, rule_id, charge_type, service_date, qty, unit_price, currency, ev_fp):
    """
    生成 BillingAccrual 的唯一指纹。

    在 event 指纹基础上追加「货主|仓库|规则ID|单价|币种」，确保：
    - 同一事件不会产生重复 accrual
    - 规则或价格变化后能产生新 accrual（因为指纹不同）

    配合 BillingAccrual.acc_fingerprint 的 unique 约束 + get_or_create 实现幂等。
    """
    return f"{owner_id}|{warehouse_id}|{rule_id}|{charge_type}|{service_date}|{_q(qty,'0.0001')}|{_q(unit_price,'0.0001')}|{currency}|{ev_fp}"


# ============================================================================
# Accrual 金额调整辅助
# ============================================================================

def _save_adjusted_accrual(accrual: BillingAccrual, new_amount: Decimal) -> None:
    """
    修改 accrual 的金额，并同步重算税额和有效单价。

    封顶/打包调整时调用此函数而非直接修改 amount，确保三个关联字段保持一致。

    行为:
        1. 将 new_amount 对齐到分（0.01），且不低于 0
        2. 如果金额未变则 early return（避免无意义的 DB 写入）
        3. 根据规则的 taxable/tax_rate 重算 tax_amount
        4. 根据 amount / quantity 重算 unit_price
        5. 只更新必要字段（update_fields），不触发 full_clean

    参数:
        accrual: 要调整的 BillingAccrual 实例（需已 select_related("rule")）
        new_amount: 调整后的目标金额
    """
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
    """
    查询适用于指定账期的「按账期打包」规则。

    过滤条件:
        - active=True
        - bundle_key 匹配
        - bundle_scope=PER_PERIOD
        - bundle_price 非空
        - owner/warehouse 匹配或通配
        - 生效日期区间与账期有交集
    """
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


def _select_bundle_rule_for_period(period: BillingPeriod, bundle_key: str,
                                   preferred_rule_ids=None, charge_types=None) -> Optional[BillingRule]:
    """
    为指定账期和打包键选择最匹配的打包规则。

    与 _select_rule 的优先级逻辑一致（owner/warehouse 非空优先）。
    额外支持 preferred_rule_ids：优先匹配已产生 accrual 的规则，
    避免关账时选到一个与已有 accrual 无关的规则。
    charge_types：限制只匹配指定 charge_type 的规则，
    避免跨 charge_type 的 bundle_key 误选。
    """
    qs = _period_bundle_rule_queryset(period, bundle_key)
    if charge_types:
        qs = qs.filter(charge_type__in=charge_types)
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
    """
    FIXED 打包模式：将一组 accrual 的总额强制调整为恰好等于 target_total。

    调整策略:
        - 如果当前总额 < target_total（不够）→ 差额追加到最后一笔
        - 如果当前总额 > target_total（超出）→ 从最后一笔开始往回扣减，直到总额匹配

    这与 CAP 打包不同：CAP 只设上限（不超过就不动），FIXED 强制总额等于打包价。

    参数:
        accs: BillingAccrual queryset 或 list（需按 service_date, id 排序）
        target_total: 打包目标总额
    """
    acc_list = list(accs)
    if not acc_list:
        return

    target_total = max(Decimal("0.00"), _q(target_total, "0.01"))
    current_total = sum((Decimal(a.amount) for a in acc_list), Decimal("0.00"))
    diff = _q(target_total - current_total, "0.01")
    if diff == 0:
        return

    if diff > 0:
        # 不够 → 追加到最后一笔
        last = acc_list[-1]
        _save_adjusted_accrual(last, Decimal(last.amount) + diff)
        return

    # 超出 → 从后往前扣减
    remaining = -diff
    for accrual in reversed(acc_list):
        if remaining <= 0:
            break
        reducible = min(Decimal(accrual.amount), remaining)
        if reducible <= 0:
            continue
        _save_adjusted_accrual(accrual, Decimal(accrual.amount) - reducible)
        remaining -= reducible


# ============================================================================
# 阶梯计价引擎
# ============================================================================

def _compute_fee_with_rule(rule: BillingRule, base_value: Decimal) -> Tuple[Decimal, Decimal]:
    """
    核心定价函数：根据规则和基础值计算费用。

    支持三种模式:

    1. **无阶梯**（最常见）:
       amount = base_value × rule.unit_price
       适用于大多数按量/按件计费场景。

    2. **WHOLE 模式**（整档/落档）:
       找到 base_value 落在哪个阶梯区间，整个数量按该档定价。
       例: 阶梯 [0-100: ¥1, 100-500: ¥0.8]，数量=300 → 300 × 0.8 = ¥240

    3. **INCREMENTAL 模式**（累进/分段）:
       不同区间的数量分别按各自档位计价，然后累加。
       例: 同上阶梯，数量=300 → 100×1 + 200×0.8 = ¥260

    **费率阶梯**: 当 tier 中填了 percent_rate 而非 unit_price 时，
    base_value 被解释为「金额」而非「数量」，percent_rate 作为费率。
    用于 PERCENT_OF_ORDER_AMOUNT（按订单金额百分比收费）场景。

    参数:
        rule: 已匹配的 BillingRule（需要 prefetch tiers）
        base_value: 计费基础值（数量 或 金额，取决于 calc_method）

    返回:
        (amount, effective_price_or_rate) 元组
        - amount: 计算出的费用金额
        - effective_price_or_rate: 有效单价/费率（用于记录到 accrual.unit_price）
    """
    bv = Decimal(base_value or 0)
    if bv <= 0:
        return (Decimal("0.00"), Decimal("0.0000"))

    tiers = list(rule.tiers.all().order_by("threshold_from"))

    # 无阶梯 → 简单乘法
    if not tiers:
        up = Decimal(rule.unit_price) if rule.unit_price is not None else Decimal("0")
        amt = _q(bv * up, "0.01")
        eff = _q(up, "0.0001")
        return (amt, eff)

    mode = rule.ladder_mode or LadderMode.WHOLE

    # 判断是「按量阶梯」还是「按金额费率阶梯」
    is_rate = any(t.percent_rate is not None for t in tiers)

    def t_price(t: BillingRuleTier) -> Decimal:
        """取阶梯的单价/费率，自动判断用哪个字段"""
        return Decimal(t.percent_rate) if is_rate else Decimal(t.unit_price)

    # ------ WHOLE 模式：落档，全量按一个档计价 ------
    if mode == LadderMode.WHOLE:
        chosen = None
        for t in tiers:
            start = Decimal(t.threshold_from)
            end = Decimal(t.threshold_to) if t.threshold_to is not None else None
            # 区间左闭右开: [start, end)，end=None 表示无上限
            if (bv >= start) and (end is None or bv < end):
                chosen = t
        # 兜底：如果 base_value 超出所有阶梯范围，使用最后一档
        if chosen is None:
            chosen = tiers[-1]
        p = t_price(chosen)
        amt = _q(bv * p, "0.01")
        eff = _q(p, "0.0001")
        return (amt, eff)

    # ------ INCREMENTAL 模式：分段累进 ------
    # 每个区间内的数量按该档定价，累加得总费用
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
    if last < bv:
        logger.warning(
            "INCREMENTAL tier gap: rule=%s base_value=%s priced_up_to=%s "
            "(quantity in [%s, %s) received zero pricing)",
            rule.id, bv, last, last, bv,
        )
    # 有效费率 = 总费用 / 总量，方便记录
    eff = _q((amt / bv) if bv > 0 else Decimal("0"), "0.0001")
    return (amt, eff)


# ============================================================================
# 日封顶 / 日打包
# ============================================================================

def _sum_amount_rule_day(rule_id, owner_id, warehouse_id, d):
    """查询某规则在某天已累计的有效 accrual 金额总和（排除 VOID/reversal，加行锁防并发超限）。"""
    agg = (BillingAccrual.objects
           .select_for_update()
           .filter(rule_id=rule_id, owner_id=owner_id, warehouse_id=warehouse_id, service_date=d)
           .exclude(status=AccrualStatus.VOID)
           .filter(is_reversal=False)
           .aggregate(s=Sum("amount"))["s"])
    return Decimal(agg or 0)


def _sum_amount_bundle_day(bundle_key, owner_id, warehouse_id, d):
    """查询某打包组（bundle_key）在某天已累计的有效 accrual 金额总和（排除 VOID/reversal，加行锁防并发超限）。"""
    if not bundle_key:
        return Decimal(0)
    agg = (BillingAccrual.objects
           .select_for_update()
           .filter(bundle_key=bundle_key, owner_id=owner_id, warehouse_id=warehouse_id, service_date=d)
           .exclude(status=AccrualStatus.VOID)
           .filter(is_reversal=False)
           .aggregate(s=Sum("amount"))["s"])
    return Decimal(agg or 0)


def _apply_caps_bundles_day(rule: BillingRule, owner_id, warehouse_id, service_date, draft_amount: Decimal) -> Decimal:
    """
    在每笔 accrual 生成时即时应用的「日口径」限额控制。

    执行顺序（与整体计费流程的约定一致）:
        阶梯计价 → 本函数(日封顶/日打包) → 最低收费

    两种限额机制:
        1. **日封顶 (cap_mode=PER_DAY)**: 单条规则在当天的总费用不超过 cap_amount。
           查询该规则当天已有 accrual 的累计金额，新笔只能用剩余额度。

        2. **日打包上限 (bundle_scope=PER_DAY, bundle_type=CAP)**: 同一个打包组
           (bundle_key) 在当天的总费用不超过 bundle_price。跨规则生效。
           注意: FIXED 打包只在账期口径处理，日口径只支持 CAP。

    参数:
        rule: 当前匹配的计费规则
        draft_amount: 阶梯计价后的初始金额
    返回:
        限额后的金额（>= 0）
    """
    amt = Decimal(draft_amount or 0)

    # 封顶（按天）
    if rule.cap_mode == CapMode.PER_DAY and rule.cap_amount:
        used = _sum_amount_rule_day(rule.id, owner_id, warehouse_id, service_date)
        remain = max(Decimal("0.00"), Decimal(rule.cap_amount) - used)
        amt = min(amt, remain)

    # 打包（按天，仅 CAP 类型）
    if rule.bundle_scope == BundleScope.PER_DAY and rule.bundle_price and rule.bundle_type == BundleType.CAP and rule.bundle_key:
        used_b = _sum_amount_bundle_day(rule.bundle_key, owner_id, warehouse_id, service_date)
        remain_b = max(Decimal("0.00"), Decimal(rule.bundle_price) - used_b)
        amt = min(amt, remain_b)

    return max(Decimal("0.00"), amt)
