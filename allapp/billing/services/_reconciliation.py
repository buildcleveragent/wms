# allapp/billing/services/_reconciliation.py
"""
计费数据核对门控模块（Billing Reconciliation Gates）

背景与设计意图
--------------
仓库计费系统的正确性依赖两类数据的一致性：
  1. 库存数据（inventory）：货物的实际库位、数量、状态；
  2. 计费数据（billing）：基于库存快照和业务操作生成的费用记录。

当这两类数据出现差异（例如：库存调整未同步到计费记录、并发写入导致的中间态残留），
若直接允许下游操作继续执行（如：出账、锁定账期、生成发票），
则会将"脏数据"固化到财务记录中，事后几乎无法自动修复。

"门控（gate）"机制
------------------
本模块通过"先核对、不通过则阻断"的模式保护所有高风险计费操作：

    _ensure_reconciliation_*(...)   ← 核对入口
        ↓
    reconcile_data_accuracy(...)    ← 调用 core 层执行实际差异检测
        ↓
    _raise_if_reconciliation_failed(...)  ← 如存在问题则抛出异常
        ↓
    BillingAccuracyGateError        ← 调用方捕获异常，终止当前操作

调用方（services 层的各计费操作函数）在执行核心逻辑前，必须先调用对应的
_ensure_reconciliation_* 函数。若核对通过（无差异），则函数静默返回，
操作继续；若核对失败，则抛出 BillingAccuracyGateError，操作被阻断。

开关控制
--------
门控可通过 Django settings 全局或按检测类型禁用（用于测试环境或
紧急绕过），避免在核对服务不可用时完全阻塞业务流程：
  - BILLING_RECONCILIATION_GATE_ENABLED = False  → 关闭所有门控
  - 具体的 setting_name 开关 = False             → 关闭某类门控
"""
import datetime
from typing import Iterable

from django.conf import settings

from allapp.billing.models import BillingPeriod
from allapp.core.data_accuracy import reconcile_data_accuracy

from ._common import BillingAccuracyGateError, logger


def _billing_accuracy_gate_enabled(setting_name: str) -> bool:
    """
    判断指定门控开关是否启用。

    设计了两级开关：
      1. 全局总开关 BILLING_RECONCILIATION_GATE_ENABLED：
         若为 False，则所有门控均被禁用，直接返回 False。
         默认值为 True（即默认启用）。
      2. 特定门控开关 setting_name：
         在全局开关未关闭的前提下，读取该具体开关的值。
         默认值也为 True（即默认启用）。

    参数
    ----
    setting_name : str
        Django settings 中特定门控开关的属性名，例如
        "BILLING_GATE_ACCRUAL_ENABLED"。

    返回值
    ------
    bool
        True 表示该门控当前处于启用状态，应执行核对逻辑；
        False 表示已被配置禁用，可跳过核对。

    典型使用场景
    ------------
    在各 _ensure_reconciliation_* 的上层包装函数中调用，例如：

        if not _billing_accuracy_gate_enabled("BILLING_GATE_ACCRUAL_ENABLED"):
            return
        _ensure_reconciliation_for_service_date(...)
    """
    if not getattr(settings, "BILLING_RECONCILIATION_GATE_ENABLED", True):
        return False
    return getattr(settings, setting_name, True)


def _date_range(start_date: datetime.date, end_date: datetime.date):
    """
    生成从 start_date 到 end_date（含两端）的逐日日期序列。

    使用 generator 实现，内存占用与日期跨度无关，适合账期通常为
    月度（~30 天）的场景。

    参数
    ----
    start_date : datetime.date
        起始日期（含）。
    end_date : datetime.date
        结束日期（含）。如果等于 start_date，则只生成一个日期。

    Yields
    ------
    datetime.date
        [start_date, start_date+1, ..., end_date] 中的每一天。

    注意
    ----
    若 end_date < start_date，day_count 为负，range(day_count + 1) 为
    空序列，函数不产生任何输出，不会抛出异常。
    """
    day_count = (end_date - start_date).days
    for offset in range(day_count + 1):
        yield start_date + datetime.timedelta(days=offset)


def _failed_check_names(result) -> list[str]:
    """
    从单次 reconcile_data_accuracy 的返回结果中提取失败检测项的名称列表。

    reconcile_data_accuracy 的返回结构如下（简化示意）：
    {
        "issue_count": 3,
        "inventory": {
            "checks": [
                {"name": "stock_location_integrity", "ok": False},
                {"name": "uom_consistency",          "ok": True},
            ]
        },
        "billing": {
            "checks": [
                {"name": "accrual_completeness", "ok": False},
            ]
        }
    }

    本函数遍历 "inventory" 和 "billing" 两个顶级 section，
    收集所有 ok == False 的检测项名称，供日志和异常详情使用。

    参数
    ----
    result : dict
        reconcile_data_accuracy 返回的原始结果字典。

    返回值
    ------
    list[str]
        失败检测项的名称列表。若所有检测均通过，返回空列表。
    """
    failed = []
    for section_name in ("inventory", "billing"):
        # 若该 section 不存在或为空（例如调用时未要求该类核对），跳过
        section = result.get(section_name)
        if not section:
            continue
        failed.extend(check["name"] for check in section["checks"] if not check["ok"])
    return failed


