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

本次更新与验收范围为 App、H5 和相关后端接口，小程序暂不验收。
`npm run test:release` 执行前端单元测试、H5 构建与浏览器回归，再构建并校验 Android 原生资源；后端 MySQL 测试单独执行。
Android 原生安装包仍需通过 HBuilderX 签名打包并做真机验证。
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
