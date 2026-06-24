# 上线前全系统测试计划

## 1. 目标

本计划用于系统正式上线前的全量验证，目标不是简单确认页面可打开，而是确认 WMS 在真实业务链路、数据一致性、权限隔离、账单结算、移动端作业、POS 收银和运维发布场景下都可控、可追踪、可回滚。

上线判断以业务结果为核心：

- 入库、库存、出库、任务、报表、计费、POS 的核心数据正确。
- `owner + warehouse` 数据隔离正确，不能串货主、串仓。
- 重复提交、并发提交、断网重试不会造成重复库存、重复计费或重复扣减。
- PDA、货主端、老板账单端、Django console/admin 均完成冒烟和 UAT。
- 发布、迁移、备份、恢复、回滚流程经过演练。

## 2. 测试范围

### 后端与 API

- 账号与权限：登录、JWT 刷新/校验、个人资料、改密、角色权限、货主/仓库范围。
- 基础资料：货主、客户、供应商、员工、承运商、车辆、产品、包装、条码、库区、库位、容器。
- 入库：正式入库单、无订单入库、收货、上架、批次/效期、审批和驳回。
- 库存：明细、汇总、交易流水、库存调整、盘点、快照、批次/效期修复。
- 出库：订单创建、提交、货主审核、仓库审核、分配、取消、关闭、重新打开。
- 任务：拣货、复核、上架、盘点、扫描日志、任务过账、任务取消。
- POS：开班、扫码选品、结账、拆分支付、打印、作废、退货、闭班、重开班、统计。
- 报表：仓库统计、PDA 吞吐、库存报表、老板驾驶舱、导出。
- 计费：规则、指标、计提、锁账、开票、导出、重开账期、调度任务。
- 管理命令：数据准确性检查、库存快照、计费调度、产品导入、报表 ETL、出库释放拣货。

### 前端与客户端

- Django admin 和 console：运营管理、任务管理、计费总览、账单详情。
- `wmspda`：登录、无订单入库、拣货、复核、POS、库存汇总、报表、改密。
- `wmsownersale`：登录、客户选择、选品、购物车、订单、审核、一件代发导入、实时库存、报表、计费。
- `wmsbossbilling`：登录、经营总览、库存与库容、预警、收入与计费、货主收入明细、账单详情。
- 硬件能力：PDA 扫码、RFID/NFC、蓝牙打印、标签/小票打印。

### 运维与发布

- MySQL、Redis、静态文件、媒体文件、Docker 镜像、CI/CD、环境变量、备份恢复、日志和告警。

## 3. 环境策略

| 环境 | 用途 | 通过要求 |
| --- | --- | --- |
| 本地 | 开发自测和定向回归 | 相关模块测试通过 |
| CI | 每次合并请求和主干校验 | lint、分组测试、覆盖率、镜像构建通过 |
| Staging | 集成测试、端到端验证、UAT | 全量自动化、核心业务链路、前端冒烟通过 |
| Pre-production | 上线彩排 | 迁移、备份恢复、回滚、性能、数据准确性通过 |
| Production smoke | 上线后冒烟 | 只执行低风险只读或小样本闭环验证 |

环境配置检查：

- 生产和预生产必须 `DEBUG=False`。
- `ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、`CORS_ALLOWED_ORIGINS` 与真实域名匹配。
- 禁止生产环境 `CORS_ALLOW_ALL_ORIGINS=True`。
- 数据库字符集、时区、Redis、静态文件、媒体目录、日志目录配置正确。
- 所有密钥来自环境变量或密钥管理，不提交到仓库。

## 4. 测试数据

准备一套可重复初始化的上线测试数据：

- 2 个货主，至少 1 个货主绑定普通用户。
- 2 个仓库，每仓有库区、库位、容器，包含跨仓负例数据。
- 产品覆盖普通 SKU、包装条码、批次管理、效期管理、FEFO、最低价、最大折扣、多货主商品。
- 库存覆盖可用库存、已分配库存、多库位库存、临期/过期库存、低库存、无库存。
- 单据覆盖正式入库、无订单入库、出库订单、多行多库位订单、取消/驳回订单。
- POS 覆盖现金、非现金、拆分支付、作废、退货、未开班、重复小票号、幂等键。
- 计费覆盖仓储费、订单处理费、阶梯价、封顶、包干、百分比、税率、开口账期、已锁账期、已开票账期。
- 用户覆盖超级管理员、仓库操作员、货主用户、计费用户、老板看板用户、POS 收银员、POS 作废/退款权限用户、无权限用户。
- 设备覆盖至少 1 台真实 PDA、1 台扫码设备、1 台打印设备。

## 5. 执行阶段

### 阶段 0：上线冻结与基线确认

负责人：项目负责人、后端、前端、运维、QA。

执行项：

- 确认上线分支、提交 SHA、Docker 镜像标签。
- 冻结非紧急需求，仅允许阻断缺陷修复。
- 整理本次上线变更清单、数据库迁移清单、配置变更清单。
- 备份 staging/pre-production 数据库和媒体文件。
- 确认回滚版本、回滚脚本和回滚负责人。

通过标准：

- 版本、配置、数据、回滚方案均可追溯。
- 无未确认的数据库破坏性变更。

### 阶段 1：静态检查、迁移检查、自动化回归

负责人：后端、QA。

建议命令：

```bash
black --check --diff .
isort --check-only --diff .
ruff check .
flake8 . --max-line-length=100 --extend-ignore=E203,W503
bandit -r allapp/ -ll
```

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py showmigrations --plan
.venv/bin/python manage.py migrate --check
```

