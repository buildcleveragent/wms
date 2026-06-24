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
