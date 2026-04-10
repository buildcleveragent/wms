# WMS 计费系统重构教程

本文档面向需要理解和维护计费模块的开发者，详细解释这次重构做了什么、为什么这样做、以及系统的运行机理。

---

## 目录

1. [重构前的问题](#1-重构前的问题)
2. [重构后的文件结构](#2-重构后的文件结构)
3. [向后兼容策略](#3-向后兼容策略)
4. [计费系统运行机理](#4-计费系统运行机理)
5. [数据流全景图](#5-数据流全景图)
6. [核心算法详解](#6-核心算法详解)
7. [新增功能：试算与撤销](#7-新增功能试算与撤销)
8. [Bug 修复说明](#8-bug-修复说明)
9. [Model 变更与迁移](#9-model-变更与迁移)
10. [如何扩展和修改](#10-如何扩展和修改)

---

## 1. 重构前的问题

重构前，所有计费业务逻辑集中在一个 `allapp/billing/services.py` 文件中（1900 行），存在以下问题：

| 问题 | 影响 |
|------|------|
| 单文件 1900 行 | 改指标逻辑要在封顶函数和开票函数之间反复跳转 |
| 无日志 | 生产环境出问题时无法从日志定位原因 |
| 无试算能力 | 关账是不可逆操作，客户无法提前看到最终金额 |
| 无撤销能力 | 关账/开票后发现错误，无法纠正 |
| 开票逐条写入 | 1000 条 accrual → 2000 次 DB 写入 |
| 封顶查询范围过大 | 查了所有 owner 的封顶规则，做了很多空循环 |
| ~105 行废弃注释代码 | 干扰阅读，分不清哪个版本是活的 |
| 竞态恢复用 sleep 轮询 | 最坏情况阻塞 1 秒 |

---

## 2. 重构后的文件结构

`services.py` 被拆分为 `services/` 包（Python package），按职责分为 8 个文件：

```
allapp/billing/services/
├── __init__.py          ← 统一导出，保证外部 import 不变
├── _common.py           ← 共享基础：精度、规则匹配、定价引擎、指纹
├── _reconciliation.py   ← 数据对账门控
├── _metrics.py          ← 指标构建器（PALLET/CBM/AREA/ORDER_AMT）
├── accrual.py           ← 费用应计生成（4 条路径）
├── metrics.py           ← 指标生成入口 + 调度器
├── period.py            ← 关账 + 试算 + 撤销
└── invoice.py           ← 开票
```

**命名约定**：
- `_` 前缀（如 `_common.py`）= 内部模块，只被 services 包内部引用
- 无前缀（如 `accrual.py`）= 包含公共 API 函数

**每个文件的职责和大小**：

| 文件 | 行数 | 核心职责 | 包含的公共函数 |
|------|------|---------|--------------|
| `_common.py` | 471 | 定价引擎、规则匹配、指纹、封顶 | （全部是内部函数） |
| `_reconciliation.py` | 442 | 数据一致性检查闸门 | （全部是内部函数） |
| `_metrics.py` | 964 | 4 种指标的构建和存储 | （全部是内部函数） |
| `accrual.py` | 811 | 从业务事件产生费用 | `accrue_for_posting`, `accrue_storage_for_date`, `accrue_metrics_for_date`, `accrue_order_processing_from_posted` |
| `metrics.py` | 432 | 指标生成和调度 | `generate_metrics_for_date`, `generate_metrics_for_range`, `run_scheduled_metric_generation_for_date`, `run_scheduled_metric_generation_for_dates` |
| `period.py` | 647 | 账期生命周期 | `lock_period`, `preview_lock_period`, `unlock_period` |
| `invoice.py` | 124 | 开票 | `generate_invoice_for_period` |
| `__init__.py` | 71 | 向后兼容导出 | （re-export 以上所有） |

---

## 3. 向后兼容策略

### 问题
项目中有 **8 个文件** 从 `allapp.billing.services` 导入函数：

```python
# views.py, admin.py, tests.py, test_business_flows.py,
# tasking/plugins/handlers.py, 以及 3 个 management commands
from allapp.billing.services import lock_period, accrue_for_posting, ...
```

### 解决方案
`services/__init__.py` 从各子模块 re-export 所有公共函数：

```python
# services/__init__.py
from allapp.billing.services.accrual import accrue_for_posting      # noqa
from allapp.billing.services.period import lock_period               # noqa
from allapp.billing.services.invoice import generate_invoice_for_period  # noqa
# ... 其余省略
```

Python 的包导入机制保证：
- `from allapp.billing.services import lock_period` → 找到 `services/__init__.py` → 从 `period.py` 获取
- `from allapp.billing import services as billing_services` → 同上

**所有 8 个消费方无需修改任何一行代码。**

### 为什么不用相对导入？
子模块之间使用相对导入（如 `from ._common import _q`），但 `__init__.py` 使用绝对导入（如 `from allapp.billing.services.accrual import ...`），避免循环导入。

---

## 4. 计费系统运行机理

### 4.1 系统总览

WMS 计费系统的核心任务是：**把仓库里发生的每一个操作，自动转化为「应该向客户收多少钱」**。

整个流程分为五个阶段：

```
仓库作业（收货/拣货/打包/...）
        │
        ▼
  ┌─────────────┐
  │  ① 事件采集  │   TaskScanLog → BillingEvent（指纹防重）
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  ② 费用计算  │   规则匹配 → 阶梯计价 → 封顶/打包 → 最低收费
  └──────┬──────┘
         │
         ▼
  ┌──────────────┐
  │ ③ 应计生成   │   BillingAccrual（status=OPEN）
  └──────┬───────┘
         │   lock_period()
         ▼
  ┌──────────────┐
  │ ④ 关账锁定   │   OPEN → LOCKED + 账期封顶/打包
  └──────┬───────┘
         │   generate_invoice_for_period()
         ▼
  ┌──────────────┐
  │ ⑤ 开票结算   │   Bill + BillLine（status=INVOICED）
  └──────────────┘
```

### 4.2 阶段详解

#### ① 事件采集

当仓库工人完成一个操作（如拣货 100 件商品），系统会：
1. 在 `TaskScanLog` 中记录扫描确认
2. 过账（posting）成功后，触发 `accrue_for_posting(task, posting_journal)`
3. 创建 `BillingEvent` — 记录「什么时候、谁的货、在哪个仓、做了什么操作、涉及多少数量」

**指纹防重**：每个 Event 都有一个唯一指纹（`event_fp`），即使同一笔过账被重复调用，也不会产生重复 Event。

```python
# 指纹格式：task_id|scanlog_id|charge_type|calc_method|service_date|qty
event_fp = "42|1001|PICK|PER_QTY_ABSDEL|2026-04-01|100.0000"
```

#### ② 费用计算

有了 Event 之后，系统根据计费规则（`BillingRule`）计算费用：

```
步骤 1: 规则匹配 → 找到最适合的价格规则
步骤 2: 阶梯计价 → 根据数量/金额计算原始费用
步骤 3: 日封顶   → 确保当天不超上限
步骤 4: 最低收费 → 确保不低于最低标准
```

**规则匹配的优先级**（从高到低）：

```
客户 A + 仓库 X 的专属价格  （最优先）
客户 A 的通用价格           （次之）
仓库 X 的默认价格           （再次）
系统全局默认价格             （兜底）
```

这允许运营人员先配一个「全仓默认价」，再为 VIP 客户覆盖特殊价格。

#### ③ 应计生成

计算出费用后，创建 `BillingAccrual`（应计）——表示「欠费已产生，但尚未开票收款」。

同样使用指纹防重（`acc_fingerprint`），相同参数的重复调用不会产生重复应计。

#### ④ 关账锁定

月底（或约定的结算周期末），运营人员执行「关账」：

```python
lock_period(owner_id=1, warehouse_id=1, label="2026-03", 
            start_date="2026-03-01", end_date="2026-03-31")
```

关账做三件事：
1. **锁定**：将日期范围内的 OPEN accrual 批量标记为 LOCKED
2. **账期封顶**：某些规则设置了月度封顶金额（如「仓储费月度不超过 10 万」），此时统一执行
3. **账期打包**：某些费用按月度打包计价（如「全品类操作费月度固定 5 万」），此时统一调整

#### ⑤ 开票结算

关账后，执行开票：

```python
generate_invoice_for_period(period, invoice_no="INV-2026-03-1-0001")
```

生成 `Bill`（发票/结算单）+ `BillLine`（明细行），标记 accrual 为 INVOICED。

---

## 5. 数据流全景图

### 5.1 四条应计生成路径

系统从四个数据源产生应计：

```
路径 1: 作业过账                    路径 2: 日在库量
TaskScanLog.posted                  InventoryDetail.onhand_qty
    │                                    │
    ▼                                    ▼
accrue_for_posting()                accrue_storage_for_date()
    │                                    │
    ├──→ BillingEvent ──→ BillingAccrual (OPEN)
    
路径 3: 日指标                      路径 4: 订单处理
BillingMetricDaily                  TaskScanLog + order resolver
    │                                    │
    ▼                                    ▼
accrue_metrics_for_date()           accrue_order_processing_from_posted()
    │                                    │
    ├──→ BillingEvent ──→ BillingAccrual (OPEN)
```

### 5.2 指标生成与调度

日指标（PALLET/CBM/AREA_M2/ORDER_AMT）的数据流：

```
每日 UTC 1:05（调度器自动触发）
    │
    ▼
run_scheduled_metric_generation_for_dates([today-3, today-2, today-1])
    │
    ▼ 三重循环: date × owner × warehouse
    │
    ├── _claim_scheduled_metric_job()  ← 分布式锁（防止多进程重复跑）
    │
    ├── [历史日期] generate_inventory_snapshot()  ← 生成库存快照
    │
    ├── generate_metrics_for_date()
    │     ├── _build_pallet_metric()   → 计算占用库位数
    │     ├── _build_cbm_metric()      → 计算在库体积
    │     ├── _build_area_metric()     → 计算占用面积
    │     └── _build_order_amount_metric() → 计算订单金额
    │
    └── _store_generated_metric() × 4  → 写入 BillingMetricDaily
```

**数据源切换**：
- 当天 → 查实时库存表 `InventoryDetail`
- 历史日期 → 查快照表 `InventorySnapshotDaily`（如果不存在会自动生成）

**手动指标保护**：
运营人员手动录入的指标（source 不以 `"AUTO:"` 开头）不会被自动覆盖，除非显式传 `overwrite=True`。

### 5.3 状态机

```
BillingAccrual:
    OPEN ──lock_period()──→ LOCKED ──generate_invoice()──→ INVOICED
      ↑                       │                               │
      └──unlock (rollback)────┘                               │
                                          unlock (red-reversal: 创建负数 VOID 冲销)

BillingPeriod:
    OPEN ──lock_period()──→ CLOSED ──generate_invoice()──→ INVOICED
      ↑                       │
      └──unlock (rollback)────┘

Bill:
    DRAFT ──generate_invoice()──→ ISSUED ──→ PAID
                                    │
                                    └──unlock──→ VOID
```

---

## 6. 核心算法详解

### 6.1 阶梯计价（`_compute_fee_with_rule`）

支持三种定价模式：

#### 无阶梯（最常见）
```
费用 = 数量 × 单价
```

#### WHOLE 模式（整档）
所有数量按同一档定价。

```
阶梯配置: [0-100: ¥1.00/件, 100-500: ¥0.80/件, 500+: ¥0.60/件]

数量 = 300件
→ 落在第二档 (100-500)
→ 费用 = 300 × 0.80 = ¥240.00
```

#### INCREMENTAL 模式（累进）
不同区间分别计价再累加，类似个人所得税。

```
同上阶梯配置，数量 = 300件
→ 前 100件 × 1.00 = ¥100.00
→ 后 200件 × 0.80 = ¥160.00
→ 总费用 = ¥260.00
```

#### 费率阶梯
当 tier 中配的是 `percent_rate`（而非 `unit_price`），系统按金额百分比计费。
用于 `PERCENT_OF_ORDER_AMOUNT` 场景（如「订单金额的 3% 作为服务费」）。

### 6.2 封顶与打包的两阶段设计

```
时间维度        日口径（即时）           账期口径（关账时）
─────────────  ──────────────────      ──────────────────
封顶(CAP)      每笔 accrual 生成时      lock_period() 中
               查当天已有金额            查整个账期已有金额
               → 新笔不超日余额          → 超过月上限的截断为 0

打包(BUNDLE)   仅支持 CAP 类型           支持 CAP 和 FIXED
               （日上限）                 CAP = 月度不超上限
                                         FIXED = 月度总额强制等于打包价
```

**为什么分两阶段？**
- 日口径：每笔生成时即时限额，提供实时保护
- 账期口径：需要看到全部 accrual 才能计算，只能关账时统一调整

### 6.3 指纹防重（Fingerprint-based Idempotency）

这是系统防重复的核心机制。每个 Event 和 Accrual 都有唯一指纹：

```python
# Event 指纹 = 任务ID | 扫描日志ID | 费用类型 | 计量方式 | 日期 | 数量
event_fp = "42|1001|PICK|PER_QTY_ABSDEL|2026-04-01|100.0000"

# Accrual 指纹 = 客户 | 仓库 | 规则ID | 费用类型 | 日期 | 数量 | 单价 | 币种 | 事件指纹
acc_fp = "1|1|5|PICK|2026-04-01|100.0000|0.8000|CNY|42|1001|..."
```

配合数据库的 unique 约束 + `get_or_create`，实现：
- 同一笔过账重复调用 → 不会产生重复费用
- 规则或价格变化后重算 → 会产生新记录（因为指纹包含单价）

---

## 7. 新增功能：试算与撤销

### 7.1 试算（`preview_lock_period`）

**解决的问题**：关账是不可逆的，封顶/打包可能大幅改变金额，客户想提前看到最终结果。

**实现原理**：
1. 查询与 `lock_period` 完全相同条件的 accrual
2. 在内存中克隆为轻量级 `_PreviewAccrual` 对象
3. 执行与 `lock_period` 完全相同的封顶/打包逻辑，但不写库
4. 返回每条 accrual 的调整前/后金额和调整原因

```python
# 试算 API
POST /api/billing/periods/{id}/preview-lock/

# 返回示例
{
    "accrual_count": 150,
    "original_subtotal": "12000.00",
    "adjusted_subtotal": "10000.00",    # 封顶后减少了 2000
    "adjustments_applied": 12,
    "accruals": [
        {
            "accrual_id": 101,
            "charge_type": "STORAGE",
            "original_amount": "200.00",
            "adjusted_amount": "150.00",
            "adjustment_reason": "per_period_cap:rule=5,cap=1000.00"
        },
        ...
    ]
}
```

**代码复用设计**：lock 和 preview 共用 `_apply_period_caps` / `_apply_period_bundles`，通过 `adjust_fn` 回调区分行为：

```python
# lock_period 中的回调：写库
def _lock_adjust_fn(accrual, new_amount, reason):
    accrual.pre_adjustment_amount = accrual.amount  # 保存原始金额
    _save_adjusted_accrual(accrual, new_amount)     # 写入 DB

# preview 中的回调：只修改内存
def _preview_adjust_fn(preview_obj, new_amount, reason):
    preview_obj.adjusted_amount = new_amount        # 仅修改内存副本
    preview_obj.adjustment_reason = reason          # 记录原因
```

### 7.2 撤销（`unlock_period`）

**两种模式**：

#### 模式 A：直接回退（CLOSED → OPEN）

适用于：关了账但还没开票，发现规则配错了。

```python
POST /api/billing/periods/{id}/unlock/
{"reason": "规则配置错误，需要修正后重新关账"}
```

操作：
1. 从 `pre_adjustment_amount` 恢复封顶前的金额
2. 重算税额和单价
3. Accrual: LOCKED → OPEN，移除 period 关联
4. Period: CLOSED → OPEN

#### 模式 B：红冲（INVOICED 期）

适用于：已开票甚至已发给客户，但发现金额有误。

```python
POST /api/billing/periods/{id}/unlock/
{"reason": "客户投诉金额有误，执行红冲"}
```

**红冲原则**：原始记录完全不动，创建负数「镜像」冲销。

```
原始 Accrual:  amount=+200.00, tax=+12.00, status=INVOICED
冲销 Accrual:  amount=-200.00, tax=-12.00, status=VOID,
               is_reversal=True, reversal_of=原始ID,
               acc_fingerprint=原指纹+"|REV"
```

Bill 状态改为 VOID。原始 Period 和 Accrual 的状态保持不变（审计轨迹完整）。

---

## 8. Bug 修复说明

### Fix A: 封顶规则查询范围过大

**修复前**（`lock_period` 中）：
```python
# 查了所有 PER_PERIOD 封顶规则，不分 owner/warehouse
cap_rules = BillingRule.objects.filter(
    active=True, cap_mode=CapMode.PER_PERIOD, cap_amount__isnull=False
)
```

**修复后**：
```python
# 只查当前 owner/warehouse 的规则
cap_rules = (BillingRule.objects
    .filter(active=True, cap_mode=CapMode.PER_PERIOD, cap_amount__isnull=False)
    .filter(Q(owner_id=owner_id) | Q(owner__isnull=True))      # 新增
    .filter(Q(warehouse_id=warehouse_id) | Q(warehouse__isnull=True))  # 新增
    .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=end_date),
            Q(effective_to__isnull=True) | Q(effective_to__gte=start_date))  # 新增
)
```

### Fix B: 移除 sleep 轮询

**修复前**：指标创建遇到竞态时，轮询 20 次 × 50ms = 最多 1 秒：
```python
def _recover_existing_metric_after_create_race(metric_filter, *, attempts=20, delay=0.05):
    for _ in range(attempts):
        existing = BillingMetricDaily.objects.select_for_update().filter(**metric_filter).first()
        if existing is not None:
            return existing
        time.sleep(delay)  # 阻塞 50ms × 20 次
    return None
```

**修复后**：单次查询，MySQL READ COMMITTED 下 IntegrityError 回滚后即可看到对方的提交：
```python
def _recover_existing_metric_after_create_race(metric_filter):
    return BillingMetricDaily.objects.filter(**metric_filter).first()
```

### Fix C: 开票批量写入

**修复前**：N 条 accrual → 2N 次 DB 写入
```python
for a in accs:
    BillLine.objects.create(...)   # N 次 INSERT
    a.status = AccrualStatus.INVOICED
    a.save(update_fields=["status"])  # N 次 UPDATE
```

**修复后**：2 次批量操作
```python
BillLine.objects.bulk_create(bill_lines)                          # 1 次 INSERT
BillingAccrual.objects.filter(id__in=acc_ids).update(status=...) # 1 次 UPDATE
```

---

## 9. Model 变更与迁移

### Migration 0009: unit_price 允许为空

`BillingRule.unit_price` 原本是 `NOT NULL`，但阶梯模式下不需要 base unit_price。

```python
# 修改前
unit_price = models.DecimalField(max_digits=18, decimal_places=4)
# 约束: unit_price >= 0

# 修改后
unit_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
# 约束: unit_price IS NULL OR unit_price >= 0
```

### Migration 0010: 撤销/红冲支持

**新增 3 个字段到 BillingAccrual**：

| 字段 | 类型 | 用途 |
|------|------|------|
| `pre_adjustment_amount` | Decimal(nullable) | 封顶/打包调整前的原始金额，unlock 时恢复 |
| `is_reversal` | Boolean(default=False) | 标记是否为红冲记录 |
| `reversal_of` | FK→self(nullable) | 指向被冲销的原始 accrual |

**移除的约束**（允许红冲的负值）：
- `chk_amount_nonneg`（BillingAccrual）
- `chk_bill_subtotal_nonneg`、`chk_bill_total_nonneg`（Bill）
- `chk_billline_amount_nonneg`、`chk_billline_unit_price_nonneg`、`chk_billline_tax_amount_nonneg`（BillLine）

**新增约束**：
- `chk_reversal_has_ref`: `is_reversal=False OR reversal_of IS NOT NULL` — 冲销记录必须指向来源

**应用层保护**（`BillingAccrual.clean()`）：
```python
if not self.is_reversal:
    if self.amount is not None and self.amount < 0:
        errors["amount"] = "非冲销记录金额不能为负。"
```

---

## 10. 如何扩展和修改

### 10.1 添加新的费用类型

1. 在 `enums.py` 的 `ChargeType` 中添加新值
2. 在 `accrual.py` 的 `accrue_for_posting` 的 `mapping` 字典中添加映射
3. 在 `BillingRule` 中创建对应的计费规则

### 10.2 添加新的指标类型

1. 在 `enums.py` 的 `MetricType` 中添加新值
2. 在 `_metrics.py` 中添加 `_build_xxx_metric()` 构建器
3. 在 `_default_metric_payload()` 中添加分发
4. 在 `_auto_metric_types()` 的 `base_types` 中添加

或者，通过 settings 配置自定义 resolver（零代码修改）：
```python
# settings.py
BILLING_MY_CUSTOM_METRIC_RESOLVER = "myapp.billing.resolvers:custom_metric_builder"
```

### 10.3 修改封顶/打包逻辑

封顶和打包逻辑在 `period.py` 的 `_apply_period_caps` 和 `_apply_period_bundles` 中。
由于使用了 `adjust_fn` 回调模式，修改业务逻辑会同时影响 lock 和 preview，保持一致性。

### 10.4 添加新的 API 端点

1. 在对应的 services 子模块中添加业务函数
2. 在 `services/__init__.py` 中添加 re-export
3. 在 `views.py` 中添加 ViewSet action
4. 如果需要请求参数，在 `serializers.py` 中添加 Serializer

### 10.5 子模块之间的依赖关系

```
__init__.py (只导入，不被导入)
    ├── accrual.py    → 依赖 _common
    ├── metrics.py    → 依赖 _common, _metrics, _reconciliation
    ├── period.py     → 依赖 _common, _reconciliation
    └── invoice.py    → 依赖 _common, _reconciliation

_common.py    ← 被所有子模块依赖（不依赖任何子模块）
_reconciliation.py ← 依赖 _common
_metrics.py   ← 依赖 _common
```

**关键原则**：`_common.py` 是叶子节点，不依赖任何其他子模块。如果新代码需要被多个子模块使用，放到 `_common.py`。
