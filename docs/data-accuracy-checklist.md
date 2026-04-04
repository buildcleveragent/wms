# Data Accuracy Checklist

这份清单的目标不是“多跑一点测试”，而是把库存与计费最关键的数据准确性检查固定成一套可执行动作。

执行层面的按天步骤见：

- [docs/data-accuracy-runbook.md](/wms/docs/data-accuracy-runbook.md)
- [docs/data-accuracy-role-checklist.md](/wms/docs/data-accuracy-role-checklist.md)
- [docs/data-accuracy-daily-record-template.md](/wms/docs/data-accuracy-daily-record-template.md)
- [docs/data-accuracy-daily-record-template.csv](/wms/docs/data-accuracy-daily-record-template.csv)

## 原则

- 先查差异，再决定是否修数据。
- 先保证 `InventoryDetail / InventorySummary / InventoryTransaction` 一致，再谈报表和 billing。
- 先保证 `BillingMetricDaily / BillingAccrual / BillLine / Bill` 一致，再谈领导展示页。
- 每次发版都跑 smoke；每天定时跑对账命令；月结前再跑一次全量对账。

## 可执行命令

已提供统一命令：

```bash
python manage.py generate_data_accuracy_workpack --owner 1 --warehouse 1 --period 12
python manage.py reconcile_data_accuracy
```

常用示例：

```bash
python manage.py reconcile_data_accuracy --owner 1
python manage.py reconcile_data_accuracy --owner 1 --warehouse 1
python manage.py reconcile_data_accuracy --owner 1 --billing-only --date 2026-04-03
python manage.py reconcile_data_accuracy --owner 1 --billing-only --period 12
python manage.py reconcile_data_accuracy --owner 1 --json
python manage.py reconcile_data_accuracy --owner 1 --fail-on-issues
```

现网全量清账命令：

```bash
python manage.py reconcile_data_accuracy_cleanup
python manage.py reconcile_data_accuracy_cleanup --apply-safe-fixes
python manage.py reconcile_data_accuracy_cleanup --apply-safe-fixes --output /tmp/data_accuracy_cleanup.json
python manage.py reconcile_data_accuracy_cleanup --owner 1 --fail-on-issues
```

剩余批次/效期问题的人工修复模板：

```bash
python manage.py export_inventory_tracking_repair_template /tmp/inventory_tracking_repair_template.csv
python manage.py export_inventory_tracking_business_reply_sheet /tmp/inventory_tracking_repair_template.csv /tmp/inventory_tracking_business_reply.csv
python manage.py merge_inventory_tracking_business_reply /tmp/inventory_tracking_repair_template.csv /tmp/inventory_tracking_business_reply.csv --output /tmp/inventory_tracking_repair_ready.csv
python manage.py apply_inventory_tracking_repairs /tmp/inventory_tracking_repair_ready.csv
```

说明：

- `--warehouse` 只影响库存明细级检查与 billing 仓库级检查。
- `InventorySummary` 是 `owner + product` 粒度，不是仓库粒度；传 `--warehouse` 时，`inventory_summary_vs_detail` 会自动跳过。
- `--fail-on-issues` 适合挂到 CI、定时任务或月结前阻断。
- `reconcile_data_accuracy_cleanup --apply-safe-fixes` 只修“安全项”：
  `InventoryDetail.available_qty` 回算、
  `InventorySummary` 从明细重建（仅 owner 级，不支持 warehouse 级重建）、
  `Bill` 头金额按 `BillLine` 重算。
  其余差异仍需人工核因后再处理。
- `export_inventory_tracking_repair_template` 会导出当前仍未通过的批次/效期缺失行，包含 `new_batch_no / new_production_date / new_expiry_date` 空列供人工填写。
- `export_inventory_tracking_business_reply_sheet` 会把技术修复模板聚合成业务确认表，按 `owner + product + warehouse + location` 分组，便于业务回填真实值。
- `merge_inventory_tracking_business_reply` 会把业务确认表自动合并回技术修复模板；默认输出一个新的 `.merged.csv` 文件，也可用 `--in-place` 直接覆盖原模板。
- `apply_inventory_tracking_repairs` 会逐行读取 CSV 并按模型校验后回填到 `InventoryDetail / InventoryTransaction`。
  命令会整批事务执行，并校验 CSV 里的 `current_*` 字段必须仍和数据库一致；
  如果期间有人手工改过库存追踪字段，必须先重新导出模板，避免旧模板覆盖新数据。

## 命令当前覆盖的检查

### Inventory

