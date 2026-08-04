# WMS 系统测试手册

> 文档基线：2026-08-01 代码库现状  
> 适用范围：Django 后端、API、Console/Admin、PDA/货主端相关接口、POS、商城小程序、报表与计费  
> 自动化基线：49 个 Python 测试文件，共 669 个测试用例

## 1. 文档目的

本手册说明本系统当前已经实现了哪些测试、测试代码位于哪里、各套件如何运行，以及上线前还需要完成哪些人工验证。它既可用于开发自测，也可作为 CI、测试环境回归和上线验收的执行入口。

本手册描述的是当前代码实际存在的测试，不把规划中但尚未落地的测试写成已完成项。更细的上线测试安排、商城专项方案和人工冒烟步骤分别见：

- `docs/release-test-plan.md`
- `docs/sales-miniapp-test-plan.md`
- `docs/business-flow-smoke-checklist.md`
- `docs/data-accuracy-runbook.md`
- `docs/test-audit-2026-08-01.md`

## 2. 测试技术与发现规则

### 2.1 测试框架

- Python 3.12
- Django 5.2
- pytest 8
- pytest-django
- pytest-xdist：并行执行
- pytest-cov / coverage：覆盖率统计
- MySQL 8：数据库、约束、行锁和并发测试
- Node.js 20：商城结构检查及 H5/微信小程序构建

pytest 配置位于 `pytest.ini`：

- Django settings：`wmsmaster.settings`
- 搜索目录：`allapp`
- 文件规则：`tests.py`、`test_*.py`、`*_tests.py`
- 已注册标记：`unit`、`integration`、`api`、`smoke`、`e2e`、`slow`
- 开启 `--strict-config` 和 `--strict-markers`

目前只有部分文件设置了 pytest 标记，因此 `pytest -m unit` 不是全部单元测试，`pytest -m integration` 也不是全部集成测试。按业务模块回归时，应优先按文件或目录执行。

### 2.2 Django 测试类型

- `SimpleTestCase`：不依赖真实测试数据库，适合配置、纯函数、命令参数和文件结构测试。
- `TestCase`：需要测试数据库，每个测试在事务隔离中执行，覆盖模型、服务、API 和页面。
- `TransactionTestCase`：允许验证真实提交、锁和多连接并发，必须使用 MySQL 才能体现生产环境语义。
- 顶层 pytest 函数：主要用于商城定价、状态映射、支付加密校验等纯单元逻辑。

## 3. 当前自动化测试总览

| 领域 | 文件数 | 用例数 | 主要覆盖 |
| --- | ---: | ---: | --- |
| 账号与权限 | 1 | 47 | 角色、范围、权限矩阵、登录审计、审计防篡改、同步命令 |
| 通用 API | 1 | 3 | PDA APK 下载页和文件响应 |
| 基础资料 | 1 | 4 | 员工、承运商仓库范围，客户租户唯一约束 |
| 核心与数据治理 | 4 | 33 | 客户端清单、配置安全、数据准确性、业务数据清理、保留数据迁移 |
| 标签、司机、库位、策略 | 4 | 13 | 应用注册、仓库派生、容器边界、司机幂等、策略模型 |
| 商品 | 6 | 66 | 分类、商品 API、Excel 导入、SKU 序列、测试数据命令 |
| 入库 | 3 | 27 | 正式/无订单入库、Excel、PDA、权限、幂等、回滚与并发 |
| 库存 | 4 | 19 | 库存范围、修复、导出、快照、多库位原子过账、并发 |
| 出库 | 6 | 59 | 标准/辅助出库、历史、权限、幂等、取消回滚、生产修复、并发 |
| 任务 | 3 | 30 | 任务 API、扫描、范围、发布回滚、权限、过账并发 |
| 计费 | 2 | 94 | 规则、计提、账期、开票精度与回滚、导出隔离、API、调度、并发 |
| 报表 | 4 | 51 | 老板看板、PDA 吞吐、ETL 作废同步、运营 V2、导出上限 |
| Console | 1 | 9 | 看板授权、操作台越权防护、商城商品批量配置 |
| POS | 2 | 85 | 收银、支付、赊销、退货、作废、打印、租户隔离、统计、并发 |
| 销售与商城 | 6 | 117 | 销售 API、商城、购物车、支付退款、对账命令、评价、后台批量上架 |
| 跨模块业务闭环 | 1 | 12 | 入库到库存、出库、PDA、报表、计费完整链路 |
| **合计** | **49** | **669** | |

