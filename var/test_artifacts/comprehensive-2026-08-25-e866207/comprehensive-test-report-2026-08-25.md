# WMS 发布级全面测试报告（2026-08-25）

## 1. 最终结论

**FAIL — NO-GO，不可发布。**

本轮没有发现自动化可复现的 P0 级跨租户泄露、库存/财务差异、重复过账或支付金额错误；后端 1,046 条测试、重点风险回归、PDA/H5、货主端 H5、老板端 H5/E2E、商城后端/H5 主体均取得了较强的通过证据。

但发布门禁仍存在明确失败：全库静态质量未清零、根 Node 生产依赖有 high 漏洞、候选镜像系统层有 47 个 high/critical、CI 固定已 EOL 的 Node 20、Gitleaks 未配置已审查的测试向量例外，以及微信正式配置和性能/真机/支付/灾备等预发布硬门禁未完成。本轮要求全部硬门禁通过，不接受 `CONDITIONAL PASS`，因此必须判定 NO-GO。

## 2. 基线与隔离

| 项目 | 结果 |
|---|---|
| Commit | `e8662074901eaf7652a06b86ce8111b0708afbf0` |
| 源码工作方式 | detached 隔离 worktree；未修改 tracked/staged 源码 |
| Python | 测试 venv 3.12.3；候选镜像 3.12.13 |
| Node / npm | 22.22.0 / 10.9.4 |
| MySQL / Redis | MySQL 8.0.46、Redis 7.4.7，独立 Compose 项目与独立 schema |
| 主机资源 | 6 vCPU、约 16 GiB 内存；不满足正式 8 vCPU 性能 runner 要求 |
| 浏览器 | 仓库 Playwright + Chromium；Browser 插件不可用 |
| 数据边界 | 未使用开发/生产数据库、生产数据或生产支付凭据 |
| 业务代码变化 | 0 |

## 3. 门禁汇总

| 门禁 | 结果 | 证据摘要 |
|---|---|---|
| Git 基线与源码隔离 | PASS | HEAD 固定；测试 worktree tracked/staged diff 均为 0 |
| Django `check` | PASS | 0 issue |
| `check --deploy` | PASS WITH WARNING | 仅 `security.W021`：HSTS preload 未启用 |
| 迁移漂移 | PASS | `makemigrations --check --dry-run` 无变化 |
| `pip check` | PASS | 无冲突 |
| Python 生产依赖审计 | PASS | 0 已知漏洞，1 项有记录的忽略项 |
| Black | FAIL | 299 个文件需格式化 |
| isort | FAIL | 144 个文件导入顺序失败 |
| Ruff | FAIL | 173 项；含 4 个 F821、60 个 F401、72 个 E402、28 个 F811 |
| Flake8 | FAIL | 1,213 行问题 |
| lint ratchet | PASS | 仅证明旧债未增长，不能替代全库零错误门禁 |
| Bandit | PASS | 0 high、0 medium |
| Gitleaks | FAIL / TRIAGED | 8 个命中均为 CI/单测固定测试向量；无真实凭据，但扫描器仍以 1 退出 |
| Trivy 文件系统 | FAIL | 根 `package-lock.json` 有 12 个 HIGH CVE 映射 |
| 根 npm 生产审计 | FAIL | 2 high、1 moderate；`axios`、`form-data`、`follow-redirects` |
| 两次全新 MySQL 迁移 | PASS | 33:32.57、32:59.32；均小于 60 分钟熔断线 |
| 后端串行 | PASS | 1,044/1,044 + 943 subtests；迁移专项另 2/2，总计 1,046/1,046 |
| 后端 xdist + coverage | PASS | 1,044/1,044 + 943 subtests；2:33:00；覆盖率 71.70% |
| 覆盖率硬门槛 | PASS | ≥70%；未达到 72% 目标，差 0.30 个百分点 |
| 重点发布风险回归 | PASS | 209/209 + 9 subtests |
| PDA | PASS | 37/37；H5 PASS；Playwright 8/8；20×37/37 稳定 |
| 货主端 | PARTIAL / BLOCKED | 契约 20/20；81/81；H5 PASS；隔离 20×81/81；微信 AppID 缐失 |
| 老板端 | PARTIAL / BLOCKED | 9/9；H5 PASS；E2E 1/1、稳定 5/5；微信 AppID 缺失 |
| 商城 | PARTIAL / BLOCKED | 25/25；DB/API 64/64；Console 16/16；Admin 10/10；准确性 0 issue；H5 PASS；微信 AppID 缺失 |
| 候选镜像构建 | PASS | 固定 Python digest、hash 锁依赖、非 root runtime；8:19 |
| 镜像 smoke | PASS | Python 3.12.13、`pip check` 通过 |
| 候选镜像 Trivy | FAIL | Debian 12.15 系统层 47 high/critical，其中 7 critical |
| SBOM | PASS | CycloneDX 已生成 |
| 196 条人工用例 | BLOCKED | 缺预发布账号、数据和人工执行条件 |
| 性能 50/100 并发 | BLOCKED | 当前主机/数据/入口不满足正式性能场景 |
| 两台 PDA、扫码/GS1/打印 | BLOCKED | 无真实设备 |
| 微信支付测试商户 | BLOCKED | 无测试商户与受控凭据 |
| 故障注入与全量灾备演练 | BLOCKED | 无预发布基础设施、媒体备份与恢复窗口 |

