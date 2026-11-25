# 发布阻断修复后的运维验收

本文件是执行要求，不是已部署或已通过的证据。未解决的高危、严重漏洞继续阻断发布，
不添加风险放行例外。所有证据绑定冻结源码和最终镜像摘要；安全扫描有效期 24 小时。

## 常驻服务发布与 Boss 心跳

生产入口为 `sh docker/production_release.sh <repository@sha256:digest>`；staging 入口为
`WMS_IMAGE=<repository@sha256:digest> sh docker/staging_release.sh`。两个入口均从仓库根目录
执行，并共用 `docker/release_services.sh` 管理 `web`、`scheduler`、`boss-check-worker`。

预检通过后停止三个服务，再运行迁移。web 恢复、迁移检查和数据核对通过后，才启动两个
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

启动探针最多等待 120 秒。生产观察期持续检查三个容器、web readiness 和 worker 心跳；
迁移、启动或观察失败，三个服务及支付定时器保持停止，命令以非零状态退出。按照已验证
备份和旧镜像恢复数据库、媒体及应用后，重新核对服务数量、镜像、数据和心跳，再恢复
调度；不要仅重启 web，也不要自动回退数据库迁移。

代码内的编排回归使用模拟 Docker/systemd，并不证明真实服务器已成功部署。真实发布
还需保存服务镜像、容器状态、启动时间、探针及数据核对结果。

## 四个运行卷

发布预检以 `10001:10001` 在每个挂载点创建临时目录和文件，读回后删除，并检查已有条目的访问权限。

| 挂载点 | 用途 | 恢复方式 |
|---|---|---|
| `/app/staticfiles` | 公共静态资源 | 使用同一版本的镜像执行 `collectstatic` |
| `/app/media` | 普通上传文件 | 从该恢复点的媒体备份恢复 |
| `/app/private_arrivals` | 到货单照片 | 从该恢复点恢复，按到货单及文件哈希核对 |
| `/app/private_returns` | 退货单照片 | 从该恢复点恢复，按退货单及文件哈希核对 |

任一卷预检失败，发布在停写前退出。先核对 Compose 项目名及容器的实际挂载，定位失败文件。
如设置了 `PRIVATE_ARRIVAL_ROOT` 或 `PRIVATE_RETURN_ROOT`，实际存储路径必须位于各自备份的私有卷内。
已有数据先做可恢复备份。按文件修复运行账号的访问权限，保留私有卷的访问限制，然后重跑预检。
候选镜像可用 `python scripts/check_release_volumes.py --image <候选镜像>` 在全新临时卷验证。

## 生产发布备份和中止恢复

维护窗口包含备份耗时。发布前通知仓库停止提交作业，并排空部署方自建的导入、同步或数据库写入作业。
脚本会检查已停用的旧 Boss 定时任务，暂停两个支付定时器，等待在途支付任务退出，再停止三个常驻服务。
确认 Compose 写服务均已停止后，数据库和附件在同一个停写窗口完成备份。备份记录位于
`WMS_BACKUP_EVIDENCE_DIR`，默认 `/var/lib/wms/release-backups`，发布账号需要写权限。

沿用 `WMS_DB_BACKUP_SCRIPT` 和 `WMS_MEDIA_BACKUP_SCRIPT` 指定的可执行文件。两个脚本均无位置参数。
每个脚本应同步完成备份、验证备份可读取，然后将一份 JSON 回执写入环境变量 `WMS_BACKUP_RECEIPT` 指定的路径。

| 回执字段 | 内容 |
|---|---|
| `recovery_point` | 原样填写本次发布传入的 `WMS_BACKUP_SET_ID` |
| `snapshot_id` | 运维恢复工具能实际定位的备份文件或快照编号。数据库与媒体可以有各自的编号 |
| `includes` | 数据库回执包含 `database`。媒体回执包含 `media`、`private_arrivals`、`private_returns` |

脚本对两份回执核对恢复点和覆盖范围，成功后生成 `manifest.json`。数据库或附件备份失败、回执缺失或
恢复点不匹配会中止迁移，并恢复之前运行的容器和定时器。如果原容器无法恢复，先排查原版本服务。
迁移已经开始时沿用停写恢复流程，按清单中的两份备份和旧镜像恢复。回执只存引用，凭据保留在现有运维配置中。

现场首次接入时，在隔离环境使用生产同款脚本备份并恢复到新实例。逐笔核对到货单、退货单及对应照片，
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