## 4. 各模块测试文件与覆盖内容

### 4.1 账号、基础能力与权限

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/accounts/tests.py` | 47 | 用户改密；JWT 登录成功/失败审计及审计故障降级；审计事件不可修改或删除；标准角色组权限矩阵；仅超级管理员管理角色组；角色组修改/删除审计；用户角色与货主/仓库范围模型约束；角色组自动同步；超级管理员、货主、仓库负责人、多仓负责人和无范围用户的数据范围；旧字段兼容必须 fail closed；`sync_wms_role_groups`、`sync_wms_user_role_memberships`、`audit_wms_role_scopes` 命令的 dry-run、幂等、冲突和 CSV 输出。 |
| `allapp/api/tests.py` | 3 | APK 下载页生成带时间戳链接；APK 文件响应头；文件不存在返回 404。该文件标记为 `api`。 |
| `allapp/baseinfo/tests.py` | 4 | 员工、承运商未明确绑定仓库时不得自动扩大范围；客户编码按货主唯一且可跨货主复用；空外部编码归一化。 |
| `allapp/driverapp/tests.py` | 3 | 配送任务必须有明确仓库；司机班次未绑定仓库时保持为空；司机打卡请求 ID 按司机幂等。 |
| `allapp/labeling/tests.py` | 1 | labeling 应用未注册前，模型不能被意外导入。该文件标记为 `unit`。 |
| `allapp/locations/tests.py` | 6 | 分仓必须明确仓库；库位从分仓派生仓库并拒绝编码错仓；容器从库位派生仓库并拒绝跨仓库位；公共/私有容器的货主绑定规则。 |
| `allapp/strategies/tests.py` | 3 | 策略树默认值和字符串表示；目标策略唯一；参数和日志保留审计上下文。该文件标记为 `unit`。 |

### 4.2 核心配置与数据治理

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/core/tests.py` | 15 | 单据序列仓库范围；核心配置接口认证和仅暴露启用、客户端可见配置；POS 打印配置 API；`reconcile_data_accuracy` 对库存汇总、账单头、库存流水重放、批次/序列号/效期问题的识别；安全清理命令；数据准确性工作包生成和范围冲突。 |
| `allapp/core/test_client_manifests.py` | 3 | 四个客户端的 `pages.json`/`manifest.json` 可解析；路由唯一且页面组件存在；Tab 路由和图标引用有效。 |
| `allapp/core/test_business_data_purge.py` | 8 | 所有托管模型必须显式分类；新增未分类模型 fail closed；业务数据清理 dry-run；保留配置并删除业务数据；执行确认参数；未知数据库表阻断；SQL 失败回滚并恢复外键设置；MySQL 命名锁避免两个清理任务并发。 |
| `allapp/core/test_preserved_data_transfer.py` | 7 | 保留数据清单；SQL 白名单验证；MySQL 凭据使用 0600 临时文件而不是命令参数；恢复 SQL 的事务和行数断言；执行确认参数；全新数据库备份/恢复往返；行数校验失败回滚。 |

