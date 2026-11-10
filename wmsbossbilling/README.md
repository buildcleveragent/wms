# wmsbossbilling

独立的 uni-app 项目，提供仓库老板的经营、库存、计费和预警查询。

使用说明见 [仓库老板经营看板手册](../docs/manuals/金桥融通WMS仓库老板经营看板手册.md)（V2.2，2026-10-17 工作区基线），包含筛选恢复、账单下钻及经营驾驶舱七个标签的口径。

页面范围：

- `pages/login`
- `pages/home/index`
- `pages/inventory/index`
- `pages/inventory/detail`
- `pages/alerts/index`
- `pages/alerts/section`
- `pages/alerts/detail`
- `pages/cockpit/index`
- `pages/billing/overview`
- `pages/billing/owner_detail`
- `pages/billing/accrual_detail`
- `pages/billing/bill_detail`

接口范围：

- `/api/token/`
- `/api/auth/profile/`
- `/api/billing/dashboard/warehouse-overview/`
- `/api/billing/periods/`
- `/api/billing/bills/`
- `/api/billing/accruals/`

经营、库存及预警还使用 `/api/reports/boss/` 下的接口；以客户端请求定义和后端路由为准。

这个项目不依赖 `wmsownersale` 的业务页，只复用同一套后端接口。
# API 与生产构建

