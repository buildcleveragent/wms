# 发布阻断修复后的运维验收

本文件是执行要求，不是已部署或已通过的证据。未解决的高危、严重漏洞继续阻断发布，
不添加风险放行例外。所有证据绑定冻结源码和最终镜像摘要；安全扫描有效期 24 小时。

## 常驻服务发布与 Boss 心跳

生产入口为 `sh docker/production_release.sh <repository@sha256:digest>`；staging 入口为
`WMS_IMAGE=<repository@sha256:digest> sh docker/staging_release.sh`。两个入口均从仓库根目录
执行，并共用 `docker/release_services.sh` 管理 `web`、`scheduler`、`boss-check-worker`、`owner-archive-worker`、`inventory-close-worker`。

预检通过后停止所有写入服务，再运行迁移。web 恢复、迁移检查和数据核对通过后，才启动
后台服务。每个服务必须恰有一个运行容器，实际镜像 ID 必须与目标不可变镜像一致；首次
部署同样创建 Boss worker。生产流程仍负责支付定时器停用、排空和恢复。

Boss worker 的只读探针为 `python manage.py check_boss_worker`：心跳必须存在且在最近
120 秒内。发布另外传入 `--started-after <容器 StartedAt>`，拒绝沿用旧容器留下的心跳。
探针支持项目本地时间配置与 Docker UTC 时间比较，不写入心跳、不触发业务巡检。
自动巡检关闭时 worker 仍发送心跳，因此不应跳过 worker 的发布和健康检查。

验收日计费到期告警时，由超级用户在经营端首页账号菜单的“刷新与检查设置”确认已开启“后台自动检查”。
首次安装默认关闭。开启后按配置的巡检间隔，核对“最近一轮结束”已更新，并在经营端“需要立即关注”的
检查状态或 `/api/reports/boss/attention/` 中确认 `BILLING_HEALTH` 有本次成功检查时间。使用合成合同在
验收环境验证一笔到期漏跑和一次补齐，记录同一工单的发现、自动解除及对应服务日。

启动探针最多等待 120 秒。生产观察期持续检查各写入容器、web readiness 和 worker 心跳；
迁移、启动或观察失败，各常驻服务及支付定时器保持停止，命令以非零状态退出。按照已验证
备份和旧镜像恢复数据库、媒体及应用后，重新核对服务数量、镜像、数据和心跳，再恢复
调度；不要仅重启 web，也不要自动回退数据库迁移。

代码内的编排回归使用模拟 Docker/systemd，并不证明真实服务器已成功部署。真实发布
还需保存服务镜像、容器状态、启动时间、探针及数据核对结果。

## 运行卷

发布预检以 `10001:10001` 在每个挂载点创建临时目录和文件，读回后删除，并检查已有条目的访问权限。

| 挂载点 | 用途 | 恢复方式 |
|---|---|---|
| `/app/staticfiles` | 公共静态资源 | 使用同一版本的镜像执行 `collectstatic` |
| `/app/media` | 普通上传文件 | 从该恢复点的媒体备份恢复 |
| `/app/private_arrivals` | 到货单照片 | 从该恢复点恢复，按到货单及文件哈希核对 |
| `/app/private_close` | 日结补证与期初附件 | 同一恢复点恢复，逐项核对复核记录、路径及文件哈希 |
| `/app/private_exports` | 货主导出文件 | 按货主归档恢复流程处理 |
| `/app/private_returns` | 退货单照片 | 从该恢复点恢复，按退货单及文件哈希核对 |

任一卷预检失败，发布在停写前退出。先核对 Compose 项目名及容器的实际挂载，定位失败文件。
如设置了 `PRIVATE_ARRIVAL_ROOT`、`PRIVATE_RETURN_ROOT` 或 `PRIVATE_CLOSE_ROOT`，实际存储路径必须位于各自备份的私有卷内。
已有数据先做可恢复备份。按文件修复运行账号的访问权限，保留私有卷的访问限制，然后重跑预检。
候选镜像可用 `python scripts/check_release_volumes.py --image <候选镜像>` 在全新临时卷验证。

## 生产发布备份和中止恢复

维护窗口包含备份耗时。发布前通知仓库停止提交作业，并排空部署方自建的导入、同步或数据库写入作业。
脚本会检查已停用的旧 Boss 定时任务，暂停两个支付定时器，等待在途支付任务退出，再停止所有常驻写入服务。
确认 Compose 写服务均已停止后，数据库和附件在同一个停写窗口完成备份。备份记录位于
`WMS_BACKUP_EVIDENCE_DIR`，默认 `/var/lib/wms/release-backups`，发布账号需要写权限。

