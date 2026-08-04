# WMS 全系统测试审计报告

> 审计日期：2026-08-01  
> 审计范围：`allapp` 全部 Django 应用、四个客户端清单、测试配置及 GitHub Actions 发现范围  
> 审计原则：只补测试和测试基础设施，不修改业务实现；已经确认的业务缺陷单列，不用预期失败测试掩盖

## 1. 审计结论

本轮把自动化测试从 **47 个文件、637 条用例**提升到 **49 个文件、669 条用例**，共新增 32 条测试。新增覆盖集中在最容易造成生产事故的租户隔离、事务原子性、幂等、金额准确性、导出边界和客户端配置完整性。

同时修正了 CI 的测试发现范围：POS 和销售商城从显式文件列表改为应用目录。当前五个主矩阵会发现并执行全部 669 条 Python 测试，包括此前遗漏的 POS 并发测试、商城 Admin 测试和以后新增的规范命名测试文件。

审计也确认了若干业务实现缺陷，其中商品详情 IDOR、Admin 多租户隔离、登录审计绕过以及库存/任务跨货主数据约束应作为发布阻断项。它们不是“缺一条测试”就能解决的问题：应先修业务实现，再把相应失败场景固化为绿色回归测试。

## 2. 审计方法

本次检查包括：

1. 收集所有 `tests.py` 和 `test_*.py`，逐应用核对模型、服务、API、Admin、管理命令、并发和跨模块测试。
2. 重点审查货主/仓库隔离、状态机、幂等键、金额精度、事务回滚、导入导出和外部支付边界。
3. 检查 pytest 配置、CI 分片与实际文件发现范围是否一致。
4. 对四个客户端的页面清单、Manifest、Tab 路由、组件和图标引用做静态契约检查。
5. 用独立 MySQL 测试库执行新增测试和相关模块回归，并进行全仓收集、Ruff、isort/Black 和 diff 检查。

## 3. 本轮新增测试

| 领域 | 新增 | 文件 | 新覆盖 |
| --- | ---: | --- | --- |
| 账号与审计 | 4 | `allapp/accounts/tests.py` | JWT 成功登录审计、失败登录脱敏、审计存储故障降级、审计事件不可篡改 |
| 基础资料 | 2 | `allapp/baseinfo/tests.py` | 客户编码按货主唯一；空外部编码归一化 |
| 核心配置 | 2 | `allapp/core/tests.py` | 配置接口认证；只暴露启用且允许客户端读取的配置 |
| 客户端清单 | 3 | `allapp/core/test_client_manifests.py` | JSONC 可解析、路由唯一、页面组件和 Tab 图标存在 |
| 司机 | 1 | `allapp/driverapp/tests.py` | 打卡请求 ID 按司机幂等 |
| 库位与容器 | 3 | `allapp/locations/tests.py` | 库位编码错仓、容器跨仓库位、公共/私有容器货主规则 |
| 入库 | 2 | `allapp/inbound/tests.py` | 无单收货跨货主商品零写入；过账失败全回滚并可重试 |
| 库存 | 1 | `allapp/inventory/tests.py` | 多库位拣货后序失败时库存、流水、汇总、日记账和扫描全部回滚 |
| 出库 | 1 | `allapp/outbound/test_production_remediation.py` | 多行取消遇冻结不足时已释放库存、任务、订单和日志整体回滚 |
| 任务 | 1 | `allapp/tasking/tests.py` | 发布时混入其他任务行会拒绝且不留下状态或指派 |
| 计费 | 3 | `allapp/billing/tests.py` | 发票小计/税额/总额；建行失败回滚；账单导出货主隔离 |
| 报表 | 2 | `allapp/reports/test_etl_operations.py`、`test_operations_v2.py` | 计费事实更新/普通作废幂等同步；导出最大行数 413 契约 |
| Console | 1 | `allapp/console/tests.py` | 跨仓任务的扫描、行编辑、过账直达 URL 返回 404 且不调用服务 |
| POS | 4 | `allapp/pos/tests.py` | 收货抬头、客户、班次仓库隔离；非法退货/还款过滤参数 |
| 商城对账命令 | 2 | `allapp/salesapp/test_management_commands.py` | 到期订单/支付/退款编排；单条失败不阻断其他记录并最终报错 |
| **合计** | **32** | **14 个修改文件、2 个新增文件** | |

