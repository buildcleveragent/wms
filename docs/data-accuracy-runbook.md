# Data Accuracy Runbook

这份 runbook 的目标是把系统数据准确性从“代码层面可信”推进到“现网层面可证明”。

适用场景：

- 新版本上线前的数据准确性收口
- 现网全量清账
- 月结前彩排
- 领导演示前的数据可信度确认

配套文档：

- [docs/data-accuracy-checklist.md](/wms/docs/data-accuracy-checklist.md)
- [docs/business-flow-smoke-checklist.md](/wms/docs/business-flow-smoke-checklist.md)
- [docs/test-plan.md](/wms/docs/test-plan.md)
- [docs/data-accuracy-role-checklist.md](/wms/docs/data-accuracy-role-checklist.md)
- [docs/data-accuracy-daily-record-template.md](/wms/docs/data-accuracy-daily-record-template.md)
- [docs/data-accuracy-daily-record-template.csv](/wms/docs/data-accuracy-daily-record-template.csv)

建议先生成一个带具体参数的 workpack，再按下面步骤执行：

```bash
python manage.py generate_data_accuracy_workpack --owner <owner_id> --warehouse <warehouse_id> --period <period_id>
```

## 目标

完成本 runbook 后，目标不是“绝对没有任何问题”，而是达到下面 4 条：

- 核心库存恒等式通过
- 事务回放与现存量一致
- 计费头行与应计映射一致
- 连续多天运行后没有新增脏数据回流

## 范围

执行时至少明确这 3 个参数：

- `owner_id`
- `warehouse_id`
- `period_id`

如果要做全量清账，可以先全局跑一次，再收口到重点 `owner + warehouse`。

## 角色

- 技术负责人：执行命令、保存报告、推动修复
- 业务负责人：确认批次、效期、序列号真实值
- 仓库负责人：配合抽盘与现场核对

## Day 0

目标：冻结范围，建立基线。

动作：

1. 明确本次验证的 `owner_id / warehouse_id / period_id`
2. 做数据库备份
3. 约定执行窗口内禁止直接改表、禁止手工修账单
4. 保存基线对账报告

命令：

```bash
python manage.py reconcile_data_accuracy --json
python manage.py reconcile_data_accuracy --owner <owner_id> --warehouse <warehouse_id> --json
```

输出物：

- 一份全局基线报告
- 一份重点范围基线报告
- 一份执行窗口说明

通过标准：

- 已确认验证范围
- 已完成备份
- 已保存基线报告

## Day 1

目标：清掉所有“安全可自动修复”的问题。

动作：

1. 跑全量对账，看当前问题总量
2. 跑一次 cleanup 预演，不自动修
3. 跑一次 cleanup 安全修复
4. 重新对账，确认自动修复后的剩余问题

命令：

```bash
python manage.py reconcile_data_accuracy --fail-on-issues
python manage.py reconcile_data_accuracy_cleanup --output /tmp/data_accuracy_cleanup.json
python manage.py reconcile_data_accuracy_cleanup --apply-safe-fixes --output /tmp/data_accuracy_cleanup.fixed.json
python manage.py reconcile_data_accuracy --fail-on-issues
```

重点关注：

- `InventoryDetail.available_qty` 恒等式
- `InventorySummary` 与明细聚合一致
- `Bill` 头金额与 `BillLine` 一致

输出物：

- `before` 报告
- `after` 报告
- 剩余问题清单

通过标准：

- 自动修复类问题清零
- 剩余问题只保留人工确认项或业务事实缺失项

## Day 2

目标：处理批次、效期、序列号等人工确认项。

动作：

1. 导出技术修复模板
2. 导出业务回复表
3. 让业务确认真实批次/效期/序列号
4. 合并业务回复
5. 应用修复
6. 再跑一次对账

命令：

