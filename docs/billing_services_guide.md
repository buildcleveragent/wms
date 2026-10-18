# 计费服务包技术讲解

- 文档版本：V2.2
- 适用系统版本：2026-10-17 当前工作区业务基线
- 适用读者：后端开发、计费维护和技术支持人员
- 核对依据：当前工作区实现与现有测试；本文不代表数据库测试或生产验收结果。

本文说明计费事件、应计、日指标、关账、开票和收款的技术关系。日常查看账单的角色操作请参阅[货主管理员手册](manuals/金桥融通WMS货主管理员操作手册.md)和[仓库老板手册](manuals/金桥融通WMS仓库老板经营看板手册.md)。

## 1. 服务结构与公共入口

原单文件已拆分为 [services 服务包](../allapp/billing/services/)。定位实现时使用模块和函数名，避免依赖易失效的源代码行号。

| 模块 | 主要职责 |
| --- | --- |
| [__init__.py](../allapp/billing/services/__init__.py) | 兼容导出应计、指标、账期、开票及完整性检查的公共入口 |
| [_common.py](../allapp/billing/services/_common.py) | 金额精度、规则选择、指纹、阶梯、最低收费、日封顶和金额调整 |
| [_reconciliation.py](../allapp/billing/services/_reconciliation.py) | 可配置的数据对账门控 |
| [_metrics.py](../allapp/billing/services/_metrics.py) | 库存来源、指标构建器、自定义 resolver 和指标存储 |
| [accrual.py](../allapp/billing/services/accrual.py) | 过账、仓储、日指标和订单处理费的应计生成 |
| [metrics.py](../allapp/billing/services/metrics.py) | 单日、日期范围指标生成及调度作业记录 |
| [integrity.py](../allapp/billing/services/integrity.py) | 关账就绪检查、未定价事件重计价 |
| [period.py](../allapp/billing/services/period.py) | 关账、关账试算、撤销关账及红冲 |
| [invoice.py](../allapp/billing/services/invoice.py) | 从已关账账期生成账单及明细 |
| [receivables.py](../allapp/billing/services/receivables.py) | 收款过账、核销分配和收款冲销 |
| [ledger.py](../allapp/billing/services/ledger.py) | 财务应计汇总的统一有效记录口径 |
| [dashboard.py](../allapp/billing/services/dashboard.py) | 仓库计费总览数据组装 |

既有公共调用可以继续使用：

```python
from allapp.billing.services import (
    accrue_for_posting,
    build_close_readiness,
    generate_invoice_for_period,
    lock_period,
    preview_lock_period,
    unlock_period,
)
```

收款服务当前从 `allapp.billing.services.receivables` 导入；不要假定服务包内所有函数都已由包入口导出。

## 2. 数据流与状态

```text
仓库过账 / 仓储库存 / 日指标 / 订单处理
  → BillingEvent：保留来源、计价状态、规则和计价明细
  → BillingAccrual：OPEN
  → 关账就绪检查 + lock_period
  → BillingAccrual：LOCKED，归属 BillingPeriod：CLOSED
  → 开票就绪检查 + generate_invoice_for_period
  → Bill + BillLine，Accrual：INVOICED，Period：INVOICED
  → PaymentReceipt + PaymentAllocation
  → 收款过账 / 冲销，更新账单收款状态
```

状态定义见 [enums.py](../allapp/billing/enums.py)：

| 对象 / 字段 | 状态 | 含义 |
| --- | --- | --- |
| `BillingEvent.pricing_status` | `PENDING / ACCRUED / NO_CHARGE / UNPRICED` | 待计价、已计费、有依据的无需收费、未定价 |
| `BillingAccrual.status` | `OPEN / LOCKED / INVOICED / VOID` | 未锁定、已锁定、已开票、作废 |
| `BillingPeriod.status` | `OPEN / CLOSED / INVOICED` | 开账、关账、已开票 |
| `Bill.document_status` | `DRAFT / ISSUED / VOID` | 单据生命周期 |
| `Bill.payment_status` | `UNPAID / PARTIAL / PAID` | 未收款、部分收款、已收清 |
| `PaymentReceipt.status` | `DRAFT / POSTED / REVERSED` | 草稿、已过账、已冲销 |