def _raise_if_reconciliation_failed(*, stage: str, results):
    """
    汇总多个核对结果，若存在任何问题则抛出 BillingAccuracyGateError。

    本函数是"门控"机制的核心判断点：它将来自不同日期/维度的多个
    reconcile_data_accuracy 结果聚合后统一判断，做到"任一维度失败
    则整体阻断"，而非逐个单独判断。

    执行逻辑
    --------
    1. 累加所有结果的 issue_count：
       - 若总计为 0，说明数据完全一致，函数直接返回（门控放行）。
    2. 存在问题时：
       a. 收集所有失败检测项名称（最多记录前 5 个用于日志，避免日志过长）；
       b. 构建带作用域标签（scope_label）的结构化结果列表；
       c. 以 WARNING 级别写入日志，便于运维快速定位；
       d. 抛出 BillingAccuracyGateError，携带完整的诊断信息。

    参数
    ----
    stage : str
        当前被保护的操作阶段名称，例如 "accrual_generation"、
        "period_lock"。用于日志和异常中标识是哪个操作被阻断。
    results : Iterable[tuple[str, dict]]
        可迭代的 (scope_label, result) 二元组序列。
        - scope_label：该结果的作用域描述，通常为日期字符串（如
          "2024-01-15"）或账期标签（如 "2024-01"），用于在错误
          详情中区分不同维度的结果。
        - result：reconcile_data_accuracy 的返回字典。

    抛出
    ----
    BillingAccuracyGateError
        当 issue_count 总和大于 0 时抛出，包含：
        - stage：被阻断的操作阶段；
        - issue_count：差异总数；
        - failed_checks：失败检测项名称列表；
        - details：按作用域分组的结构化结果，供上层序列化后返回给客户端。

    设计决策
    --------
    - results 参数设计为可迭代（而非列表），支持惰性计算，但由于需要
      两次遍历（先求和、再构建详情），调用方传入的实际上都是已实例化的
      列表，此处保留灵活性以便未来优化。
    - 日志中 failed_checks[:5] 截断，避免检测项过多时日志行过长。
    """
    # 第一步：快速汇总总差异数，无问题则直接放行，避免不必要的遍历
    issue_count = sum(result["issue_count"] for _, result in results)
    if issue_count <= 0:
        return

    # 第二步：存在差异，收集详细信息用于日志和异常
    failed_checks = []
    scoped_results = []
    for scope_label, result in results:
        failed_checks.extend(_failed_check_names(result))
        scoped_results.append(
            {
                "scope": scope_label,        # 作用域标签（日期或账期）
                "issue_count": result["issue_count"],
                "result": result,            # 原始完整结果，供调试使用
            }
        )

    # 第三步：写入 WARNING 日志（最多展示前 5 个失败项，防止日志膨胀）
    logger.warning(
        "Reconciliation gate blocked %s: %d issues, checks=%s",
        stage, issue_count, failed_checks[:5],
    )

    # 第四步：抛出门控异常，阻断调用方的后续操作
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
    """
    对单个服务日期执行数据核对门控。

    这是最细粒度的核对入口，适用于针对"某一天"生成费用的操作，
    例如：每日计费跑批、单日费用重算。

    执行逻辑
    --------
    调用 reconcile_data_accuracy 对指定的 (owner, warehouse, date)
    组合执行核对，然后将结果交给 _raise_if_reconciliation_failed 处理。
    若核对通过则静默返回，否则抛出异常阻断后续操作。

    参数
    ----
    stage : str
        被保护操作的阶段名称，透传给异常和日志。
    owner_id :
        货主 ID（通常为整数），用于数据隔离。
        计费数据按货主严格隔离，跨货主操作需分别调用本函数。
    warehouse_id :
        仓库 ID，与 owner_id 共同确定核对范围。
    service_date : datetime.date
        需要核对的服务日期。核对结果的 scope_label 为该日期的
        ISO 格式字符串（如 "2024-01-15"）。
    include_inventory : bool, 默认 True
        是否包含库存一致性检测。对于纯计费核对可设为 False，
        减少不必要的查询开销。
    include_billing : bool, 默认 True
        是否包含计费记录完整性检测。

    抛出
    ----
    BillingAccuracyGateError
        核对发现差异时抛出。

    调用时机
    --------
    通常由 services 层的计费跑批函数在生成当日费用前调用：

        _ensure_reconciliation_for_service_date(
            stage="daily_accrual",
            owner_id=owner.id,
            warehouse_id=warehouse.id,
            service_date=today,
        )
        # 上方若未抛出，则继续执行生成费用逻辑
    """
    result = reconcile_data_accuracy(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        service_date=service_date,
        include_inventory=include_inventory,
        include_billing=include_billing,
        limit=5,   # 每类检测最多返回 5 条问题样本，够诊断用，不过载内存
    )
    _raise_if_reconciliation_failed(
        stage=stage,
        results=[(service_date.isoformat(), result)],  # scope_label 为日期字符串
    )