- `inventory_detail_available_identity`
  校验每条 `InventoryDetail.available_qty == onhand - allocated - locked - damaged`
- `inventory_summary_available_identity`
  校验每条 `InventorySummary.available_qty == onhand - allocated - locked - damaged`
- `inventory_summary_vs_detail`
  校验 `InventorySummary` 是否等于同 `owner + product` 下全部 `InventoryDetail` 的聚合结果
- `inventory_transaction_replay_onhand`
  回放全部已过账 `InventoryTransaction.qty_delta`，校验每个库存维度上的 `InventoryDetail.onhand_qty`
- `inventory_batch_tracking_integrity`
  校验启用批次管理的商品在 `InventoryDetail / InventoryTransaction` 上是否缺失 `batch_no`
- `inventory_expiry_tracking_integrity`
  校验启用效期管理的商品在 `InventoryDetail / InventoryTransaction` 上是否缺失必需效期字段，以及是否存在 `expiry_date < production_date`
- `inventory_serial_tracking_integrity`
  校验启用序列号管理的商品在 `InventoryDetail / InventoryTransaction` 上是否缺失 `serial_no`、是否存在明细数量超 1、以及事务流水 `abs(qty_delta) != 1`

### Billing

- `billing_metric_non_negative`
  校验 `BillingMetricDaily.value >= 0`
- `billing_accrual_consistency`
  校验 `BillingAccrual` 与 `rule / period / event` 的 owner、warehouse、currency、charge_type、service_date 一致性
- `bill_header_totals`
  校验 `Bill.total == subtotal + tax_total`，以及账单头与账单行汇总一致
- `bill_line_matches_accrual`
  校验 `BillLine` 与来源 `BillingAccrual` 的数量、单价、金额、税额、账期一致
- `billing_invoiced_accrual_linkage`
  校验 `INVOICED` 的应计必须且只能落到一条账单行；非 `INVOICED` 应计不应已有账单行

## Daily Checklist

每天建议至少执行一次：

1. 跑 `python manage.py reconcile_data_accuracy --fail-on-issues`
2. 如果失败，先看 inventory，再看 billing
3. 先定位差异范围：`owner / warehouse / product / period / service_date`
4. 确认是数据脏写、人工修数、还是流程幂等问题
5. 未确认原因前，不要直接重跑出账或手工改账单

## Release Checklist

每次发版前至少做这些：

1. 跑业务闭环测试：`allapp/test_business_flows.py`
2. 跑 `reconcile_data_accuracy --fail-on-issues`
3. 抽 1 个 owner 做人工核对：
   对库存明细页
   对库存汇总页
   对 billing 总览
   对账单明细
4. 确认当天没有重复 job run、重复 bill、重复 accrual

## Month-End Checklist

月结或正式给领导看数据前，建议固定执行：

1. 先跑库存快照生成
2. 再跑 billing 指标生成
3. 再跑 `reconcile_data_accuracy --owner <owner_id> --period <period_id> --fail-on-issues`
4. 只有 inventory 和 billing 都通过，才允许锁账
5. 锁账后再次跑 billing-only 对账
6. 出账后再核对一次 `BillLine` 与 `BillingAccrual`

## Smoke 场景

下面这些不是替代命令，而是配合命令一起跑的业务 smoke：

1. 无单收货后库存入账正确
2. 正式入库单完成收货与上架后库存位置正确
3. 出库审批、分配、拣货、过账后库存扣减正确
4. 取消出库或释放预留后 allocated 正确回退
5. 快速调整后库存与报表同步
6. 多商品、多库位拆分拣货后汇总库存正确
7. 仓内作业生成 billing 指标、应计、账单金额正确
8. 账单导出与账单详情页一致

## 结果解释

- `PASS`
  当前检查范围内未发现已知差异，可以继续下一步。
- `FAIL`
  已发现差异，不建议继续锁账、出账或人工对外承诺数据准确。
- `SKIP`
  不是通过，也不是失败，而是当前作用域无法做该校验，比如传了 `--warehouse` 后跳过 `InventorySummary` 聚合对账。

## 当前边界

这版命令当前重点查：

- 核心库存恒等式
- 汇总与明细一致
- 已过账事务回放与现存量一致
- 批次、序列号、效期关键字段完整性
- billing 头行一致
- 应计与账单映射一致

还没有覆盖的高风险项：

- 并发写入冲突
- `InventoryTransaction` 只能回放 `onhand_qty`，还不能重建 `allocated / locked / damaged`
- 真实历史账与旧系统/人工台账三方核对

这些建议作为下一阶段补充。