### 4.3 商品与商品测试数据

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/products/tests.py` | 44 | 三级分类树、路径、循环和重挂限制；分类回填原子性；商品 ViewSet 的货主范围和跨货主管理权限；条码 ZPL；模板下载；批量启停；包装单位详情和自动完成；Excel 模板元数据、整批原子导入、重复编码/标识、软删除冲突、公式、文件类型/大小/行数、权限、并发唯一冲突；商品主数据命令及 dry-run。 |
| `allapp/products/test_sku_sequence.py` | 5 | 货主 SKU 序列递增；软删除不释放序号；迁移从有效和软删除商品初始化；更新不消耗序号；失败插入不消耗序号。 |
| `allapp/products/test_seed_product_categories_unit.py` | 5 | 预置分类定义唯一、父级优先、最多三级；商品关键词映射；礼盒误分类保护；默认分类；数据库名安全保护。 |
| `allapp/products/test_seed_product_categories.py` | 4 | 分类预置命令默认只预览；apply 创建三级分类且只处理空分类商品；幂等；失败整体回滚。 |
| `allapp/products/test_seed_product_prices_unit.py` | 4 | 测试价格生成稳定；分类价格区间；未知分类默认正价格；数据库名安全保护。 |
| `allapp/products/test_seed_product_prices.py` | 4 | 价格预置默认预览；只补缺失或非正价格；幂等；失败整体回滚。 |

### 4.4 入库

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/inbound/tests.py` | 15 | 入库单和批次仓库约束；收货单从订单派生仓库；收货任务草稿幂等；PDA 无订单收货后台过滤；驳回后两级复提；后台和动作的货主范围；销售员只看自己创建的订单；无订单收货专用权限；跨货主商品拒绝且零写入；请求重放和载荷冲突；过账失败整体回滚且可重试；收货打印/导出范围及实收数量；收货流水与待上架任务可追溯；并发创建收货任务保持唯一。 |
| `allapp/inbound/test_excel_import.py` | 9 | 无订单收货模板字段和货主元数据；包装数量换算预览不写库存；序列号商品整单拒绝；Excel 公式拒绝；批次/效期必填；文件类型和缺列；跨货主模板；预览数据防篡改；确认过账与重试幂等。 |
| `allapp/inbound/test_order_and_pda_api.py` | 3 | 货主销售员创建、列表、详情、复提的操作者范围；PDA 收货任务池/个人范围和收货幂等；上架目标记录及跨仓库位拒绝。 |

### 4.5 库存

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/inventory/tests.py` | 11 | 库存明细和流水从库位派生仓库；拒绝分仓不一致；差异复核要求明确仓库；快速调整拒绝库位/仓库不一致；多库位拣货中途失败时库存、流水、汇总、日记账和扫描状态整体回滚；批次/效期修复模板导出、业务回填合并、应用及过期模板拒绝；库存快照并发串行化。 |
| `allapp/inventory/test_admin_export.py` | 2 | Admin 导出沿用当前搜索条件；导出保持仓库访问范围。 |
| `allapp/inventory/test_management_commands.py` | 3 | `inventory_generate_snapshot` 单日、日期范围、货主/仓库范围和非法日期参数。该文件标记为 `unit`。 |
| `allapp/inventory/test_posting_concurrency.py` | 3 | 过账在锁定库存明细后刷新汇总；一次拣货生成多条可追溯出库流水；不同任务对同一库存读改写串行化，避免汇总丢失更新。 |

### 4.6 出库

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/outbound/tests.py` | 7 | 出库单明确仓库；货主审批分配幂等；提交/驳回/取消状态机；一件代发模板；拣货行完成后才可创建复核；拣货人不能复核自己的任务；并发审批只分配一次。 |
| `allapp/outbound/test_assisted_flow.py` | 13 | 辅助出库从创建、审批、分配到释放；request_id 重放和载荷指纹冲突；缺货整体回滚；权限和货主开关；无默认价格时使用提交价；多商品；包装目录及基础数量换算；跨货主客户、现金收件信息校验；同一操作员提交/复核/过账/关闭/重试；失败任务恢复；shadow 模式不能扩大范围。 |
| `allapp/outbound/test_assisted_history.py` | 6 | 历史在同仓共享、跨仓隔离；搜索筛选；禁用货主选项；来源不一致任务禁止打印；统计状态和金额；默认当天；最多 366 天。 |
| `allapp/outbound/test_assisted_idempotency.py` | 4 | ETD 时区归一化；业务载荷变化识别；相同 request_id 不同载荷冲突；创建响应的重放标志。 |
| `allapp/outbound/test_assisted_schema_auth.py` | 8 | 辅助出库货主开关和订单唯一约束；自定义权限；用户 profile 返回绑定、权限和能力；混合绑定 fail closed；JSON/CSV 权限风险审计；旧授权模式只能为 shadow 或 enforce。 |
| `allapp/outbound/test_production_remediation.py` | 21 | 生产问题修复回归：销售员选仓、ETD 本地时间、任务号排序规则、shadow 范围、销售员/货主经理订单和客户范围、授权仓库目录、操作员任务池、短分配禁止释放、取消释放库存及短缺时整体回滚、开始拣货后禁止取消、真实复核/打包/发运闭环、Admin 写入最终范围、禁止直接关闭/重开、仓库确认和货主审批状态约束。 |