沿用 `WMS_DB_BACKUP_SCRIPT` 和 `WMS_MEDIA_BACKUP_SCRIPT` 指定的可执行文件。两个脚本均无位置参数。
每个脚本应同步完成备份、验证备份可读取，然后将一份 JSON 回执写入环境变量 `WMS_BACKUP_RECEIPT` 指定的路径。

| 回执字段 | 内容 |
|---|---|
| `recovery_point` | 原样填写本次发布传入的 `WMS_BACKUP_SET_ID` |
| `snapshot_id` | 运维恢复工具能实际定位的备份文件或快照编号。数据库与媒体可以有各自的编号 |
| `includes` | 数据库回执包含 `database`。媒体回执包含 `media`、`private_arrivals`、`private_returns`、`private_close` |

脚本对两份回执核对恢复点和覆盖范围，成功后生成 `manifest.json`。数据库或附件备份失败、回执缺失或
恢复点不匹配会中止迁移，并恢复之前运行的容器和定时器。如果原容器无法恢复，先排查原版本服务。
迁移已经开始时沿用停写恢复流程，按清单中的两份备份和旧镜像恢复。回执只存引用，凭据保留在现有运维配置中。

现场首次接入时，在隔离环境使用生产同款脚本备份并恢复到新实例。逐笔核对到货单、退货单及对应照片，并核对库存日结复核记录与凭据，
记录最后一笔已恢复业务和恢复完成时间。按快照到故障的时差验证 RPO ≤24 小时，
按故障发生到业务验收完成的时差验证 RTO ≤1 小时，其中包含开始恢复前的等待时间。将清单、校验结果和实测时间绑定到候选版本。

在装有 Docker、MySQL 客户端和项目虚拟环境的测试机上，可从仓库根目录运行隔离实演。选择全新的私有证据目录，
例如 `/tmp/wms-dr-<运行编号>`。工具创建独立的 MySQL 8 源实例和恢复实例，使用合成业务数据与临时卷。

```bash
.venv/bin/python -m scripts.dr_recovery_drill --image "$WMS_IMAGE" --artifacts "$DR_EVIDENCE_DIR"
.venv/bin/python scripts/validate_external_acceptance.py recovery "$DR_EVIDENCE_DIR/recovery.json"
```

## HTTPS 与健康检查

生产 `ALLOWED_HOSTS` 必须同时包含正式域名和 `127.0.0.1`（容器内部探针 Host）。
仅 `/healthz/live`、`/healthz/ready` 精确路径豁免 HTTPS 跳转，其他 HTTP 路径继续跳转。
反向代理仍负责外部 TLS；必须覆盖来自客户端的 `X-Forwarded-Proto`，不能信任原始头。
验收包含正常数据库 200、断连 503、业务 HTTP 301，以及 HTTPS 业务访问。

## 配置与验收交接

锁验证固定 Python 3.12.15、pip 26.2.1、pip-tools 7.6.1；前端使用 `.node-version` 与
`.npm-version` 的版本。npm 11.21.0 用于解析 Vitest 4 的依赖关系；npm 10 在该升级上
会触发 Arborist 内部错误。CI 同时审计运行和开发依赖，不使用忽略规则放行高危问题。

- 三个小程序分别确认正式 AppID、HTTPS API 基址和微信合法域名；CI 的
  `WMS_PUBLIC_API_BASE_URL` 由负责人配置。占位地址只能做编译兼容性测试，不能作为正式构建验收。
- 密钥通过部署环境或既有秘密管理通道提供，不能写入仓库、日志或聊天。
- 预生产使用最终候选镜像，观察 60 分钟，验证数据库迁移、单实例调度、支付定时任务。
- 真实微信支付验证支付、回调、重复通知、关单、退款、对账，保留脱敏业务证据。
- 签名 APK 和小程序体验版在实际扫码、打印等适用设备上验收。
- 容量严格采用 `docs/external-acceptance-profile.json`；库存和财务差错必须为零。
- 在隔离环境恢复一致的数据库与媒体备份并演练回滚，RPO ≤24 小时、RTO ≤1 小时。
- `docs/manual-test-cases.csv` 的 196 项逐项签收。PDF 风险另需资源实测和具名评审。
- 未完成项记为 BLOCKED / NOT RUN，不复用旧候选结果冒充本次通过。

### 比较同仓与跨仓过账

本地专项实验使用 MySQL 8 的 `wms_db_test`。按仓库规范确认测试库可达和身份后，串行运行：

