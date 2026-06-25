# 系统上线测试进展 2026-06-25

## 当前结论

系统当前不能按“数据准确已保证”放行。

主要原因是 2026-06-24 的数据准确性检查已经记录为失败，且本次复查确认业务回填文件仍未填写，批次/生产日期/效期追溯问题尚未具备修复闭环条件。

## 已执行检查

### Django 系统检查

命令：

```bash
.venv/bin/python manage.py check
```

结果：通过。

输出摘要：

```text
System check identified no issues (0 silenced).
```

### 数据库迁移状态

本机 MySQL 当前以 `--skip-networking` 运行，`127.0.0.1:3306` 不开放；测试时改用 Unix socket：

```bash
DB_HOST=localhost DB_PORT= DB_SOCKET=/run/mysqld/mysqld.sock
```

已执行：

```bash
.venv/bin/python manage.py migrate --check
.venv/bin/python manage.py showmigrations --plan
```

结果：

- `migrate --check`：通过，无待应用迁移。
- `showmigrations --plan`：所有迁移均显示 `[X]`。

## 数据准确性状态

### 现有权威测试结论

参考文件：

- `var/data-accuracy/system-test-summary-2026-06-24.md`

该报告记录：

- 数据准确性总结果：FAIL
- 总问题数：540
- 库存明细可用量：OK
- 库存汇总 vs 明细：OK
- 库存流水回放现存量：OK
- 序列号追溯：OK
- 计费指标、账单、开票关联：OK
- 批次追溯：FAIL，391
- 效期/生产日期追溯：FAIL，149

### 本次复查追溯修复输入

检查文件：

- `var/data-accuracy/inventory-tracking-repair-template.csv`
- `var/data-accuracy/inventory-tracking-business-reply.csv`
- `var/data-accuracy/inventory-tracking-priority.csv`

行数：

```text
inventory-tracking-repair-template.csv: 403 data rows
inventory-tracking-business-reply.csv: 182 data rows
inventory-tracking-priority.csv: 182 data rows
```

业务回填完成度：

| 文件 | 字段 | 已填 | 未填 |
| --- | --- | ---: | ---: |
| inventory-tracking-business-reply.csv | business_confirmed_batch_no | 0 | 182 |
| inventory-tracking-business-reply.csv | business_confirmed_production_date | 0 | 182 |
| inventory-tracking-business-reply.csv | business_confirmed_expiry_date | 0 | 182 |
| inventory-tracking-business-reply.csv | evidence_source | 0 | 182 |
| inventory-tracking-business-reply.csv | confirmed_by | 0 | 182 |
| inventory-tracking-business-reply.csv | confirmed_at | 0 | 182 |
| inventory-tracking-priority.csv | business_confirmed_batch_no | 0 | 182 |
| inventory-tracking-priority.csv | business_confirmed_production_date | 0 | 182 |
| inventory-tracking-priority.csv | business_confirmed_expiry_date | 0 | 182 |
| inventory-tracking-priority.csv | evidence_source | 0 | 182 |
| inventory-tracking-priority.csv | confirmed_by | 0 | 182 |
| inventory-tracking-priority.csv | confirmed_at | 0 | 182 |

优先级分布：

| 优先级 | 数量 |
| --- | ---: |
| P0 | 45 |
| P1 | 15 |
| P2 | 122 |

建议动作分布：

| 建议动作 | 数量 |
| --- | ---: |
| 补真实批次/生产日期/到期日期 | 45 |
| 优先确认商品主数据：是否应关闭效期控制 | 10 |
| 业务确认真实生产日期/效期，或确认是否只保留批次 | 3 |
| 确认是否需要批次管理；不需要则调整商品主数据 | 3 |
| 业务确认：补真实追溯信息或调整商品控制口径 | 121 |

结论：

- 当前没有任何业务确认行可用于生成 `inventory-tracking-ready.csv`。
- 不能执行 `apply_inventory_tracking_repairs`。
- 不能宣布数据准确性完成闭环。

### 本次真实库只读 SQL 复核

由于真实库自定义管理命令尚未获得 explicit approval，本次先用只读 SQL 复核核心对账口径。

连接方式：

```bash
mysql --socket=/run/mysqld/mysqld.sock -u wmsuser wms_db
```

库存检查结果：

| 检查项 | 问题数 |
| --- | ---: |
| inventory_detail_available_identity | 0 |
| inventory_summary_available_identity | 0 |
| inventory_summary_vs_detail | 0 |
| inventory_transaction_replay_onhand | 0 |
| inventory_batch_tracking_integrity | 391 |
| inventory_expiry_tracking_integrity | 149 |
| inventory_serial_tracking_integrity | 0 |

计费检查结果：

| 检查项 | 问题数 |
| --- | ---: |
| billing_metric_non_negative | 0 |
| billing_accrual_consistency | 0 |
| bill_line_matches_accrual | 0 |
| bill_header_totals | 0 |
| billing_invoiced_accrual_linkage | 0 |

关键表行数：

