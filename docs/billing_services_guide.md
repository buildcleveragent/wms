# allapp/billing/services.py 技术讲解

本文档对 `allapp/billing/services.py`（约 1900 行）进行逐层讲解。该文件是 WMS 计费系统的**核心业务逻辑层**，负责从仓库作业事件产生费用、进行阶梯/封顶/打包计算、生成日指标、完成关账与开票的完整流程。

---

## 目录

1. [整体架构与数据流](#1-整体架构与数据流)
2. [基础工具函数（L1–60）](#2-基础工具函数)
3. [数据对账门控（L35–162）](#3-数据对账门控)
4. [规则选择引擎（L175–194）](#4-规则选择引擎)
5. [指纹防重机制（L197–201）](#5-指纹防重机制)
6. [金额调整辅助函数（L204–277）](#6-金额调整辅助函数)
7. [阶梯计价引擎（L280–335）](#7-阶梯计价引擎)
8. [封顶与打包——日口径（L337–372）](#8-封顶与打包日口径)
9. [作业计费：accrue_for_posting（L375–453）](#9-作业计费)
10. [仓储日在库计费：accrue_storage_for_date（L456–511）](#10-仓储日在库计费)
11. [日指标体系（L514–1051）](#11-日指标体系)
12. [指标调度器（L1193–1437）](#12-指标调度器)
13. [指标转计费：accrue_metrics_for_date（L1440–1502）](#13-指标转计费)
14. [订单处理费：accrue_order_processing_from_posted（L1505–1711）](#14-订单处理费)
15. [关账与开票（L1714–1889）](#15-关账与开票)
16. [函数调用关系图](#16-函数调用关系图)
17. [关键设计决策](#17-关键设计决策)

---

## 1. 整体架构与数据流

计费系统的数据流经过五个阶段：

```
仓库作业 / 库存数据
        │
        ▼
  ┌─────────────┐     ┌──────────────┐
  │ BillingEvent │ ←── │ 指纹防重(fp) │
  └──────┬──────┘     └──────────────┘
         │
         ▼  规则匹配 → 阶梯计价 → 封顶/打包(日) → 最低收费
  ┌──────────────┐
  │BillingAccrual│  status=OPEN
  └──────┬───────┘
         │  lock_period()
         ▼
  ┌──────────────┐
  │BillingAccrual│  status=LOCKED，挂靠 BillingPeriod
  └──────┬───────┘  + 封顶/打包(账期口径)
         │  generate_invoice_for_period()
         ▼
  ┌──────┐  ┌──────────┐
  │ Bill │──│ BillLine │  status=INVOICED
  └──────┘  └──────────┘
```

**核心状态机：**
- **BillingAccrual**: `OPEN → LOCKED → INVOICED`（或 `VOID`）
- **BillingPeriod**: `OPEN → CLOSED → INVOICED`
- **Bill**: `DRAFT → ISSUED → PAID`（或 `VOID`）

---

## 2. 基础工具函数

```python
def _q(val, q="0.01"):                      # L26
def _days_in_month(d: datetime.date) -> int: # L29
```

- **`_q(val, q)`**：统一的 Decimal 四舍五入函数。`q` 决定精度（`"0.01"` = 分，`"0.0001"` = 万分位）。全文件所有金额计算最终都经过 `_q` 对齐精度，防止浮点漂移。
- **`_days_in_month(d)`**：返回某月天数，用于面积月租按日分摊。

---

## 3. 数据对账门控

```python
class BillingAccuracyGateError(ValueError):              # L35
def _billing_accuracy_gate_enabled(setting_name) -> bool: # L49
def _ensure_reconciliation_for_service_date(...)          # L94
def _ensure_reconciliation_for_date_range(...)            # L117
def _ensure_reconciliation_for_period(...)                # L150
```

**作用**：在关键节点（日调度前/后、锁账、开票）调用 `core.data_accuracy.reconcile_data_accuracy`，验证库存/计费数据是否一致。如果发现问题，抛出 `BillingAccuracyGateError` 中断流程。

**控制开关**（在 `settings.py` 中配置）：
- `BILLING_RECONCILIATION_GATE_ENABLED` — 全局总开关
- `BILLING_RECONCILIATION_GATE_DAILY_ENABLED` — 日调度
- `BILLING_RECONCILIATION_GATE_LOCK_ENABLED` — 锁账
- `BILLING_RECONCILIATION_GATE_INVOICE_ENABLED` — 开票

**设计意图**：防止在数据不一致的情况下生成错误账单。这是一道安全闸门（gate），不是自动修复——发现问题就停下来，由人工排查。

---

## 4. 规则选择引擎

```python
def _select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date)  # L175
```

给定 **"谁（owner）、在哪（warehouse）、什么类型的费用（charge_type）、怎么算（calc_method）、哪天（service_date）"**，返回最匹配的一条 `BillingRule`。

**匹配逻辑**：
1. 过滤 `active=True`、`charge_type` 和 `calc_method` 完全匹配
2. `owner_id` 匹配或为 NULL（通配）；`warehouse_id` 同理
3. 生效日期区间包含 `service_date`
4. **排序优先级**：`owner_id` 非空 > NULL → `warehouse_id` 非空 > NULL → `priority` 升序 → `id` 升序
5. 取第一条（`.first()`）

**为什么这样排序**：更具体的规则（指定了 owner + warehouse）优先于通配规则。这允许系统先配一个「默认价」，再为特定客户覆盖。

**相关变体**：
```python
def _select_bundle_rule_for_period(period, bundle_key, preferred_rule_ids=None)  # L237
```
关账时查找账期口径的打包规则，逻辑相同，但额外优先匹配 `preferred_rule_ids`（已产生 accrual 的那些规则）。

---

## 5. 指纹防重机制

```python
def _event_fp(task_id, scanlog_id, charge_type, calc_method, service_date, qty)  # L197
def _acc_fp(owner_id, warehouse_id, rule_id, charge_type, service_date, ...)     # L200
```

**Event 指纹**（`event_fp`）：`task_id|scanlog_id|charge_type|calc_method|service_date|qty`
**Accrual 指纹**（`acc_fingerprint`）：在 event 指纹基础上追加 `owner|warehouse|rule_id|unit_price|currency`

两者都用 `get_or_create(fp=..., defaults=...)` 实现**幂等写入**。同一笔扫描日志被重复处理时，不会产生重复费用。这是整个计费系统防重复的核心手段。

---

## 6. 金额调整辅助函数

```python
def _save_adjusted_accrual(accrual, new_amount)                    # L204
def _apply_fixed_bundle_total(accs, target_total)                  # L253
def _period_bundle_rule_queryset(period, bundle_key)               # L219
def _select_bundle_rule_for_period(period, bundle_key, ...)        # L237
```

### `_save_adjusted_accrual`

封顶/打包后需要修改 accrual 金额时调用。它：
1. 将 `new_amount` 对齐到分（`"0.01"`），且不低于 0
2. 如果金额未变则 early return（避免无意义的 DB 写入）
3. 同步重算 `tax_amount` 和 `unit_price`
4. 只更新必要字段（`update_fields`），不触发 `full_clean`

### `_apply_fixed_bundle_total`

FIXED 打包模式：将一组 accrual 的总额调整为恰好等于 `target_total`。
- 多了 → 从最后一笔开始往回扣减
- 少了 → 追加到最后一笔

---

## 7. 阶梯计价引擎

```python
def _compute_fee_with_rule(rule, base_value) -> (amount, effective_price)  # L281
```

这是计价的**核心算法**，支持三种模式：

### 无阶梯（最常见）
```
amount = base_value × rule.unit_price
```

### WHOLE 模式（整档/落档）
所有数量按同一档定价。例如：

| 阈值区间 | 单价 |
|---------|------|
| 0–100   | 1.00 |
| 100–500 | 0.80 |
| 500+    | 0.60 |

数量 = 300 → 落在第二档 → `300 × 0.80 = 240.00`

### INCREMENTAL 模式（累进/分段）
不同区间分别计价再累加。同上例：

数量 = 300 → `100 × 1.00 + 200 × 0.80 = 260.00`

**费率阶梯**：当 tier 中填了 `percent_rate` 而非 `unit_price` 时，`base_value` 被解释为「金额」而非「数量」，`percent_rate` 作为费率。用于按订单金额百分比收费的场景。

---

## 8. 封顶与打包——日口径

```python
def _apply_caps_bundles_day(rule, owner_id, warehouse_id, service_date, draft_amount)  # L353
```

在每笔 accrual 生成时即时应用的限额控制：

1. **封顶（cap）**：如果规则设置了 `cap_mode=PER_DAY`，查询当天该规则已累计金额，新笔费用不超过剩余额度。
2. **打包上限（bundle CAP）**：如果规则属于某个打包组（`bundle_key`），查询当天整个打包组已累计金额，新笔费用不超过打包组剩余额度。

**执行顺序**（系统设计约定）：
```
阶梯计价 → 封顶/打包(日) → 最低收费
```

> 注意：最低收费在封顶之后，意味着最低收费可能让日总额超过上限。这是有意为之的设计选择（代码注释 L430 提到了这一点）。

---

## 9. 作业计费

```python
@transaction.atomic
def accrue_for_posting(task, posting_journal, by_user=None) -> (created_events, created_accruals)  # L377
```

**触发时机**：任务过账成功后调用。

**流程**：
1. 查询该 task + posting_journal 下所有已过账的 `TaskScanLog`
2. 根据任务类型映射到费用类型和计量方式：

| task_type | charge_type | calc_method |
|-----------|-------------|-------------|
| RECEIVE   | RECEIVE     | PER_QTY_ABSDEL（按数量绝对值）|
| PUTAWAY   | PUTAWAY     | PER_QTY_ABSDEL |
| PICK      | PICK        | PER_QTY_ABSDEL |
| REVIEW    | REVIEW      | PER_LINE（按行）|
| PACK      | PACK        | PER_LINE |
| LOAD      | LOAD        | PER_TASK（按任务）|
| DISPATCH  | DISPATCH    | PER_TASK |
| COUNT     | COUNT       | PER_LINE |

3. 对每条 scan log：
   - 创建 `BillingEvent`（指纹防重）
   - 匹配规则 → 阶梯计价 → 日封顶/打包 → 最低收费
   - 创建 `BillingAccrual`（指纹防重）

**关键细节**：
- `PER_TASK` / `PER_LINE` 时 `qty_bill = 1`，按件数计费；`PER_QTY_ABSDEL` 时用扫描日志的实际数量
- 如果匹配不到规则，跳过（不报错，因为可能确实未配置该类费用）

---

## 10. 仓储日在库计费

```python
@transaction.atomic
def accrue_storage_for_date(owner_id, warehouse_id, service_date, by_user=None)  # L457
```

**触发时机**：由管理命令或 API 手动触发。

**流程**：
1. 匹配 `STORAGE / PER_DAY_ONHAND_BASE` 规则
2. 查询 `InventoryDetail` 中该 owner/warehouse 的在库总量
3. 以在库量为 `base_value` 走阶梯计价 → 封顶/打包 → 最低收费
4. 创建 Event + Accrual

**与指标计费的区别**：这是直接查实时库存；后面第 13 节的 `accrue_metrics_for_date` 则基于预先计算好的日指标（PALLET/CBM/AREA 等）。

---

## 11. 日指标体系

这是文件中最大的功能模块（L514–L1051），负责每天自动计算四类仓储运营指标：

| 指标类型 | 说明 | 数据来源 |
|---------|------|---------|
| `PALLET` | 占用库位数 | 去重统计 `location_id` |
| `CBM` | 在库体积(m³) | `onhand_qty × unit_volume_m3` |
| `AREA_M2` | 占用面积(m²) | 去重统计 `location.area_m2` |
| `ORDER_AMT` | 订单金额 | `OutboundOrderLine` 聚合 |

### 数据源切换逻辑

```python
def _inventory_metric_rows(owner_id, warehouse_id, service_date)  # L606
```

- **当天**：查实时 `InventoryDetail`
- **历史日期**：优先查 `InventorySnapshotDaily`；如果快照不存在，自动生成后再查
- 这保证了即使补跑历史日期的指标，也能拿到当时的库存数据

### 指标构建器

每个指标都有独立的构建函数：

- **`_build_pallet_metric`**（L636）：统计有在库量的去重 location 数
- **`_build_cbm_metric`**（L651）：累加 `onhand_qty × unit_volume`。体积优先从 product.volume 取，fallback 到 `ProductPackage.volume_m3 / qty_in_base`
- **`_build_area_metric`**（L700）：累加去重 location 的面积。支持 snapshot 模式和 fallback（用库位数替代面积）
- **`_build_order_amount_metric`**（L855）：`Sum(final_line_amount)`，未冻结时 fallback 到 `base_qty × base_price`

### 自定义扩展点

```python
def _load_metric_resolver(metric_type)  # L515
```

通过 `settings.BILLING_{TYPE}_METRIC_RESOLVER`（格式 `"module:function"`）可以替换任意指标的构建逻辑，无需修改 services.py。

### 指标存储

```python
def _store_generated_metric(*, owner_id, warehouse_id, service_date, metric_payload, overwrite)  # L931
```

核心的指标写入函数，策略：
1. 如果已存在**手动录入**的指标（source 不以 `"AUTO:"` 开头），默认不覆盖（除非 `overwrite=True`）
2. 值 ≤ 0 的指标：删除已有的自动指标，不创建新的
3. 并发创建时通过 `_recover_existing_metric_after_create_race` 处理竞态
4. 已存在且未变化则返回 `noop`

### 指标生成入口

```python
def generate_metrics_for_date(owner_id, warehouse_id, service_date, ...)   # L1054
def generate_metrics_for_range(owner_id, warehouse_id, start_date, end_date, ...)  # L1126
```

遍历 `[PALLET, CBM, AREA_M2, ORDER_AMT]`，构建 → 归一化 → 存储。返回包含计数器的 summary dict。

---

## 12. 指标调度器

```python
def _claim_scheduled_metric_job(owner_id, warehouse_id, service_date, *, force)              # L1197
def _run_scheduled_metric_generation_for_scope(owner_id, warehouse_id, service_date, ...)    # L1251
def run_scheduled_metric_generation_for_dates(service_dates, *, owner_id, warehouse_id, ...) # L1354
```

**调度模型**：由管理命令 `billing_run_scheduler` 在后台持续运行，每天 UTC 1:05（可配置）触发，回看最近 3 天。

### 作业锁机制

通过 `BillingJobRun` 模型 + `select_for_update` 实现分布式锁：
- **新建**：`claimed` → 开始执行
- **已成功**：`skipped_success`（不重复跑）
- **运行中但未超时**：`skipped_running`（另一个进程在跑）
- **运行中已超时**（默认 180 分钟）：重新 claim
- **force=True**：强制重跑

### 单个 scope 的执行流程

```python
_run_scheduled_metric_generation_for_scope:
    1. claim 作业锁
    2. [可选] 数据对账门控（日调度前）
    3. [如果是历史日期] 生成库存快照
    4. generate_metrics_for_date() 生成四类指标
    5. [可选] 数据对账门控（日调度后）
    6. 记录成功/失败到 BillingJobRun
```

### 批量调度

```python
run_scheduled_metric_generation_for_dates:
    for service_date in sorted(dates):
        for owner in owners:
            for warehouse in warehouses:
                _run_scheduled_metric_generation_for_scope(...)
```

三重循环遍历所有 owner × warehouse × date 组合。

---

## 13. 指标转计费

```python
@transaction.atomic
def accrue_metrics_for_date(owner_id, warehouse_id, service_date, by_user=None)  # L1440
```

将日指标转化为 accrual（费用应计）。映射关系：

| 指标类型 | calc_method | charge_type | 特殊处理 |
|---------|-------------|-------------|---------|
| PALLET  | PER_PALLET_DAY | STORAGE | — |
| CBM     | PER_CBM_DAY | STORAGE | — |
| AREA_M2 | PER_AREA_MONTH | STORAGE | **月价按日分摊** |
| ORDER_AMT | PERCENT_OF_ORDER_AMOUNT | DISPATCH | base_value 为金额 |

**面积月价分摊**（L1464–1467）：
```python
monthly_amount, monthly_eff = _compute_fee_with_rule(rule, qty_bill)
amount = monthly_amount / days_in_month(service_date)
```
先用整月面积量走阶梯求出月总价，再除以当月天数得日金额。

---

## 14. 订单处理费

```python
@transaction.atomic
def accrue_order_processing_from_posted(owner_id, warehouse_id, start_date, end_date, by_user=None)  # L1514
```

基于已过账的 `TaskScanLog`，通过可配置的 resolver（`BILLING_TASKLINE_ORDER_RESOLVER`）反推出订单维度的信息，然后按四种维度分别计费：

### 数据收集阶段

遍历 scan logs，调用 resolver 提取：
- `order_ids` → 用于 PER_ORDER 计费
- `order_line_ids` → 用于 PER_ORDER_LINE 计费
- `parcels` → 用于 PER_PARCEL 计费
- `order_amount` → 用于 PERCENT_OF_ORDER_AMOUNT 计费

### 计费阶段

四个独立循环，各自走：规则匹配 → 阶梯 → 封顶 → 最低 → Event + Accrual。

**PERCENT_OF_ORDER_AMOUNT 的特殊处理**（L1661–1672）：
如果 resolver 没有返回某些日期的金额，会 fallback 到 `BillingMetricDaily`（ORDER_AMT 指标）补全。

---

## 15. 关账与开票

### 关账 `lock_period`

```python
@transaction.atomic
def lock_period(owner_id, warehouse_id, label, start_date, end_date) -> BillingPeriod  # L1738
```

**步骤**：

1. **数据对账门控**（可选）
2. **创建/获取 Period**：通过 `_get_or_create_period_locked` 安全获取（处理并发创建）
3. **批量锁定 Accrual**：将 owner/warehouse/日期范围内、`status=OPEN` 且 `period__isnull=True` 的 accrual 批量 update 为 `LOCKED`，挂靠到 period
4. **账期封顶**：遍历所有 `cap_mode=PER_PERIOD` 的规则，对本期 accrual 按时间顺序累加，超过 `cap_amount` 的部分截断为 0
5. **账期打包**：遍历所有 `bundle_key`，找到对应的打包规则：
   - `BundleType.CAP`：总额不超过 `bundle_price`（类似封顶）
   - `BundleType.FIXED`：总额强制调整为恰好等于 `bundle_price`
6. **Period 状态 → CLOSED**

### 开票 `generate_invoice_for_period`

```python
@transaction.atomic
def generate_invoice_for_period(period, invoice_no, issue_date=None, due_date=None) -> Bill  # L1842
```

**前置校验**：
- Period 必须是 `CLOSED` 状态
- 不能重复开票
- 数据对账门控（可选）
- 至少有一条 `LOCKED` 的 accrual

**步骤**：
1. 创建 `Bill`
2. 遍历 LOCKED accrual，逐条创建 `BillLine`，累加 subtotal 和 tax
3. Accrual 状态 → `INVOICED`
4. Bill 写入总计金额，状态 → `ISSUED`
5. Period 状态 → `INVOICED`

---

## 16. 函数调用关系图

```
accrue_for_posting()                          ← 任务过账触发
  ├→ _select_rule()
  ├→ _compute_fee_with_rule()
  ├→ _apply_caps_bundles_day()
  └→ BillingEvent/BillingAccrual.get_or_create()

accrue_storage_for_date()                     ← 手动/调度触发
  └→ (同上流程)

generate_metrics_for_date()                   ← 手动/调度触发
  ├→ _inventory_metric_rows()
  │     ├→ _current_inventory_metric_rows()   (当天)
  │     └→ _snapshot_inventory_metric_rows()  (历史)
  ├→ _build_pallet_metric()
  ├→ _build_cbm_metric()
  ├→ _build_area_metric()
  ├→ _build_order_amount_metric()
  └→ _store_generated_metric()

run_scheduled_metric_generation_for_dates()   ← 调度器主入口
  └→ _run_scheduled_metric_generation_for_scope()
       ├→ _claim_scheduled_metric_job()
       ├→ _ensure_reconciliation_for_service_date()
       ├→ generate_metrics_for_date()
       └→ _finish_scheduled_metric_job()

accrue_metrics_for_date()                     ← 手动/调度触发
  ├→ _select_rule()
  ├→ _compute_fee_with_rule()
  └→ _apply_caps_bundles_day()

accrue_order_processing_from_posted()         ← 手动/调度触发
  ├→ _load_taskline_order_resolver()
  └→ 四轮计费循环 (PER_ORDER / PER_ORDER_LINE / PER_PARCEL / PERCENT_OF_ORDER_AMOUNT)

lock_period()                                 ← API/Admin 触发
  ├→ _ensure_reconciliation_for_date_range()
  ├→ _get_or_create_period_locked()
  ├→ Accrual 批量 OPEN → LOCKED
  ├→ 账期封顶 (PER_PERIOD cap)
  │     └→ _save_adjusted_accrual()
  └→ 账期打包 (PER_PERIOD bundle)
        ├→ _select_bundle_rule_for_period()
        ├→ _save_adjusted_accrual() (CAP 模式)
        └→ _apply_fixed_bundle_total() (FIXED 模式)

generate_invoice_for_period()                 ← API/Admin 触发
  ├→ _ensure_reconciliation_for_period()
  ├→ Bill.create + BillLine.create × N
  └→ Accrual LOCKED → INVOICED, Period → INVOICED
```

---

## 17. 关键设计决策

### 指纹防重 vs 唯一约束

系统同时使用了两层防重：
- **指纹（fp）字段 + `get_or_create`**：应用层幂等，无论调用多少次同一笔过账，结果相同
- **数据库 unique 约束**：作为最终保障，防止竞态条件下的重复

### 封顶/打包的两阶段设计

- **日口径**（`_apply_caps_bundles_day`）：在每笔 accrual 生成时即时限额，实时生效
- **账期口径**（`lock_period` 内）：在关账时才统一调整，因为账期打包需要看到全部 accrual 才能计算

这样设计的原因是：日口径提供即时限额保护；账期口径则允许跨天的费用打包优惠。

### 手动指标 vs 自动指标

- 自动指标以 `"AUTO:"` 为 source 前缀
- 手动录入的指标不会被自动覆盖（除非 `overwrite=True`）
- 这允许运营人员在系统自动计算有误时手动修正

### 规则优先级层级

```
owner 指定 + warehouse 指定  >  owner 指定 + warehouse 通配  >  owner 通配 + warehouse 指定  >  全通配
```

在同一优先级层级内，`priority` 数值小的优先，最后按 `id` 确定性排序。

### 面积月价按日分摊

面积费按月定价但按日入账：先用整月面积走阶梯计算月价，再除以当月天数。这样 28 天的 2 月和 31 天的 1 月，每日单价不同，但月总价由阶梯决定。

### 事务原子性

所有涉及多表写入的函数都使用 `@transaction.atomic`，确保要么全部成功要么全部回滚。关账和开票尤其重要——不会出现 "accrual 锁了但 period 状态没更新" 的中间状态。