### 4.7 任务、扫描与过账

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/tasking/tests.py` | 14 | 任务必须明确仓库；扫描日志派生仓库并拒绝库位错仓；盘点分仓范围；过账幂等；任务发布拒绝其他任务的行且不留下指派；任务列表/详情货主和仓库范围；创建绑定；生命周期和分配动作委托服务层；扫描及日志 API；任务行绑定/解绑；并发过账只执行一次。 |
| `allapp/tasking/test_assisted_outbound_integrity.py` | 12 | 拣货扫描选择未完成库位行并保持追加写；手工数量纠正；任务关联资源按任务仓库 fail closed；辅助出库专用可见性；shadow 不开放原始任务 API；扫描、绑定、创建权限；负责人/货主经理不能直接使用原始任务 API；操作员只能处理本人或任务池任务。 |
| `allapp/tasking/test_console_scope.py` | 4 | Console 的 shadow 仅作遥测；enforce 模式严格按仓；辅助任务不启用旧兼容动作；无绑定用户不能通过行编辑绕过仓库检查。 |

### 4.8 计费

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/billing/tests.py` | 89 | 订单处理费和仓储费计提；按服务日期选规则；订单行/任务行解析；基础金额；包干、阶梯、百分比、日/月封顶；账期锁定、解锁、重开、重新开票；开票金额/税额精度及建行失败整体回滚；对账门禁；规则、阶梯、账期、计提、事件、指标、任务运行、账单和账单行模型约束；CSV 导入范围；菜单；规则、账期、账单、导出隔离和看板 API；旧范围写入 fail closed；历史快照生成指标；调度幂等；调度、指标生成、锁账、开票并发；Console 总览和明细。 |
| `allapp/billing/test_management_commands.py` | 5 | 仓储费计提命令的货主/仓库范围和汇总；指标生成日期范围；失败任务重试的 dry-run、成功重试和长消息标记。该文件标记为 `integration`。 |

### 4.9 报表与运营分析

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/reports/tests.py` | 28 | 报表快照仓库范围；发货单 HTML；老板看板首页、预警、库存、临期、滞销、冷热库位和货主筛选；禁止用无仓汇总兜底；多仓负责人范围；PDA 月度/区间吞吐、货主拆分、来源明细、无订单入库、已过账口径、日期及跨仓跨货主权限。 |
| `allapp/reports/test_etl_operations.py` | 9 | 全量 ETL 幂等和业务里程碑；计费事实金额更新与普通作废同步；保留软删除商品的已过账流水；排除内部上架移动和取消发运；同 SKU 保持源订单行；日期窗口重建；全量失败回滚；增量更新；失败不推进 watermark。该文件标记为 `integration`。 |
| `allapp/reports/test_management_commands.py` | 4 | 日期维度生成、反向日期拒绝、聚合报表刷新幂等和非法日期。该文件标记为 `integration`。 |
| `allapp/reports/test_operations_v2.py` | 10 | 五类角色范围及 actor-only；草稿/取消/未发运不计实际吞吐；出库库存口径排除内部上架；操作员不能查询计划口径；方向约束；稳定分页；跨范围拒绝；导出权限及最大行数契约；无尾斜杠接口不重定向丢失 POST。该文件标记为 `api` 和 `integration`。 |

### 4.10 Console

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/console/tests.py` | 9 | 看板要求登录、JSON 结构、旧用户字段不能授权；操作台扫描、行编辑和过账 URL 对跨仓对象返回 404 且不调用服务；商城商品列表筛选；批量创建商城配置且不改库存；无价格商品不上架；价格/角标只修改商城配置；起购递增规则必须显式启用。 |

