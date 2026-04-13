# allapp/billing/services/__init__.py
"""
计费服务包 — 向后兼容的公共 API 统一导出。

本文件是 services/ 包的入口，负责从各子模块重新导出所有公共函数。
这样设计的目的是：将 1900 行的 services.py 拆分为多个子模块后，
外部消费方的 import 路径无需任何改动。

外部消费方（共 8 处）可以继续使用:
    from allapp.billing.services import lock_period, accrue_for_posting, ...

也支持 import 整个模块:
    from allapp.billing import services as billing_services
    billing_services.accrue_for_posting(...)

子模块划分:
    _common.py          — 共享工具函数、定价引擎、规则选择（内部模块，不对外暴露）
    _reconciliation.py  — 数据对账门控（内部模块）
    _metrics.py         — 指标构建器和存储（内部模块）
    accrual.py          — 费用应计生成（4 个 accrue_* 函数）
    metrics.py          — 指标生成和调度器
    period.py           — 关账、试算、撤销
    invoice.py          — 开票
"""

# 数据对账异常 — 供外部 except 捕获
from allapp.billing.services._common import BillingAccuracyGateError  # noqa: F401

# 费用应计生成 — 从作业过账、仓储在库、日指标、订单处理四个维度产生 accrual
from allapp.billing.services.accrual import (  # noqa: F401
    accrue_for_posting,
    accrue_metrics_for_date,
    accrue_order_processing_for_task,
    accrue_order_processing_from_posted,
    accrue_storage_for_date,
)

# 指标生成与调度 — 每日自动计算 PALLET/CBM/AREA/ORDER_AMT 四类指标
from allapp.billing.services.metrics import (  # noqa: F401
    generate_metrics_for_date,
    generate_metrics_for_range,
    run_scheduled_metric_generation_for_date,
    run_scheduled_metric_generation_for_dates,
)

# 账期管理 — 关账（锁定 accrual + 应用封顶/打包）、试算（dry-run）、撤销（回退/红冲）
from allapp.billing.services.period import (  # noqa: F401
    lock_period,
    preview_lock_period,
    unlock_period,
)

# 开票 — 从已关账的 period 生成 Bill + BillLine
from allapp.billing.services.invoice import (  # noqa: F401
    generate_invoice_for_period,
)

__all__ = [
    "BillingAccuracyGateError",
    "accrue_for_posting",
    "accrue_metrics_for_date",
    "accrue_order_processing_for_task",
    "accrue_order_processing_from_posted",
    "accrue_storage_for_date",
    "generate_metrics_for_date",
    "generate_metrics_for_range",
    "generate_invoice_for_period",
    "lock_period",
    "preview_lock_period",
    "run_scheduled_metric_generation_for_date",
    "run_scheduled_metric_generation_for_dates",
    "unlock_period",
]
