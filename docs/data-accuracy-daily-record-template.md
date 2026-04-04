# Data Accuracy Daily Record Template

配合 [docs/data-accuracy-runbook.md](/wms/docs/data-accuracy-runbook.md) 使用。

每天复制一份填写。

## 基本信息

- 日期：
- 执行人：
- 技术负责人：
- 业务负责人：
- 仓库负责人：
- 作用域：`owner_id= / warehouse_id= / period_id=`

## 命令执行

- 已执行 `python manage.py reconcile_data_accuracy --owner <owner_id> --warehouse <warehouse_id> --fail-on-issues`
- 已执行 `billing-only` 对账：
- 已执行自动化回归：
- 已执行人工 smoke：

命令输出摘要：

```text
在这里粘贴关键输出或报告路径
```

## 结果

- 对账结果：`PASS / FAIL`
- 问题数量：
- 是否执行安全修复：`Y / N`
- 是否执行 tracking repair：`Y / N`
- 是否执行抽盘：`Y / N`

## 异常清单

- 异常 1：
- 异常 2：
- 异常 3：

## 抽盘记录

- SKU：
- 商品类型：`普通 / 批次 / 效期 / 序列号`
- 系统数量：
- 实盘数量：
- 是否一致：`Y / N`

## billing 记录

- 是否有重复 `BillingJobRun`：`Y / N`
- 是否有重复 `Bill`：`Y / N`
- 是否有快照缺天：`Y / N`
- 是否存在异常 `BillingAccrual`：`Y / N`

## 结论

- 今天是否允许继续锁账/开票：`Y / N`
- 今天是否允许对外演示/承诺数据准确：`Y / N`
- 结论说明：

## 待办

- 明日待办 1：
- 明日待办 2：
- 明日待办 3：