## 4. CI 修正

修改文件：`.github/workflows/ci.yml`。

- `pos-sales` 从只执行 `allapp/pos/tests.py` 改为执行 `allapp/pos`，现在会包含 `test_checkout_concurrency.py`。
- `settlement` 从只执行 `allapp/salesapp/tests.py` 改为执行 `allapp/salesapp`，现在会包含 Admin、纯单元、管理命令及后续新增文件。
- 当前分组收集数：platform 166、warehouse 135、settlement 271、pos-sales 85、business-flows 12，合计 669。

## 5. 已确认的业务风险

### 5.1 P0：发布阻断

1. **商品详情存在匿名与跨货主 IDOR。** `allapp/products/views.py:286` 的 `get_product_details` 直接按主键查询，`allapp/products/urls.py:15` 暴露该路由，但没有认证或 `AccessScope` 过滤。匿名用户或其他货主可枚举商品及包装信息。
2. **多个 Admin 缺少多租户隔离。** `allapp/baseinfo/admin.py` 和 `allapp/locations/admin.py` 的多个 `ModelAdmin` 没有按访问范围过滤 queryset 和对象写权限；`allapp/products/admin.py:225` 的商品查看权限无条件返回 `True`。需要统一使用 `AccessScope`，同时覆盖列表、搜索、自动完成、详情、修改和删除。
3. **通用 JWT 登录可绕过审计。** `wmsmaster/urls.py:20` 的审计登录与第 51、54 行的通用 `/api/token/` 同时存在，后者还重复注册并复用同一个 URL name。调用 `/api/token/` 不经过自定义登录审计，`reverse("token_obtain_pair")` 也可能解析到非预期入口。
4. **任务和库存缺少跨货主关系约束。** `allapp/tasking/models.py:302` 未校验任务行商品属于任务货主且没有 `save/full_clean` 兜底；`TaskScanLog.clean` 未校验任务行属于任务、扫描商品与任务行商品一致、商品属于任务货主；`allapp/inventory/models.py:149` 和第 482 行未校验库存明细/流水的商品货主与记录货主一致。直接 ORM 或 Admin 写入可形成跨货主脏数据，并可能污染普通任务过账。

### 5.2 P1：高风险功能缺陷

1. `allapp/outbound/management/commands/release_to_pick.py:38` 调用了不存在的 `ob_services.release_to_pick`，管理命令当前会抛 `AttributeError`。
2. `allapp/tasking/services_posting.py:41` 在整体原子事务中把日记账和任务标记为失败后再次抛异常；第 102—114 行的失败状态和 `attempt_count` 会随事务回滚，与函数说明不一致，失败审计不可见。
3. `allapp/reports/etl_operations.py:646` 和第 749 行无条件排除 `VOID` 计提。但账期解锁产生的冲销记录是 `VOID + is_reversal=True + 负金额`，冲销不会进入事实/聚合表，原收入可能继续保留并被高估。
4. `allapp/reports/dispatch_note_builder.py:157` 读取不存在的 `OutboundOrderLine.price`，实际字段为 `base_price`，配送单价和金额会变为零；同文件的人民币大写算法对整数存在前导“零”和跨万分组错误。
5. `allapp/console/views_op.py` 的成功路径存在三处不一致：默认 JSON session 写入 `Decimal/date` 会序列化失败；第 219—235 行保存收货时写 `qty`，快照服务读取 `qty_ok`；第 241—259 行调用 `scan_task` 的参数与实际签名不一致。
6. `allapp/products/management/commands/import_products_excel.py:221` 在单行事务之外创建或恢复计量单位，后续行校验失败会留下孤立单位，违背导入失败零副作用原则。
7. `allapp/driverapp/models.py:154` 起的轨迹、预签收和异常模型缺少“恰好一个设备来源”以及“站点属于当前配送任务”的约束，可把其他任务站点串入当前任务。
8. `allapp/strategies/models.py` 未保证策略分类与模板分类一致，也未限制策略分配结束时间不得早于开始时间。
9. `allapp/tasking/services.py:17` 导入的 Django `ValidationError` 在第 28 行被 DRF 同名异常覆盖，领域服务对调用方抛出的异常类型不稳定。