| 表/范围 | 行数 |
| --- | ---: |
| inventory_inventorydetail active | 191 |
| inventory_inventorytransaction posted active | 218 |
| inventory_inventorysummary active | 182 |
| billing_billingmetricdaily | 0 |
| billing_billingaccrual | 0 |
| billing_bill | 0 |
| billing_billline | 0 |

注意：

- 当前真实库计费表为空，因此计费检查的 `0` 问题只能说明当前没有坏账单/坏计提记录，不能替代账单业务链路 UAT。
- 真实库只读 SQL 复核再次确认，当前阻断集中在库存批次和效期追溯。

追溯控制影响范围：

| 指标 | 数量 |
| --- | ---: |
| 启用批次管理的商品 | 202 |
| 启用效期管理的商品 | 67 |
| 启用序列号管理的商品 | 0 |
| 有批次缺失问题的商品 | 182 |
| 有效期/生产日期问题的商品 | 56 |

已生成商品级业务审阅清单：

- `var/data-accuracy/inventory-tracking-product-review-2026-06-25.csv`

该文件基于真实库只读查询生成，粒度为受影响商品。它不是修复导入文件，只用于业务判断每个商品应补真实追溯信息，还是调整商品主数据控制口径。

商品级处理分类：

| 分类 | 商品数 | 批次问题行 | 效期/生产日期问题行 | 当前库存量 |
| --- | ---: | ---: | ---: | ---: |
| A_疑似包装耗材_确认控制口径 | 16 | 35 | 28 | 14732.0000 |
| B_效期商品_补真实批次生产效期 | 43 | 109 | 121 | 24787.0000 |
| C_批次商品_补批次或确认关闭控制 | 123 | 247 | 0 | 883.0000 |

说明：

- A 类由商品名称启发式识别，例如包装、罐子、盖子、铝膜、胶带、纸箱、保鲜袋等；该分类只作为业务审阅入口，不能自动修改主数据。
- B 类需要优先补真实批次号、生产日期、到期日期。
- C 类需要补真实批次号，或由业务确认该商品不需要批次管理后再调整主数据。

### 追溯工单新鲜度校验

已生成工单校验报告：

- `var/data-accuracy/inventory-tracking-workpack-validation-2026-06-25.md`
- `var/data-accuracy/inventory-tracking-workpack-mismatches-2026-06-25.csv`
- `var/data-accuracy/validate_inventory_tracking_workpack.py`

校验结果：

| 项目 | 数量 |
| --- | ---: |
| 当前真实库问题对象 | 403 |
| 当前真实库问题标记 | 677 |
| repair template 行数 | 403 |
| business reply 行数 | 182 |
| priority 行数 | 182 |
| product review 行数 | 182 |
| 当前问题对象未被 repair template 覆盖 | 0 |
| repair template 中已过期对象 | 0 |
| live vs template 问题标记不一致 | 0 |
| repair template 重复 source/id | 0 |
| business reply 未覆盖当前问题对象 | 0 |
| business reply 引用了非当前问题对象 | 0 |
| product review 未覆盖当前问题商品 | 0 |
| product review 额外商品 | 0 |

说明：

- 业务当前可以基于现有 `inventory-tracking-business-reply.csv` 填写，不需要重新导出 repair template。
- `inventory-tracking-workpack-mismatches-2026-06-25.csv` 只有表头，无实际 mismatch 行。
- 当前阻断不是工单过期，而是业务确认字段未填写。

脚本质量检查：

```bash
.venv/bin/python -m black --check --diff var/data-accuracy/validate_inventory_tracking_workpack.py
.venv/bin/python -m isort --check-only --diff var/data-accuracy/validate_inventory_tracking_workpack.py
.venv/bin/python -m ruff check var/data-accuracy/validate_inventory_tracking_workpack.py
.venv/bin/python var/data-accuracy/validate_inventory_tracking_workpack.py
```

结果：均通过，脚本复跑后仍输出相同校验结论。

追溯问题样本：

| 来源 | ID | 货主 | 仓库 | 商品 | 商品名称 | 库位 | 问题 |
| --- | ---: | ---: | ---: | --- | --- | ---: | --- |
| detail | 1 | 1 | 1 | BYNY-0003 | 坚果包装罐子 | 1 | missing_batch_no; missing_expiry_date; missing_production_date |
| detail | 2 | 1 | 1 | BYNY-0004 | 坚果包装盖子 | 1 | missing_batch_no; missing_expiry_date; missing_production_date |
| detail | 3 | 1 | 1 | BYNY-0005 | 坚果包装封口铝膜 | 1 | missing_batch_no; missing_expiry_date; missing_production_date |
| detail | 4 | 1 | 1 | BYNY-0007 | 全豆豆奶自立袋248ml | 1 | missing_batch_no; missing_expiry_date; missing_production_date |
| detail | 5 | 1 | 1 | BYNY-0008 | 2.0全豆豆奶250ml标准包 | 1 | missing_batch_no; missing_expiry_date; missing_production_date |

## 真实库数据准确性命令状态

计划执行命令：

```bash
DB_HOST=localhost DB_PORT= DB_SOCKET=/run/mysqld/mysqld.sock \
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues --limit 20
```