`Bill.status` 仍保留兼容状态 `DRAFT / ISSUED / PAID / VOID`。解释应收时应同时核对单据状态、收款状态和有效核销金额；“已开票”不代表收款。这里的开票指系统生成 Bill 结算单，不表示已向外部税务系统开具税务发票。

## 3. 规则选择、金额精度与防重

### 3.1 规则匹配

`_select_rule(owner_id, warehouse_id, charge_type, calc_method, service_date)` 筛选启用规则、收费类型、计算方式和生效日期，并允许货主或仓库为空作为通配。

优先级为：

```text
指定货主 + 指定仓库
  > 指定货主 + 通配仓库
  > 通配货主 + 指定仓库
  > 全通配
```

同层级按 `priority` 升序，再按 `id` 升序选第一条。账期打包还通过 `_select_bundle_rule_for_period` 优先考虑本期实际产生应计的规则。

匹配不到规则时，应计生成路径会保留事件并标记 `UNPRICED`，不是默认免费。已计价但最终费用为零的事件以 `NO_CHARGE` 留存规则、原因和明细，供关账就绪检查核验。

### 3.2 指纹

`_event_fp` 组合任务、扫描日志、收费类型、计算方式、服务日期和四位小数数量。仓储等无任务来源使用包含货主、仓库的 `scope_key`，避免不同范围的同日同量事件碰撞。

`_acc_fp` 还包含货主、仓库、规则、数量、有效单价、币种和事件指纹。数据库字段分别是 `event_fp` 与 `acc_fingerprint`，不是统一的 `fp` 字段。

唯一约束和 `get_or_create` 对相同指纹防重。改价可能产生不同应计指纹，因此不能把防重理解为“同一事件永远只有一条历史记录”；关账会将同一事件下重复的锁定应计作废并保留最新价格记录。

### 3.3 定价顺序

`_q` 使用 Decimal 四舍五入；金额通常保留两位小数，数量和有效单价通常保留四位小数。

`_compute_fee_with_rule` 支持普通单价、百分比费率和两种阶梯：

| 方式 | 计算示例：前 100 单位为 1.00，之后为 0.80，数量 300 |
| --- | --- |
| 整档 `WHOLE` | 全部 300 按所在档单价计算：240.00 |
| 累进 `INCREMENTAL` | 100 × 1.00 + 200 × 0.80：260.00 |

百分比规则将计费基数解释为金额，使用配置的费率；普通数量规则按数量计价。

面积月费 `PER_AREA_MONTH` 由 `_compute_daily_fee_with_rule` 按服务日期所在月份的实际天数分摊，再应用最低收费、日封顶及税额；正常日指标计费和未定价事件重计价采用同一口径。例如月单价 31.00 元/平方米、10 平方米，在 31 天月份分摊为每日 10.00 元，在 30 天月份原始日费为 10.33 元。不要在重计价或对账时统一除以 30，最终金额还应结合最低收费与封顶结果。

所有应计路径通过 `_finalize_daily_price` 统一执行：

```text
原始阶梯金额 → 每笔最低收费 → 日规则 CAP → 日打包 CAP → 有效单价
```

最低收费只在计费数量大于零时应用。日 CAP 是最终上限，不能再用最低收费把已封顶的金额抬高。日累计查询排除作废应计。最终价格及调整原因写入事件的计价明细。

`_save_adjusted_accrual` 在调整金额时同步重算税额及有效单价；`_apply_fixed_bundle_total` 将一组费用调整到固定总价，多余金额从后往前扣，差额追加到最后一笔。

## 4. 作业过账与订单处理费

### 4.1 作业计费

