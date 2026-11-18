# V2.3 手册内容核对记录

核对日期：2026-11-13。按当前工作区（含原有未提交修改）的服务端权限、状态校验和已注册页面编写五类岗位手册，不表示这些功能已部署或完成业务实测。

## 五类岗位的内容分工

| 手册 | 本次主要补充 | 实现依据 |
|---|---|---|
| 货主端管理员 | 岗位入口与交接、到货审核、客户退货审批、实际收发记录、预警与来源保护 | [角色模板](../../allapp/accounts/roles.py)、[货主菜单](../../wmsownersale/utils/ownerAccess.js)、[到货动作](../../allapp/inbound/arrival_api.py)、[客户退货动作](../../allapp/inbound/customer_return_api.py)、[历史收发](../../allapp/inventory/owner_history_views.py) |
| 货主端业务员 | 开单交接、入库申请、原销售单退货申请、只读预警及履约口径 | [入库申请入口](../../wmsownersale/pages/inbound/index.vue)、[共享到货表单](../../frontend/shared/arrivals/Workspace.vue)、[共享客户退货表单](../../frontend/shared/customer-returns/Workspace.vue)、[货主菜单](../../wmsownersale/utils/ownerAccess.js) |
| 仓库端操作员 | 分批验收与传统累计收货的区别、客户退货验收、实盘总量及复盘、库存查询、打印单位 | [PDA 首页](../../wmspda/pages/index/index.vue)、[到货表单](../../frontend/shared/arrivals/Workspace.vue)、[客户退货表单](../../frontend/shared/customer-returns/Workspace.vue)、[盘点详情](../../wmspda/pages/inventory/stocktake/detail.vue)、[库存查询](../../wmspda/pages/inventory/company.vue) |
| 仓库管理员 | 分批验收／上架独立审核、客户退货接收、盘点范围与冻结、复盘／差异审核／过账与失败恢复 | [到货服务](../../allapp/inbound/arrival_services.py)、[盘点创建](../../wmspda/pages/inventory/stocktake/create.vue)、[盘点动作](../../allapp/tasking/counting.py)、[盘点范围](../../allapp/tasking/count_options.py)、[盲盘策略](../../allapp/tasking/count_policy.py) |
| 系统管理员 | 五类开通对照、到货／退货／盘点的权限检查、来源绑定保护与打印快照边界 | [角色模板](../../allapp/accounts/roles.py)、[转货保护](../../allapp/products/transfer_protection.py)、[来源绑定](../../allapp/products/models.py)、[打印快照](../../allapp/outbound/print_units.py) |

## 核对重点

- 货主端管理员仍使用 `WMS::货主管理员`，业务员与仓库端操作员同理；没有新建角色或改变业务权限。
- 五类主岗位单独发放；仓库老板扩展指南保留，不作为仓库管理员的替代手册。
- 分批到货填本批数量，传统普通收货填累计量，上架填本次增量，盘点填该行实盘总量；明确四种数量口径。
- 分批验收／上架审核不能自审；全拒收没有库存和上架影响；验收批准、库存入账及上架结束分开确认。
- 客户退货入库、PDA 向外退货和 POS 退货分别使用对应流程。专用客户退货原领取人可重试未入账批次，不推广为所有任务的操作员过账权限。
- 盘点草稿不冻结，发布重新取最新库存并冻结；范围超限或冲突拒绝发布。盲盘隐藏操作员账面量及差异，差异可触发复盘；无差异可能自动闭环，有差异审核后过账，失败保留冻结，驳回释放冻结。
- 管理员出入库历史按实际入账，业务员履约查询保留计划／发运口径；不同单位分开核对。
- 转货以明确来源绑定为准，历史绑定不因取消或停用解除；接收身份和已有包装计量受保护，经营价格独立。旧单缺少可靠包装比例时不使用当前箱规补猜。

以上为静态代码核对。专项指南正文及老板扩展正文沿用上版；配套版本、共用状态说明和生成文件同步更新。本次不复述历史业务测试结果，不将其他任务的验收记录作为本次已执行结果。实际文档检查及未执行项目见 [文档验收记录](VALIDATION.md)。
