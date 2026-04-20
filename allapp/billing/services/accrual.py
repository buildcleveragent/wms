# allapp/billing/services/accrual.py
"""
费用应计（Accrual）生成模块
============================

"应计"是指：业务事件已经发生、费用已经产生，但尚未开具发票或收款的状态。
每条 BillingAccrual 记录代表"某客户在某日因某规则产生了多少费用"，
之后由对账/开票流程将多条应计汇总到账单（BillingInvoice）。

本模块提供四条应计生成路径
--------------------------
1. accrue_for_posting
   任务扫描日志（TaskScanLog）过账后触发。
   覆盖收货(RECEIVE)、上架(PUTAWAY)、移位(RELOC)、拣货(PICK)、
   复核(REVIEW)、打包(PACK)、装车(LOAD)、派发(DISPATCH)、
   盘点(COUNT) 等操作类型。

2. accrue_storage_for_date
   每日定时任务触发，对当日库存在库量按"每日在库基本单位"计费（存储费）。

3. accrue_metrics_for_date
   读取当日 BillingMetricDaily 中的托盘(PALLET)、立方米(CBM)、
   面积(AREA_M2)、订单金额(ORDER_AMT) 等指标，逐一生成应计。
   AREA_M2 使用月费按当月天数摊销为日费。

4. accrue_order_processing_from_posted
   汇总指定日期区间内已过账扫描日志，从四个维度产生订单处理费：
   - PER_ORDER             每单费
   - PER_ORDER_LINE        每单行费
   - PER_PARCEL            每包裹费
   - PERCENT_OF_ORDER_AMOUNT  订单金额百分比费

公共计算流程（所有路径共享）
-----------------------------
  规则匹配(_select_rule)
    → 阶梯定价(_compute_fee_with_rule)
    → 每日上限/打包(_apply_caps_bundles_day)
    → 最低收费(min_charge)
    → 指纹去重(fingerprint)
    → 幂等写入(get_or_create BillingAccrual)

指纹机制（Fingerprint-based Idempotency）
-----------------------------------------
- BillingEvent 使用 _event_fp() 生成事件指纹，确保同一操作不会重复写入事件。
- BillingAccrual 使用 _acc_fp() 生成应计指纹，确保同一计价结果不会重复写入应计。
- 两个 get_or_create 均以指纹字段作为唯一键，可安全重入（idempotent）。
"""
import datetime
import importlib
from decimal import Decimal
from typing import Dict, Iterable, Optional, Set, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from allapp.billing.enums import (
    AccrualStatus, CalcMethod, ChargeType, MetricType,
)
from allapp.billing.models import (
    BillingAccrual, BillingEvent, BillingMetricDaily, BillingRule,
)

from ._common import (
    _acc_fp, _apply_caps_bundles_day, _compute_fee_with_rule, _days_in_month,
    _event_fp, _q, _select_rule, logger,
)

# AUTO_REVIEW_ORDER_PROCESSING_METHODS = {
#     CalcMethod.PER_ORDER,
#     CalcMethod.PER_ORDER_LINE,
# }

# REVIEW 过账自动触发订单处理费时，开放这三个维度。
# PER_PARCEL 仍保留给批量补算入口，待 resolver 补齐 parcels 后再自动化。
AUTO_REVIEW_ORDER_PROCESSING_METHODS: Set[str] = {
    CalcMethod.PER_ORDER,
    CalcMethod.PER_ORDER_LINE,
    CalcMethod.PERCENT_OF_ORDER_AMOUNT,
}

# ------------------------ 路径一：过账触发的扫描日志应计 ------------------------ #

@transaction.atomic
def accrue_for_posting(task, posting_journal, by_user=None) -> Tuple[int, int]:
    """
    根据任务过账日志（TaskScanLog）生成费用应计。

    调用时机
    --------
    任务过账（posting）完成后由过账服务调用，通常在仓库操作扫描确认并审核通过后触发。

    参数
    ----
    task            : 当前操作任务对象（Task），携带 task_type 字段决定费用类型。
    posting_journal : 过账凭证对象（PostingJournal），用于关联和过滤扫描日志。
    by_user         : 操作人（User），写入 BillingAccrual.created_by，可为 None。

    task_type → (ChargeType, CalcMethod) 映射表
    --------------------------------------------
    任务类型        收费类型           计算方式
    RECEIVE   →  RECEIVE   ,  PER_QTY_ABSDEL   # 收货：按实际数量（绝对增量）
    PUTAWAY   →  PUTAWAY   ,  PER_QTY_ABSDEL   # 上架：按实际数量
    RELOC     →  RELOC     ,  PER_QTY_ABSDEL   # 移位：按实际数量
    PICK      →  PICK      ,  PER_QTY_ABSDEL   # 拣货：按实际数量
    REVIEW    →  REVIEW    ,  PER_LINE          # 复核：按行数
    PACK      →  PACK      ,  PER_LINE          # 打包：按行数
    LOAD      →  LOAD      ,  PER_TASK          # 装车：按任务次数（固定1次）
    DISPATCH  →  DISPATCH  ,  PER_TASK          # 派发：按任务次数（固定1次）
    COUNT     →  COUNT     ,  PER_LINE          # 盘点：按行数

    完整执行流程
    ------------
    1. 查询该任务、该过账凭证下所有状态为 OK 且已过账的 TaskScanLog。
    2. 按 task_type 从 mapping 表确定 (charge_type, calc_method)；
       未知类型直接跳过（continue）。
    3. 计算计费数量：
       - PER_QTY_ABSDEL：取 qty_base_delta 的绝对值（库存绝对变动量）；
       - PER_LINE / PER_TASK：计费数量固定为 Decimal("1")。
    4. 生成事件指纹（ev_fp）并 get_or_create BillingEvent，保证幂等。
    5. 调用 _select_rule 匹配有效计费规则；无规则则跳过。
    6. 调用 _compute_fee_with_rule 按阶梯定价计算金额和单价。
    7. 调用 _apply_caps_bundles_day 应用当日总量上限/打包规则。
    8. 若设置了 min_charge 且计算金额不足，则补足到最低收费。
    9. 计算税额（taxable=True 时：amount × tax_rate）。
    10. 生成应计指纹（acc_fp）并 get_or_create BillingAccrual。

    返回
    ----
    (created_events, created_accruals): 本次调用新创建的事件数与应计数。
    """
    from allapp.tasking.models import TaskScanLog

    # 查询该任务在本次过账凭证下的所有成功（OK）且已过账的扫描日志
    logs = (TaskScanLog.objects
            .filter(task=task, status="OK", posted_at__isnull=False)
            .filter(Q(posting_journal=posting_journal))
            .select_related("task", "task_line", "owner", "warehouse"))

    # task_type → (ChargeType, CalcMethod) 映射表
    # 决定：①产生哪种费用类型；②按什么口径计算数量（绝对增量/行/任务）
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

    # 预查已关账 period，用于检测晚到过账
    from allapp.billing.models import BillingPeriod
    from allapp.billing.enums import PeriodStatus as _PeriodStatus
    _closed_period_cache: Dict[tuple, Optional[str]] = {}

    def _check_closed_period(owner_id_chk, warehouse_id_chk, svc_date):
        cache_key = (owner_id_chk, warehouse_id_chk, svc_date)
        if cache_key not in _closed_period_cache:
            cp = BillingPeriod.objects.filter(
                owner_id=owner_id_chk, warehouse_id=warehouse_id_chk,
                start_date__lte=svc_date, end_date__gte=svc_date,
                status__in=[_PeriodStatus.CLOSED, _PeriodStatus.INVOICED],
            ).values_list("label", flat=True).first()
            _closed_period_cache[cache_key] = cp
        return _closed_period_cache[cache_key]

    created_events = created_accruals = 0
    for log in logs:
        ttype = task.task_type
        # 不在映射表中的任务类型暂不计费，直接跳过
        if ttype not in mapping:
            continue
        ctype, cm = mapping[ttype]

        # 计算原始数量：优先使用 qty_base_delta（增量），为空则用 qty_base（总量）
        qty = (log.qty_base_delta if log.qty_base_delta is not None else log.qty_base) or Decimal("0")
        # 取绝对值：收货/拣货等操作可能产生负增量，但计费只关心绝对变动量
        qty_abs = abs(Decimal(qty))
        # 服务日期：以过账时间为准，无过账时间则取当前时间
        service_date = (log.posted_at or timezone.now()).date()

        # 步骤 4：生成事件指纹并幂等创建 BillingEvent
        # _event_fp 将 (task_id, log_id, charge_type, calc_method, date, qty) 哈希为唯一字符串
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

        # 步骤 5：按 (客户, 仓库, 费用类型, 计算方式, 服务日期) 匹配有效计费规则
        rule = _select_rule(log.owner_id, log.warehouse_id, ctype, cm, service_date)
        if not rule:
            # 无对应规则则此条日志不产生应计
            continue

        # 步骤 6：确定计费数量
        # PER_TASK / PER_LINE 类型固定为 1（按次/按行，不乘以货品数量）
        # 其他类型（PER_QTY_ABSDEL）使用实际货品数量
        qty_bill = Decimal("1") if cm in (CalcMethod.PER_TASK, CalcMethod.PER_LINE) else Decimal(event.quantity)
        # 调用阶梯定价引擎计算金额和有效单价
        amount, eff_price = _compute_fee_with_rule(rule, qty_bill)
        # 步骤 7：应用当日上限/打包规则（例如：当日最高收费上限、阶梯打包单位）
        amount = _apply_caps_bundles_day(rule, log.owner_id, log.warehouse_id, service_date, amount)
        # 步骤 8：最低收费保障——若计费金额低于规则设定的最低收费，则补足
        if rule.min_charge and qty_bill > 0 and amount < rule.min_charge:
            amount = rule.min_charge
        # 金额为零或负数的应计无意义，跳过
        if amount <= 0:
            continue

        # 反推有效单价（实际金额 / 实际数量），用于账单明细展示
        eff_price = _q((amount / qty_bill) if qty_bill > 0 else eff_price, "0.0001")
        # 步骤 9：计算税额；不含税规则税额为 0
        tax_amount = _q(amount * (rule.tax_rate or 0), "0.01") if rule.taxable else Decimal("0.00")

        # 步骤 10：生成应计指纹并幂等创建 BillingAccrual
        # _acc_fp 将 (客户, 仓库, 规则ID, 费用类型, 日期, 数量, 单价, 币种, 事件指纹) 哈希为唯一字符串
        # 这保证了相同参数重复调用不会产生重复应计记录
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
            # 晚到过账检测：accrual 落在已关账区间时发出警告
            closed_label = _check_closed_period(log.owner_id, log.warehouse_id, service_date)
            if closed_label:
                logger.warning(
                    "accrue_for_posting: late-posting detected — accrual for date %s "
                    "falls in closed/invoiced period '%s' (task=%s, may need re-lock)",
                    service_date, closed_label, task.id,
                )

    logger.info(
        "accrue_for_posting: task=%s posting_journal=%s events=%d accruals=%d",
        task.id, posting_journal.id, created_events, created_accruals,
    )
    return created_events, created_accruals