```sh
APP_ENV=test WMS_POSTING_BENCHMARK_REPORT=/tmp/wms-posting-capacity.json \
  .venv/bin/pytest --reuse-db -q scripts/test_posting_capacity.py
```

实验分别用 4、8 个连接执行已审核收货任务的实际过账。每个连接处理独立商品，先预热 1 笔，再连续过账 5 笔。同仓组使用一个仓库，跨仓组均分到两个仓库。报告保留每笔过账耗时、仓库行锁查询耗时、吞吐量，并核对每笔库存流水和各商品的明细、汇总。行锁查询耗时含 SQL 执行及数据库往返，解读时结合 MySQL 锁等待计数。

正式验收在固定配置的候选部署使用合成数据，按 `docs/external-acceptance-profile.json` 的数据量和五个岗位比例准备作业。两个场景使用相同的请求序列、商品分布及资源限额，只调整作业所属仓库。同仓组集中到一个仓库，跨仓组均分到两个仓库。各预热 5 分钟，然后测 50 人 30 分钟、100 人 5 分钟。分别保留请求与过账时序、阶段前后 MySQL 锁等待计数及库存财务对账结果。把四组采集结果写入容量报告，运行 `.venv/bin/python scripts/validate_external_acceptance.py capacity <容量报告.json>`。采集器应保留真实的零值，缺失数据应重新采集。

#### 五岗位 HTTP 压测与归档

负责人先确认隔离预生产的 HTTPS 源、候选镜像摘要、各服务副本和 CPU/内存限额、测试时段及合成数据快照。两组从相同合成基线开始。记录实际数据量，覆盖 2 仓、10 货主、1 万商品、10 万库存明细、100 万库存流水、10 万订单和 12 个月账期。压测会写扫码记录、POS 小票和库存。以下命令由有该候选访问权的执行人在确认现场资源后运行。

准备好全部合成数据及账号后，创建基线快照，把它恢复到独立 MySQL 8 实例。配置 Django 默认连接指向该恢复库，使用只读凭据。从运维资产记录核对恢复实例的 MySQL `server_uuid`、目标库名和快照 ID。预热前执行一次下列命令，两场景均从这份已计数的快照恢复。

```sh
.venv/bin/python -m scripts.wms_capacity_dataset \
  --snapshot-db "$RESTORED_DB" --snapshot-server-uuid "$RESTORED_MYSQL_UUID" \
  --snapshot-id "$BASELINE_SNAPSHOT_ID" --deployment-identity "$CANDIDATE_DIGEST" \
  --output "$EVIDENCE/baseline.json"
```

命令核对实际连接的实例 UUID 和库名，并在同一可重复读的只读事务中计数。仓库、货主、商品、库存明细统计已启用且未软删除的记录。库存流水统计未软删除的明细流水。订单是未软删除的入库单头与出库单头数量之和，文件保留两个分项；POS、商城派生的出库单随出库单头计一次。账期按 `BillingPeriod.start_date` 的自然年月去重，同月不同货主、仓库的账期只计一个月，文件列出每个计入的月份。输出使用新文件路径；任一维度不足时仍保存实测值并以非零状态退出，补足合成数据后重新制作快照。

每个场景在仓库外准备一个仅执行人可读的 JSON。顶层包含 `origin`、`scenario`、`deployment_identity`、`resource_limits`、`synthetic_only: true` 和 `actors`。两个场景使用同一候选、资源限额与合成基线快照。`actors` 放 100 个独立账号，每项有 `role`、`username`、`password`、数字型 `warehouse_id`，其余参数按表提供。账号由预生产的既有身份和业务建档流程发放。所有 ID 均取自新建的合成夹具。

| `role` | 50 人 / 100 人 | 额外字段及预置业务 |
| --- | --- | --- |
| `pda` | 25 / 50 | `task_id`、`line_id`、`to_location_id`。每人专用的普通 PUTAWAY 任务已分配、开始，选择同仓可上架库位。每次登记 1 件，前半数员工每人至少预留 240 件未登记量，后半数至少 30 件。 |
| `owner` | 10 / 20 | `owner_id` 指定授权货主，`order_id` 指向该货主在分配仓库的入库单。 |
| `pos` | 5 / 10 | `product_id` 对应分配仓库在售且现价 10.00 元的商品，收银员已开班。热仓共需至少 1350 件可销售库存，每次现金收 10.00 元、销售 1 件。按既定商品分布预留库存。 |
| `mall` | 5 / 10 | `owner_id` 指向该买家绑定的货主，买家已有仓库、客户绑定及至少一件可售商品的活动购物车。读取时验证仓库、货主及试算结果。 |
| `reports` | 5 / 10 | 授权查看分配仓库的老板库存报表。固定同一业务日期及数据基线。 |