### 4.11 POS

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/pos/tests.py` | 80 | 日期与 Admin 注册；POS 销售只读后台；散客；开班/重复开班；权限和仓库；收货抬头、客户和班次的仓库隔离；非法退货/还款筛选参数；条码及包装查货；结账生成出库单、拣货、库存流水和计费；失败回滚；原库存层序列号；多货主拆单；现金、非现金、拆分支付、赊销、还款；金额平衡和舍入；幂等键和重复小票；作废、部分/全部退货及权限；库存原层恢复；最低价和最大折扣；库存不足、重复行、微量库存尾差；指定库区和 FEFO；销售列表、统计、班次关闭/重开；打印、Excel 和打印日志。 |
| `allapp/pos/test_checkout_concurrency.py` | 5 | 同 SKU 并发结账不超卖；反向商品顺序不死锁；POS 与普通拣货串行；作废与结账串行；退货与结账串行。该文件依赖真实事务和 MySQL 行锁。 |

### 4.12 销售与商城小程序

| 文件 | 用例数 | 当前覆盖 |
| --- | ---: | --- |
| `allapp/salesapp/tests.py` | 77 | 销售后台 ViewSet 货主范围、审核状态和批量动作；商城目录初始化命令；移动销售目录的价格、包装单位和库存；商城评价及图片；公开商品、标签、分类、品牌和搜索的数据脱敏与上架规则；起购/递增规则；不完整追踪字段库存；微信登录绑定；预览服务端重算；优惠券、积分、地址；服务端购物车及多货主分包；统一下单拆分多个 WMS 出库单；库存分配；微信预支付、查询、回调签名后的业务状态、金额不符、延迟支付；退款、售后、退款回调；未支付订单过期；支付和退款并发幂等。 |
| `allapp/salesapp/test_admin_bulk_listing.py` | 9 | Admin 批量上架入口、中文字段、审计用户、起购规则、商品自动完成；按货主创建并上架有效商品；保留已有单品价格；整货主下架但保留配置。 |
| `allapp/salesapp/test_salemini_unit.py` | 22 | 买家状态文案和查询条件；起购递增规则；单位文案；自提/配送字段；金额分厘舍入；应付金额；准确性问题采样；评价分数、文字、真实图片；微信支付响应身份/币种/金额；结构化错误；回调时效、平台密钥序列号、RSA 验签、AES-GCM 解密及生产安全配置。 |
| `allapp/salesapp/test_mobile_api_unit.py` | 4 | 多行合计库存校验；嵌套校验错误提取；允许欠货；非法策略包装单位不可下单。 |
| `allapp/salesapp/test_services_pricing_unit.py` | 3 | 渠道价优先默认价；无客户渠道时忽略渠道价；忽略其他客户的促销特价。 |
| `allapp/salesapp/test_management_commands.py` | 2 | `reconcile_sale_mini_payments` 编排到期订单、支付和退款；单条失败后继续处理其他记录并最终报告失败。 |

### 4.13 跨模块业务闭环

`allapp/test_business_flows.py` 当前有 12 条跨应用闭环：

1. 无订单收货后库存可见。
2. 出库审批、扫描、复核、过账。
3. 反审批或取消后释放预占。
4. 快速调整后库存与企业报表一致。
5. 快照、计费指标、计提、锁账、开票。
6. 货主端查看库存、账单并导出。
7. PDA 拣货扫描及状态推进。
8. Console 计费总览和账单详情。
9. 正式入库单到出库的完整链路。
10. 正式入库、上架再到出库的完整链路。
11. 多行、多库位出库链路。
12. 运营库存到计费开票链路。

这些测试验证最终业务结果，不只是接口返回 200，是上线回归中优先级最高的自动化套件。

## 5. 商城前端质量门禁

商城命令定义在 `sales-miniapp/package.json`，实际编排位于 `sales-miniapp/scripts/run-quality-gate.mjs`。

| 命令 | 内容 |
| --- | --- |
| `npm run test:structure` | 校验商城路由、页面、tab、买家端结构和旧销售工作台文件隔离。 |
| `npm run test:quality` | 结构检查、Django system check、29 个纯单元测试、微信小程序构建、H5 构建；默认跳过数据库测试。 |
| `npm run test:quality -- --skip-build` | 与上一项相同，但跳过两个前端构建，适合快速反馈。 |
| `npm run test:release` | 完整发布门禁：加入商城 API 数据库测试、支付并发、Console 商品管理、Admin 按货主批量上架和数据准确性校验。 |
| `npm run test:quality -- --skip-build --db --fast-db` | 使用已有测试库并跳过迁移，适合本地快速数据库回归；不能替代发布前正常迁移模式。 |

构建输出：

- 微信小程序：`sales-miniapp/dist/build/mp-weixin`
- H5：`sales-miniapp/dist/build/h5`

## 6. CI 当前实际执行范围

CI 配置位于 `.github/workflows/ci.yml`。

### 6.1 静态检查

`lint` job 执行：

```bash
black --check --diff .
isort --check-only --diff .
ruff check .
flake8 . --max-line-length=100 --extend-ignore=E203,W503
bandit -r allapp/ -ll
safety check --json
```

其中 Bandit 和 Safety 当前使用容错执行，不会单独阻断流水线；Black、isort、Ruff 和 Flake8 会阻断。

### 6.2 后端测试矩阵

CI 启动 MySQL 8 和 Redis 7，先执行迁移和 `--collect-only`，再用 `pytest -n auto` 分五组运行：

- `platform`：accounts、api、baseinfo、core、driverapp、labeling、locations、products、strategies。
- `warehouse`：inbound、inventory、outbound、tasking。
- `settlement`：reports、billing、console、整个 `salesapp` 目录。
- `pos-sales`：整个 `pos` 目录（包括 POS 并发测试）。
- `business-flows`：`allapp/test_business_flows.py`。

五个主矩阵分组分别覆盖 166、135、271、85 和 12 个测试，共覆盖当前全部 669 个 Python 测试。`sale-mini-payment-gate` 另行运行 `npm run test:release`，重复验证商城关键 API、支付并发、Console、后台批量上架、纯单元测试和两个前端构建。

### 6.3 CI 收集完整性

本次审计已把 `pos-sales` 和 `settlement` 从显式测试文件改为应用目录，修复此前遗漏 POS 并发测试、商城 Admin 测试及新增测试文件的问题。因此：

- 主矩阵会执行当前全部 669 个 Python 测试。
- 后续在 `allapp/pos` 或 `allapp/salesapp` 新增符合 pytest 命名规则的文件会自动纳入。
- 仍应保留全仓 `--collect-only` 检查，防止其他应用新增文件后没有同步加入对应矩阵目录。

### 6.4 覆盖率与后续门禁

- 五个主矩阵分片分别生成 coverage 数据。
- `coverage-aggregate` 合并分片并执行 `coverage report --fail-under=70`。
- 安全 job 执行 Trivy 文件系统扫描和 OWASP Dependency Check。
- 主分支 push 在覆盖率通过后构建 Docker 镜像，再部署 staging。

注意：将来若新增应用或把测试放到五个矩阵目录之外，它们不会自动进入聚合覆盖率，即使在其他 job 中单独运行。

## 7. 本地测试环境准备

### 7.1 Python 依赖

```bash
cd /wms
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements/dev.txt
```

如果 `mysqlclient` 编译失败，Ubuntu/Debian 通常需要先安装 MySQL 开发头文件和编译工具。

### 7.2 环境变量

从 `.env.example` 创建本地 `.env`，至少确认：

```dotenv
APP_ENV=test
SECRET_KEY=test-secret-key
DEBUG=False
DB_ENGINE=django.db.backends.mysql
DB_NAME=wms_db
DB_TEST_NAME=wms_db_test
DB_USER=wmsuser
DB_PASSWORD=<test-password>
DB_HOST=127.0.0.1
DB_PORT=3306
```

安全要求：

- `DB_TEST_NAME` 必须是可清理的独立测试数据库，绝不能指向生产库。
- 测试用户需有创建、删除和修改测试库结构的权限。
- 并发测试必须在 MySQL 上执行，SQLite 不能代表 MySQL 行锁行为。
- 与 CI 对齐时同时启动 Redis 7。

### 7.3 基础检查

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest --collect-only -q allapp
```