### 5.3 P2：可维护性问题

- `labeling` 当前没有注册到 `INSTALLED_APPS`，其模型约束、Admin 权限和标签渲染链路无法进行真实集成测试。
- 大量模型仍使用即将在 Django 6.0 移除的 `CheckConstraint.check`，目前测试会产生 139 条同类弃用警告。

## 6. 仍缺少的测试

### 6.1 修复业务后必须立即补的回归

- 商品详情匿名拒绝、跨货主 404；Baseinfo/Locations/Products Admin 的列表与对象级隔离。
- 所有登录别名均产生成功/失败审计，且 URL name 唯一。
- 任务行、扫描、库存明细和库存流水的货主—商品—任务关系矩阵；服务和直接 ORM/Admin 两条写入路径。
- 计费冲销从账期解锁到 ETL 事实及日聚合的端到端净额。
- 配送单正确读取基础单价，PDF/快照优先读取、人民币大写边界及 `backfill_dispatch_snapshots`。
- Console 作业台保存、恢复、收货、扫描、过账的成功路径和每个阶段失败回滚。
- `release_to_pick` 管理命令的成功、非法状态、幂等和错误路径。

### 6.2 仓储流程

- 标准出库 `withdraw_order`、`wave_release` 的状态矩阵和整体回滚。
- COUNT 正差、负差、零差异的真实过账闭环。
- PUTAWAY 多行中途失败回滚及并发；任务 `start/complete/cancel` 使用真实服务的状态矩阵。
- 同一任务 `claim/unclaim` 并发，以及取消与拣货并发竞态。

### 6.3 计费、报表与后台

- 计费 Admin actions；CSV 重复行、跨范围和批次事务回滚。
- 报表维度 SCD2 历史、审计事件契约、ETL 冲销及日期窗口组合。
- 商品旧 Excel 导入的整批事务、单位副作用和失败清理。

### 6.4 系统级与外部环境

- 四个旧客户端目前只有清单结构契约，没有浏览器/真机组件单测或端到端自动化。
- 真实 PDA、扫码枪、标签/小票打印机、微信支付沙箱及回调重放仍需环境测试。
- 还缺负载/容量、长时间稳定性、故障注入、备份恢复 RTO/RPO 和灾难切换演练。

## 7. 验证记录

截至审计报告生成时已完成：

- 全仓收集：`669 tests collected`。
- POS 主套件：`80 passed`。
- POS 真实事务并发专项：`5 passed`。
- 仓储域四个相关文件回归：`61 passed`。
- 平台新增及直接相邻回归：`17 passed`；通用 API：`3 passed`。
- 计费/报表/Console 新增用例：`6 passed`。
- 客户端清单和商城对账命令纯测试：`5 passed`。
- 全部变更文件通过 Ruff 和 `git diff --check`；两个新增独立测试文件通过 Black、isort；Django `manage.py check` 通过。历史大文件保留原格式，未为满足格式工具而夹带整文件重排。

没有在本地串行重跑全部 669 条数据库测试；全量运行仍由 CI 的 MySQL/Redis 五分片矩阵承担。所有新增用例均已被精确执行，关键受影响模块做了相邻回归。

## 8. 建议处理顺序

1. 先修复 P0 的商品 IDOR、Admin 隔离、登录审计绕过和跨货主数据约束，并补对应回归。
2. 修复计费冲销、配送单金额、Console 成功路径和失败审计持久化；用端到端测试锁定财务与库存结果。
3. 补齐标准撤回/波次、盘点、上架和任务竞态测试。
4. 在预生产补浏览器/真机、支付沙箱、打印、容量和恢复演练。

当前测试文件、每个领域的详细覆盖、执行命令和发布门禁见 `docs/system-testing-manual.md`。