`accrue_for_posting(task, posting_journal, by_user=None)` 查询本任务、本次过账关联的成功扫描日志，并按以下映射生成事件：

| 任务类型 | 收费类型 | 计算方式 |
| --- | --- | --- |
| `RECEIVE` | 收货 | `PER_QTY_ABSDEL` |
| `PUTAWAY` | 上架 | `PER_QTY_ABSDEL` |
| `RELOC` | 移库 | `PER_QTY_ABSDEL` |
| `PICK` | 拣货 | `PER_QTY_ABSDEL` |
| `REVIEW` | 复核 | `PER_LINE` |
| `PACK` | 打包 | `PER_LINE` |
| `LOAD` | 装车 | `PER_TASK` |
| `DISPATCH` | 发运 | `PER_TASK` |
| `COUNT` | 盘点 | `PER_LINE` |

实际实现逐扫描日志处理；`PER_LINE`、`PER_TASK` 在每条记录中取计费数量 1，不能据名称推断已按整张任务或任务行再次聚合。数量口径取 `qty_base_delta`，为空时取 `qty_base`，再取绝对值；服务日期取过账日期。

每条记录依次建立事件、匹配规则、完成定价、保存应计和计价证据。缺少规则留下未定价事件；业务过账成功不代表其计费数据已具备关账条件。已关闭账期内的迟到业务须由完整性检查和账期处理流程解决。

### 4.2 订单处理费

两个入口用途不同：

- `accrue_order_processing_for_task(task, posting_journal, ...)`：当前任务、本次过账的即时处理。
- `accrue_order_processing_from_posted(owner_id, warehouse_id, start_date, end_date, ...)`：按日期范围处理已过账来源。

通过 `BILLING_TASKLINE_ORDER_RESOLVER` 从任务行提取订单、订单行、包裹数、订单金额和打包键，支持 `PER_ORDER`、`PER_ORDER_LINE`、`PER_PARCEL`、`PERCENT_OF_ORDER_AMOUNT`，可用 `allowed_methods` 限定计算方式。

两个入口检查另一种来源是否已存在相应事件，避免即时计费与批量计费叠加。金额来源还结合订单行金额及 `ORDER_AMT` 指标；排查重计或漏计时需一并核对 resolver、日期和事件来源，不能只统计扫描日志数量。

## 5. 仓储计费与日指标

### 5.1 按在库基本数量收费

`accrue_storage_for_date(owner_id, warehouse_id, service_date, by_user=None)` 使用 `STORAGE / PER_DAY_ONHAND_BASE`：

1. 当天读取活动库存明细；历史日期读取对应的 `InventorySnapshotDaily`。
2. 汇总在库数量，数量不大于零时不生成该项费用。
3. 建立带货主、仓库范围的事件，匹配规则并定价。
4. 创建应计，传播历史快照的来源可信度。

这个函数不能被描述为“历史补算也直接查实时库存”。历史快照缺失的检查、生成和重建应在指标或维护流程中处理。

### 5.2 指标及计费映射

| 日指标 | 构建口径 | 应计计算方式 |
| --- | --- | --- |
| `PALLET` | 有库存的去重库位数，当前实现的托盘位代理口径 | `STORAGE / PER_PALLET_DAY` |
| `CBM` | 在库量 × 单位体积 | `STORAGE / PER_CBM_DAY` |
| `AREA_M2` | 占用库位面积去重合计 | `STORAGE / PER_AREA_MONTH` |
| `ORDER_AMT` | 业务日期下已提交、未删除、未取消出库订单行金额 | `DISPATCH / PERCENT_OF_ORDER_AMOUNT` |

`ORDER_AMT` 优先取大于零的 `final_line_amount`，否则取 `base_qty × base_price`。它与按过账日志提取订单处理费的来源不同，不能把两个入口的结果无条件相加。

面积月价先按当前面积计算月金额，再除以服务日期所在月的天数，最后进入日定价流程。每天的面积变化和按日舍入都会影响整月累计值，不能保证任意月的累计值恰好等于某一天面积的月报价。