## 4. 后端与数据库结果

### 4.1 空库迁移

两次使用全新 MySQL schema 完成全部 140 个迁移：

- 第一次：33 分 32.57 秒，最大 RSS 317,784 KiB。
- 第二次：32 分 59.32 秒，最大 RSS 319,820 KiB。
- 无 SQL 错误、无遗留锁等待、无迁移漂移。
- `salesapp.0013_localize_admin_fields` 分别约 374.18 秒、377.52 秒，是最明显的单迁移热点。
- `tasking.0001`、`salesapp.0001` 各约 156–158 秒，`salesapp.0005` 约 132–133 秒。

结论：功能门禁通过，60 分钟只能作为熔断上限；33 分钟不应成为长期性能目标，应继续 profile 最慢迁移。

### 4.2 全量与覆盖率

- 收集：1,046 条，无无说明下降。
- 串行：1,044 条普通用例 + 943 subtests 全通过；商品标识迁移专项 2/2 通过。
- 并行：1,044 条普通用例 + 943 subtests 全通过；6 个 worker 各自建库导致启动成本很高，总耗时 2:33:00。
- 覆盖率：71.70%，通过 70% 硬门槛，低于 72% 目标 0.30 个百分点。

### 4.3 已知风险定向复验

209 条用例与 9 个 subtests 全部通过，覆盖：

- 商品匿名/租户范围与商品访问。
- 配送单来源、价格、数量和快照构建。
- 任务、库存明细和流水的货主一致性。
- 过账失败审计、失败回滚、重试和并发单执行者。
- 辅助出库绑定与生产整改回归。
- `release_to_pick` 命令。
- 业务数据清理、保留数据备份/恢复 round-trip。

这些是自动化功能证据，不等于完成真实仓库业务人员的 196 条人工闭环验收。

## 5. 客户端结果

### 5.1 PDA

- 单测 37/37。
- H5 构建通过。
- Playwright 8/8，通过登录后首屏、无单收货、POS 打印失败后的幂等键轮换、GS1 快建、扫码竞态等场景。
- 单测连续 20 轮，每轮 37/37，总计 740 次通过。
- 未执行 Android/HBuilderX 签名制品、真实 PDA、蓝牙打印和断网真机恢复。

### 5.2 货主端

- 页面契约 20/20；单测 81/81；H5 构建通过。
- 正式隔离单 worker 稳定性：20×81/81，总计 1,620 次通过。
- 额外在与 PDA 并发负载下，第 6 轮出现一次 `inventory-component` 5 秒超时（80/81）。该轮不属于正式隔离门禁，但说明测试对 CPU/事件循环争用敏感，应保留为 P2 稳定性风险。
- 微信构建 fail-closed：缺少批准的生产 AppID。

### 5.3 老板端

- 单测 9/9，H5 构建通过。
- 登录 Playwright 1/1，通过时间约 13 秒；连续 5 轮全部通过。
- 未复现此前 0/1 导航超时。
- 微信构建 fail-closed：缺少批准的生产 AppID。

### 5.4 商城

- 快速门禁：结构检查、25/25 纯单测、H5 构建通过。
- 完整 DB 门禁：API/支付并发 64/64，Console 16/16，Admin 批量上架 10/10，数据准确性 `issue_count=0`。
- `test:release` 最终失败仅发生在微信构建阶段：缺少批准的生产 AppID。

## 6. 安全与供应链发现

### P1-01：全库静态质量门禁失败

Black、isort、Ruff、Flake8 均未清零。Ruff 的 4 个 F821 包括 `allapp/tasking/posting.py` 的未定义名称，属于可达性需进一步核实的语义风险。当前 ratchet 只锁定旧债，不满足本轮“全库零错误”发布标准。

### P1-02：根 Node 生产依赖 high 漏洞

根 `package-lock.json` 的 `axios 1.12.2`、`form-data 4.0.4`、`follow-redirects` 被 npm/Trivy 确认存在 high/moderate 漏洞；四个客户端自己的生产树为 0 high/critical，但不能抵消根生产树失败。

### P1-03：候选镜像系统层存在 47 个 high/critical

Trivy 0.73.0 在 Debian 12.15 层报告 47 项 high/critical，其中 7 项 critical，涉及 `libglib2.0-0`、`libsqlite3-0`、`libxml2`、`perl-base`、`zlib1g` 等。多数当前没有 fixed version 或处于 Debian `fix_deferred/will_not_fix`；按本计划 `ignore-unfixed=false`，必须阻断发布并制定基础镜像/组件可达性与例外策略。

