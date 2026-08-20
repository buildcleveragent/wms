# WMS 发布级全面测试报告

> 执行日期：2026-08-20（Asia/Shanghai）  
> 被测提交：`2d48512cf086fc6f84e6eb9a2a8362b66e546923`（`main`）  
> 最终结论：**FAIL**

## 1. 执行摘要

本轮按[全面测试计划](/wms/docs/comprehensive-test-plan-2026-08-20.md)在本机隔离环境执行。
没有修改公共 API、数据库 schema 或业务代码；新增内容仅为计划、报告和测试制品。

不能发布的主要原因如下：

- 存在匿名商品详情读取和 Django Admin 多租户范围缺失等 P0/P1 权限问题。
- 配送单对不存在的价格字段静默回退为 0，存在财务单据错误风险。
- 后端 1,026 条测试中 1,021 通过、3 失败、2 阻塞，强制测试门禁未通过。
- 已执行分片的聚合覆盖率为 65%，低于 70% 门槛；该数字不包含中止后未保留的早期平台分片覆盖数据，因此是保守值，但不能替代完整覆盖门禁。
- lint ratchet、Python/Node 依赖安全审计不通过；Trivy 未完成。
- `wmsbossbilling` 缺少独立 `package.json` 和锁文件，无法可重复构建。
- 50/100 并发性能、真实 PDA/扫码/打印设备、微信支付测试环境和 RPO/RTO 演练未具备执行条件。

因此不满足 `PASS` 或 `CONDITIONAL PASS` 的前提，发布判定为 `FAIL`。

## 2. 环境与隔离性

| 项目 | 结果 |
|---|---|
| Python | 3.12.3（临时测试 venv）；仓库 `.venv` 为 3.11.9 |
| Django | 5.2.5 |
| Node / npm | 22.22.0 / 10.9.4 |
| MySQL | 8.0.46，Docker 本机端口 `127.0.0.1:33306` |
| Redis | 7.4.10，Docker 本机端口 `127.0.0.1:36379` |
| Docker / Compose | 29.2.1 / 5.1.0 |
| Playwright | 1.61.1 |
| 测试 schema | `wms_codex_full_20260820_c`，与开发/生产库隔离 |

全新 schema 从零执行全部 Django migrations 成功。第一次长迁移尝试因执行时限被终止；MySQL 的非事务 DDL 使该中间 schema 不能安全续跑，已仅删除该临时 schema，随后在新的唯一 schema 上完成干净迁移。数据库测试始终串行启动；由于串行套件已有失败，本轮未启动 xdist。

测试账号最初只对仓库默认测试库有授权。重跑并发用例前，对上述唯一隔离 schema 补充了精确授权；没有扩大到其他 schema。

## 3. 门禁结果

### 3.1 配置、迁移与代码质量

| 门禁 | 结果 | 说明 |
|---|---:|---|
| `manage.py check` | PASS | 未发现 system check 问题 |
| `makemigrations --check --dry-run` | PASS | 无迁移漂移 |
| 全新 MySQL schema 迁移 | PASS | MySQL 8 从零迁移成功 |
| `check --deploy` | FAIL（符合 fail-closed） | 缺少支付公钥/App/Mch 配置时产生 `salesapp.E002/E003`；另有 HSTS preload 告警 |
| `pip check` | PASS | 已安装 Python 依赖关系一致 |
| `git diff --check` | PASS | 无空白错误 |
| lint ratchet | FAIL | 20 个文件涉及 Black、isort、Flake8；主要集中在 products、locations、inbound、core、salesapp 等模块 |
| Bandit `-r allapp -ll` | FAIL | 6 high、11 medium、271 low；high 主要为 MD5/SHA1 标识用途，仍需逐条可利用性处置 |
| Python 依赖审计 | FAIL | 5 个包共 62 个已知漏洞；涉及 Django、cryptography、Pillow、sqlparse、WeasyPrint |
| npm audit | FAIL | `wmspda` 39、`wmsownersale` 76、`sales-miniapp` 74 个漏洞 |
| Trivy | BLOCKED | 漏洞库下载仅完成约 50/108 MB，速率过低，按时限终止；无有效扫描结论 |