### 5.3 历史来源与近似数据

`_inventory_metric_rows` 当天取实时库存，历史日期取快照；快照为空时尝试生成。正常生成依赖快照链和库存事务，链断裂等情形可能回退到 bootstrap。

bootstrap 基于当前库存生成历史日期快照，不能因此声称恢复了真实历史库存。`BOOTSTRAP_DETAIL`、`TX_ROLLFORWARD_APPROX` 等来源会传播为 `APPROXIMATE` 指标或应计，并被关账就绪检查阻断。

历史体积和面积使用快照保存的单位体积、库位面积，避免用现有主数据覆盖历史口径。面积缺失时仅在启用 `allow_area_fallback` 后允许库位数代理；代理数量不等同于已测量平方米，应核对指标的 source、note 和合同约定。

### 5.4 扩展与存储

`BILLING_{TYPE}_METRIC_RESOLVER` 可配置 `module:function`，为指标提供自定义构建器。实际配置名及 resolver 加载规则见 `_load_metric_resolver`。

`_store_generated_metric` 的主要行为：

- `AUTO:` 前缀标识自动指标；手动指标默认保留，显式 `overwrite=True` 才允许覆盖。
- 零值或负值不会创建新的正费用指标；已有自动指标按零值清理流程处理。
- 相同值、来源及备注无需重复更新；并发创建按唯一范围恢复读取已有记录。
- 结果保留创建、更新、删除、跳过、无变化等处理信息及来源可信度。

`generate_metrics_for_date` 和 `generate_metrics_for_range` 负责生成指标，`accrue_metrics_for_date` 才负责将指标转为应计。生成指标成功不等同于已经收费或关账成功。

## 6. 日调度与失败恢复

`billing_run_scheduler` 调用 `run_scheduled_metric_generation_for_dates`，按日期、货主和仓库范围生成指标。设置见 [settings.py](../wmsmaster/settings.py)：默认时、分为 1:05，回看此前 3 天，轮询 60 秒，运行记录过期阈值 180 分钟。触发时间依据命令使用的 `timezone.now()` 与部署时区设置，不应固定写成 UTC。

`BillingJobRun` 与行锁管理每个范围的执行：

| 结果 | 处理 |
| --- | --- |
| `claimed` | 已取得执行权 |
| `skipped_success` | 已成功，默认跳过 |
| `skipped_running` | 未过期的作业仍在运行，跳过 |
| 超时作业 / `force=True` | 按认领逻辑重新执行 |

执行顺序为认领、可选日对账、历史快照准备、指标生成、可选日对账、写入成功或失败记录。单个范围失败会记录结果，不等于整个批次没有其他成功项。

维护人员应先核对失败日期、货主、仓库、错误原因和数据来源，再选择有范围的重试。历史可信数据修复可参考 [billing_rebuild_trusted_range](../allapp/billing/management/commands/billing_rebuild_trusted_range.py)，未定价事件可参考 [billing_reprice_unpriced](../allapp/billing/management/commands/billing_reprice_unpriced.py)。这些命令会写业务数据，不属于纯查看操作。

## 7. 关账就绪与数据对账

两类检查相互独立：

| 检查 | 入口 | 行为 |
| --- | --- | --- |
| 关账就绪 | `build_close_readiness / ensure_close_readiness` | 锁账和开票必经；阻断时抛出 `BillingCloseBlocked` |
| 数据对账 | `_reconciliation.py` 中的检查 | 根据配置启用，失败抛出 `BillingAccuracyGateError` |

就绪检查覆盖待计价或未定价事件、已计费事件缺有效应计、免费事件缺计价证据、近似应计或指标、无账期的锁定应计，以及合同要求的计费来源缺失。开票时额外检查账期日期范围内仍未锁定的应计和关账后到达的计费事件。

