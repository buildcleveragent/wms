"""Publish a local execution report only from completed, identified evidence."""
import json
from datetime import datetime,timezone
from pathlib import Path
root=Path('/wms/var/system-tests/remediation-2026-09-06')
load=lambda p:json.loads((root/p).read_text())
review=load('phase6-v7/final-results-review.json')
coverage=load('phase6-v7/coverage-gate.json')
readiness=load('final-v7-readiness.json')
candidate=load('phase5/final-candidate-v7.json')
schema=load('phase6-v7/final-schema-verification.json')
assert schema['status']=='PASS'
link=lambda label,p:f'[{label}]({root/p})'
cov=coverage['measured']
mat=review['matrix']
text=f'''# WMS 测试与整改执行报告（2026-09-06）

当前环境内的功能测试、定向整改复验、独立迁移验证及覆盖率汇总已完成。发布门禁仍为 **BLOCKED**：镜像和部分依赖风险尚未完成处置，PDF 资源预算尚无发布评审，外部实环境验收未执行。没有部署生产。

结果时间：{datetime.now(timezone.utc).isoformat()}。起始提交为 `c9175bfcb837fe740471710c5267ac8d5147911d`，工作树包含未提交变更；实际验收以文件哈希和镜像身份为准。{link('完整候选记录','phase5/final-candidate-v7.json')}。

| 实际执行 | 结果与证据边界 |
| --- | --- |
| 候选 v6 功能全量 | **1,248 主测试通过、2 失败，945 子测试通过**；3 个迁移测试按分组另行执行。耗时 3,698.76 秒。原始 FAIL 记录保留。 |
| 失败最小复现复验 | 补齐测试层 MySQL 8.0.46 客户端后，2 项通过；复用测试库。 |
| 候选 v7 受影响模块 | 保留数据备份/恢复完整模块 **7 项通过**，含真实往返恢复及行数不符时事务回滚。 |
| 独立迁移专组 | **{review['stages']['migrations']['main_counts']['PASS']} 项通过**：POS 非空升级、商品历史标识回填、冲突预检与重试；独立实例均按运行记录清理。 |
| 移库、补货 API 验收 | **{mat['movement_api']['count']} 项通过**；开发阶段相关完整模块另有 43 项通过，存在重叠，不相加。 |
| 跨模块业务闭环 | **{mat['cross_app_business_flows']['count']} 项通过**。 |
| PDF 接口与内容验收 | **{mat['pdf_acceptance']['count']} 项通过**；含中文、分页、金额、零价、权限、转义及非内联资源拒绝。 |
| 五端逻辑测试 | **251 项通过**。 |
| 现有浏览器测试 | **26 项通过**，包含拦截或模拟响应。 |
| 真实 API 浏览器闭环 | **7 个流程通过**，由 2 个 Python 浏览器场景承载；后端为宿主 Python 3.12.3，后端权威测试使用候选派生 Python 3.12.14。 |
| 构建 | 五端 H5 与 Android App 构建通过；签名 APK、真机扫码打印和微信运行验收尚未执行。 |

原全量中的两个失败均来自测试镜像缺少 `mysql` / `mysqldump`，错误发生在备份预检阶段。修正仅涉及 `docker/Dockerfile.test`，客户端取自固定摘要的官方 MySQL 8.0.46 镜像，并补入 `libncurses6`。原生产镜像完全复用；46 个既有测试 Python 包的版本和文件、178 个既有系统包的版本均未改变。

按冻结的影响范围复验规则，最终功能验收集合由 1,243 个未受影响的原通过节点和新运行的 7 个节点组成；再加入 3 个独立迁移节点，共 **1,253 个唯一主测试**。这是原全量与独立复验的证据组合，v7 没有再次执行完整功能分组。每个原失败均关联新运行，原失败产物未被覆盖。{link('逐节点复验台账','phase6-v7/functional-reverification-review.json')}、{link('冻结的复用规则','phase6-v7/reuse-policy.json')}、{link('最终测试集合复核','phase6-v7/final-results-review.json')}。

**数据库与证据可信度**

开发阶段默认复用 `wms_db_test`。最终 v6 门禁按授权将该库重建 **一次**，从空库真实应用 **141 条迁移**；后续客户端修正及模块复验继续复用。POS、商品标识及失锁故障注入使用独立 MySQL 8 实例，各自库名仍为 `wms_db_test`。没有使用 SQLite、跳过迁移或 fake，也没有对 `wms_db` 执行写入。

最终只读模式检查为 PASS：迁移历史一致、无待应用迁移，所有受管理模型的表和列存在；原运行镜像的 `makemigrations --check --dry-run` 通过。此模式检查用于证明结构，不用清理后的空库对账替代业务闭环证据。{link('最终模式检查','phase6-v7/final-schema-verification.json')}。

原历史 JUnit 保留 **1,122 个 testcase / 2,067 汇总计数 / 945 个仅汇总子测试** 的限制。本次 v6 原全量 XML 有 **1,250 个 testcase、2,195 汇总计数**，另有 945 个子测试事件，已按运行号、父测试、尝试号和子测试号核验独立身份。本次证据不恢复旧运行缺失的子测试身份。{link('历史证据复核','phase1/historical-review.json')}。

**实际整改**

- 修复完成任务的原操作员查询/扫码重放被错误 404 拒绝；分别验证计划 4 件成功过账后的 10→6/0→4，以及计划 10 件按 4、4、2 扫描时遵循整任务过账规则。
- PDA/Android 为连续扫描生成正确的请求身份，不确定响应保留原请求并阻止修改内容后重试；该机制为当前页面内恢复，未验证杀进程后的持久恢复。
- 商城累加加购在响应不确定时不自动重发，重新读取服务端购物车；历史 HTTP 400 因缺少原请求/响应仍无法定位，当前成功不能替代历史原因证明。
- 测试记录器保留失败、退出码、中断和逐项事件；修复 collect-only 查询记录时意外插入空记录造成的误判。协调锁按稳定资源命名，失锁后中止并核验旧进程、容器、连接和事务清理；实际故障注入验证新运行不能抢先进入。
- Python 3.12.14、Node 22.23.2/npm 10.9.8、WeasyPrint 69.0 已验证，依赖锁和实际产物摘要留档；测试层不替换原运行依赖。本地整改、允许发布与发布后验收分别判定。

**覆盖率**

| 范围 | 最终覆盖率 | 门槛 |
| --- | ---: | ---: |
| allapp | {cov['allapp']:.2f}% | 72.38% |
| 移库 API | {cov['relocation_api']:.2f}% | 80% |
| 补货 API | {cov['replenishment_api']:.2f}% | 80% |

门禁使用未四舍五入的实际值，状态 **{coverage['status']}**。覆盖率由原全量、受影响模块复验和迁移专组的相同 Python 源码测量合并，保留每份输入哈希。{link('覆盖率门禁','phase6-v7/coverage-gate.json')}。

**未关闭、阻断与未执行**

- 原运行镜像扫描仍为 **65 HIGH、8 CRITICAL、88 MEDIUM、75 LOW、6 UNKNOWN**。历史与候选合并的 45 个告警标识中，8 项新扫描未检出、11 项缺少受影响组件、3 项架构前提不适用、1 项实际客户端版本已修复，**22 项仍需安全评估**。这些处置不改写原始包级扫描计数，也不授予发布例外。{link('逐项安全处置','phase5/image-security-dispositions-v6-reviewed.json')}。
- Python 生产依赖审计零漏洞；五端 npm HIGH/CRITICAL 为零。PDA/Android 各保留 13 LOW，货主/老板/商城各保留 15 LOW、5 MODERATE，尚未全部处置，依赖门禁仍 BLOCKED。
- PDF 仍仅允许 `data:`。宿主 1/100/1,000 行量测约 1.66/7.83/86.42 秒、峰值 RSS 261/282/495 MiB。`R-PDF-RESOURCE` 保持 REVIEW_REQUIRED，缺少实际部署限制及具名安全评估/发布处置；120 秒测试超时不是生产防护。
- 正式容量、灾备、微信支付、签名 APK/现场设备、预发布演练与至少 60 分钟观察、生产部署及部署后 60 分钟观察均未执行。**196 条人工用例为 NOT_RUN**。容量/灾备证据校验器和测试配置已完成，实际容量执行器及正式数据生成仍待准备，不能算已完成压力测试。
- 备份恢复仅在配有客户端的测试执行环境通过；原 Web 运行镜像仍未携带运维客户端，实际生产运维环境及媒体备份恢复未验收。
- 远程 CI 未运行；已做本地配置与 lint ratchet 核查。保留既有格式/构建提示，不将静态校验称作远程 CI 通过。

最终三阶段判定：`local_complete={str(readiness['local_complete']).lower()}`、`release_permitted={str(readiness['release_permitted']).lower()}`、`release_accepted={str(readiness['release_accepted']).lower()}`。{link('完整 readiness','final-v7-readiness.json')}、{link('逐门禁证据','final-v7-gate-evidence.json')}、{link('外部开放项','phase4/external-open-items.json')}。

完整原始产物在 `{root}`；{link('失败及修复运行索引','phase5/attempt-ledger-final-v7.json')}；{link('真实浏览器截图索引','phase3/real-browser-screenshots/manifest.json')}。没有提交、推送或部署。
'''
Path('/wms/docs/remediation-execution-2026-09-06.md').write_text(text)
with (root/'execution-report-v7.md').open('x') as f:f.write(text)
print('Execution report written from completed evidence')