# ------------------------ 路径二：每日在库存储费 ------------------------ #

@transaction.atomic
def accrue_storage_for_date(owner_id, warehouse_id, service_date: datetime.date, by_user=None) -> Tuple[int, int]:
    """
    生成指定日期的在库存储费应计（每日在库基本单位计费）。

    调用时机
    --------
    每日定时任务（通常午夜后）触发，对当天快照时刻的在库数量计算存储费。

    参数
    ----
    owner_id      : 货主 ID。
    warehouse_id  : 仓库 ID。
    service_date  : 计费服务日期（通常为当天或昨日）。
    by_user       : 操作人，写入 BillingAccrual.created_by。

    执行流程
    --------
    1. 查找 (客户, 仓库, STORAGE, PER_DAY_ONHAND_BASE, 日期) 对应的有效规则；
       无规则则直接返回 (0, 0)。
    2. 从 InventoryDetail 按 (owner_id, warehouse_id) 聚合 onhand_qty 总量；
       在库量 ≤ 0 跳过（无库存不产生存储费）。
    3. 生成事件指纹 → get_or_create BillingEvent。
    4. 阶梯定价 → 每日上限 → 最低收费 → 税额。
    5. 生成应计指纹 → get_or_create BillingAccrual。

    注意
    ----
    - qty_bill 直接使用在库数量（单位：BASE，如件/箱/托盘等规则配置的基本单位）。
    - 存储费规则通常按天计费（PER_DAY_ONHAND_BASE），月租则用路径三的 AREA_M2。

    返回
    ----
    (created_events, created_accruals)
    """
    from allapp.inventory.models import InventoryDetail, InventorySnapshotDaily

    # 步骤 1：查找存储费计费规则；无规则则本次无需产生应计
    rule = _select_rule(owner_id, warehouse_id, ChargeType.STORAGE, CalcMethod.PER_DAY_ONHAND_BASE, service_date)
    if not rule:
        return (0, 0)

    # 步骤 2：汇总该客户在该仓库的在库总量
    # 历史日期使用库存快照（InventorySnapshotDaily），当天使用实时库存（InventoryDetail）
    today = timezone.now().date()
    if service_date < today:
        qs = (InventorySnapshotDaily.objects
              .filter(owner_id=owner_id, warehouse_id=warehouse_id,
                      snapshot_date=service_date, onhand_qty__gt=0)
              .values("owner_id", "warehouse_id")
              .annotate(onhand=Sum("onhand_qty")))
    else:
        qs = (InventoryDetail.objects
              .filter(owner_id=owner_id, warehouse_id=warehouse_id, is_active=True)
              .values("owner_id", "warehouse_id")
              .annotate(onhand=Sum("onhand_qty")))

    created_events = created_accruals = 0
    for row in qs:
        qty = Decimal(row["onhand"] or 0)
        # 在库量为零或负数，跳过（无存储费）
        if qty <= 0:
            continue

        # 步骤 3：生成事件指纹（task_id=None, log_id=None 因为存储费无对应扫描日志）
        ev_fp = _event_fp(None, None, ChargeType.STORAGE, CalcMethod.PER_DAY_ONHAND_BASE, service_date, qty,
                         scope_key=f"{owner_id}:{warehouse_id}")
        event, ev_created = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.STORAGE,
                service_date=service_date, quantity=_q(qty, "0.0001"), quantity_uom="BASE"
            )
        )
        if ev_created:
            created_events += 1

        # 步骤 4a：存储费以实际在库数量参与阶梯定价
        qty_bill = qty
        amount, eff_price = _compute_fee_with_rule(rule, qty_bill)
        # 步骤 4b：应用每日上限/打包
        amount = _apply_caps_bundles_day(rule, owner_id, warehouse_id, service_date, amount)
        # 步骤 4c：最低收费保障
        if rule.min_charge and qty_bill > 0 and amount < rule.min_charge:
            amount = rule.min_charge
        if amount <= 0:
            continue

        # 反推有效单价，用于账单明细
        eff_price = _q((amount / qty_bill) if qty_bill > 0 else eff_price, "0.0001")
        # 步骤 4d：税额计算
        tax_amount = _q(amount * (rule.tax_rate or 0), "0.01") if rule.taxable else Decimal("0.00")

        # 步骤 5：生成应计指纹并幂等写入 BillingAccrual
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

    logger.info(
        "accrue_storage_for_date: owner=%s warehouse=%s date=%s events=%d accruals=%d",
        owner_id, warehouse_id, service_date, created_events, created_accruals,
    )
    return created_events, created_accruals


# ------------------------ 路径三：指标驱动的应计 ------------------------ #

