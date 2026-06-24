# 系统上线前测试结论 2026-06-24

## 当前结论

功能回归、POS、库存数量、库存汇总、库存流水回放、计费账单已经通过当前测试验证。

但系统还不能按“数据准确已完全保证”放行，因为批次/效期追溯数据仍缺失。该问题需要业务提供真实批次、生产日期、到期日期，不能使用假数据自动填充。

## 已通过项目

- 后端全量业务回归测试：208 passed
- POS 回归测试：35 passed
- POS 数据迁移：pos.0006_backfill_pos_payment_lines 已应用
- POS 实际数据核对：销售、收款、支付明细一致，issues 0
- 数据库迁移检查：通过，无待执行迁移
- Django system check：通过
- 前端页面配置检查：通过
  - wmspda/pages.json 可解析，声明页面均存在
  - wmsownersale/pages.json 可解析，声明页面均存在
  - wmsbossbilling/pages.json 可解析，声明页面均存在
- 变更文件 Ruff / Black / isort：通过
- git diff --check：通过

## 数据准确性检查

命令：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues --limit 3
```

结果：

- Overall：FAIL，issues=540
- 库存明细可用量：OK
- 库存汇总 vs 明细：OK
- 库存流水回放现存量：OK
- 序列号追溯：OK
- 计费指标、账单、开票关联：OK
- 批次追溯：FAIL，391
- 效期/生产日期追溯：FAIL，149

## 追溯整改文件

- inventory-tracking-repair-template.csv：403 条待修对象行
- inventory-tracking-business-reply.csv：182 条业务确认行
- inventory-tracking-priority.csv：182 条业务确认行，已补充优先级和建议动作

优先级统计：

- P0：45 行，食品/饮品/粮油/生鲜等，建议补真实批次、生产日期、到期日期
- P1：15 行，日化/包装耗材或大库存项目，需要业务优先确认
- P2：122 行，需业务确认是否补追溯信息或调整商品控制口径

## 业务下一步

业务人员需要填写：

- var/data-accuracy/inventory-tracking-business-reply.csv

必填列：

- business_confirmed_batch_no
- business_confirmed_production_date
- business_confirmed_expiry_date
- evidence_source
- confirmed_by
- confirmed_at

可先参考：

- var/data-accuracy/inventory-tracking-priority.csv

## 修复闭环命令

业务填写完成后，执行：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py merge_inventory_tracking_business_reply \
var/data-accuracy/inventory-tracking-repair-template.csv \
var/data-accuracy/inventory-tracking-business-reply.csv \
--output var/data-accuracy/inventory-tracking-ready.csv
```

再执行：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py apply_inventory_tracking_repairs \
var/data-accuracy/inventory-tracking-ready.csv
```

最后复核：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost \
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues
```

只有最后一条命令通过后，才能宣布数据准确性完成闭环。