预期最后一条收集 669 个测试。用例数发生变化时，应同步更新本手册的基线统计和 CI 覆盖说明。

### 7.4 本地隔离 MySQL 测试库

仓库提供 `compose.test.yml` 和 `docker/run_mysql_tests.sh`。测试库固定使用
`127.0.0.1:33306/wms_db_test`，与开发及生产数据库隔离。

首次使用：

```bash
cp .env.test.example .env.test.local
# 只修改本地测试密码；该文件已被 Git 忽略。
./docker/run_mysql_tests.sh -q allapp/outbound/tests.py
```

脚本启动 MySQL 8.4、等待健康检查，然后使用 `pytest --reuse-db` 执行测试。
它会强制检查 `APP_ENV=test`、测试库名称、测试用户、回环地址和 33306
端口，任一条件不符都会拒绝执行。运行 pytest 或 `manage.py test` 时，
Django 会在文件存在的情况下优先加载 `.env.test.local`；其他命令不会加载它。

如果首次迁移被中断，或复用测试库时出现表已存在等半建库错误，只重建
`wms-test` 的专用数据卷后再测试：

```bash
./docker/run_mysql_tests.sh --fresh -q allapp/outbound/tests.py
```

`--fresh` 不会访问项目的开发数据库或生产数据库。

常用维护命令：