def _ensure_reconciliation_for_date_range(
    *,
    stage: str,
    owner_id,
    warehouse_id,
    start_date: datetime.date,
    end_date: datetime.date,
):
    """
    对日期范围内的所有服务日期执行数据核对门控。

    适用于覆盖多天的操作，例如：账期结算、批量出账、月度对账。
    与单日核对不同，本函数采用"分离策略"以避免重复核对库存：

    分离策略
    --------
    库存数据（inventory）不依赖具体服务日期，核对一次即可；
    计费数据（billing）按服务日期存储，需逐日核对。因此：

      1. 先对整个 (owner, warehouse) 维度执行一次库存核对
         （include_inventory=True, include_billing=False）；
      2. 再对 [start_date, end_date] 内每一天逐日执行计费核对
         （include_inventory=False, include_billing=True）。

    所有结果统一汇总后交给 _raise_if_reconciliation_failed，
    任意维度/日期存在问题即阻断整个操作。

    参数
    ----
    stage : str
        被保护操作的阶段名称，透传给异常和日志。
    owner_id :
        货主 ID。
    warehouse_id :
        仓库 ID。
    start_date : datetime.date
        日期范围的起始日（含）。
    end_date : datetime.date
        日期范围的结束日（含）。

    抛出
    ----
    BillingAccuracyGateError
        任意日期或库存核对发现差异时抛出。

    性能注意
    --------
    对于跨度较大的日期范围（如整季度），本函数会发起 N+1 次数据库
    查询（1 次库存 + N 天计费）。调用方应确保在合理的范围内使用，
    通常月度账期约 30 次查询，可接受。
    """
    results = []

    # 第一步：执行一次全量库存核对（不带 service_date，针对当前库存状态）
    # scope_label 固定为字符串 "inventory"，便于在错误详情中识别
    inventory_result = reconcile_data_accuracy(
        owner_id=owner_id,
        warehouse_id=warehouse_id,
        include_inventory=True,
        include_billing=False,
        limit=5,
    )
    results.append(("inventory", inventory_result))

    # 第二步：逐日执行计费核对，将每天的结果追加到 results 列表
    for service_date in _date_range(start_date, end_date):
        billing_result = reconcile_data_accuracy(
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            service_date=service_date,
            include_inventory=False,   # 库存已在第一步核对，此处跳过
            include_billing=True,
            limit=5,
        )
        results.append((service_date.isoformat(), billing_result))

    # 第三步：汇总所有结果，任一存在问题则抛出异常阻断操作
    _raise_if_reconciliation_failed(stage=stage, results=results)


def _ensure_reconciliation_for_period(*, stage: str, period: BillingPeriod):
    """
    对指定账期（BillingPeriod）执行数据核对门控。

    适用于账期级别的操作，例如：账期锁定（period lock）、
    账期审核、生成对账单。与日期范围核对不同，本函数直接将
    period_id 传递给 reconcile_data_accuracy，由核心层根据账期
    边界和关联记录执行针对性检测，无需调用方手动展开日期。

    参数
    ----
    stage : str
        被保护操作的阶段名称，透传给异常和日志。
    period : BillingPeriod
        目标账期对象，需包含：
        - period.owner_id：货主 ID；
        - period.warehouse_id：仓库 ID；
        - period.id：账期主键，用于精确定位账期内的计费记录；
        - period.label：账期显示标签（如 "2024-01"），用作 scope_label。

    抛出
    ----
    BillingAccuracyGateError
        账期内计费数据核对发现差异时抛出。

    设计决策
    --------
    - 本函数只检测计费数据（include_billing=True），不检测库存
      （include_inventory=False）。原因：账期锁定时关注的是计费
      记录是否完整、一致，而非实时库存状态。若需同时检测库存，
      请改用 _ensure_reconciliation_for_date_range。
    - scope_label 使用 period.label 而非日期字符串，使错误详情
      更易于人工识别（"2024-01" 比 "2024-01-01...2024-01-31" 简洁）。

    调用时机
    --------
    通常在账期锁定前调用：

        _ensure_reconciliation_for_period(
            stage="period_lock",
            period=billing_period,
        )
        # 上方若未抛出，则继续执行锁定逻辑
    """
    result = reconcile_data_accuracy(
        owner_id=period.owner_id,
        warehouse_id=period.warehouse_id,
        period_id=period.id,       # 按账期 ID 精确查询，效率优于按日期范围扫描
        include_inventory=False,   # 账期核对只关注计费记录一致性
        include_billing=True,
        limit=5,
    )
    _raise_if_reconciliation_failed(
        stage=stage,
        results=[(period.label, result)],  # scope_label 使用账期标签（如 "2024-01"）
    )