H5 默认使用同源 `/api/...`。App 与小程序必须设置 `VITE_API_BASE_URL`（只填写服务源地址，不追加 `/api`）。
当前临时使用 HTTP 时，显式设置 `VITE_ALLOW_HTTP_API=true`；关闭开关后恢复 HTTPS 要求。
Android 构建读取 `config/android/.env.production`，开发读取同目录 `.env.development`，可从 `.env.example` 复制。
现有本地 Android 配置已开启 HTTP。执行 `npm run build:app` 会同步生成当前 API 主机的 Android 网络许可配置；
其清单与资源接入方式参见 [DCloud 文档](https://uniapp.dcloud.io/tutorial/app-sec-android#未配置网络安全属性漏洞)。
H5 和小程序读取项目根目录环境文件；shell 中的同名变量优先。

HBuilder 运行 H5 时，浏览器仍请求同源 `/api/...`。开发模式通过根目录
`.env.development` 的 `WMS_DEV_API_TARGET` 将这些请求代理到 Django，避免请求落到
HBuilder 的前端端口并返回 404。修改该配置后需要停止并重新运行 H5。

本次更新与验收范围为 App、H5 和相关后端接口，小程序暂不验收。
`npm run test:release` 执行前端单元测试、H5 构建与浏览器回归，再构建并校验 Android 原生资源；后端 MySQL 测试单独执行。
Android 原生安装包仍需通过 HBuilderX 签名打包并做真机验证。
覆盖旧版的版本预检、云端证书复用和真机验收步骤见
[Android 内部测试包说明](docs/android-internal-release.md)。
以后切回 HTTPS 时，修改服务地址并设 `VITE_ALLOW_HTTP_API=false`，重新构建即可撤销 Android 的 HTTP 主机许可。

## P1 经营驾驶舱

`pages/cockpit/index` 使用与首页相同的全局仓库、货主和日期范围，提供收入保障、原币应收、运营履约、订单级 OTIF、资源收益、目标预测、FIFO 库存风险、预警闭环和经营例会快照。

后台调度需增加：

- `python manage.py sync_boss_alert_cases`：同步动态预警案件；
- `python manage.py capture_task_state_snapshot`：每日保存任务积压状态；
- `python manage.py inventory_rebuild_fifo_layers`：默认预览 FIFO 期初重建，确认后加 `--commit`。

FIFO 上线必须先在全仓范围执行预览与 `--commit`，确认成本层数量按基本单位与
`InventoryDetail` 对平后，才可设置 `INVENTORY_FIFO_ENABLED=True`。启用后收货、
出库、上架、补货、移库和调整与库存事务在同一事务中双写成本层；任一侧失败会
整体回滚。

老板端不包含收款登记、催收修改、预警处置或目标维护按钮；这些写接口均要求独立权限。

## 商品资料与统计口径

- 库存、临期/呆滞和 FIFO 商品描述采用当前商品档案，显示通用品名、品牌型号和规格；商品 ID、编码和库存关联保持不变。
- 运营计划单据的分页明细及导出读取单据快照，改名不会覆盖旧单描述。库存日快照保存历史数量与单位，不保存历史商品名称。
- 收入、资源收益、期间比较和预测采用统一财务账本，包含有效负数冲销；流程待办统计仍按各自状态计算。
- 首页、逾期预警和驾驶舱的未收余额按所选截止日的有效净收款计算。账单总额与未收余额分别显示，币种不混算。
- 首页收发卡片及趋势按所选区间的单量显示，不把不同基本单位的数量直接相加。
- 空值、非法数字和不可用指标不显示为零；历史单位未知或仅由当前档案推断时，不计入可信单位汇总。

此版本依赖商品资料改造的五个迁移及历史单据快照补录，发布顺序见
[商品资料上线说明](../docs/product-identity-rollout.md)。应先完成后端结构与兼容接口更新，
确认所有服务实例更新后补录快照；核对完成后才能整理商品名称并更新客户端。
已有单据快照和例会冻结快照不重新生成。

## 首页重要动态与明细

首页筛选下方的“今日重要动态”使用今天的业务时间，只共享仓库、货主筛选，不受经营汇总历史日期影响。首页最多 10 条；“业务活动”页支持日期、类型、经办人编号、单号查询，每次加载 20 条。点经办人可直接筛选。

- 普通出库只展示成功正式提交；草稿不展示，提交不表示已经扣库存。
- POS 结账、部分退货、作废分别展示一条主动态，关联的任务和流水收进详情；兼容旧版直接扣库存和任务过账。多货主查询只展示可见商品及其金额，无法可靠拆分的整单收退款不展示。
- 库存只展示已过账流水，按商品与单位分别列示；移位显示配对移出、移入。当前同库位批次库存与本次增减分开，不推算历史余额；缺失历史日志会提示。
- 全部活动还包括任务领取、状态变化及计费事件；扫码记录在任务详情中分页。运营、应收、收入保障、FIFO 风险、异常卡片提供明细入口。
- 首页账号菜单提供“刷新与检查设置”，页面自动刷新和后台自动检查均默认关闭，间隔支持 1～1440 整数分钟。页面设置按设备和账号保存，后台设置仅超级用户可修改并全系统生效。关闭页面自动刷新后仍首次加载、响应筛选和手动刷新；开启后前台按所选间隔查询活动及详情，隐藏时暂停、恢复后立即刷新，失败按所选间隔的 2 倍、4 倍退避。阅读历史及详情时提示“有更新”，点击后才替换内容。部署后台常驻进程及状态说明见 `docs/boss-focus-and-periods.md`。
- SLA 显示当前授权和日期范围内事实记录的最近加工时间；履约周期未保存加工时间时显示未知，不借用其他日期的 ETL 完成时间。接口查询时间不代表报表加工时间。超时与计费异常由 `boss_run_checks` 按全局开关和分钟间隔检查；页面轮询只读取结果。

新增只读接口：

- `GET /api/reports/boss/activities/`：`date_from/date_to`、`warehouse/owner`、`priority=important|all`、`category`、`actor`、`search`、`limit`（默认 20，上限 100）、`cursor`。
- `GET /api/reports/boss/activities/{kind}/{id}/`：各区块使用 `{section}_page` 和 `page_size`；返回安全投影及历史完整性提示。
- `GET /api/reports/boss/tasks/`：`mode=created|completed|backlog`、`task_type`、`actor`、`search`、范围及分页参数。
- `GET /api/reports/boss/tasks/{id}/`：任务商品、作业历史、扫码、领取和真实过账流水。
- 收入保障 section 接口增加 `details=1` 进入真实分页；关账阻断按账期、阻断类型下钻。原有响应保持兼容。催收历史及异常历史传 `page` 后使用数据库分页。

活动标识为来源类型与记录 ID 的组合；签名游标绑定用户、授权范围及筛选，连续翻页不依赖偏移量。查询不写动态。提交入口补记的日志与业务提交同事务；本次不新增表或迁移，也不补造旧记录。

发布顺序：先部署兼容后端接口，再发布 H5，最后使用现有安装身份和证书打包 Android。保持既有 HTTP 例外和 FRP 链路。`build:app` 只生成 App 资源，签名 APK、远程浏览器生产访问、Wi-Fi／移动网络及 Android 真机仍需部署后验收。

针对性验证：在仓库根目录使用 `.env.test.local` 的 MySQL `wms_db_test`，运行 `APP_ENV=test .venv/bin/pytest --reuse-db allapp/reports/test_boss_activity.py`；前端目录运行 `npm run test:unit`、`npm run build:h5`、`npm run test:e2e:built`、`npm run build:app`。不要为本次无迁移更新重建业务库或测试库。

重点关注、离开期间摘要、超时检查部署及不限天数周期统计见 [Boss 重点关注与周期统计](docs/boss-focus-and-periods.md)。本次不包含主动推送。