CI 已有分组：

```bash
.venv/bin/python -m pytest -q allapp/accounts/tests.py allapp/baseinfo/tests.py allapp/core/tests.py allapp/driverapp/tests.py allapp/locations/tests.py allapp/products/tests.py
.venv/bin/python -m pytest -q allapp/inbound/tests.py allapp/inventory/tests.py allapp/outbound/tests.py allapp/tasking/tests.py
.venv/bin/python -m pytest -q allapp/reports/tests.py allapp/billing/tests.py allapp/console/tests.py allapp/salesapp/tests.py
.venv/bin/python -m pytest -q allapp/test_business_flows.py
```

上线前额外必须跑 POS：

```bash
.venv/bin/python -m pytest -q allapp/pos/tests.py
```

通过标准：

- 所有自动化测试通过。
- 覆盖率不低于 CI 门槛 70%。
- 没有新增 migration 漏提交。
- 没有新增高危安全扫描问题。
- POS 套件必须单独通过，不能只依赖现有 CI 分组。

### 阶段 2：数据准确性与账务基线

负责人：后端、财务、业务、QA。

执行项：

```bash
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues
.venv/bin/python manage.py reconcile_data_accuracy_cleanup --output /tmp/data_accuracy_cleanup.json
```

如果需要修复库存批次/效期追踪，先导出、业务确认，再应用：

```bash
.venv/bin/python manage.py export_inventory_tracking_repair_template /tmp/inventory_tracking_repair_template.csv
.venv/bin/python manage.py export_inventory_tracking_business_reply_sheet /tmp/inventory_tracking_repair_template.csv /tmp/inventory_tracking_business_reply.csv
.venv/bin/python manage.py merge_inventory_tracking_business_reply /tmp/inventory_tracking_repair_template.csv /tmp/inventory_tracking_business_reply.csv --output /tmp/inventory_tracking_repair_ready.csv
.venv/bin/python manage.py apply_inventory_tracking_repairs /tmp/inventory_tracking_repair_ready.csv
.venv/bin/python manage.py reconcile_data_accuracy --fail-on-issues
```

账务链路抽查：

```bash
.venv/bin/python manage.py inventory_generate_snapshot --date <service_date> --owner <owner_id> --warehouse <warehouse_id>
.venv/bin/python manage.py billing_run_scheduler --once --date <service_date> --owner <owner_id> --warehouse <warehouse_id>
.venv/bin/python manage.py reconcile_data_accuracy --owner <owner_id> --warehouse <warehouse_id> --billing-only --date <service_date> --fail-on-issues
```

通过标准：

- 全局数据准确性检查通过。
- 抽样货主/仓库的库存汇总、交易流水、计费指标、账单金额可解释。
- 所有修复动作有审批、备份和执行记录。

### 阶段 3：核心业务端到端测试

负责人：QA 主导，仓库业务、货主代表、财务参与。

必须验证的业务闭环：