CI 中 Bandit 与 Safety 使用 `|| true`，不会阻断 lint job（`.github/workflows/ci.yml:83-86`）；`release-gate` 的依赖列表也不包含独立安全 job（`:428-439`）。这意味着当前 CI 不能证明“无未处置高危漏洞”。

### 3.2 后端、数据库与闭环

完整收集数为 1,026。为规避单命令时限并保留 MySQL 串行语义，按模块串行分片执行，汇总如下：

| 分组 | 收集 | 通过 | 失败 | 阻塞 | 结果/耗时 |
|---|---:|---:|---:|---:|---|
| 平台：accounts/api/baseinfo/core/driver/labeling/locations/products/strategies | 278 | 274 | 2 | 2 | FAIL；两条迁移单测超过 5 分钟且无 DB 活动后按界限终止 |
| 入库 | 50 | 50 | 0 | 0 | PASS；277.88 s |
| 库存 | 52 | 52 | 0 | 0 | PASS；158.71 s |
| 出库 | 181 | 180 | 1 | 0 | FAIL；926.11 s |
| 任务/过账 | 53 | 53 | 0 | 0 | PASS；403.34 s |
| POS | 94 | 94 | 0 | 0 | PASS；高风险并发尾集 10/10，282.29 s |
| 业务闭环、计费、Console | 129 | 129 | 0 | 0 | PASS；383.90 s |
| 报表、商城后端 | 189 | 189 | 0 | 0 | PASS；647.47 s |
| **合计** | **1,026** | **1,021** | **3** | **2** | **FAIL** |

三个失败断言：

1. `core/test_business_data_purge.py:340`：dry-run 预期不改变货主 SKU 序号，实际从 50 变为 51。
2. `core/test_preserved_data_transfer.py:38`：保留数据清单预期排除 `products_product`，实际仍包含。
3. `outbound/test_assisted_flow.py:316`：包装目录请求预期 200，实际 403“货主未关联当前授权仓库”。

两条阻塞用例均位于 `products/test_identifier_migrations.py`。终止后 `showmigrations products` 显示 `0001` 至 `0014` 均已应用，schema 未损坏；但用例本身不能记为通过。

库存、POS、任务过账和支付/退款相关并发测试中，已执行部分未发现超卖、丢失更新或死锁遗留。POS 的反向商品顺序结账、同 SKU 防超卖、退货与结账并发均通过。业务闭环、计费、报表 ETL 与商城后端共 318 条全部通过。

备份/恢复方面，`PreservedDataTransferMySQLTests` 的全新库 round-trip 和失败回滚 2/2 通过；这证明功能级导出/恢复路径可用，但不等于完成基础设施 RPO/RTO 演练。

### 3.3 客户端与浏览器

| 客户端 | 结果 | 证据 |
|---|---|---|
| `wmspda` | 单测 37/37；H5 构建通过；Playwright 8/8；三视口 smoke 通过 | 桌面/手机/PDA 截图及 GS1、扫码截图 |
| `wmsownersale` | 页面契约 20/20；单测 78/79，1 条 5 s 超时；失败文件单独重跑 7/7；H5/微信构建通过 | 判定为负载敏感的 P2 flaky，但 release gate 本次失败 |
| `sales-miniapp` | pure unit 25/25；H5/微信构建通过；quality gate 通过 | quality 脚本中的 DB/API catalog/admin/data accuracy 明确为 SKIP，不计作通过 |
| `wmsbossbilling` | BLOCKED | 根目录只有源码和 Vite 配置，无独立 `package.json`/锁文件，无法可重复安装、测试、构建 |
| Web Admin/Console | 后端测试已执行；完整认证浏览器矩阵未执行 | 缺少本轮可用的认证浏览器夹具/预发布目标 |