每个岗位按清单顺序取前半数作为 50 人组。跨仓场景在每个岗位交替分配两仓。PDA、POS 的第一人从甲仓开始，商城、报表从乙仓开始，货主均分，这样两个阶段总人数均分。脚本先逐人登录并读取分配的业务对象，使用 PDA 专属令牌端点，普通账号在令牌到期前续期。检测到账号、仓库或单据不符时会退出。在两个场景中使用相同的价格和商品分布。每人每 5 秒计划一次业务请求，角色错峰启动，PDA 和 POS 的第二次、第四次等偶数次操作执行写入。每个场景依次预热 5 分钟、压 50 人 30 分钟和 100 人 5 分钟，两个测量阶段分别排程 18,000 和 6,000 次业务请求。

```sh
.venv/bin/python scripts/wms_capacity.py run --baseline "$BASELINE_JSON" --config "$HOT_ROSTER" --scenario same-warehouse-hotspot \
  --confirm-preproduction "$STAGING_ORIGIN" --output "$EVIDENCE/hot"
# 在同一候选恢复等量的合成基线并确认隔离后，运行跨仓场景
.venv/bin/python scripts/wms_capacity.py run --baseline "$BASELINE_JSON" --config "$CROSS_ROSTER" --scenario cross-warehouse \
  --confirm-preproduction "$STAGING_ORIGIN" --output "$EVIDENCE/cross"
```

输出目录须为新路径。每组生成 `warmup.jsonl`、`50.jsonl`、`100.jsonl` 和 `client.json`，保存每个时间槽、匿名账号序号、实际请求时间、端点、耗时、HTTP 状态、结果及写入幂等键。超时写入按幂等键在候选库核查。中断运行保留已有原始记录，可以用新的输出路径和重置过的合成夹具重新开始。

监控应在预热前启动。按每个阶段的 UTC 时间窗从监控平台导出一个 JSON 对象，字段 `run_id`、`started_at`、`finished_at` 从对应 `client.json` 取，`samples` 为按实测时间的对象数组。每个对象用带时区的 `at` 标记采样时间，另附该时点采到的一个或多个字段。保留监控查询、带时间戳的原始导出与合成交易凭据以供复核。

| 样本字段 | 来源及计算 |
| --- | --- |
| `cpu_percent`、`rss_bytes`、`database_connections`、`backlog` | 以固定副本和限额统计应用总 CPU、总 RSS、MySQL Threads_connected 和任务队列深度，报告窗口峰值。 |
| `lock_waits`、`slow_queries` | 同一 MySQL 实例的 Innodb_row_lock_waits、Slow_queries 单调累计值，使用窗口末值减初值。保留慢查询阈值。 |
| `queue_wait_ms`、`payment_external_ms` | 网关或服务排队、测试支付网关出站调用的逐笔跨度耗时（毫秒），各取 P95。另行在同窗口发起已授权的测试商户合成交易并归档出站 trace。现金结算本身不会提供支付网关的测量。 |

峰值和计数器每 5–10 秒采一次，覆盖阶段起止；超过 30 秒的缺口、计数器重置以及缺少支付或排队样本都会阻止验收。阶段间没有停顿，应提前开启按时间点还原或等效的阶段边界快照。每一阶段结束后，将独立只读实例恢复到对应的 `finished_at`，记录实际的恢复点和快照 ID，配置该恢复库的专用只读连接。运行下列命令可在同一只读事务内核对完整库存、账务及各成交仓的 POS 业务日数据，按本阶段唯一业务键回读 POS 销售小票和 PDA 上架审计记录。保留原始快照凭据和对账校验中的警告。下例对应同仓 50 人阶段，其他三组更换客户端文件、人数和恢复点。

```sh
.venv/bin/python -m scripts.wms_capacity_reconciliation --client "$EVIDENCE/hot/client.json" \
  --users 50 --snapshot-db "$RESTORED_DB" --snapshot-server-uuid "$RESTORED_MYSQL_UUID" \
  --snapshot-id "$SNAPSHOT_ID" --as-of "$PHASE_FINISHED_AT" \
  --output "$EVIDENCE/hot50-checkpoint.json"
```