```bash
docker compose --env-file .env.test.local -f compose.test.yml ps
docker compose --env-file .env.test.local -f compose.test.yml stop mysql-test
docker compose --env-file .env.test.local -f compose.test.yml down
```

不要对该测试实例导入生产数据，也不要把 `.env.test.local` 提交到版本库。

## 8. 常用执行命令

### 8.1 最快反馈

无需数据库的商城纯单元测试：

```bash
APP_ENV=test SECRET_KEY=test-secret-key python -m pytest -q \
  allapp/salesapp/test_salemini_unit.py \
  allapp/salesapp/test_mobile_api_unit.py \
  allapp/salesapp/test_services_pricing_unit.py
```

当前基线应为 29 passed。

### 8.2 全量 669 个测试

```bash
python -m pytest -q --tb=short allapp
```

并行执行：

```bash
python -m pytest -n auto -q --tb=short --maxfail=1 allapp
```

首次排查失败时建议先不用 `-n auto`，以便获得稳定、完整的错误堆栈。

### 8.3 按领域执行

```bash
python -m pytest -q allapp/accounts allapp/core allapp/products
python -m pytest -q allapp/inbound allapp/inventory allapp/outbound allapp/tasking
python -m pytest -q allapp/reports allapp/billing allapp/console
python -m pytest -q allapp/pos allapp/salesapp
python -m pytest -q allapp/test_business_flows.py
```

### 8.4 并发专项

```bash
python -m pytest -q \
  allapp/inbound/tests.py::InboundConcurrencyTests \
  allapp/inventory/tests.py::InventorySnapshotConcurrencyTests \
  allapp/inventory/test_posting_concurrency.py \
  allapp/outbound/tests.py::OutboundConcurrencyTests \
  allapp/tasking/tests.py::TaskPostingConcurrencyTests \
  allapp/billing/tests.py::BillingSchedulerConcurrencyTests \
  allapp/billing/tests.py::BillingSettlementConcurrencyTests \
  allapp/pos/test_checkout_concurrency.py \
  allapp/salesapp/tests.py::SaleMiniPaymentConcurrencyTests
```

不要对这一组使用 SQLite。若并发失败，应保留 MySQL 死锁、锁等待和事务日志，不要只通过增加重试掩盖问题。

### 8.5 单文件、单类和单用例

```bash
python -m pytest -q allapp/outbound/tests.py
python -m pytest -q allapp/outbound/tests.py::OutboundWarehouseScopeTests
python -m pytest -q \
  allapp/outbound/tests.py::OutboundWarehouseScopeTests::test_owner_approve_is_idempotent_for_allocation
```

### 8.6 覆盖率

```bash
python -m pytest -n auto -q allapp \
  --cov=allapp \
  --cov-report=term-missing \
  --cov-report=html \
  --cov-fail-under=70
```

HTML 报告位于 `htmlcov/index.html`。本地全量命令和当前 CI 主矩阵都会把 669 个测试计入覆盖率。

### 8.7 代码质量和安全检查

```bash
black --check --diff .
isort --check-only --diff .
ruff check .
flake8 . --max-line-length=100 --extend-ignore=E203,W503
bandit -r allapp/ -ll
pre-commit run --all-files
```

## 9. 测试数据与隔离原则

自动化测试主要在各 `TestCase.setUp()`、测试辅助函数和临时文件中创建数据，不依赖生产数据。新增用例应遵循：

- 至少准备两个货主和两个仓库，用正例和跨范围负例共同验证隔离。
- 商品覆盖普通、包装、批次、效期、序列号、FEFO、最低价和折扣限制。
- 库存覆盖单库位、多库位、库存不足、已预占和软删除来源层。
- 所有重复提交接口都要验证“相同请求重放”和“相同幂等键但载荷变化冲突”。
- 金额测试使用 Decimal 和明确的分/厘舍入断言。
- 导入测试使用临时 Excel/CSV，验证预览不写库、确认原子性和错误行完整反馈。
- 并发测试使用独立数据库连接和 `TransactionTestCase`。
- 失败用例必须断言没有库存、账单、任务、支付等残留副作用。

pytest 默认管理测试数据库。普通人工测试环境在执行页面/UAT 前另行运行：

```bash
python manage.py migrate --noinput
```

不要把自动化测试数据库和人工验收数据库混用。

## 10. 人工测试与上线验收