返回结果包含 `ready`、`blocker_count`、`blockers`、`by_code`，便于从阻断代码定位记录。API 在就绪检查失败时返回 `409 BILLING_CLOSE_BLOCKED` 及 readiness 明细。

可选对账的开关包括 `BILLING_RECONCILIATION_GATE_ENABLED` 及 `DAILY_ENABLED`、`LOCK_ENABLED`、`INVOICE_ENABLED` 对应完整配置名。关闭可选对账不会关闭强制就绪检查；检查失败后应处理来源和计价缺口，不应直接改账单金额掩盖问题。

## 8. 关账、试算与撤销

### 8.1 锁账

`lock_period(owner_id, warehouse_id, label, start_date, end_date, *, by_user=None)` 在事务内执行：

1. 检查关账就绪；按配置执行数据对账。
2. 获取或创建账期并加行锁；已有账期须为 OPEN，日期范围须一致。
3. 将同货主、仓库、日期范围内 `OPEN + period=NULL` 的应计锁定到本期。
4. 对同一事件的重复锁定应计保留最新价格，旧记录标记作废。
5. 依次应用账期 FIXED 打包、规则 CAP、打包 CAP，确保 CAP 最终作为硬上限。
6. 首次调整时保存 `pre_adjustment_amount`，记录调整明细；账期变为 CLOSED，写入操作人及关账时间。

已关联其他账期的应计不会被本次锁账抢占。账期业务范围创建后不可通过普通编辑改写，API 只允许删除没有应计、账单关联的 OPEN 账期。

### 8.2 试算

`preview_lock_period` 在内存中模拟关账金额调整，返回原金额、调整金额及调整原因，不锁定应计、不修改账期。API 的 `preview-lock` 仅用于 OPEN 账期。

普通 `preview` 是应计汇总预览，`preview-lock` 是关账调整试算，`close-readiness` 是完整性检查；三者用途不同，试算金额正确不代表就绪检查一定通过。

### 8.3 撤销

`unlock_period(period, *, by_user=None, reason="")` 根据状态处理：

| 原状态 | 结果 |
| --- | --- |
| OPEN | 拒绝，无需撤销 |
| CLOSED | 没有有效账单时恢复封顶前金额，LOCKED → OPEN，解除账期关联，账期回到 OPEN |
| INVOICED | 作废有效账单，创建或复用一致的负数红冲记录，保留审计关系并重开账期 |

红冲指纹发生字段冲突会中止并回滚，不能强行复用不同金额或来源的记录。撤销已开票账期不代表已经向客户退款，也不等同于撤销收款单；资金差异须另走收款冲销和财务核对流程。

## 9. 开票

公共入口：

```python
generate_invoice_for_period(
    period, invoice_no, issue_date=None, due_date=None, *, by_user=None
)
```

虽然签名允许省略 `due_date`，业务校验要求到期日必填，且不得早于开票日。开票日期未提供时取当前日期；账单号必须满足模型唯一性要求。

执行过程：

1. 重新读取并行锁定账期，防止并发重复开票。
2. 校验 CLOSED 状态、有效账单不存在、到期日合法。
3. 强制开票就绪检查，按配置执行数据对账。
4. 读取本期 LOCKED 应计，无可开票应计则拒绝。
5. 创建 Bill，用 `bulk_create` 创建一条应计对应一条 BillLine，累计不含税金额和税额。
6. 批量将应计更新为 INVOICED，账单单据状态设为 ISSUED、收款状态设为 UNPAID。
7. 账期更新为 INVOICED，记录开票时间和操作人。

全过程在事务内完成。重复开票限制是“同一账期不能并存有效账单”；作废后的历史账单保留，账期经正式撤销、重新计费和关账后可以重新开票。

## 10. 收款、红冲与财务汇总

`post_receipt` 锁定草稿收款单、核销分配和账单，检查至少有一笔分配、分配不超过收款金额、货主/仓库/币种一致、账单有效且核销不超过未收金额，再将收款单过账并同步账单收款状态。