```bash
python manage.py export_inventory_tracking_repair_template /tmp/inventory_tracking_repair_template.csv
python manage.py export_inventory_tracking_business_reply_sheet /tmp/inventory_tracking_repair_template.csv /tmp/inventory_tracking_business_reply.csv
python manage.py merge_inventory_tracking_business_reply /tmp/inventory_tracking_repair_template.csv /tmp/inventory_tracking_business_reply.csv --output /tmp/inventory_tracking_repair_ready.csv
python manage.py apply_inventory_tracking_repairs /tmp/inventory_tracking_repair_ready.csv
python manage.py reconcile_data_accuracy --fail-on-issues
```

原则：

- 不允许技术人员拍脑袋填批次和效期
- 必须让业务或仓库确认真实值
- 如果模板导出后数据库被别人改过，先重导模板，不用旧模板强覆盖

通过标准：

- `inventory_batch_tracking_integrity` 清零
- `inventory_expiry_tracking_integrity` 清零
- `inventory_serial_tracking_integrity` 清零
- 重点 `owner + warehouse` 下对账通过

## Day 3

目标：做一次完整月结彩排。

顺序必须固定：

1. 生成库存快照
2. 生成 billing 指标
3. 跑 billing-only 对账
4. 锁账
5. 再跑 billing-only 对账
6. 开票
7. 再跑 billing-only 对账
8. 跑业务闭环测试

命令：

```bash
python manage.py inventory_generate_snapshot --date <service_date> --owner <owner_id> --warehouse <warehouse_id>
python manage.py billing_run_scheduler --once --date <service_date> --owner <owner_id> --warehouse <warehouse_id>
python manage.py reconcile_data_accuracy --owner <owner_id> --warehouse <warehouse_id> --billing-only --date <service_date> --fail-on-issues
./.venv/bin/python -m pytest -q allapp/test_business_flows.py
```

后续的锁账和开票使用后台或 API 执行，但必须在每一步后重新跑 billing-only 对账。

通过标准：

- 快照、指标、应计、锁账、开票链路完整跑通
- 无重复 `BillingJobRun`
- 无重复 `Bill`
- `BillLine` 与 `BillingAccrual` 一致
- 业务闭环测试通过

## Day 4 到 Day 10

目标：进入影子运行期，确认系统不会“今天修好、明天回脏”。

每天至少执行：

```bash
python manage.py reconcile_data_accuracy --owner <owner_id> --warehouse <warehouse_id> --fail-on-issues
```

每天同时做：

- 按 [docs/business-flow-smoke-checklist.md](/wms/docs/business-flow-smoke-checklist.md) 跑一轮人工 smoke
- 抽盘少量 SKU
- 检查是否有重复 job run、重复 bill、异常 accrual、快照缺天

建议抽盘覆盖：

- 普通商品
- 批次商品
- 效期商品
- 序列号商品

通过标准：

- 连续 7 天 `reconcile_data_accuracy --fail-on-issues` 通过
- 没有新增脏数据回流
- 抽盘账实一致

## 每日记录模板

直接使用下面两份模板：

- [docs/data-accuracy-daily-record-template.md](/wms/docs/data-accuracy-daily-record-template.md)
- [docs/data-accuracy-daily-record-template.csv](/wms/docs/data-accuracy-daily-record-template.csv)

## 停止条件

出现下面任一情况时，停止继续锁账、出账或对外承诺：

- `reconcile_data_accuracy --fail-on-issues` 不通过
- 抽盘账实不一致
- 出现重复 bill
- 出现快照缺天
- 出现无法解释的负数、重复应计或头行不一致

## 完成标准

只有当下面条件全部成立，才认为数据准确性达到可强承诺水平：

- Day 1 到 Day 3 全部通过
- 连续 7 天影子运行通过
- 重点范围抽盘通过
- 关键业务闭环 smoke 通过

## 信心分级

- `7/10`
  代码、模型、服务、自动化测试已经比较稳，但现网还没完成清账与连续核验
- `8/10`
  已完成全量清账和人工 tracking 修复
- `8.5/10`
  已完成月结彩排并通过
- `9/10`
  连续 7 天影子运行通过，且抽盘账实一致