@transaction.atomic
def accrue_metrics_for_date(owner_id, warehouse_id, service_date: datetime.date, by_user=None) -> Tuple[int, int]:
    """
    读取当日 BillingMetricDaily 指标记录，按指标类型生成对应的费用应计。

    调用时机
    --------
    每日定时任务触发，在指标汇总作业完成后运行。

    参数
    ----
    owner_id      : 货主 ID。
    warehouse_id  : 仓库 ID。
    service_date  : 计费服务日期。
    by_user       : 操作人。

    指标类型 → (CalcMethod, ChargeType) 映射
    -----------------------------------------
    PALLET    → PER_PALLET_DAY         , STORAGE    # 托盘存储：按托盘数/天
    CBM       → PER_CBM_DAY            , STORAGE    # 体积存储：按立方米/天
    AREA_M2   → PER_AREA_MONTH         , STORAGE    # 面积租金：月费摊日（见下方说明）
    ORDER_AMT → PERCENT_OF_ORDER_AMOUNT, DISPATCH   # 订单金额百分比（见路径四）

    AREA_M2 月费摊销逻辑（重点）
    ----------------------------
    面积类存储（如租用固定库区）通常按月收费。为实现每日应计，本函数将
    月费按当月实际天数均摊到每一天：

        每日费用 = _compute_fee_with_rule(rule, 面积) / 当月天数
        每日单价 = 月单价                             / 当月天数

    _days_in_month(service_date) 返回 service_date 所在月份的实际天数
    （如 2024年2月=29天，2024年3月=31天），确保月费均摊精确。

    执行流程
    --------
    1. 查询当日所有该客户/仓库的 BillingMetricDaily 记录。
    2. 按 metric_type 确定 (calc_method, charge_type)；未知类型跳过。
    3. 查找对应有效规则；无规则跳过。
    4. 计算金额：AREA_M2 先算月费再按天数均摊，其他类型直接计算。
    5. 应用每日上限 → 最低收费 → 税额。
    6. 生成事件指纹 → get_or_create BillingEvent。
    7. 生成应计指纹 → get_or_create BillingAccrual。

    返回
    ----
    (created_events, created_accruals)
    """
    created_events = created_accruals = 0

    # 步骤 1：取当日该客户/仓库的所有指标记录
    ms = BillingMetricDaily.objects.filter(owner_id=owner_id, warehouse_id=warehouse_id, service_date=service_date)
    for m in ms:
        # 步骤 2：将指标类型映射到计算方法和费用类型
        if m.metric_type == MetricType.PALLET:
            cm, ctype = CalcMethod.PER_PALLET_DAY, ChargeType.STORAGE
        elif m.metric_type == MetricType.CBM:
            cm, ctype = CalcMethod.PER_CBM_DAY, ChargeType.STORAGE
        elif m.metric_type == MetricType.AREA_M2:
            # 面积指标使用月费率规则，后续需要按月天数摊销
            cm, ctype = CalcMethod.PER_AREA_MONTH, ChargeType.STORAGE
        elif m.metric_type == MetricType.ORDER_AMT:
            logger.info(
                "accrue_metrics_for_date: skip ORDER_AMT metric owner=%s warehouse=%s date=%s",
                owner_id,
                warehouse_id,
                service_date,
            )
            continue
        else:
            # 未知指标类型，暂不支持，跳过
            continue

        # 步骤 3：查找有效的计费规则
        rule = _select_rule(owner_id, warehouse_id, ctype, cm, service_date)
        if not rule:
            continue

        qty_bill = Decimal(m.value)

        # 步骤 4：计算费用金额
        if cm == CalcMethod.PER_AREA_MONTH:
            # AREA_M2 特殊处理：先用月费率算出月总费用，再按当月天数均摊到日
            # 例如：100 m² × 月租 5元/m² = 500元/月，当月31天 → 每日应计 ≈ 16.13元
            monthly_amount, monthly_eff = _compute_fee_with_rule(rule, qty_bill)
            amount = _q(Decimal(monthly_amount) / Decimal(_days_in_month(service_date)), "0.01")
            eff_price = _q(Decimal(monthly_eff) / Decimal(_days_in_month(service_date)), "0.0001")
        else:
            # 托盘、CBM、订单金额百分比：直接用规则计算日费
            amount, eff_price = _compute_fee_with_rule(rule, qty_bill)

        # 步骤 5a：应用每日上限/打包规则
        amount = _apply_caps_bundles_day(rule, owner_id, warehouse_id, service_date, amount)
        # 步骤 5b：最低收费保障
        if rule.min_charge and qty_bill > 0 and amount < rule.min_charge:
            amount = rule.min_charge
        if amount <= 0:
            continue

        # 反推有效单价（实际金额 / 指标数量）
        eff_price = _q((amount / qty_bill) if qty_bill > 0 else eff_price, "0.0001")
        # 步骤 5c：税额计算
        tax_amount = _q(amount * (rule.tax_rate or 0), "0.01") if rule.taxable else Decimal("0.00")

        # 步骤 6：生成事件指纹并幂等创建 BillingEvent
        ev_fp = _event_fp(None, None, ctype, cm, service_date, qty_bill,
                         scope_key=f"{owner_id}:{warehouse_id}")
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ctype, service_date=service_date,
                quantity=_q(qty_bill, "0.0001"), quantity_uom="BASE"
            )
        )

        # 步骤 7：生成应计指纹并幂等创建 BillingAccrual
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

    logger.info(
        "accrue_metrics_for_date: owner=%s warehouse=%s date=%s events=%d accruals=%d",
        owner_id, warehouse_id, service_date, created_events, created_accruals,
    )
    return created_events, created_accruals


# ------------------------ 路径四：订单处理费（从已过账扫描日志汇总） ------------------------ #

def _load_taskline_order_resolver():
    """
    从 Django settings 动态加载订单解析器函数（可插拔设计）。

    配置方式
    --------
    在 settings.py 中设置：
        BILLING_TASKLINE_ORDER_RESOLVER = "myapp.billing.resolvers:resolve_taskline_to_order"

    解析器函数签名
    --------------
        def resolve_taskline_to_order(task_line) -> dict:
            ...

    解析器返回值字典（所有键均可选）
    ----------------------------------
    {
        "order_ids":      set[int],         # 本任务行关联的订单 ID 集合（用于 PER_ORDER）
        "order_line_ids": set[tuple[int,int]], # (order_id, line_id) 元组集合（用于 PER_ORDER_LINE）
        "parcels":        int,              # 包裹数（用于 PER_PARCEL）
        "order_amount":   Decimal,          # 订单商品金额（用于 PERCENT_OF_ORDER_AMOUNT）
        "bundle_key":     str,              # 打包分组键（可选）
    }

    未配置 BILLING_TASKLINE_ORDER_RESOLVER 时返回 None，
    accrue_order_processing_from_posted 将跳过所有日志的订单维度汇总。
    """
    path = getattr(settings, "BILLING_TASKLINE_ORDER_RESOLVER", None)
    if not path:
        return None
    mod, func = path.split(":")
    return getattr(importlib.import_module(mod), func)


def _is_order_processing_amount_source(task) -> bool:
    if task is None:
        return False

    from allapp.tasking.models import WmsTask

    return (
        task.task_type == WmsTask.TaskType.REVIEW
        or (
            task.task_type == WmsTask.TaskType.PICK
            and task.review_status == WmsTask.ReviewStatus.APPROVED
        )
    )


def _load_order_line_amounts(line_ids: Iterable[int]) -> Dict[int, Decimal]:
    line_ids = sorted(set(line_ids))
    if not line_ids:
        return {}

    from allapp.outbound.models import OutboundOrderLine

    line_amount_by_id: Dict[int, Decimal] = {}
    rows = (
        OutboundOrderLine.objects
        .filter(id__in=line_ids)
        .only("id", "final_line_amount", "base_qty", "base_price")
    )
    for row in rows:
        final_amt = Decimal(row.final_line_amount or 0)
        if final_amt > 0:
            line_amount_by_id[row.id] = final_amt
        else:
            line_amount_by_id[row.id] = Decimal(row.base_qty or 0) * Decimal(row.base_price or 0)
    return line_amount_by_id