`reverse_receipt` 保留原收款及核销记录，通过冲销单和反向分配抵消原收款，再计算账单剩余应收。有效收款使用带符号的核销合计，不能仅凭原收款单标记为 REVERSED 就丢弃其历史金额。

`ledger.py` 统一财务应计口径：

- 非 VOID 且不是冲销标记的普通应计纳入汇总。
- VOID、带原记录引用且金额为负、单价和税额不为正的真实红冲纳入带符号汇总。
- 改价去重产生的正金额作废记录不作为真实红冲再次扣减。

经营看板、应计汇总、已出账金额和已收款金额的对象及时间口径不同。追查差异时应保留账期、服务日期、账单号、来源事件和收款核销记录。

## 11. API、后台入口与权限边界

收款与核销维护须遵守当前约束：只有草稿收款单可以新增或修改核销分录；已过账或已冲销单据不能通过改核销行修正金额。错误应按原收款冲销流程处理，再核对账单余额及有效核销，不删除原财务证据。收款修改也重新校验最终货主与仓库范围，不能通过编辑归属跨范围迁移收款。

路由见 [billing/urls.py](../allapp/billing/urls.py)，动作实现见 [views.py](../allapp/billing/views.py)。以下路径挂在项目 `/api/` 下：

| 方法及路径 | 用途 |
| --- | --- |
| `GET /api/billing/periods/{id}/preview/` | 应计汇总预览 |
| `GET /api/billing/periods/{id}/close-readiness/` | 查询阻断项 |
| `POST /api/billing/periods/{id}/preview-lock/` | OPEN 账期关账试算 |
| `POST /api/billing/periods/{id}/lock/` | OPEN → CLOSED |
| `POST /api/billing/periods/{id}/invoice/` | CLOSED → INVOICED |
| `POST /api/billing/periods/{id}/unlock/` | 撤销 CLOSED / INVOICED 账期 |
| `GET /api/billing/bills/{id}/` | 账单及明细查看 |

`lock / unlock` 要求 `change_billingperiod`，`invoice` 要求 `add_bill`，同时受货主、仓库范围限制。Django Admin 的计费账期详情也提供受权限控制的动作。

`BillViewSet` 是只读接口；账单金额不能通过普通更新接口直接修改。仓库老板和货主管理员的前台账单入口用于查询、核对和按权限导出，不能据此推断具备关账、开票、收款或冲销权限。

## 12. 维护核对与验证依据

维护顺序建议为：核对来源和规则 → 检查事件计价状态 → 检查指标可信度 → 查询关账阻断项 → 试算 → 授权关账 → 授权开票 → 收款核销与对账。每一步保留范围、日期、操作人和结果。

现有测试可作为阅读行为边界的依据：

| 测试文件 | 主要核对场景 |
| --- | --- |
| [billing/tests.py](../allapp/billing/tests.py) | 阶梯及封顶、开票条件、金额汇总、事务回滚、并发开票、撤销后重开和红冲冲突 |
| [test_integrity_unit.py](../allapp/billing/test_integrity_unit.py) | 计价及来源可信度等纯逻辑 |
| [test_p1_receivables.py](../allapp/billing/test_p1_receivables.py) | 收款、核销、状态、应收与账龄相关行为 |
| [test_management_commands.py](../allapp/billing/test_management_commands.py) | 维护命令及范围处理 |

数据库测试遵循仓库 [AGENTS.md](../AGENTS.md)：使用 MySQL 8 和 `.env.test.local`，先有界检查连通性并确认目标恰为 `wms_db_test`；正常迭代复用健康测试库，按需从相关用例扩展。迁移变更、测试库异常或最终发布验证才按规则重建，并执行真实迁移链。业务库 `wms_db` 不用于自动测试写入。

本版仅依据源码及现有测试进行文档校对；没有在本次文档更新中执行计费任务、关账、开票、收款、数据库测试或发布验收。