| 编号 | 链路 | 关键断言 |
| --- | --- | --- |
| E2E-01 | 无订单入库 -> 收货/上架 -> 库存可见 | 明细、汇总、流水一致 |
| E2E-02 | 正式入库单 -> 审批 -> 收货 -> 上架 | 状态流转正确，批次/效期正确 |
| E2E-03 | 出库订单 -> 审批 -> 分配 -> 拣货 -> 复核 -> 过账 | 可用库存扣减，分配释放 |
| E2E-04 | 出库取消/驳回/关闭/重开 | 冻结库存正确回滚 |
| E2E-05 | 多行多库位出库 | 每行、每库位扣减正确 |
| E2E-06 | 库存调整/盘点 -> 报表 | 库存与报表同步 |
| E2E-07 | 快照 -> 计费指标 -> 计提 -> 锁账 -> 开票 -> 导出 | 金额、税额、账期状态正确 |
| E2E-08 | 货主端下单 -> 审核 -> 查看库存和账单 -> 导出 | 货主只能看自己的数据 |
| E2E-09 | PDA 登录 -> 扫码拣货/复核 -> 状态推进 | 扫描数量、任务状态、日志正确 |
| E2E-10 | Console 计费总览 -> 账单详情 | 总览与明细范围一致 |
| E2E-11 | POS 开班 -> 扫码 -> 结账 -> 打印 -> 闭班 | 订单、库存、支付、班次统计一致 |
| E2E-12 | POS 作废/退货/重试 | 库存恢复，支付统计正确，无重复处理 |

通过标准：

- 每条链路都有测试账号、测试数据、执行记录、截图或导出凭证。
- 每条链路的最终业务数据可由源单据追溯。
- 任何库存或账单差异必须阻断上线。

### 阶段 4：API、权限和边界测试

负责人：后端、QA。

重点接口：

- `/api/auth/*`
- `/api/inbound/*`
- `/api/tasking/*`
- `/api/reports/*`
- `/api/pos/*`
- `/api/v1/*`
- `/api/` 下的 inventory、outbound、billing 路由
- `/products/*`
- `/console/*`

测试点：

- 未登录、过期 token、错误 token、权限不足。
- 货主用户访问其他货主数据。
- 仓库用户访问其他仓库数据。
- 筛选、分页、排序、搜索、导出。
- 重复提交、重复审批、重复过账、重复开票。
- 空数据、大数据、非法日期、非法数量、负数、超库存、低价/超折扣。
- 导入文件格式错误、缺失列、重复行、非法编码。

通过标准：

- 无越权读写。
- 错误返回可理解，且不会产生副作用。
- 导出内容与页面/API 查询结果一致。

### 阶段 5：前端冒烟和 UAT

负责人：前端、QA、业务代表。

#### Django admin / console

- 登录、菜单、列表、筛选、详情、创建、编辑、批量动作。
- 入库审批、出库审批、任务释放/过账、库存调整、计费锁账/开票。
- 大列表横向滚动、固定表头、日期筛选、导出。

#### `wmspda`

- 登录和 token 过期重登。
- 无订单入库：选择货主/供应商、扫码录入产品、提交。
- 拣货任务：任务列表、任务详情、扫码、数量修改、提交。
- 复核任务：列表、明细、复核提交。
- POS：开班、扫码、购物车、结算、打印、作废/退货。
- 报表、库存汇总、个人中心、修改密码。

#### `wmsownersale`

- 登录、工作台、客户选择、选品、购物车、提交销售订单。
- 订单列表、订单详情、审核、一件代发导入。
- 实时库存、报表、计费总览、账单详情、导出。
- 修改密码和无权限页面。

#### `wmsbossbilling`

- 登录、经营总览、库存与库容、预警中心、收入与计费。
- 货主收入明细、收费记录详情、账单详情、库存明细。
- 仓库范围和货主筛选正确。

通过标准：

- Chrome/Edge 桌面浏览器冒烟通过。
- 至少 1 台真实 Android PDA 真机通过。
- 关键页面无白屏、无 JS 错误、无明显布局遮挡。
- 关键操作成功后刷新页面仍显示正确状态。

### 阶段 6：硬件和外设测试

负责人：仓库业务、前端、QA。

测试点：

- PDA 摄像头/扫码头扫商品条码、库位码、任务码。
- RFID/NFC 插件加载和异常提示。
- 蓝牙打印、标签打印、小票打印。
- 打印断连、重复打印、打印份数、打印日志。
- 弱网、断网、重连、重复点击提交。

通过标准：

- 实机可以完成至少一条入库、一条出库、一条 POS 打印链路。
- 扫描错误码、无效条码、重复扫描都有可理解反馈。
- 打印记录可追踪到原始业务单据。

### 阶段 7：非功能测试

负责人：后端、运维、QA。

#### 性能

上线前临时目标，后续可按正式 SLA 调整：

- 登录和首页首屏：P95 小于 2 秒。
- 常用列表筛选：P95 小于 3 秒。
- 常用导出：小批量小于 10 秒，中等批量小于 60 秒。
- PDA 扫码提交：P95 小于 1 秒。
- POS 结账：P95 小于 3 秒。
- 日终计费/快照任务在业务窗口内完成。