仓库没有 Browser 插件，按前端测试规范使用 Playwright。`wmspda` 在 `1440x900`、`390x844`、`720x1280` 下均返回 200、页面非空、无 Vite error overlay、无 console/page error；截图未见明显遮挡。

浏览器验证同时发现 PDA 登录页在源码中预填用户名和明文密码（报告不记录密码值），必须在发布前移除并轮换可能受影响的凭据。

### 3.4 性能、稳定性、设备与支付

| 项目 | 结果 | 阻塞原因 |
|---|---|---|
| 50 用户 30 分钟、100 用户 5 分钟 | BLOCKED | 无预发布 API 目标和脱敏负载 actor/数据集，不能用空数据伪造结果 |
| CPU/内存/慢查询/吞吐趋势 | BLOCKED | 依赖上述有效负载 |
| 数据库断连、服务重启、部分写入失败全套注入 | PARTIAL | 单元/事务回滚与重复请求已有覆盖；系统级中断演练未执行 |
| 主力/备用 PDA、扫码、RFID/NFC、蓝牙打印 | BLOCKED | 未提供实体设备和预发布环境 |
| 微信支付测试商户、小额支付、退款、对账 | BLOCKED | 未提供测试商户/受控凭据；后端验签、金额不一致、重放单测已执行 |
| RPO <= 24h、RTO <= 60min | BLOCKED | 功能级 round-trip 通过，但没有基础设施级定时恢复演练 |

## 4. 缺陷与风险清单

### P0

| ID | 问题与证据 | 影响 | 建议回归 |
|---|---|---|---|
| P0-01 | 商品详情函数直接 `Product.objects.get(id=...)`，无认证/货主/仓库范围（`allapp/products/views.py:402-429`）；现有测试还以匿名 RequestFactory 期望 200 | 匿名枚举跨租户商品包装/单位信息 | 匿名、九角色、跨货主直接 ID、404 不泄露存在性 |
| P0-02 | `ProductAdmin.has_view_permission` 和 autocomplete 权限无条件返回 True，且未见租户 scoped queryset（`allapp/products/admin.py:537-542`） | Admin/自动完成跨租户读取；其他基础资料 Admin 也需矩阵复核 | Admin 列表、详情、autocomplete、导入导出、历史记录 |
| P0-03 | 配送单从 `OutboundOrderLine.price` 取价；模型无该字段，异常被吞并并回退 0（`allapp/reports/dispatch_note_builder.py:152-160`） | 配送单金额静默为 0，属于财务单据错误 | 基础/辅助单位价格、折扣、税费、合计与源订单对账 |

### P1

| ID | 问题与证据 | 影响/建议 |
|---|---|---|
| P1-01 | 三个客户端将 API 固定到开发/LAN HTTP 地址（`wmspda/utils/request.js:5-13`、`wmsownersale/utils/request.js:7-16`、`sales-miniapp/utils/request.js:1-4`） | 生产客户端可能无法连接且无 TLS；改为构建期受控配置并 fail-closed |
| P1-02 | PDA 登录页预填真实格式用户名和明文密码（`wmspda/pages/login.vue:27-28`） | 凭据泄露/误登录；移除、轮换、做 secret scanning 回归 |
| P1-03 | `release_to_pick` 管理命令调用不存在的 `ob_services.release_to_pick`（`allapp/outbound/management/commands/release_to_pick.py:38`） | 命令执行即失败；补契约测试和实际命令 smoke |
| P1-04 | `post_task` 在外层 `transaction.atomic` 内写 FAILED journal/task 后重新抛异常（`allapp/tasking/services_posting.py:53,131-145`） | 失败审计写入随事务回滚；使用独立事务/调用边界验证 |
| P1-05 | dry-run 改变 SKU 分配状态；保留数据清单契约失败 | 清理预演有副作用，恢复范围不可信；修复后做干净库与失败回滚回归 |
| P1-06 | 辅助出库包装目录返回意外 403 | 核心出库流程受阻；覆盖货主-仓库授权组合与包装换算 |
| P1-07 | 覆盖率 65%、lint ratchet 失败 | 两项强制发布门禁失败 |
| P1-08 | Python/NPM 漏洞审计不通过，Bandit high 未完成逐条处置；CI 安全任务非阻断 | 无法满足“无未处置高危”标准；升级依赖、验证兼容、把安全 job 纳入 release gate |
| P1-09 | `wmsbossbilling` 无独立 manifest/lock | 无法可重复构建和 CI 验收 |