# @transaction.atomic
# def accrue_order_processing_from_posted(
#         owner_id, warehouse_id, start_date, end_date,
#         by_user=None,
#         allowed_methods: Optional[Set] = None,
# ) -> Tuple[int, int]:
#     """
#     汇总指定日期区间内已过账扫描日志，从四个维度生成订单处理费应计。
#
#     调用时机
#     --------
#     可由日结任务或手动触发，通常在过账后批量补算订单处理费。
#     支持指定任意日期区间（start_date ～ end_date，含首尾），可安全重入。
#
#     参数
#     ----
#     owner_id     : 货主 ID。
#     warehouse_id : 仓库 ID。
#     start_date   : 区间起始日期（含）。
#     end_date     : 区间结束日期（含）。
#     by_user      : 操作人。
#
#     Resolver 机制（可插拔订单关联）
#     --------------------------------
#     不同业务系统的"任务行→订单"关系各不相同（有的一行对应一单，
#     有的一行关联多单）。本函数通过 _load_taskline_order_resolver()
#     加载外部解析函数，将 TaskLine 解析为标准字典格式，
#     从而解耦计费引擎与具体业务模型。
#
#     四维度汇总逻辑
#     ---------------
#     第一阶段（扫描日志聚合）：
#       遍历区间内 DISPATCH/PACK/REVIEW/PICK 类型的已过账扫描日志，
#       通过 resolver 解析每条日志的 task_line，将结果累积到：
#         - order_ids          : set of (order_id, svc_date)      → PER_ORDER
#         - order_lines        : set of (order_id, line_id, date) → PER_ORDER_LINE
#         - parcels_by_date    : dict[date, int]                  → PER_PARCEL
#         - order_amount_by_date: dict[date, Decimal]             → PERCENT_OF_ORDER_AMOUNT
#         - bundle_by_date     : dict[date, str]                  → 打包键
#
#     第二阶段（四维度逐一生成应计）：
#
#     维度 1 - PER_ORDER（每单费）
#       对 order_ids 中每个 (order_id, svc_date) 生成一条事件和应计，
#       计费数量固定为 1（每单）。ChargeType=DISPATCH。
#
#     维度 2 - PER_ORDER_LINE（每单行费）
#       对 order_lines 中每个 (order_id, line_id, svc_date) 生成一条事件和应计，
#       计费数量固定为 1（每行）。ChargeType=DISPATCH。
#
#     维度 3 - PER_PARCEL（每包裹费）
#       对 parcels_by_date 按日期汇总包裹总数，整日一条应计，
#       计费数量 = 当日包裹总数。ChargeType=PACK。
#
#     维度 4 - PERCENT_OF_ORDER_AMOUNT（订单金额百分比费）
#       先从 order_amount_by_date（来自扫描日志解析）取订单金额；
#       对于区间内尚无订单金额数据的日期，回退查询 BillingMetricDaily
#       中的 ORDER_AMT 指标记录作为兜底（fallback）。
#       ChargeType=DISPATCH，计费数量为订单金额（币种金额），
#       单价字段实际存储费率（百分比值）。
#
#     ORDER_AMT 兜底（Fallback）说明
#     --------------------------------
#     某些日期可能扫描日志中无 order_amount 数据（例如：当日无派发操作，
#     但存在外部系统录入的销售额）。此时函数检查 by_date_amounts 中的
#     缺失日期，从 BillingMetricDaily 补充 ORDER_AMT 指标值，
#     确保不遗漏任何有效计费日。
#
#     规则缓存（rule_cache）
#     ----------------------
#     _rule_for 内部使用 dict 缓存 (charge_type, calc_method, date) → rule，
#     避免对同一规则参数重复查库（一个日期区间内同类型规则通常固定）。
#
#     通用计算流程（四个维度共享）
#     -----------------------------
#     _compute_fee_with_rule → _apply_caps_bundles_day → min_charge → tax → get_or_create
#
#     返回
#     ----
#     (created_events, created_accruals): 本次调用新创建的总事件数和总应计数。
#     """
#     from allapp.tasking.models import TaskScanLog
#     # 加载可插拔的任务行→订单解析器；未配置则为 None
#     resolver = _load_taskline_order_resolver()
#     # 规则缓存：避免同一 (charge_type, calc_method, date) 重复查库
#     rule_cache: Dict[Tuple[str, str, datetime.date], Optional[BillingRule]] = {}
#
#     # 第一阶段：查询区间内相关任务类型的已过账扫描日志
#     # 只处理与订单处理直接相关的任务类型（派发/打包/复核/拣货）
#     logs = (TaskScanLog.objects
#             .filter(owner_id=owner_id, warehouse_id=warehouse_id, status="OK", posted_at__isnull=False)
#             .filter(posted_at__date__gte=start_date, posted_at__date__lte=end_date)
#             .filter(task__task_type__in=["DISPATCH", "PACK", "REVIEW", "PICK"])
#             .select_related("task", "task_line"))
#
#     # 各维度的汇总容器
#     order_ids: Set[Tuple[int, datetime.date]] = set()           # (order_id, svc_date) → PER_ORDER 去重
#     order_lines: Set[Tuple[int, int, datetime.date]] = set()    # (order_id, line_id, svc_date) → PER_ORDER_LINE 去重
#     parcels_by_date: Dict[datetime.date, int] = {}              # date → 包裹总数
#     order_amount_by_date: Dict[datetime.date, Decimal] = {}     # date → 订单金额总计
#     bundle_by_date: Dict[datetime.date, str] = {}               # date → 打包键（最后一条覆盖，通常同日一致）
#
#     for log in logs:
#         # 无 task_line 或未配置 resolver 则跳过（无法关联到订单维度）
#         if not log.task_line or resolver is None:
#             continue
#         # 调用可插拔解析器，将任务行解析为标准字典
#         mapping = resolver(log.task_line) or {}
#         svc_date = (log.posted_at or timezone.now()).date()
#
#         # 累积 PER_ORDER 维度：用集合自动去重（同一订单在同日多次扫描不重复计费）
#         for oid in mapping.get("order_ids", set()):
#             order_ids.add((oid, svc_date))
#         # 累积 PER_ORDER_LINE 维度：(order_id, line_id, date) 三元组去重
#         for olid in mapping.get("order_line_ids", set()):
#             order_lines.add((olid[0], olid[1], svc_date))
#         # 累积 PER_PARCEL 维度：同日包裹数累加
#         if mapping.get("parcels"):
#             parcels_by_date[svc_date] = parcels_by_date.get(svc_date, 0) + int(mapping["parcels"])
#         # 累积 PERCENT_OF_ORDER_AMOUNT 维度：同日订单金额累加
#         if mapping.get("order_amount"):
#             order_amount_by_date[svc_date] = order_amount_by_date.get(svc_date, Decimal("0")) + Decimal(mapping["order_amount"])
#         # 记录打包键（用于 bundle 打包分组，同日多条取最后一条）
#         if mapping.get("bundle_key"):
#             bundle_by_date[svc_date] = mapping["bundle_key"]
#
#     created_events = created_accruals = 0
#
#     def _rule_for(charge_type: str, calc_method: str, service_date: datetime.date) -> Optional[BillingRule]:
#         """
#         带缓存的规则查找辅助函数。
#         同一 (charge_type, calc_method, date) 组合只查库一次，
#         结果缓存在 rule_cache 中供后续调用复用。
#         """
#         key = (charge_type, calc_method, service_date)
#         if key not in rule_cache:
#             rule_cache[key] = _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date)
#         return rule_cache[key]
#
#     # ── 维度 1：PER_ORDER（每单费，ChargeType=DISPATCH）────────────────────
#     # 每个 (order_id, svc_date) 独立产生一条事件和一条应计，计费数量固定为 1
#     for (_oid, svc_date) in sorted(order_ids):
#         rule_order = _rule_for(ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date)
#         if not rule_order:
#             continue
#         # 事件指纹：以订单ID为第一维度，确保同一订单同日唯一
#         ev_fp = _event_fp(_oid, None, ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date, 1)
#         event, ev_new = BillingEvent.objects.get_or_create(
#             event_fp=ev_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.DISPATCH,
#                 service_date=svc_date, quantity=1, quantity_uom="ORDER"
#             )
#         )
#         # 固定数量 1，阶梯定价通常为固定单价
#         amount, eff_price = _compute_fee_with_rule(rule_order, Decimal(1))
#         amount = _apply_caps_bundles_day(rule_order, owner_id, warehouse_id, svc_date, amount)
#         if rule_order.min_charge and amount < rule_order.min_charge:
#             amount = rule_order.min_charge
#         if amount <= 0:
#             continue
#         # 数量为 1 时，有效单价等于总金额
#         eff_price = _q(amount, "0.0001")
#         tax_amount = _q(amount * (rule_order.tax_rate or 0), "0.01") if rule_order.taxable else Decimal("0.00")
#         # 打包键优先从解析器结果取，否则用规则默认打包键
#         bk = bundle_by_date.get(svc_date) or (rule_order.bundle_key or "")
#         acc_fp = _acc_fp(owner_id, warehouse_id, rule_order.id, ChargeType.DISPATCH, svc_date, 1, eff_price, rule_order.currency, ev_fp)
#         _, acc_new = BillingAccrual.objects.get_or_create(
#             acc_fingerprint=acc_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.DISPATCH, rule=rule_order,
#                 service_date=svc_date, currency=rule_order.currency, quantity=1, unit_price=_q(eff_price, "0.0001"),
#                 amount=amount, tax_amount=tax_amount, status=AccrualStatus.OPEN, event=event, created_by=by_user,
#                 bundle_key=bk
#             )
#         )
#         created_events += int(ev_new)
#         created_accruals += int(acc_new)
#
#     # ── 维度 2：PER_ORDER_LINE（每单行费，ChargeType=DISPATCH）───────────────
#     # 每个 (order_id, line_id, svc_date) 独立产生一条事件和一条应计，计费数量固定为 1
#     for (_oid, _olid, svc_date) in sorted(order_lines):
#         rule_line = _rule_for(ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date)
#         if not rule_line:
#             continue
#         # 事件指纹：同时包含订单ID和行ID，(order_id, line_id, date) 全局唯一
#         ev_fp = _event_fp(_oid, _olid, ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date, 1)
#         event, ev_new = BillingEvent.objects.get_or_create(
#             event_fp=ev_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.DISPATCH,
#                 service_date=svc_date, quantity=1, quantity_uom="ORDER_LINE"
#             )
#         )
#         amount, eff_price = _compute_fee_with_rule(rule_line, Decimal(1))
#         amount = _apply_caps_bundles_day(rule_line, owner_id, warehouse_id, svc_date, amount)
#         if rule_line.min_charge and amount < rule_line.min_charge:
#             amount = rule_line.min_charge
#         if amount <= 0:
#             continue
#         eff_price = _q(amount, "0.0001")
#         tax_amount = _q(amount * (rule_line.tax_rate or 0), "0.01") if rule_line.taxable else Decimal("0.00")
#         bk = bundle_by_date.get(svc_date) or (rule_line.bundle_key or "")
#         acc_fp = _acc_fp(owner_id, warehouse_id, rule_line.id, ChargeType.DISPATCH, svc_date, 1, eff_price, rule_line.currency, ev_fp)
#         _, acc_new = BillingAccrual.objects.get_or_create(
#             acc_fingerprint=acc_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.DISPATCH, rule=rule_line,
#                 service_date=svc_date, currency=rule_line.currency, quantity=1, unit_price=_q(eff_price, "0.0001"),
#                 amount=amount, tax_amount=tax_amount, status=AccrualStatus.OPEN, event=event, created_by=by_user,
#                 bundle_key=bk
#             )
#         )
#         created_events += int(ev_new)
#         created_accruals += int(acc_new)
#
#     # ── 维度 3：PER_PARCEL（每包裹费，ChargeType=PACK）─────────────────────
#     # 同日所有包裹合并为一条应计，计费数量为当日包裹总数（整日汇总后统一计费）
#     for svc_date, cnt in sorted(parcels_by_date.items()):
#         rule_parcel = _rule_for(ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date)
#         if not rule_parcel or cnt <= 0:
#             continue
#         # 事件指纹：task_id=None, log_id=None，以日期+数量区分（同日包裹已汇总）
#         ev_fp = _event_fp(None, None, ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date, cnt)
#         event, ev_new = BillingEvent.objects.get_or_create(
#             event_fp=ev_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.PACK,
#                 service_date=svc_date, quantity=_q(cnt, "0.0001"), quantity_uom="PARCEL"
#             )
#         )
#         # 以当日包裹总数参与阶梯定价（多包裹可能触发阶梯折扣）
#         amount, eff_price = _compute_fee_with_rule(rule_parcel, Decimal(cnt))
#         amount = _apply_caps_bundles_day(rule_parcel, owner_id, warehouse_id, svc_date, amount)
#         if rule_parcel.min_charge and cnt > 0 and amount < rule_parcel.min_charge:
#             amount = rule_parcel.min_charge
#         if amount <= 0:
#             continue
#         # 反推每包裹均摊单价
#         eff_price = _q((amount / Decimal(cnt)) if cnt > 0 else eff_price, "0.0001")
#         tax_amount = _q(amount * (rule_parcel.tax_rate or 0), "0.01") if rule_parcel.taxable else Decimal("0.00")
#         bk = bundle_by_date.get(svc_date) or (rule_parcel.bundle_key or "")
#         acc_fp = _acc_fp(owner_id, warehouse_id, rule_parcel.id, ChargeType.PACK, svc_date, cnt, eff_price, rule_parcel.currency, ev_fp)
#         _, acc_new = BillingAccrual.objects.get_or_create(
#             acc_fingerprint=acc_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.PACK, rule=rule_parcel,
#                 service_date=svc_date, currency=rule_parcel.currency, quantity=_q(cnt, "0.0001"),
#                 unit_price=_q(eff_price, "0.0001"), amount=amount, tax_amount=tax_amount,
#                 status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=bk
#             )
#         )
#         created_events += int(ev_new)
#         created_accruals += int(acc_new)
#
#     # ── 维度 4：PERCENT_OF_ORDER_AMOUNT（订单金额百分比，ChargeType=DISPATCH）─
#     # 计费基数：订单商品金额（货值），费率从规则获取（如 1.5% 的增值服务费）
#     # quantity 字段存储金额，unit_price 字段存储费率（百分比）
#
#     # 先从扫描日志解析结果取已有的订单金额数据
#     by_date_amounts = dict(order_amount_by_date)
#     # 识别区间内没有订单金额数据的日期（需要从指标表兜底）
#     missing_dates = []
#     for n in range((end_date - start_date).days + 1):
#         d = start_date + datetime.timedelta(days=n)
#         if d not in by_date_amounts:
#             missing_dates.append(d)
#     # ORDER_AMT 兜底：从 BillingMetricDaily 补充缺失日期的订单金额
#     # 场景：当日可能无相关扫描操作，但订单金额已由其他系统录入指标表
#     if missing_dates:
#         ms = (BillingMetricDaily.objects
#               .filter(owner_id=owner_id, warehouse_id=warehouse_id, service_date__in=missing_dates, metric_type=MetricType.ORDER_AMT))
#         for m in ms:
#             by_date_amounts[m.service_date] = by_date_amounts.get(m.service_date, Decimal("0")) + Decimal(m.value)
#
#     for svc_date, amt in sorted(by_date_amounts.items()):
#         rule_pct = _rule_for(ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date)
#         if not rule_pct:
#             continue
#         amt = Decimal(amt or 0)
#         if amt <= 0:
#             continue
#         # 以订单金额为基数乘以费率，_compute_fee_with_rule 对 PERCENT 类型返回 (金额×费率, 费率)
#         amount, eff_rate = _compute_fee_with_rule(rule_pct, amt)
#         amount = _apply_caps_bundles_day(rule_pct, owner_id, warehouse_id, svc_date, amount)
#         if rule_pct.min_charge and amount < rule_pct.min_charge:
#             amount = rule_pct.min_charge
#         if amount <= 0:
#             continue
#         # 反推有效费率（费用 / 订单金额），单价字段语义为"费率"而非"单价"
#         eff_rate = _q((amount / amt) if amt > 0 else eff_rate, "0.0001")
#         tax_amount = _q(amount * (rule_pct.tax_rate or 0), "0.01") if rule_pct.taxable else Decimal("0.00")
#         # 事件数量单位为 CURRENCY（货币金额），表示计费基数是一笔订单金额
#         ev_fp = _event_fp(None, None, ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date, amt)
#         event, ev_new = BillingEvent.objects.get_or_create(
#             event_fp=ev_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, charge_type=ChargeType.DISPATCH,
#                 service_date=svc_date, quantity=_q(amt, "0.01"), quantity_uom="CURRENCY"
#             )
#         )
#         bk = bundle_by_date.get(svc_date) or (rule_pct.bundle_key or "")
#         acc_fp = _acc_fp(owner_id, warehouse_id, rule_pct.id, ChargeType.DISPATCH, svc_date, amt, eff_rate, rule_pct.currency, ev_fp)
#         _, acc_new = BillingAccrual.objects.get_or_create(
#             acc_fingerprint=acc_fp,
#             defaults=dict(
#                 owner_id=owner_id, warehouse_id=warehouse_id, period=None, charge_type=ChargeType.DISPATCH, rule=rule_pct,
#                 service_date=svc_date, currency=rule_pct.currency, quantity=_q(amt, "0.01"),
#                 unit_price=_q(eff_rate, "0.0001"), amount=amount, tax_amount=tax_amount,
#                 status=AccrualStatus.OPEN, event=event, created_by=by_user, bundle_key=bk
#             )
#         )
#         created_events += int(ev_new)
#         created_accruals += int(acc_new)
#
#     logger.info(
#         "accrue_order_processing_from_posted: owner=%s warehouse=%s %s~%s events=%d accruals=%d",
#         owner_id, warehouse_id, start_date, end_date, created_events, created_accruals,
#     )
#     return created_events, created_accruals