压测场景：

- 30 个 PDA 操作员并发扫码/提交。
- 20 个货主用户并发查库存、下单、导出。
- 5 个计费/报表用户并发查大报表。
- 5 个 POS 收银台并发结账、退货、作废。

#### 稳定性

- 并发审批、并发过账、并发锁账、并发开票。
- 定时任务重复启动。
- 服务重启后未完成任务可恢复。
- 数据库锁等待和慢查询检查。

#### 安全

- `DEBUG=False`、密钥不泄露、管理后台强密码。
- CSRF、CORS、JWT 过期策略。
- 文件上传和导入限制。
- 越权访问、水平越权、批量导出越权。
- 依赖扫描和镜像扫描无高危阻断项。

#### 备份恢复和回滚

- 数据库全量备份可恢复。
- 媒体文件备份可恢复。
- 迁移失败可以回滚到上一版本。
- 应用镜像可以回退到上一稳定版本。
- 回滚后核心只读查询、登录、库存查询正常。

通过标准：

- 无性能阻断问题。
- 无 P0/P1 安全问题。
- 完成一次可复盘的备份恢复和回滚演练。

## 6. 准入和退出标准

### 测试准入

- 上线候选版本已冻结。
- Staging 或 pre-production 已部署候选版本。
- 数据库迁移可执行。
- 测试账号、测试数据、真实 PDA 和打印设备已准备。
- 业务负责人已确认 UAT 场景。

### 上线退出

必须全部满足：

- CI 通过，覆盖率不低于 70%。
- POS 单独回归通过。
- 数据准确性检查通过。
- 12 条核心端到端链路通过。
- PDA、货主端、老板账单端、admin/console 冒烟通过。
- 无 P0/P1 缺陷，P2 缺陷有明确规避方案和负责人。
- 备份、恢复、回滚演练通过。
- 业务、财务、运维、研发、QA 共同签字 Go/No-Go。

## 7. 缺陷分级

- P0 阻断：系统无法登录、无法下单、库存/账单核心数据错误、数据串货主/串仓、迁移失败、无法回滚。
- P1 严重：核心链路不可用或高频错误，但有短期人工规避方案。
- P2 一般：功能可用但体验、提示、低频边界存在问题。
- P3 轻微：文案、样式、小范围兼容问题。

处理规则：

- P0/P1 必须当天评审，修复后跑相关模块回归和业务链路回归。
- 涉及库存、账单、权限的缺陷必须扩大回归范围。
- 所有缺陷保留复现步骤、账号、数据、截图、日志、修复版本和复测结果。

## 8. 建议排期

| 时间 | 工作 |
| --- | --- |
| D-5 | 版本冻结、测试数据准备、账号和设备准备、测试计划评审 |
| D-4 | CI、自动化回归、POS 回归、迁移检查 |
| D-3 | 数据准确性、库存/账单基线、核心 E2E 链路 |
| D-2 | 前端冒烟、PDA 真机、打印、UAT、权限和导出 |
| D-1 | 缺陷回归、性能抽测、备份恢复、回滚演练、Go/No-Go |
| 上线日 | 部署、迁移、生产冒烟、关键指标监控 |
| D+1 | 数据准确性复查、账单/库存抽查、问题复盘 |

## 9. 风险与缓解

| 风险 | 缓解措施 |
| --- | --- |
| POS 测试未纳入现有 CI 分组 | 上线前固定单跑 `allapp/pos/tests.py`，后续补入 CI 分组 |
| 真实 PDA/打印机行为与模拟环境不同 | 必须做真机入库、出库、POS 打印闭环 |
| 数据修复误操作 | 所有修复先导出、业务确认、备份，再执行 |
| 货主/仓库越权 | 每个关键接口和页面都放入跨货主、跨仓负例 |
| 快照和计费依赖历史数据 | 使用固定历史日期抽样，验证快照来源不是当前库存 |
| 并发导致重复库存/重复计费 | 保留并发自动化门禁，并做 staging 并发抽测 |
| 大导出拖慢系统 | 统计导出耗时，必要时限制范围或改异步导出 |
| 迁移后无法回退 | 上线前完成迁移和回滚演练，并保留备份 |

## 10. 交付物

- 自动化测试结果和覆盖率报告。
- 数据准确性检查报告。
- 核心 E2E 执行记录。
- PDA/货主端/老板账单端/admin 冒烟记录。
- POS 回归记录。
- 性能抽测记录。
- 安全扫描和配置检查记录。
- 备份恢复、回滚演练记录。
- 缺陷清单和 Go/No-Go 结论。