状态：未执行。

原因：

- 工具权限审核认为该自定义管理命令连接真实配置库，存在潜在数据变更风险，需要用户明确批准 exact 操作。
- 源码复核显示 `reconcile_data_accuracy` 的 help 为“不修改业务数据”，实际入口只调用 reconcile 函数。
- 真正会修改数据的是 `reconcile_data_accuracy_cleanup --apply-safe-fixes` 和 `apply_inventory_tracking_repairs`。

下一步：

- 如需复查当前真实库最新状态，需要用户明确批准上面的 exact 命令。

## 自动化测试状态

### 数据准确性相关 core 测试

尝试命令：

```bash
DB_HOST=localhost DB_PORT= DB_SOCKET=/run/mysqld/mysqld.sock \
.venv/bin/python -m pytest -q --create-db --disable-warnings allapp/core/tests.py
```

结果：未完成。

现象：

- 初次 `--reuse-db` 失败，因为旧 `test_wms_db` 残留表导致 `Table already exists`。
- 删除 `test_wms_db` 后使用 `--create-db`，测试库完整 migration 初始化超过 15 分钟仍未进入断言，手动中断。
- 改跑单个最小用例仍被测试库 schema 初始化拖住超过 10 分钟，手动中断。
- 改用 `--no-migrations` 后，单个最小用例仍在 schema 外键/索引创建阶段耗时超过 9 分钟，手动中断。
- 每次中断后已删除残缺 `test_wms_db`，避免污染后续测试。

结论：

- 当前本机 MySQL 测试库初始化性能异常，阻断自动化回归。
- 这不是业务断言失败；测试未能跑到断言阶段。
- 自动化回归需要先恢复一个可用的测试数据库环境。

## 前端页面配置检查

已执行轻量页面配置检查，不依赖测试库。

| 客户端 | pages.json 页面数 | 缺失页面文件数 |
| --- | ---: | ---: |
| wmspda | 17 | 0 |
| wmsownersale | 18 | 0 |
| wmsbossbilling | 9 | 0 |

结论：

- 三个 uni-app 客户端的 `pages.json` 可解析。
- 当前声明的 `.vue` 页面文件均存在。

## 生产配置检查

执行命令：

```bash
APP_ENV=production DEBUG=False \
DB_HOST=localhost DB_PORT= DB_SOCKET=/run/mysqld/mysqld.sock \
.venv/bin/python manage.py check --deploy
```

结果：无 error，有 2 个 security warning。

| 检查项 | 结果 |
| --- | --- |
| security.W004 | 未设置 `SECURE_HSTS_SECONDS` |
| security.W008 | `SECURE_SSL_REDIRECT` 未设置为 `True` |

说明：

- 如果 HTTPS、HSTS、HTTP -> HTTPS 跳转由 Nginx、负载均衡或 CDN 统一处理，需要上线配置文档明确责任边界。
- 如果由 Django 负责，则上线前应补充生产安全配置。

## 文件格式检查

执行命令：

```bash
git diff --check
```

结果：通过。

## Python 语法编译检查

执行命令：

```bash
.venv/bin/python -m compileall -q allapp wmsmaster
```

结果：通过。

## 上线阻断项

1. 数据准确性未闭环：批次/生产日期/效期追溯问题仍未获得业务真实值回填。
2. 真实库最新 `reconcile_data_accuracy --fail-on-issues` 尚未获得 explicit approval 执行。
3. 本机 Django 测试库初始化过慢，当前无法完成自动化回归。
4. 当前真实库计费表为空，计费准确性仍需要准备账单样本或通过 staging/UAT 数据验证完整链路。
5. 生产配置检查存在 2 个 security warning，需要确认是否由反向代理/负载均衡承担 HTTPS/HSTS。

## 建议下一步

1. 业务先填写 `var/data-accuracy/inventory-tracking-business-reply.csv` 的 182 行确认信息。
2. 研发/QA 获得批准后，对真实库重新执行只读对账：

```bash
DB_HOST=localhost DB_PORT= DB_SOCKET=/run/mysqld/mysqld.sock \
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues --limit 20
```

3. 业务回填完成后执行合并、修复、复核闭环：

```bash
.venv/bin/python manage.py merge_inventory_tracking_business_reply \
var/data-accuracy/inventory-tracking-repair-template.csv \
var/data-accuracy/inventory-tracking-business-reply.csv \
--output var/data-accuracy/inventory-tracking-ready.csv
```

```bash
.venv/bin/python manage.py apply_inventory_tracking_repairs \
var/data-accuracy/inventory-tracking-ready.csv
```

```bash
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues
```

4. 修复测试数据库环境，建议：

- 正常启动 MySQL networking，或统一配置测试使用 socket。
- 清理旧 `test_wms_db`。
- 优先跑 `allapp/core/tests.py`、`allapp/inventory/tests.py`、`allapp/billing/tests.py`、`allapp/pos/tests.py`。
- 再跑完整 platform、warehouse、settlement、business-flows 分组。