当前自动化覆盖较完整，但浏览器端、真实 PDA、扫码枪、打印机、微信支付沙箱和部署恢复仍需人工或外部环境验证。

### 10.1 核心人工闭环

1. 使用超级管理员、仓库负责人、货主经理、货主销售员、仓库操作员、POS 收银员和无权限用户分别登录。
2. 创建商品、包装、条码、仓库、分仓、库位和货主绑定。
3. 执行正式入库及无订单入库，核对收货、上架、库存明细、汇总和流水。
4. 创建出库单，完成货主审批、仓库确认、分配、拣货、复核、打包、发运和过账。
5. 执行取消、驳回、重复提交和断网重试，核对预占释放和幂等。
6. 执行盘点、快速调整和追踪字段修复，核对报表。
7. 生成库存快照、计费指标、计提、账期、账单和导出，进行金额勾稽。
8. POS 完成开班、扫码、现金/拆分/赊销结账、打印、作废、退货、还款和闭班。
9. 商城完成公开浏览、微信登录、购物车、多货主统一下单、支付、退款、售后和评价。
10. 用越权账号直接访问其他货主/仓库对象 URL 和 API，确认读写均被拒绝。

详细逐步检查表见 `docs/business-flow-smoke-checklist.md` 和 `docs/release-test-plan.md`。

### 10.2 数据准确性门禁

```bash
python manage.py reconcile_data_accuracy --fail-on-issues
python manage.py validate_sale_mini_data_accuracy --fail-on-issues --limit 20
```

库存、流水、账单、支付或退款出现不可解释差异时，必须阻断发布。

### 10.3 发布通过标准

- 669 个 Python 测试全部通过（其中包括 POS 并发专项）。
- `npm run test:release` 通过。
- 覆盖率不低于 70%。
- `manage.py check`、迁移漂移检查、数据准确性检查通过。
- 没有新增未处理的高危安全问题。
- 核心人工闭环有执行人、环境、版本、结果和证据。
- 数据库迁移、备份、恢复和回滚至少在预生产环境演练一次。

## 11. 结果判读与常见问题

### 11.1 测试数据库无法创建

检查 `DB_TEST_NAME` 是否为独立库、MySQL 用户是否有建库/删库权限，以及是否有上次异常退出遗留的测试库。使用 `--reuse-db` 可加快本地循环，但发布门禁仍需使用正常迁移模式验证。

### 11.2 并行通过、串行失败或反之

先去掉 `-n auto` 重跑失败用例。若只在并发专项失败，重点检查事务边界、`select_for_update`、唯一约束、锁顺序和幂等键，而不是降低断言。

### 11.3 Django 6.0 弃用警告

当前模型仍会出现 `CheckConstraint.check` 将改为 `condition` 的弃用警告。这些警告目前不代表测试失败，但应作为升级 Django 6.0 前的技术债跟踪，不能永久用全局忽略掩盖新增警告。

### 11.4 用例被收集但 CI 没执行

先比较：

```bash
python -m pytest --collect-only -q allapp
```

再核对 `.github/workflows/ci.yml` 的显式文件/目录列表。新增 `test_*.py` 时，如果 CI 指向整个目录会自动纳入；如果 CI 只指向某个 `tests.py`，必须同步修改工作流。

## 12. 新增或修改功能时如何补测试

1. 在对应应用目录的 `tests.py` 或 `test_<feature>.py` 中添加测试。
2. 至少覆盖成功路径、权限/范围、非法输入、重复提交和失败无副作用。
3. 涉及库存、金额或状态机时，补充跨模块结果断言。
4. 涉及唯一约束、库存扣减、支付、退款或过账时，评估是否必须增加 `TransactionTestCase` 并发用例。
5. 涉及管理命令时覆盖 dry-run、apply、幂等、错误参数和整体回滚。
6. 涉及文件导入时覆盖模板、类型、大小、公式、重复、跨范围、预览和确认。
7. 涉及公开 API 时验证敏感内部字段不泄漏。
8. 新增测试文件后执行全量收集，并检查 CI 是否按目录包含它。
9. 更新本手册中的文件、用例数、覆盖说明和 CI 缺口。

建议提交前最少执行：

```bash
python manage.py check
python -m pytest -q <受影响模块>
python -m pytest -q allapp/test_business_flows.py
pre-commit run --all-files
```

上线候选版本必须执行本手册第 10.3 节的全部发布门禁。