@transaction.atomic
def accrue_order_processing_from_posted(
    owner_id,
    warehouse_id,
    start_date,
    end_date,
    by_user=None,
    allowed_methods: Optional[Set[str]] = None,
) -> Tuple[int, int]:
    from allapp.tasking.models import TaskScanLog

    resolver = _load_taskline_order_resolver()

    def _method_enabled(method: str) -> bool:
        if allowed_methods is None:
            return True
        return method in allowed_methods

    def _rule_for(
        rule_cache: Dict[Tuple[str, str, datetime.date], Optional[BillingRule]],
        charge_type: str,
        calc_method: str,
        service_date: datetime.date,
    ) -> Optional[BillingRule]:
        key = (charge_type, calc_method, service_date)
        if key not in rule_cache:
            rule_cache[key] = _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date)
        return rule_cache[key]

    def _task_level_accrual_exists(charge_type: str, calc_method: str, svc_date: datetime.date) -> bool:
        scope_prefix = f"{owner_id}:{warehouse_id}|-|-|"
        return (
            BillingEvent.objects
            .filter(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                charge_type=charge_type,
                service_date=svc_date,
            )
            .exclude(event_fp__startswith=scope_prefix)
            .filter(event_fp__contains=f"|{calc_method}|")
            .exists()
        )

    logs = (
        TaskScanLog.objects
        .filter(owner_id=owner_id, warehouse_id=warehouse_id, status="OK", posted_at__isnull=False)
        .filter(posted_at__date__gte=start_date, posted_at__date__lte=end_date)
        .filter(task__task_type__in=["DISPATCH", "PACK", "REVIEW", "PICK"])
        .select_related("task", "task_line")
    )

    rule_cache: Dict[Tuple[str, str, datetime.date], Optional[BillingRule]] = {}
    order_ids: Set[Tuple[int, datetime.date]] = set()
    order_lines: Set[Tuple[int, int, datetime.date]] = set()
    parcels_by_date: Dict[datetime.date, int] = {}
    bundle_by_date: Dict[datetime.date, str] = {}
    mapped_order_amount_by_task_date: Dict[Tuple[datetime.date, int], Decimal] = {}
    line_ids_by_task_date: Dict[Tuple[datetime.date, int], Set[int]] = {}

    for log in logs:
        if not log.task_line or resolver is None:
            continue

        mapping = resolver(log.task_line) or {}
        svc_date = (log.posted_at or timezone.now()).date()
        pct_amount_key = None
        if log.task_id and _is_order_processing_amount_source(log.task):
            pct_amount_key = (svc_date, log.task_id)

        for oid in mapping.get("order_ids", set()):
            order_ids.add((oid, svc_date))

        for order_id, line_id in mapping.get("order_line_ids", set()):
            order_lines.add((order_id, line_id, svc_date))
            if pct_amount_key is not None:
                line_ids_by_task_date.setdefault(pct_amount_key, set()).add(line_id)

        if mapping.get("parcels"):
            parcels_by_date[svc_date] = parcels_by_date.get(svc_date, 0) + int(mapping["parcels"])

        if pct_amount_key is not None and mapping.get("order_amount"):
            mapped_order_amount_by_task_date[pct_amount_key] = (
                mapped_order_amount_by_task_date.get(pct_amount_key, Decimal("0"))
                + Decimal(mapping["order_amount"])
            )

        if mapping.get("bundle_key"):
            bundle_by_date[svc_date] = mapping["bundle_key"]

    line_amount_by_id = _load_order_line_amounts(
        line_id
        for line_ids in line_ids_by_task_date.values()
        for line_id in line_ids
    )
    created_events = created_accruals = 0

    for (_oid, svc_date) in sorted(order_ids):
        if not _method_enabled(CalcMethod.PER_ORDER):
            continue

        rule_order = _rule_for(rule_cache, ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date)
        if not rule_order:
            continue

        ev_fp = _event_fp(_oid, None, ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date, 1)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                charge_type=ChargeType.DISPATCH,
                service_date=svc_date,
                quantity=1,
                quantity_uom="ORDER",
            ),
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
        acc_fp = _acc_fp(
            owner_id,
            warehouse_id,
            rule_order.id,
            ChargeType.DISPATCH,
            svc_date,
            1,
            eff_price,
            rule_order.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                period=None,
                charge_type=ChargeType.DISPATCH,
                rule=rule_order,
                service_date=svc_date,
                currency=rule_order.currency,
                quantity=1,
                unit_price=_q(eff_price, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    for (_oid, _olid, svc_date) in sorted(order_lines):
        if not _method_enabled(CalcMethod.PER_ORDER_LINE):
            continue

        rule_line = _rule_for(rule_cache, ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date)
        if not rule_line:
            continue

        ev_fp = _event_fp(_oid, _olid, ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date, 1)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                charge_type=ChargeType.DISPATCH,
                service_date=svc_date,
                quantity=1,
                quantity_uom="ORDER_LINE",
            ),
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
        acc_fp = _acc_fp(
            owner_id,
            warehouse_id,
            rule_line.id,
            ChargeType.DISPATCH,
            svc_date,
            1,
            eff_price,
            rule_line.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                period=None,
                charge_type=ChargeType.DISPATCH,
                rule=rule_line,
                service_date=svc_date,
                currency=rule_line.currency,
                quantity=1,
                unit_price=_q(eff_price, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    for svc_date, cnt in sorted(parcels_by_date.items()):
        if not _method_enabled(CalcMethod.PER_PARCEL):
            continue

        rule_parcel = _rule_for(rule_cache, ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date)
        if not rule_parcel or cnt <= 0:
            continue

        if _task_level_accrual_exists(ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date):
            logger.warning(
                "accrue_order_processing_from_posted: skipping PER_PARCEL for date=%s "
                "(task-level accrual already exists)",
                svc_date,
            )
            continue

        ev_fp = _event_fp(
            None,
            None,
            ChargeType.PACK,
            CalcMethod.PER_PARCEL,
            svc_date,
            cnt,
            scope_key=f"{owner_id}:{warehouse_id}",
        )
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                charge_type=ChargeType.PACK,
                service_date=svc_date,
                quantity=_q(cnt, "0.0001"),
                quantity_uom="PARCEL",
            ),
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
        acc_fp = _acc_fp(
            owner_id,
            warehouse_id,
            rule_parcel.id,
            ChargeType.PACK,
            svc_date,
            cnt,
            eff_price,
            rule_parcel.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                period=None,
                charge_type=ChargeType.PACK,
                rule=rule_parcel,
                service_date=svc_date,
                currency=rule_parcel.currency,
                quantity=_q(cnt, "0.0001"),
                unit_price=_q(eff_price, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    by_task_amounts: Dict[Tuple[datetime.date, int], Decimal] = dict(mapped_order_amount_by_task_date)
    for key, line_ids in line_ids_by_task_date.items():
        if key in by_task_amounts:
            continue
        total = sum(line_amount_by_id.get(line_id, Decimal("0")) for line_id in line_ids)
        if total > 0:
            by_task_amounts[key] = total

    for (svc_date, task_id), amt in sorted(by_task_amounts.items()):
        if not _method_enabled(CalcMethod.PERCENT_OF_ORDER_AMOUNT):
            continue

        rule_pct = _rule_for(rule_cache, ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date)
        if not rule_pct:
            continue

        amt = Decimal(amt or 0)
        if amt <= 0:
            continue

        amount, eff_rate = _compute_fee_with_rule(rule_pct, amt)
        amount = _apply_caps_bundles_day(rule_pct, owner_id, warehouse_id, svc_date, amount)
        if rule_pct.min_charge and amount < rule_pct.min_charge:
            amount = rule_pct.min_charge
        if amount <= 0:
            continue

        eff_rate = _q((amount / amt) if amt > 0 else eff_rate, "0.0001")
        tax_amount = _q(amount * (rule_pct.tax_rate or 0), "0.01") if rule_pct.taxable else Decimal("0.00")
        ev_fp = _event_fp(task_id, None, ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date, amt)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                charge_type=ChargeType.DISPATCH,
                service_date=svc_date,
                quantity=_q(amt, "0.01"),
                quantity_uom="CURRENCY",
            ),
        )

        bk = bundle_by_date.get(svc_date) or (rule_pct.bundle_key or "")
        acc_fp = _acc_fp(
            owner_id,
            warehouse_id,
            rule_pct.id,
            ChargeType.DISPATCH,
            svc_date,
            amt,
            eff_rate,
            rule_pct.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=owner_id,
                warehouse_id=warehouse_id,
                period=None,
                charge_type=ChargeType.DISPATCH,
                rule=rule_pct,
                service_date=svc_date,
                currency=rule_pct.currency,
                quantity=_q(amt, "0.01"),
                unit_price=_q(eff_rate, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    logger.info(
        "accrue_order_processing_from_posted: owner=%s warehouse=%s %s~%s events=%d accruals=%d",
        owner_id,
        warehouse_id,
        start_date,
        end_date,
        created_events,
        created_accruals,
    )
    return created_events, created_accruals


@transaction.atomic
def accrue_order_processing_for_task(
    task,
    posting_journal,
    by_user=None,
    allowed_methods: Optional[Set[str]] = None,
) -> Tuple[int, int]:
    from allapp.tasking.models import TaskScanLog

    resolver = _load_taskline_order_resolver()

    def _method_enabled(method: str) -> bool:
        if allowed_methods is None:
            return True
        return method in allowed_methods

    def _rule_for(
        rule_cache: Dict[Tuple[str, str, datetime.date], Optional[BillingRule]],
        charge_type: str,
        calc_method: str,
        service_date: datetime.date,
    ) -> Optional[BillingRule]:
        key = (charge_type, calc_method, service_date)
        if key not in rule_cache:
            rule_cache[key] = _select_rule(
                task.owner_id,
                task.warehouse_id,
                charge_type,
                calc_method,
                service_date,
            )
        return rule_cache[key]

    def _batch_accrual_exists(charge_type: str, calc_method: str, svc_date: datetime.date) -> bool:
        scope_prefix = f"{task.owner_id}:{task.warehouse_id}|-|-|"
        return (
            BillingEvent.objects
            .filter(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                charge_type=charge_type,
                service_date=svc_date,
                event_fp__startswith=scope_prefix,
                event_fp__contains=f"|{calc_method}|",
            )
            .exists()
        )

    logs = (
        TaskScanLog.objects
        .filter(
            task=task,
            status="OK",
            posted_at__isnull=False,
            posting_journal=posting_journal,
        )
        .select_related("task", "task_line")
    )

    rule_cache: Dict[Tuple[str, str, datetime.date], Optional[BillingRule]] = {}
    order_ids: Set[Tuple[int, datetime.date]] = set()
    order_lines: Set[Tuple[int, int, datetime.date]] = set()
    parcels_by_date: Dict[datetime.date, int] = {}
    bundle_by_date: Dict[datetime.date, str] = {}
    mapped_order_amount_by_date: Dict[datetime.date, Decimal] = {}
    line_ids_by_date: Dict[datetime.date, Set[int]] = {}

    log_count = 0
    for log in logs:
        log_count += 1
        if not log.task_line or resolver is None:
            logger.debug(
                "accrue_order_processing_for_task: skip log=%s task_line=%s resolver=%s",
                log.id,
                log.task_line_id,
                resolver is not None,
            )
            continue

        mapping = resolver(log.task_line) or {}
        svc_date = (log.posted_at or timezone.now()).date()
        logger.info(
            "accrue_order_processing_for_task: log=%s task_line=%s mapping_keys=%s "
            "order_ids=%s order_line_ids=%s",
            log.id,
            log.task_line_id,
            list(mapping.keys()),
            mapping.get("order_ids"),
            mapping.get("order_line_ids"),
        )

        for oid in mapping.get("order_ids", set()):
            order_ids.add((oid, svc_date))

        for order_id, line_id in mapping.get("order_line_ids", set()):
            order_lines.add((order_id, line_id, svc_date))
            line_ids_by_date.setdefault(svc_date, set()).add(line_id)

        if mapping.get("parcels"):
            parcels_by_date[svc_date] = parcels_by_date.get(svc_date, 0) + int(mapping["parcels"])

        if mapping.get("order_amount"):
            mapped_order_amount_by_date[svc_date] = (
                mapped_order_amount_by_date.get(svc_date, Decimal("0"))
                + Decimal(mapping["order_amount"])
            )

        if mapping.get("bundle_key"):
            bundle_by_date[svc_date] = mapping["bundle_key"]

    logger.info(
        "accrue_order_processing_for_task: scan_log_count=%d order_ids=%s order_line_ids=%s "
        "line_ids_by_date=%s mapped_order_amount_by_date=%s",
        log_count,
        order_ids,
        order_lines,
        line_ids_by_date,
        mapped_order_amount_by_date,
    )

    line_amount_by_id = _load_order_line_amounts(
        line_id
        for line_ids in line_ids_by_date.values()
        for line_id in line_ids
    )
    by_date_amounts: Dict[datetime.date, Decimal] = dict(mapped_order_amount_by_date)
    for svc_date, line_ids in line_ids_by_date.items():
        if svc_date in by_date_amounts:
            continue
        total = sum(line_amount_by_id.get(line_id, Decimal("0")) for line_id in line_ids)
        if total > 0:
            by_date_amounts[svc_date] = total

    logger.info(
        "accrue_order_processing_for_task: line_amount_by_id=%s by_date_amounts=%s",
        line_amount_by_id,
        by_date_amounts,
    )

    created_events = created_accruals = 0

    for (_oid, svc_date) in sorted(order_ids):
        if not _method_enabled(CalcMethod.PER_ORDER):
            continue

        rule_order = _rule_for(rule_cache, ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date)
        if not rule_order:
            continue

        ev_fp = _event_fp(_oid, None, ChargeType.DISPATCH, CalcMethod.PER_ORDER, svc_date, 1)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                charge_type=ChargeType.DISPATCH,
                service_date=svc_date,
                quantity=1,
                quantity_uom="ORDER",
            ),
        )

        amount, eff_price = _compute_fee_with_rule(rule_order, Decimal(1))
        amount = _apply_caps_bundles_day(rule_order, task.owner_id, task.warehouse_id, svc_date, amount)
        if rule_order.min_charge and amount < rule_order.min_charge:
            amount = rule_order.min_charge
        if amount <= 0:
            continue

        eff_price = _q(amount, "0.0001")
        tax_amount = _q(amount * (rule_order.tax_rate or 0), "0.01") if rule_order.taxable else Decimal("0.00")
        bk = bundle_by_date.get(svc_date) or (rule_order.bundle_key or "")
        acc_fp = _acc_fp(
            task.owner_id,
            task.warehouse_id,
            rule_order.id,
            ChargeType.DISPATCH,
            svc_date,
            1,
            eff_price,
            rule_order.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                period=None,
                charge_type=ChargeType.DISPATCH,
                rule=rule_order,
                service_date=svc_date,
                currency=rule_order.currency,
                quantity=1,
                unit_price=_q(eff_price, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    for (_oid, _olid, svc_date) in sorted(order_lines):
        if not _method_enabled(CalcMethod.PER_ORDER_LINE):
            continue

        rule_line = _rule_for(rule_cache, ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date)
        if not rule_line:
            continue

        ev_fp = _event_fp(_oid, _olid, ChargeType.DISPATCH, CalcMethod.PER_ORDER_LINE, svc_date, 1)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                charge_type=ChargeType.DISPATCH,
                service_date=svc_date,
                quantity=1,
                quantity_uom="ORDER_LINE",
            ),
        )

        amount, eff_price = _compute_fee_with_rule(rule_line, Decimal(1))
        amount = _apply_caps_bundles_day(rule_line, task.owner_id, task.warehouse_id, svc_date, amount)
        if rule_line.min_charge and amount < rule_line.min_charge:
            amount = rule_line.min_charge
        if amount <= 0:
            continue

        eff_price = _q(amount, "0.0001")
        tax_amount = _q(amount * (rule_line.tax_rate or 0), "0.01") if rule_line.taxable else Decimal("0.00")
        bk = bundle_by_date.get(svc_date) or (rule_line.bundle_key or "")
        acc_fp = _acc_fp(
            task.owner_id,
            task.warehouse_id,
            rule_line.id,
            ChargeType.DISPATCH,
            svc_date,
            1,
            eff_price,
            rule_line.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                period=None,
                charge_type=ChargeType.DISPATCH,
                rule=rule_line,
                service_date=svc_date,
                currency=rule_line.currency,
                quantity=1,
                unit_price=_q(eff_price, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    for svc_date, cnt in sorted(parcels_by_date.items()):
        if not _method_enabled(CalcMethod.PER_PARCEL):
            continue

        if _batch_accrual_exists(ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date):
            logger.warning(
                "accrue_order_processing_for_task: skipping PER_PARCEL for task=%s date=%s "
                "(batch accrual already exists)",
                task.id,
                svc_date,
            )
            continue

        rule_parcel = _rule_for(rule_cache, ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date)
        if not rule_parcel or cnt <= 0:
            continue

        ev_fp = _event_fp(task.id, None, ChargeType.PACK, CalcMethod.PER_PARCEL, svc_date, cnt)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                charge_type=ChargeType.PACK,
                service_date=svc_date,
                quantity=_q(cnt, "0.0001"),
                quantity_uom="PARCEL",
            ),
        )

        amount, eff_price = _compute_fee_with_rule(rule_parcel, Decimal(cnt))
        amount = _apply_caps_bundles_day(rule_parcel, task.owner_id, task.warehouse_id, svc_date, amount)
        if rule_parcel.min_charge and cnt > 0 and amount < rule_parcel.min_charge:
            amount = rule_parcel.min_charge
        if amount <= 0:
            continue

        eff_price = _q((amount / Decimal(cnt)) if cnt > 0 else eff_price, "0.0001")
        tax_amount = _q(amount * (rule_parcel.tax_rate or 0), "0.01") if rule_parcel.taxable else Decimal("0.00")
        bk = bundle_by_date.get(svc_date) or (rule_parcel.bundle_key or "")
        acc_fp = _acc_fp(
            task.owner_id,
            task.warehouse_id,
            rule_parcel.id,
            ChargeType.PACK,
            svc_date,
            cnt,
            eff_price,
            rule_parcel.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                period=None,
                charge_type=ChargeType.PACK,
                rule=rule_parcel,
                service_date=svc_date,
                currency=rule_parcel.currency,
                quantity=_q(cnt, "0.0001"),
                unit_price=_q(eff_price, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    for svc_date, amt in sorted(by_date_amounts.items()):
        if not _method_enabled(CalcMethod.PERCENT_OF_ORDER_AMOUNT):
            continue

        if _batch_accrual_exists(ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date):
            logger.warning(
                "accrue_order_processing_for_task: skipping PERCENT_OF_ORDER_AMOUNT for task=%s date=%s "
                "(batch accrual already exists)",
                task.id,
                svc_date,
            )
            continue

        rule_pct = _rule_for(rule_cache, ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date)
        if not rule_pct:
            continue

        amt = Decimal(amt or 0)
        if amt <= 0:
            continue

        amount, eff_rate = _compute_fee_with_rule(rule_pct, amt)
        amount = _apply_caps_bundles_day(rule_pct, task.owner_id, task.warehouse_id, svc_date, amount)
        if rule_pct.min_charge and amount < rule_pct.min_charge:
            amount = rule_pct.min_charge
        if amount <= 0:
            continue

        eff_rate = _q((amount / amt) if amt > 0 else eff_rate, "0.0001")
        tax_amount = _q(amount * (rule_pct.tax_rate or 0), "0.01") if rule_pct.taxable else Decimal("0.00")
        ev_fp = _event_fp(task.id, None, ChargeType.DISPATCH, CalcMethod.PERCENT_OF_ORDER_AMOUNT, svc_date, amt)
        event, ev_new = BillingEvent.objects.get_or_create(
            event_fp=ev_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                charge_type=ChargeType.DISPATCH,
                service_date=svc_date,
                quantity=_q(amt, "0.01"),
                quantity_uom="CURRENCY",
            ),
        )

        bk = bundle_by_date.get(svc_date) or (rule_pct.bundle_key or "")
        acc_fp = _acc_fp(
            task.owner_id,
            task.warehouse_id,
            rule_pct.id,
            ChargeType.DISPATCH,
            svc_date,
            amt,
            eff_rate,
            rule_pct.currency,
            ev_fp,
        )
        _, acc_new = BillingAccrual.objects.get_or_create(
            acc_fingerprint=acc_fp,
            defaults=dict(
                owner_id=task.owner_id,
                warehouse_id=task.warehouse_id,
                period=None,
                charge_type=ChargeType.DISPATCH,
                rule=rule_pct,
                service_date=svc_date,
                currency=rule_pct.currency,
                quantity=_q(amt, "0.01"),
                unit_price=_q(eff_rate, "0.0001"),
                amount=amount,
                tax_amount=tax_amount,
                status=AccrualStatus.OPEN,
                event=event,
                created_by=by_user,
                bundle_key=bk,
            ),
        )
        created_events += int(ev_new)
        created_accruals += int(acc_new)

    logger.info(
        "accrue_order_processing_for_task: task_id=%s owner=%s warehouse=%s journal=%s events=%d accruals=%d",
        task.id,
        task.owner_id,
        task.warehouse_id,
        posting_journal.id if posting_journal else None,
        created_events,
        created_accruals,
    )
    return created_events, created_accruals