### P2 / 待验证风险

- `wmsownersale/tests/inventory-component.test.js` 在全套运行中超过 5 秒，单文件重跑通过；判为负载敏感 flaky。
- 146 个 Django 6 `CheckConstraint.check` 弃用告警；商城 JWT 测试密钥长度低于推荐值。
- `WmsTaskLine.clean`、`InventoryDetail.clean`、`InventoryTransaction.clean` 未见一致的 product-owner 关联校验。这是源码确认的结构性风险，尚未完成从可控入口到持久化的利用链验证，不能写成已复现漏洞。
- Bandit 的高等级结果主要是用于确定性标识/幂等键的 MD5/SHA1；不能直接等同密码学漏洞，但在逐条标注用途前仍是未处置扫描发现。

## 5. 证据索引

测试制品目录：`/wms/var/test_artifacts/comprehensive-2026-08-20/`

- `coverage-aggregate.xml`：已执行分片聚合覆盖率，65%。
- `execution-summary.json`：环境、分组计数、门禁和阻塞项的机器可读摘要。
- `wmspda-desktop-1440x900.png`、`wmspda-mobile-390x844.png`、`wmspda-pda-720x1280.png`：三视口 smoke。
- `wmspda-h5-smoke.png`、`wmspda-selectowner-*.png`、`wmspda-gs1-*.png`、`wmspda-keyboard-scanner-latest.png`：仓库 Playwright 场景证据。

原始命令输出中未写入密码、JWT 或支付凭据。Bandit/pip-audit 原始 JSON 未能在临时工具环境清理后重建，因此报告仅保留已执行时的计数摘要，未伪造原始证据。Trivy 未完成，目录中不提供空结果文件。

## 6. 发布建议

当前应停止发布。优先顺序为：先修复 P0 权限与财务单据问题，再处理客户端凭据/API 配置、清理/恢复断言、出库 403、过账失败审计和可重复构建；随后升级易受攻击依赖并让安全扫描成为强制门禁。修复必须新增持久化回归测试，并在全新 MySQL schema 上重新跑完整 1,026 条及 >=70% 覆盖率。

上述自动化全部通过后，仍需在预发布完成 50/100 并发、真实双 PDA/扫码/打印、微信测试支付和 RPO/RTO 演练，才可能重新评估为 `PASS` 或 `CONDITIONAL PASS`。

## 7. 可发布状态整改复验（2026-08-20）

> 本节是在第 1 至 6 节原始失败基线之上的增量复验。原始证据和缺陷定性保留，
> 不用整改后的结果覆盖首次发现记录。本次工作树基于 commit `2d48512cf086`，包含尚未提交的
> 整改修改，因此正式候选版本仍须在最终 commit SHA 上重新生成全部制品。

### 7.1 环境与门禁变化