在归档目录建立 `observations.json`，顶层带 `deployment_identity`、`resource_limits` 及 `runs` 数组。四行各填 `run_id`、`scenario`、`users`、`started_at`、`finished_at`、`telemetry_file`（监控 JSON）、`checkpoint_file`（对应阶段恢复点的上述对账 JSON）。文件路径相对该清单。汇总会重读逐条请求记录，计算服务端指标，从检查明细和 POS 的错误数生成差错计数，再调用既有容量门槛。

```sh
.venv/bin/python scripts/wms_capacity.py assemble --client-dirs "$EVIDENCE/hot" "$EVIDENCE/cross" \
  --observations "$EVIDENCE/observations.json" --output "$EVIDENCE/capacity.json"
.venv/bin/python scripts/validate_external_acceptance.py capacity "$EVIDENCE/capacity.json"
```

## 待验证与业务确认

下表记录尚缺的证据。执行后将候选版本、日期、经办人及脱敏证据位置填入本次验收记录。

| 待现场验证 | 建议经办人 | 取证方式 |
| --- | --- | --- |
| 中文发运单并发打印与扫码 | 运维、仓库 QA | 共享主机曾单次渲染一行中文单据耗时 43.843 秒。在候选机器上先单测扫码，再以相同扫码负载并发打印常规单和达到当前配置行数上限的单据。记录行数、并发数、PDF 耗时、繁忙响应（503 及重试等待时间），比较相同时长内的扫码 P95 和错误率，据此决定优化优先级。[PDF 资源预算](pdf-resource-budget.md) · [容量验收指标](external-acceptance-profile.json) |
| 微信支付回单 | 财务、商城运营 | 用测试商户完成一笔支付和退款，将订单号、交易号、退款单号及金额与商户平台逐笔核对，归档脱敏回单和对账结果。[支付现场验收步骤](sales-miniapp-payment-test-report.md) |
| 签名 APK、真实打印质量 | QA、现场主管 | 记录候选 APK 哈希、签名证书指纹和设备型号。在配发 PDA 上安装并验证原生扫码。在现场打印机上检查中文发运单的字形、分页和裁切，用 PDA 回扫实际打印的商品标签，留存纸样或照片及识别结果。[设备取证模板](release-device-smoke-evidence-template.md) |

| 待业务确认 | 建议确认人 | 待记录的决定与样单 |
| --- | --- | --- |
| 标准客户退货后的财务交接 | 仓库、货主和财务负责人 | 用一笔分批实收的非 POS 退货，确认谁在何时交接实收数量、谁办理退款或账单调整。记录原销售行、退货号、实收数量，以及结清凭证或免结算原因。 |
| 赊销还款核销 | 销售和财务负责人 | 用一笔涉及多张销售单的还款，确认是否逐单核销、由谁分配和复核。留存还款凭证及适用的销售单分配明细或账余额记录。 |
| 承运签收回单 | 发运和承运对接负责人 | 抽一张已签收的发运单，确认谁取得、维护回单，保存在哪个系统，以及如何由发运单查到签收凭证。记录单号、回单号和维护岗位。 |

## 回归与正式发布

迭代默认复用健康的 `wms_db_test`，冻结候选后完整门禁才重建一次。
历史迁移使用独立 MySQL 实例；自动测试绝不写入 `wms_db`。
正式部署需先完成发布审批、备份恢复验证及全部前置门禁，再执行生产发布脚本。
上线后核对镜像身份并观察 60 分钟。技术门禁通过不等于已获部署授权。

### 货主完整 Excel 档案

应用和后台进程需使用同一版本和同一私有目录。生产 Compose 的 `web` 与
`owner-archive-worker` 共用 `wms-private-exports` 卷，挂载到
`/app/private_exports`。该目录只供服务账号读写，文件通过鉴权下载接口提供。
部署候选镜像并加载对应环境后，在现有发布流程的迁移步骤运行：

```sh
docker compose -f compose.production.yml run --rm web python manage.py migrate --noinput
docker compose -f compose.production.yml run --rm web python manage.py sync_wms_role_groups
docker compose -f compose.production.yml up -d web owner-archive-worker
docker compose -f compose.production.yml ps owner-archive-worker
```

开发环境使用原有的 `python manage.py runserver` 即可。服务监听成功后会自动启动导出进程，
接手仍有效的排队任务；开发服务退出时导出进程一同退出。货主只需在报表页点击导出。

常规发布脚本会启动此后台服务。非 Compose 的生产部署用进程管理器常驻运行
`python manage.py run_owner_archives`，并为 Web 和工作进程设置相同的
`OWNER_ARCHIVE_ROOT`。后台进程会接手中断的任务并清理过期文件。
任务生成失败可在「报表与查询 → 导出完整 Excel」重新生成。
