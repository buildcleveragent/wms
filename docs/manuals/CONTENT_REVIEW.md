# V2.1 手册内容核对记录

核对日期：2026-09-07。基线为本次工作区代码、已注册页面、权限模板和现有测试源码，包含未提交改动；不据此推断生产环境已经部署或启用相同能力。

本记录供维护人员复核，岗位手册正文优先说明入口、步骤、结果及异常交接。这里列出的测试仅作为行为契约阅读，没有在本次文档更新中运行；未连接业务数据库、未执行数据库测试、未重建或复用测试数据库，也未进行真机、扫描头、外部 GS1 服务或打印机实测。文档构建与静态校验结果由交付流程单独报告。

## 角色与现场作业

| 核对点 | 文档处理 | 当前代码证据 | 已阅读的测试契约 |
|---|---|---|---|
| 规范角色已包含移库/补货相关职责，但开关默认关闭 | 更新角色矩阵、系统管理员及仓库两册；区分申请、管理、PDA 执行和整容器启用条件 | [角色模板](../../allapp/accounts/roles.py#L19)、[Profile 能力](../../wmsmaster/views.py#L97)、[默认开关](../../wmsmaster/settings.py#L102) | [关闭功能与无范围账号拒绝](../../allapp/tasking/test_movement_api_acceptance.py#L194)、[领取/开始的角色范围](../../allapp/tasking/test_movement_api_acceptance.py#L164) |
| 普通商品有订单收货提交累计快照；破损/拒收和差异结束须填写原因 | 补累计示例、保存与结束差异行的按钮区别；不描述为多次数量增量入账 | [收货页面数量和结束行](../../wmspda/pages/inbound/receive_task_detail.vue#L78)、[后端保存累计量及差异控制](../../allapp/inbound/views.py#L579)、[原因校验](../../allapp/inbound/serializers.py#L583) | 以当前页面和保存逻辑静态核对，未运行收货测试 |
| 后端序列号收货存在，当前 PDA 收货页没有序列号控件 | 明确该页面限制，要求安排已验证的入口；不指导关闭序列号控制 | [后端逐件序列号要求](../../allapp/inbound/views.py#L612)、[当前收货页完整表单](../../wmspda/pages/inbound/receive_task_detail.vue#L82) | 不以接口校验替代客户端可操作性验证 |
| 上架按本次增量，可分次完成，目标不能与来源相同 | 补 10 件计划、已上架 4 件、本次 3 件的例子及原请求重试 | [上架输入说明](../../wmspda/pages/inbound/putaway_task_detail.vue#L99)、[数量和目标校验](../../wmspda/pages/inbound/putaway_task_detail.vue#L277) | [领取/开始/分次完成](../../allapp/inbound/test_putaway_comprehensive.py#L277)、[重试及目标锁定](../../allapp/inbound/test_putaway_comprehensive.py#L346)、[全部关联上架过账后关闭入库单](../../allapp/inbound/test_putaway_comprehensive.py#L648) |
| 移库申请、管理员直接发布、任务执行和异常处理均有页面 | 增加申请至过账的步骤，区别库存层移动与整容器；管理员管理、操作员领取扫码 | [申请和管理页](../../wmspda/pages/inventory/move/request.vue#L2)、[任务页面与扫描](../../wmspda/pages/inventory/move/detail.vue#L5)、[服务端角色限制](../../allapp/tasking/views_relocation.py#L72)、[同位置/容器校验](../../allapp/tasking/relocation.py#L339) | [库存层审批和过账](../../allapp/tasking/test_relocation.py#L90)、[容器层级移动](../../allapp/tasking/test_relocation.py#L214)、[异常恢复和作废](../../allapp/tasking/test_movement_api_acceptance.py#L267) |
| 手工要货、补货策略和现场补货是不同环节 | 增加申请、审核、领取、扫描和过账核对；自动来源按独立开关说明 | [要货页面](../../wmspda/pages/replenishment/request.vue#L3)、[补货执行页](../../wmspda/pages/inventory/replenish/detail.vue#L10)、[来源库存及在途数量](../../allapp/tasking/replenishment.py#L96)、[管理员审批](../../allapp/tasking/views_replenishment.py#L197) | [库存不足申请保持待审核](../../allapp/tasking/test_replenishment.py#L137)、[补货幂等和成对库存移动](../../allapp/tasking/test_replenishment.py#L157) |
| 移库/补货分次扫描成功不等于库存已过账；网络丢失结果要保留原内容 | 更新公共状态、异常处理和岗位交接；不承诺离开页面后仍保留原扫描请求 | [移库原扫描保护](../../wmspda/pages/inventory/move/detail.vue#L35)、[补货原扫描保护](../../wmspda/pages/inventory/replenish/detail.vue#L89)、[完成任务的原领取人访问](../../allapp/tasking/views_relocation.py#L389) | [4+4+2 累计完成后只过账一次](../../allapp/tasking/test_movement_api_acceptance.py#L115)、[同请求变内容冲突](../../allapp/tasking/test_movement_api_acceptance.py#L156)、[扫描已保存但过账失败及管理员重试](../../allapp/tasking/test_movement_api_acceptance.py#L225) |

## 商品、登录、打印与财务

| 核对点 | 文档处理 | 当前代码证据 | 已阅读的测试契约 |
|---|---|---|---|
| 商品标识历史已在旧手册中描述，不能当作全新能力 | 保留唯一命名空间、主码、退役仍占码和包装快照；补新旧箱码维护例子 | [条码维护后台动作](../../allapp/products/admin.py#L841)、[标识工作簿操作](../../allapp/products/identifier_excel.py#L164) | 本次按既有说明与维护实现核对，未执行标识数据库测试 |
| 无订单收货可通过 GS1 辅助建档，建档不等于增加库存 | 补本地优先、人工核对资料、分类/单位/批次/效期、加入清单及正式提交步骤；权限使用无订单收货及货主授权范围，不误写成商品 Excel 导入权限 | [GS1 权限及货主范围](../../allapp/inbound/gs1_services.py#L26)、[候选表单](../../wmspda/components/Gs1QuickCreateModal.vue#L1)、[查询及加入清单](../../wmspda/pages/products/search.vue#L584) | [建档并返回购物车项](../../allapp/inbound/test_gs1_quick_create.py#L86)、[追溯字段及范围](../../allapp/inbound/test_gs1_quick_create.py#L198) |
| 登录恢复、账号切换及 Web 登录限流 | 增加重新核对范围、临时网络故障与登录失效的区别、按提示等待；不给固定会话时长承诺 | [PDA 请求封装](../../wmspda/utils/request.js)、[货主端请求封装](../../wmsownersale/utils/request.js)、[Web 登录限流](../../allapp/accounts/login_middleware.py#L11) | [账号切换](../../wmspda/tests/unit/accountSwitch.test.js)、[不同 Web 入口共享限流](../../allapp/accounts/test_session_login_throttle.py#L20)、[密码修改撤销既有登录](../../allapp/accounts/tests.py#L374) |
| 打印凭据短期有效；结账成功但打印失败不应重结账 | 公共异常流程说明从原单补打、过期重开、核对实际出纸；设备支持仍以实际部署为准 | [打印凭据范围和过期处理](../../allapp/core/print_tickets.py#L12)、[收货单打印](../../wmspda/utils/receiveTaskDocument.js#L3)、[出库打印窗口提示](../../wmspda/utils/outboundPrint.js#L32)、[POS 打印失败提示](../../wmspda/pages/pos/index.vue#L2392) | [客户端打印凭据契约](../../wmspda/tests/unit/printTickets.test.js)，未实测打印机 |
| POS 独立客户、赊账还款、退货和班次不得与仓储计费混用 | 各角色只补权限边界和交接点，完整收银操作见 [POS 使用手册](../pos_user_manual.md) | [POS 权限](../../allapp/pos/views.py#L78)、[POS 仓库授权](../../allapp/pos/access.py#L6)、[还款及原请求恢复](../../wmspda/pages/pos/index.vue#L3204)、[还款持久化](../../wmspda/utils/repaymentRecovery.js#L7) | [还款客户端恢复](../../wmspda/tests/unit/repaymentRecovery.test.js)，未执行收款、退款或数据库测试 |
| 货主账单只展示费用行；老板端可继续下钻收费记录 | 两册分别写真实入口；补整张账单金额与筛选小计、未出账不等于无费用 | [货主当前账单按钮](../../wmsownersale/pages/billing/overview.vue#L171)、[货主费用行展示](../../wmsownersale/pages/billing/bill_detail.vue#L103)、[老板账单行下钻](../../wmsbossbilling/pages/billing/bill_detail.vue#L101) | 仅核对当前页面结构和调用，无账单业务操作 |
| 老板首页按原币、单位及不同时间口径展示，收入不含成本 | 更新今日实绩、所选区间应计、草稿不计已出账、旧数据提示及不能直接当利润/实收的解释 | [首页实际指标与原币说明](../../wmsbossbilling/pages/home/index.vue#L46)、[首页数据状态](../../wmsbossbilling/pages/home/index.vue#L42) | 仅静态核对，不以页面代码证明生产数据准确或已刷新 |

## 维护与验证边界

- 本次角色正文未加入新的业务截图或伪造实机画面。现有截图与入口示意图由统一构建流程核对，不作为功能已启用或外部设备已验收的证据。
- 角色手册的 PDA 步骤以 `wmspda` 当前页面为核对入口；不把其页面核对等同于 `wmsandroid` 安装包、不同设备扫描头或浏览器打印的真机验收，也未增加独立 Android 分册。
- 角色源片段会同时组合到分册与合订版，正文使用专项手册书名；技术证据的相对文件链接集中在本文件及 [角色矩阵](ROLE_MATRIX.md)，避免不同输出目录造成链接失效。
- 仍需在实际部署环境确认功能开关、账号权限、真机网络、扫描设备和打印机；本次只更新使用文档，不为功能开通、生产数据操作或业务验收背书。
