# wmsbossbilling

独立的 uni-app 项目，只保留仓库老板查看计费看板相关功能。

页面范围：

- `pages/login`
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

这个项目不依赖 `wmsownersale` 的业务页，只复用同一套后端接口。
# API 与生产构建

H5 默认使用同源 `/api/...`。App 与小程序构建必须注入
`VITE_API_BASE_URL=https://your-api.example.com`；生产构建发现缺失地址或明文 HTTP
会直接失败。本机开发仅允许 `http://127.0.0.1` 或 `http://localhost`。

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
