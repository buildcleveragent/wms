# 工程、发布、可恢复性和容量审核

2026 年 11 月 18 日，核查对象为 `836ce7ca9fc4` 及当时工作树，包括未跟踪的覆盖合并、备份和巡检代码。本轮没有操作数据库、部署环境或商户。工程判断以当前代码、无库回归和模拟发布为依据，已有隔离演练记录另列。

## 需要改进

| 优先级和影响 | 当前证据 | 责任和验收 |
| --- | --- | --- |
| 高。观察期失败时，按提示还原到迁移前，可能覆盖此后已提交的仓储或资金业务 | 生产脚本在同一停写窗口完成备份，随后启动 Web、后台作业和支付补偿，默认观察 60 分钟。观察失败时指示恢复该备份。模拟事件依次是备份（19、20）、启动 Web（24）、启动后台（32）、支付补偿（47）、再次停写（63），退出码 1。Compose 端口从 Web 启动时对本机代理开放。仓库手册要求通知停单，支付补偿本身也可改变支付及退款状态。实际站点是否有额外隔离、重放程序，以及是否已有数据损失，均缺少现场证据。[生产发布](/wms/docker/production_release.sh:87) · [支付补偿](/wms/allapp/salesapp/management/commands/reconcile_sale_mini_payments.py:22) · [模拟记录](/wms/var/assessments/2026-11-18/current-worktree/engineering-probes.json) | 运维和财务在恢复指引中区分开始写入前后的失败。首次恢复写入后，先保存现场并核对新增订单、库存和资金流水，把可验证的修复或重放步骤纳入回退。隔离验收在观察期提交一笔订单和一笔支付通知，再触发失败，恢复后逐笔核对数量、金额、业务号及重放幂等性。 |
| 高。镜像可能已推送，来源证明及后续预生产发布却受权限阻断 | 构建 job 仅授予 contents 读取、id-token 写入、packages 写入。它随后使用默认令牌生成 GitHub 来源证明并上传镜像 SARIF，缺少两项操作所需的写权限，预生产依赖该 job 成功。此结论来自 YAML 和官方接口契约，未运行远端 Actions。Docker Hub 镜像身份还需按动作说明使用完整 registry 前缀。[构建配置](/wms/.github/workflows/ci.yml:495) · [来源证明动作](/wms/.github/workflows/ci.yml:581) · [GitHub 动作说明](https://github.com/actions/attest-build-provenance/blob/v2/README.md#usage) · [GitHub SARIF 说明](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file) · [未声明权限的规则](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions) | 研发效能在相应 job 明确授予 `attestations: write` 和 `security-events: write`，核对完整镜像名。在获准的发布流水线上按摘要查取得到的来源证明和两类扫描结果，再验证 staging 接收相同摘要。 |
| 中。预生产可能拿旧编排发布新镜像 | 工作流在 runner 检出仓库，但 SSH 只将镜像摘要传给预生产主机。远端随即使用其本地 `/app/wms` 中的发布脚本和 Compose，未核对这两者对应的提交。独立的主机同步机制可能满足要求，仓库本身没有证明。[预生产入口](/wms/.github/workflows/ci.yml:620) | 运维绑定镜像摘要与编排版本。预生产故意放置前版脚本或 Compose 时，部署应在停写前明确拒绝，更新为候选版本后能成功。 |
| 中。容量报告可以在所有底层遥测都缺失的情况下通过 | 使用现有测试夹具构造两个场景、两个并发阶段。每次保留读写延迟和请求统计，将规定的 12 项遥测全部设为 JSON null，校验器返回零错误。把读 P95 改为 801ms 的对照得到超标错误。这个问题影响对锁等待、资源和拥塞原因的判断。[容量校验](/wms/scripts/validate_external_acceptance.py:89) · [实测记录](/wms/var/assessments/2026-11-18/current-worktree/engineering-probes.json) | 性能负责人根据各指标定义检查类型、有限值和取值范围，保留合理的零值，缺数给出字段及场景。验收覆盖四个阶段全部有值、单项丢失、全空、采集失败与延迟超限。 |

## 已有改进和优势

- 历史覆盖率问题有回归保护。CI 明确下载功能、迁移两份产物，缺失、损坏或未测任何文件会失败。聚合后分别检查总体 ≥72.38%、移库和补货各 ≥80%，临界值不足也会失败。本轮运行合成分片，当前候选的全库覆盖率尚未测量。[CI 聚合](/wms/.github/workflows/ci.yml:378) · [边界回归](/wms/allapp/core/test_coverage_gate.py:15)
- 工作树已经排除混合大小写、递归和压缩 SQL 名称，CI 配置同时检查构建上下文和镜像，并加载实际控制台模板。此项在本轮作静态核查。候选镜像未构建。[忽略规则](/wms/.dockerignore:40) · [CI 双层检查](/wms/.github/workflows/ci.yml:510)
- 发布预检涵盖静态、公共媒体、私有到货及退货附件四个卷，同时限制私有根目录归属。静态资源通过同一镜像重新收集。模拟发布在停写后收集数据库及三种媒体，回执缺项、恢复点错配、迁移前失败时恢复旧容器等都有针对性回归。[四卷检查](/wms/docker/check_volume_access.py:40) · [备份清单](/wms/docker/create_release_backup.py:12) · [发布回归](/wms/allapp/core/test_release_orchestration.py:151)
- 已有隔离合成 Docker 演练记录涵盖不同 MySQL 实例、5 对业务单据及对应附件。当前校验器重新读取该记录通过，记录中的 RPO 为 26.33 秒、RTO 为 205.80 秒。该历史证据代表该次隔离演练。现场备份钩子、异地恢复、生产数据量和恢复后新增流水仍需实证。[既有演练记录](/wms/var/assessments/2026-11-18/isolated-recovery.json)
- 日计费巡检按合同、货主、仓库和业务日期查找漏跑或只完成部分指标的义务，能重查较早的未结工单。部署时要开启后台自动检查。此处按实现及运行手册确认。本分项没有对真实账号、告警渠道或 MySQL 进行试验。[巡检逻辑](/wms/allapp/reports/services_daily_billing_alerts.py:137) · [现场验证](/wms/docs/release-remediation-runbook.md:20)

## 本轮执行记录

- 五个工程测试文件共 87 项通过，0 失败、0 跳过，141.07 秒。范围包括覆盖率、HTTP 探针和四卷配置、模拟 Docker/systemd 发布、外部验收格式及发布准备度。这里的发布失败和覆盖不足都是有意构造的反例。[JUnit](/wms/var/assessments/2026-11-18/current-worktree/engineering-pytest.xml)
- PDF 资源限制另有 6 项通过，0 失败、0 跳过，74.17 秒。在独立临时目录运行真实子进程，验证短中文单据、并发限额、超时回收、内存和输入输出超限。该耗时是整组测试时间。生产打印负载或扫码延迟没有在这里测量。两组共 93 项通过。[PDF 回归](/wms/var/assessments/2026-11-18/current-worktree/engineering-pdf.xml)
- 额外执行了本报告的离线探针和已有恢复记录校验。本次未运行远端 CI、镜像构建、真实部署或负载压测。真实机器上建议按现有 50/100 并发、同仓热点和跨仓两个场景运行，同时观察扫码与发运单打印，结合读 P95 ≤800ms、写 P95 ≤1500ms 和库存/财务差错为零判定。[探针源码](/wms/var/assessments/2026-11-18/current-worktree/engineering_probes.py) · [容量标准](/wms/docs/external-acceptance-profile.json)