| 项目 | 整改后状态 | 说明 |
|---|---:|---|
| Python 运行契约 | PASS | 项目固定 `>=3.12,<3.13`；CI/镜像固定 3.12.13，当前本机 `.venv` 为 3.12.3 |
| MySQL / Redis | PASS | 隔离容器 MySQL 8.0.46、Redis 7.4.7；仅使用 `wms_db_test` |
| 全新 schema 迁移 | PASS | 从空库完成完整迁移，耗时约 2,300 s |
| 商品标识迁移专项 | PASS | 独立一次性 schema，2/2 通过，269.56 s；执行后安全删除临时 schema |
| Django check / 迁移漂移 | PASS | `manage.py check` 与 `makemigrations --check --dry-run` 均通过 |
| lint ratchet / diff check | PASS | Black、isort、Ruff、Flake8 统一 100 字符配置；无新增 lint 或空白错误 |
| Bandit 生产代码 | PASS | 排除测试和迁移后 0 high、0 medium；剩余 25 low 已按用途审阅 |
| Python 依赖审计 | PASS（限时例外） | 仅 `PYSEC-2026-3412`；无修复版，按 30 天例外和离线资源控制精确忽略，门禁为 0 未处置发现 |
| Node 生产依赖审计 | PASS | PDA、货主端、商城、老板端均以 high 阈值通过 |
| 生产代码覆盖率 | PASS | MySQL 4-worker 全量回归 71.69%，超过 70% 硬门槛，达到整数化 72% 目标 |
| Trivy 文件系统/镜像 | BLOCKED | 本机未取得完整漏洞库和有效结论；CI 下载失败亦配置为失败 |
| 生产配置 fail-closed | PASS | 使用临时测试 RSA 密钥验证 `check --deploy`；仅保留 HSTS preload 告警 |

本地 Node 为 22.22.0，仅用于本轮前端复验；发布 CI 固定 Node 20，最终候选必须以 CI
结果为准。测试环境 `SECRET_KEY` 已改为不少于 32 字节，并通过将
`InsecureKeyLengthWarning` 提升为错误的 5 条 JWT 定向回归。

### 7.2 后端与数据库最终串行结果

整改后常规后端套件在 MySQL 8 上以单进程、复用已完成干净迁移的隔离测试库执行；迁移专项
独立运行，避免在常规套件中反复回滚完整迁移图。

| 分组 | 测试 | subtests | 失败 | 跳过 | 结果/耗时 |
|---|---:|---:|---:|---:|---|
| 常规 `allapp`（排除迁移专项） | 1,044 | 943 | 0 | 0 | PASS；9,510.77 s（2:38:30） |
| 商品标识迁移专项 | 2 | 0 | 0 | 0 | PASS；269.56 s |
| **合计** | **1,046** | **943** | **0** | **0** | **PASS** |

串行成功后，以 4 个 xdist worker 在 4 个独立 MySQL schema 上再次执行 1,044 条常规测试，
结果为 `1044 passed, 943 subtests passed`，耗时 14,947.17 s（4:09:07）；覆盖率 71.69%。
覆盖率只统计 `allapp` 生产代码，排除测试、迁移和生成文件，不排除业务模块。首次多库准备中
每库实际应用 124 条迁移；未使用 SQLite 或共享单一测试 schema。

收集数相对原基线从 1,026 增加到 1,046，源于整改回归新增；没有通过删除、跳过或改用
SQLite 降低门禁。完整串行执行曾暴露 3 个新增回归，修复后对应定向用例 3/3 通过，最终
全量结果为零失败：

1. 数据准确性测试由数组位置断言改为按检查项名称定位，避免新增 owner 不变量检查改变顺序。
2. PDA 上架权限夹具改用真实的异货主商品，生产 owner 不变量仍保持拒绝跨货主关系。
3. Admin inline 范围校验不再绕过条码 formset 的领域服务保存链。

原报告中的 dry-run、标准保留清单和辅助出库夹具契约均已按整改计划修正，并由本轮全量
回归覆盖。商品范围、Admin 中央范围策略、辅助出库绑定、配送单 v2、owner 一致性、失败
过账审计、单入口过账和 `release_to_pick` 均已加入定向及全量回归。

### 7.3 客户端、CI 与部署复验

