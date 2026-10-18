# V2.2 手册内容核对记录

核对日期：2026-10-17。采用当前工作区（含未提交修改）的服务端校验、客户端页面和现有测试源码；不表示相同功能已部署。测试文件仅作为行为契约阅读，本次未运行业务测试。

## 新增与修订内容

| 核对主题 | 使用说明变更 | 实现依据 | 已阅读的测试契约 |
|---|---|---|---|
| 共享客户 | 通用档案与货主业务资料分开；货主编号、业务员和 CASH 识别按有效关联；跨货主关联由全局管理员维护 | [客户模型](../../allapp/baseinfo/models.py)、[客户范围](../../allapp/baseinfo/customer_access.py)、[后台表单](../../allapp/baseinfo/admin.py)、[标准开单](../../allapp/outbound/serializers.py) | [客户后台](../../allapp/baseinfo/test_customer_admin.py)、[共享迁移](../../allapp/baseinfo/test_customer_sharing_migration.py) |
| 代办转货后销售 | 原货主、新货主、客户路线；基本单位转货/销售价；预留、一次实物拣货和完整过账；双方证据边界 | [转货服务](../../allapp/outbound/ownership.py)、[只读后台](../../allapp/outbound/transfer_admin.py)、[代办页](../../wmspda/pages/outbound/assisted.vue)、[订单面板](../../wmspda/components/outbound/AssistedOrderPanel.vue) | [转货与失败回滚](../../allapp/outbound/test_ownership_transfer.py)、[请求防重](../../allapp/outbound/test_assisted_idempotency.py)、[客户端转货](../../wmspda/tests/unit/assistedTransfer.test.js) |
| PDA 退货出库 | 选具体库存、三类数量、原供应商来源、确认即扣库、本人整单冲销、原请求恢复及事件日期统计 | [退货页面](../../wmspda/pages/returns/index.vue)、[服务端范围及库存](../../allapp/outbound/return_services.py)、[统计与接口限制](../../allapp/outbound/return_api.py) | [退货业务](../../allapp/outbound/test_pda_returns.py)、[客户端数量](../../wmspda/tests/unit/pdaReturns.test.js)、[浏览器流程](../../wmspda/tests/e2e/pda-returns.spec.js) |
| 商品导入/导出 | 正数数量自动收货及整批回滚；单位预建；全部授权货主商品及包装导出与缺标准条码导出 | [导入器](../../allapp/products/excel_import.py)、[导出范围](../../allapp/products/excel_export.py)、[导入页](../../wmspda/pages/products/import.vue)、[首页入口](../../wmspda/pages/index/index.vue) | [商品导入契约](../../allapp/products/tests.py)、[全部商品导出](../../allapp/products/test_all_export.py)、[导出客户端](../../wmspda/tests/unit/allProductExport.test.js) |
| Android POS | 全仓库存、同码多货主、包装合计库存校验、实际付款后记录销售、销售和退货原请求恢复、零元退货 | [收银页](../../wmsandroid/pages/pos/index.vue)、[销售历史](../../wmsandroid/pages/pos/history.vue)、[扫码及库存工具](../../wmsandroid/utils/posMobile.js)、[POS 商品契约](../../allapp/pos/serializers.py) | [Android 后端契约](../../allapp/pos/test_android_contract.py)、[客户端工具](../../wmsandroid/tests/unit/posMobile.test.js)、[待确认恢复](../../wmsandroid/tests/unit/pendingOperation.test.js) |
| 拣货结果恢复 | Android 保留原扫描，按原账号/仓库/任务恢复，不重复扫实物；不泛化为所有客户端能力 | [Android 拣货页](../../wmsandroid/pages/picking/task_detail.vue)、[扫描回放](../../allapp/tasking/services.py) | [移动拣货恢复](../../allapp/tasking/test_mobile_pick_recovery.py)、[移动恢复场景](../../wmsandroid/tests/e2e/mobile-recovery.spec.js) |
| 老板经营端 | 日期边界、全部授权仓库、原页重试及导出失败；账单下钻；驾驶舱指标和快照独立权限及默认模板 | [日期范围](../../allapp/reports/boss_contract.py)、[驾驶舱](../../wmsbossbilling/pages/cockpit/index.vue)、[范围状态](../../wmsbossbilling/store/bossScope.js)、[货主收费明细](../../wmsbossbilling/pages/billing/owner_detail.vue) | [后端回归](../../allapp/reports/test_boss_regressions.py)、[页面回归](../../wmsbossbilling/e2e/boss-regressions.spec.js)、[账单导航](../../wmsbossbilling/e2e/billing-navigation.spec.js) |
| 商城关联 | 买家当前货主须有有效客户关联，停用后不可凭旧绑定继续购买 | [商城接口](../../allapp/salesapp/salemini_api.py)、[买家后台](../../allapp/salesapp/admin.py) | 静态阅读绑定与权限路径，未执行支付或买家操作 |
| 计费日分摊/收款 | 月面积费先按服务月天数日分摊再最低收费、封顶、税额；正常计费与重计价一致；已过账核销不可改写 | [公共定价](../../allapp/billing/services/_common.py)、[日指标应计](../../allapp/billing/services/accrual.py)、[重计价](../../allapp/billing/services/integrity.py)、[核销模型](../../allapp/billing/models.py)、[写入范围](../../allapp/billing/views.py) | [计费回归](../../allapp/billing/test_billing_regressions.py) |

## 保留流程与边界

- 收货累计量、上架增量、标准出库双重审核、独立复核、移库/补货按开关开放等原有说明继续适用；新增流程不替代原岗位权限。
- 六本角色正文只说明本岗入口及交接点，专项指南提供完整步骤；部署和接口历史资料不作为当次业务验证结果。
- 商品历史标识继续保留占码和包装快照；标准贸易条码为空不等于所有条码不可用。
- 计费重构教程标为历史资料并指向当前技术指南，避免继续把旧文件布局与示例当作当前操作手册。
- 本次不执行生产数据操作、功能开通、迁移或发布；文档静态校验及构建结果见 [文档验收记录](VALIDATION.md)。
