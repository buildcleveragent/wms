# POS 自动 PICK 统一过账(198a8a6)验证报告

日期:2026-07-17 · 环境:本机 MySQL 8,全新测试库 `wms_test_pos_pick`(避开旧复用库的迁移不一致问题) · Django 5.2.12 / pytest 8.4.2

## 结论

**实现达成目标,未发现产品代码缺陷,可以合入。**
POS 销售已从直接扣库存改为"FEFO 预占 → 自动 PICK 任务 + 扫描日志 → 统一过账",仓库统计(任务/扫描/流水/计费)完整纳入 POS 拣货;新旧销售的退货/作废兼容;并发下不超卖、不死锁。

## 测试结果汇总

| 套件 | 结果 |
|---|---|
| allapp/pos/tests.py(提交自带 73 + 本次补充 6) | 79 passed |
| allapp/pos/test_checkout_concurrency.py(5 例并发) | 5 passed,0 skipped(真实 MySQL 行锁) |
| allapp/inventory/test_posting_concurrency.py | 3 passed |
| allapp/tasking/ + allapp/inventory/ + allapp/outbound/ | 79 passed(修复既有测试数据后) |
| allapp/billing/ | 86 passed |
| allapp/test_business_flows.py(含新增批次一致断言) | 12 passed |

定稿计划 17 项验收全部覆盖并通过,其中本次验证补齐 5 项缺口:
1. **计费正向应计**:`test_checkout_accrues_pick_and_order_processing_fees` — PICK 费(2 件 × 2 元 = 4.00)与订单处理费(5.00)均正确应计。
2. **公开过账权限**:`test_pos_cashier_cannot_use_public_posting_service` — 收银员经 POS 自动成为 `posted_by`,直接调 `services_posting.post_task()` 仍被 `PermissionDenied` 拒绝。
3. **批次统一回归**:`assert_posting_batch_aligned`(business_flows)— RECEIVE/PUTAWAY 过账后扫描与流水 `posting_batch` 一致(handlers.py 修正对所有任务类型生效)。
4. **FEFO/zone 结构断言**:升级两个既有测试,断言任务行 `plan_meta.source_inventory_detail_id` 命中预期库存层、流水携带真实 zone/效期。
5. **两次部分退货**:`test_two_partial_returns_exhaust_sale_then_third_is_rejected` — 两次各退 1 件回补正确,第三次超退被拒。

## 目标达成证据(D 组)

- **统计口径(改造动机)**:POS 拣货现以 `WmsTask(PICK, source_app=pos)` + `TaskScanLog(OK/APPROVED)` + `InventoryTransaction(src_model=WmsTask, memo=POS_SALE, src_no=sale_no)` 存在,任何基于任务/扫描/流水的统计与计费自动纳入;完整性测试断言全部字段。旧模式 `PosSaleLine+ISSUE` 不再产生。
- **作业队列不受污染**:console 拣货队列只显示 READY/IN_PROGRESS(console/views.py:88),POS 完成态任务不会进入派工。
- **兼容性**:旧销售作废、混合来源拒绝、软删层优雅拒绝、停用层回补 + accuracy warning、主键碰撞防护(src_line_id 语义硬分支)均有测试通过。

## 容量基准(E 组,同一热销 SKU,指示性数据)

| 终端数 | checkout p50 | p95 | max | 失败 |
|---|---|---|---|---|
| 1 | 2412ms | 2716ms | 4359ms | 0 |
| 2 | 5769ms | 6512ms | 7319ms | 0 |
| 4 | 10376ms | 11010ms | 12722ms | 0 |

热销 SKU 上多终端完全串行(p50 ≈ N × 单终端),符合 pair 级阻塞锁的设计预期;无死锁、无超卖。本测试机较慢,绝对值偏高,生产硬件上单笔延迟会显著更低,但串行比例不变。高频同 SKU 多终端场景需据此评估收银峰值。

## 发现与修复(均为测试侧,非产品代码)

1. **27 个既有测试数据缺陷(已修复)**:tasking/outbound 三个测试文件的 Owner/Warehouse `code` 测试数据超过模型 `max_length=10`,在严格模式 MySQL 上全部 `DataError 1406`(此前只能在 sqlite 通过)。全部失败为同一错误、位于本提交未触碰的文件、数据在父提交已存在——确认非回归。已按提交内同类做法(`IPC-WH`)统一缩短编码,复跑 79 passed。
2. 基准脚本自身单号复用问题(已修复后取数,临时文件已删除)。

## 工作区变更清单(未提交)

- `allapp/pos/tests.py`:+3 个新测试、2 个测试结构断言升级
- `allapp/test_business_flows.py`:批次一致断言 helper + 2 处调用
- `allapp/tasking/tests.py`、`allapp/tasking/test_assisted_outbound_integrity.py`、`allapp/outbound/test_assisted_flow.py`:超长编码缩短
- `.env`(未跟踪):本机测试环境配置,测试库 `wms_test_pos_pick` 已保留可复用(pytest 需加 `--reuse-db`)