| 客户端/项目 | 整改后结果 | 仍需完成 |
|---|---:|---|
| `wmspda` | 37/37 unit、H5、Playwright 8/8 PASS | 固定 HBuilderX 的 Android 签名制品、双真机与打印 |
| `wmsownersale` | 81/81 unit、H5 PASS；原 flaky 连续 20 轮共 140 个断言全通过 | 真实 AppID、HTTPS API 地址和微信构建 |
| `sales-miniapp` | 快速 quality gate 与 H5 已通过；release gate 已改为禁止 skip | 真实 AppID、测试商户及完整 release CI |
| `wmsbossbilling` | 新增独立 manifest/lock；9/9 unit、H5、Playwright 1/1 PASS | 真实 AppID、HTTPS API 地址和微信构建 |

四客户端现在统一使用受校验的 `VITE_API_BASE_URL`：H5 可同源 `/api`，App/微信必须是绝对
HTTPS；凭据、query、fragment、非授权私网和生产 HTTP 均 fail-closed。商城持久化 API
覆盖和 PDA 登录预填凭据已删除。当前微信 manifest 的 AppID 特意保持为空，因此微信发布
构建会正确失败，不能把测试 AppID 或借用工具链记为通过。

CI 已固定 Python 3.12.13、Node 20、MySQL 8.0.46 和 Redis 7.4.7；顺序为静态与安全门禁、
干净 schema 串行测试、迁移专项、并发/分片、覆盖率和客户端、候选镜像、Trivy/SBOM/
provenance/smoke，最终 release gate 依赖所有强制 job。安全命令不再使用 `|| true`。

生产 Compose 和发布脚本已改为只接受镜像 digest。Web、billing scheduler 和支付 one-shot
使用同一候选镜像；发布脚本会拒绝旧支付 timer、校验备份、停止入口与调度、执行迁移和
审计、等待 readiness、确认单 scheduler，并默认观察 60 分钟。

### 7.4 尚未关闭的硬门禁

以下事项需要预发布环境、真实设备、外部凭据或完整 CI 执行，代码修改不能替代证据：

- 完成 CI 的 secret scan、Trivy 文件系统与候选镜像扫描、SBOM、provenance 和镜像 smoke，
  且无未处置可利用 high/critical；`PYSEC-2026-3412` 例外须在 2026-09-19 前复审或关闭。
- 提供三个微信客户端的真实 AppID、经批准的 `VITE_API_BASE_URL` 和微信合法域名；
  AppSecret 只能从运行环境注入。
- 对此前登录预填涉及的账号执行改密/禁用、JWT 与会话吊销，并完成历史登录审计；源码删除
  不能替代凭据轮换。
- 在两台 PDA 上完成一维码、GS1、连续/重复/破损码、断网恢复、蓝牙标签/小票打印；
  RFID/NFC 只能按设备能力实测或记 N/A。
- 使用微信测试商户完成成功、取消、超时查询、伪签名、重放、金额不符、退款和对账。
- 执行 50 并发 30 分钟、100 峰值 5 分钟并满足查询 p95 <= 800 ms、写 p95 <= 1.5 s、
  错误率 < 1%，且库存、过账、支付和财务对账无差异。
- 执行数据库断连、容器重启、重复调度、支付超时和部分写入的系统级故障注入。
- 完成全 MySQL 与媒体恢复演练，证实 RPO <= 24 小时、RTO <= 60 分钟。
- 在生产发布前完成角色范围、跨货主数据、历史配送单 v1/v2、支付配置和数据准确性的只读
  审计；历史异常须经业务复核，不得自动猜测或静默修正。

### 7.5 当前发布结论

**FAIL。** 代码整改、本地 MySQL 串行/并行自动化和覆盖率已经通过，但本计划明确不接受
`CONDITIONAL PASS`。完整 CI/Trivy、凭据轮换、真实 AppID/微信支付、双 PDA、
性能、故障注入和恢复演练尚无有效证据，因此现在仍不能发布。只有第 7.4 节全部关闭、
最终 commit SHA 制品齐全且无 P0/P1 或可利用高危时，结论才能改为 `PASS`。