### P1-04：CI 固定 Node 20，当前已 EOL

CI 的 `NODE_VERSION` 为 `20`，四个客户端也没有 `engines` 约束。本轮在 Node 22.22.0 下测试通过，但 CI 仍会使用停止安全维护的 Node 20。Node 官方当前将 v20 标为 EOL，v22/v24 为 LTS：<https://nodejs.org/en/about/previous-releases>。

### P1-05：Gitleaks 门禁缺少受控测试例外

8 个命中经人工核对均为固定测试内容：6 个 CI 测试 `SECRET_KEY`、1 个订单幂等测试键、1 个微信 AES-GCM 单测向量。未发现真实凭据，但扫描器仍失败；应以精确规则/路径/指纹配置受审计 allowlist，而不是关闭 secret scan。

### P1-06：微信与外部预发布硬门禁未完成

货主端、老板端、商城均因缺少批准 AppID 而拒绝微信构建；真机、测试商户、正式负载和全量恢复演练也未提供条件。若本次 release scope 明确排除这些能力，可另行重算门禁；本轮按原计划全部纳入，因此阻断。

### P2-01：迁移与 xdist 启动成本高

空库迁移稳定约 33 分钟；6 worker 各自迁移导致并行门禁总耗时 2:33。建议把 60 分钟保持为 kill switch，再基于独占 8C/16G/SSD runner 制定更严格基线并 profile `salesapp.0013`。

### P2-02：货主端用例对共享负载敏感

隔离 20 轮全部通过，但并发负载下出现一次 5 秒超时。发布 CI 应维持单 worker/禁止与重型构建争抢资源，并最终用可控 Promise/fake timer 消除墙钟依赖。

### P3-01：HSTS preload 警告

`check --deploy` 仅有 `security.W021`。若生产域名满足永久 HTTPS 与子域策略，应在上线审批后启用；不能在不了解域名影响时机械开启。

## 7. 未完成的外部硬门禁

以下项目因缺少预发布入口、账号、标准仓数据、设备、支付测试商户或恢复基础设施而未执行，均不能记为 PASS：

- 50 个持续并发 30 分钟、100 个峰值并发 5 分钟及 p95/错误率/对账验收。
- 两台真实 PDA、一维码/GS1/连续扫码/破损码、蓝牙标签或小票打印、断线重连。
- 微信支付成功、取消、超时查询、验签、伪签名、重放、金额不符、退款与对账。
- 数据库断连、容器重启、重复调度、外部支付超时与部分写入故障注入。
- 全量 MySQL + 媒体恢复演练，以及 RPO ≤24 小时、RTO ≤60 分钟实测。
- 196 条人工业务用例的角色、设备和人工确认。

## 8. 发布判定与下一步

当前不是“系统主体存在大量已确认业务回归”，而是“核心自动化功能基本健康，但发布验证尚未满足全部强门禁”。在以下事项全部关闭前不得发布：

1. 清零或经正式政策处置静态质量门禁，尤其是 4 个 F821。
2. 升级根 Node 生产依赖，取得 npm/Trivy 文件系统 0 high/critical。
3. 选择可接受的受支持基础镜像，或对镜像 47 项发现逐项完成可达性、责任人、期限和批准例外；当前不能直接放行。
4. 把 CI 从 Node 20 提升到受支持 LTS，并在四端干净 `npm ci` 下复验。
5. 配置精确 Gitleaks 测试向量例外，确保真实 secret 仍 fail-closed。
6. 明确本次 release scope；若包含微信/支付，提供正式 AppID、测试商户、合法域名与真机。
7. 在合格预发布环境完成性能、设备、支付、故障注入、全量恢复和 196 条人工验收。

满足以上条件并重新取得全部门禁 PASS 后，才可把结论调整为发布候选。

## 9. 主要证据索引

- `migration-1.log`、`migration-2.log`：两次空库迁移与逐迁移耗时。
- `pytest-serial.log`、`identifier-migrations.log`：串行及迁移专项。
- `pytest-xdist.log`、`coverage.xml`：并行结果与覆盖率。
- `targeted-release-risks.log`：209 条重点发布风险回归。
- `pda-test-quality.log`、`pda-unit-stability-20x.log`。
- `owner-test-quality.log`、`owner-unit-stability-20x.log`、`owner-unit-stability-isolated-20x.log`。
- `boss-test-quality.log`、`boss-e2e-stability-5x.log`。
- `mall-test-quality.log`、`mall-test-release.log`。
- `black.log`、`isort.log`、`ruff.log`、`flake8.log`、`bandit.json`、`pip-audit.json`。
- `gitleaks.json`、`trivy-fs.json`、`trivy-image.json`。
- `docker-build-retry.log`、`image-python-version.log`、`image-pip-check.log`、`candidate-sbom.cdx.json`。

