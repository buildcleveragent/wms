# WMS 核心业务只读评估（2026-09-06）

范围：当前 /wms 工作树，含 POS 已有未提交导出变更。重点阅读 inventory、inbound、outbound、tasking、pos、salesapp、billing 的模型、关键服务、测试。未修改仓库代码；未连接或写入任何数据库；数据库复现由主代理统一执行。本报告内“确定”指静态可证代码行为，最终实测状态以主代理执行结果为准。

## 总体判断

业务广度明显超过演示系统：收货、上架、冻结、拣货、复核、包装、发运、盘点、补货、移库、POS 多支付/退货/班次、商城支付退款、仓储计费与核销均有领域模型及测试。核心库存过账和商城支付可靠性有明确防线，但横跨模块的派生库存一致性、POS 还款与售后结算仍存在遗漏。不能仅凭功能数量或既有测试文件判定生产可靠。

## 高置信问题

### 1. P1：订单冻结/释放库存未同步货主“实时库存”汇总

- `/wms/allapp/outbound/services.py:620` 至 626：冻结使用 `InventoryDetail.objects...update()` 修改 allocated/available；该函数到 687 直接返回，没有汇总更新。
- `/wms/allapp/outbound/services.py:1543` 至 1549：释放库存同样只更新 detail，函数到 1594 返回。
- `/wms/allapp/outbound/models.py:354`：正式货主审核调用该冻结路径，没有其他汇总刷新。
- `/wms/allapp/inventory/views.py:34` 至 50：“货主端实时库存”直接查询 InventorySummary；该表本身有 allocated/available 存储字段。
- 全局搜索未发现 InventoryDetail 同步 signal 或数据库 trigger；QuerySet.update 也不会调用 model.save。
- 具体结果：初始 detail/summary 可用均为10，审核冻结3，detail=7，summary仍10。若其他库存过账已把 summary 刷到7，再取消订单，detail=10，summary仍7。核心明细防超卖可继续成立，但不同页面口径及对账不一致。
- 建议：在同一事务、统一锁序内按触及的 owner/product 更新汇总；回归必须跨“分配→其他过账→取消”的状态序列。
- 临时复现：`/tmp/test_wms_business_review.py::ReviewInventorySummaryTests` 前2个方法，主代理执行。

### 2. P1：POS 还款缺少重试幂等及客户余额并发保护

- `/wms/allapp/pos/serializers.py:662` 至 687：创建还款请求没有 idempotency_key，reference_no 只是可选字符串。
- `/wms/allapp/pos/models.py:467` 至 553：仅生成的 repayment_no 唯一；reference_no 非唯一，没有请求唯一键。
- `/wms/allapp/pos/services.py:1277` 至 1308：先普通查询客户，只锁当前收银员班次，然后读取汇总欠款并新建还款。
- `/wms/allapp/pos/shift_services.py:86` 至 95：班次锁以 cashier 为维度，两名收银员会拿到不同锁。还款单号生成在余额校验之后，不能补回客户余额保护。
- 确定的顺序行为：欠款10，同一还款4的 HTTP 请求因网络重试发送两次，会创建两条还款，欠款变2；无稳定请求标识可判定重放。
- 并发风险（需 MySQL 并发实测）：两名收银员都在余额10时验证还款8，之后可能分别成功或出现编号竞争/死锁；当前没有客户级互斥，也没有对这条路径进行锁冲突重试。
- 建议：用户/仓库范围的稳定请求键+载荷指纹；锁客户余额所属记录，再校验与入账；两收银员并发用例。

### 3. P1/P2（取决于财务流程）：还款后的赊销作废缺少结算处理

- `/wms/allapp/pos/services.py:1755` 至 1800：作废检查班次已关闭/已有退货/原因，却未检查还款；随后恢复库存，作废支付明细与销售单。
- `/wms/allapp/pos/services.py:1127` 至 1156：欠款=`COMPLETED销售的CREDIT支付`－`CREDIT退款`－`COMPLETED还款`。销售作废后第一项消失，原还款仍在。
- 明确序列：同班次赊销10，现金还款4，再作废赊销；欠款变-4，作废代码未生成现金退款或还款冲销。
- 反证核查：没有找到 POS 预收款/客户钱包/还款退款 API；还款服务1296–1298明确禁止超过欠款，说明普通还款入口也未按主动预收业务设计。PosCustomerRepayment 有 VOIDED 字段，但没有对应作废/退款服务。
- 客观边界：负数可以是“系统欠客户款”的业务表达，所以不能只因出现负数判为账错；确定缺口是作废未提供这4元的清晰结算路径、单据关联及审计处理。需要业务明确禁止、退款还是转客户余额。

### 4. P2：零价赠品能够售出，但无法单独退回库存

- `/wms/allapp/pos/services.py:303` 至 307 支持零金额赠品；既有测试 `/wms/allapp/pos/tests.py:3244` 明确验证零总额销售及库存扣减。
- `/wms/allapp/pos/serializers.py:550` 至 555 退款最小0.01；579–585 要求退款列表非空。
- `/wms/allapp/pos/services.py:469` 至 498 也要求每条退款>0，合计必须等于退货金额；1666–1667无零金额分支。
- 因此赠品退货金额0没有合法退款输入，不能完成实物归还。低金额分批退货舍入为0也可能触发同类阻断。
- 建议：零应退金额允许空退款明细，同时完整执行退货库存及审计；正金额继续要求退款总额准确。

### 5. P2：标准出库与 POS 的 FEFO 空效期排序不同

- `/wms/allapp/outbound/services.py:532` 至 562，标准分配候选直接 `order_by('expiry_date', '-onhand_qty')`，没有到期过滤或空值后排；在 MySQL 升序排序时 NULL 在前。
- `/wms/allapp/pos/services.py:543` 至 550 则明确 Coalesce NULL 为9999-12-31，日期库存优先；`/wms/allapp/pos/tests.py:3593` 有对应业务测试。
- 标准入口 `/wms/allapp/outbound/models.py:354`→allocate_inventory:598→此查询，未发现额外排序。结果可能先用没有效期的库存，而保留已知较早到期库存。
- 边界：产品是否允许无效期库存取决于商品/历史数据；这一风险优先级低于汇总错位，不能笼统称所有出库错误。应统一空效期规则，并明确是否允许过期库存被分配；此次未把“未过滤过期”直接定成缺陷，业务策略尚待确认。

## 已有可靠设计

- 库存数据库层有维度唯一、序列号唯一、数量非负、可用量恒等式约束：`/wms/allapp/inventory/models.py:122` 至 181。
- 统一过账入口在仓库→任务→PostingJournal锁内检查双重已过账状态、不一致则拒绝；显式任务策略，扫描归属和打点数量校验，失败整体回滚：`/wms/allapp/inventory/services.py:1701` 至 1907。
- 明细写入有稳定顺序、owner/product完整范围锁和汇总锁：`/wms/allapp/inventory/services.py:483` 至 543。库存写锁必须处于事务：`/wms/allapp/inventory/locking.py:56` 至 76；MySQL1205/1213有有限重试和业务错误转换：80–116。
- 正式出库对“货主审核”和“仓库发布”分离；冻结行保留精确 inventory_detail_id；不足库存/已执行任务取消拒绝：`/wms/allapp/outbound/services.py:620`、654、773、1511、1554。
- 无单收货有请求键、载荷哈希及竞争创建恢复：`/wms/allapp/inbound/services.py:396` 至 440。用户、货主、仓库、商品关系先校验。
- POS销售、退货、作废使用事务及仓库锁，销售/退货幂等同时比较载荷指纹；退货使用累计金额分摊使尾笔准确：`/wms/allapp/pos/services.py:946` 至 957、1380–1404、1588–1607。
- 商城支付校验商户号、AppID、币种、金额；迟到/重复到账有唯一全额退款键，保留退款重试与人工处理状态：`/wms/allapp/salesapp/services_salemini_payments.py:120` 至 169、172–207、536–634。预支付网络调用与本地准备/记录事务分离：`/wms/allapp/salesapp/salemini_api.py:3006`、3095、3302。
- 计费有应计/事件/指标/账期/账单/收款核销分离、关账数据质量门、已过账记录禁止修改及冲销：`/wms/allapp/billing/services/integrity.py:47`、`/wms/allapp/billing/services/receivables.py:59` 至 169、`/wms/allapp/billing/models.py:1362` 至 1425。核销金额正值且同收款-账单-方向唯一。

## 结构性风险与测试评价

- 仓库级互斥易理解，也使同仓库所有库存写入串行化；完整 owner/product范围锁可能跨仓库。它是可靠性/吞吐量权衡，不等于性能已不合格。需真实SKU分布和峰值吞吐压测后再决定细化锁，不应凭感觉移除。
- 业务逻辑有重复：标准出库与POS有各自FEFO，inventory与POS各自汇总刷新，模型方法和服务之间双向调用。上面的FEFO及汇总遗漏已体现维护代价。
- 复杂函数存在：inventory `_apply_putaway`371行（services.py:1323）；billing `accrue_order_processing_for_task`499行（services/accrual.py:1658）；tasking `scan_task`302行（services.py:373）。这是变更审查与覆盖难度风险，不是按行数直接判低质量。建议围绕明确不变量提取纯计算/选择器，避免整体重写。
- 静态统计这7域共59个测试文件、685个test_方法：库存59、入库50、出库186、POS101、计费101、商城135、任务53。它不是pytest收集数，不包含参数展开，也不代表覆盖率或执行通过。
- 已存在真正TransactionTestCase并发用例：库存59域内的`test_posting_concurrency.py`、`test_posting_fail_closed.py:934`，出库`tests.py:320`，商城`tests.py:4060`，计费scheduler/settlement `tests.py:3789`、4092；并非只测成功路径。
- 主要遗漏是跨流程组合：库存冻结后查询汇总、还款后作废/退货、赠品售后；已有单功能测试通过也不能证明这些组合正确。
- POS当前未提交导出变更只扩展合并信息sheet和中文文件名，保留默认参数兼容，新增111行测试；未见其触及上述核心库存/支付缺陷，不能把历史问题归咎于这批dirty改动。

## 推荐分层验证

1. 主代理先跑 `/tmp/test_wms_business_review.py` 六个静态发现复现（只用已验证wms_db_test，遵循原conftest）。
2. 最小业务回归：`allapp/outbound/tests.py`，`allapp/inventory/test_posting_concurrency.py`，`allapp/inventory/test_posting_fail_closed.py`；POS只选credit/repayment/return/void/idempotency/FEFO/gift相关方法。
3. 稳定后扩展：`allapp/test_business_flows.py`，`allapp/salesapp/tests.py::SaleMiniPaymentConcurrencyTests`，`allapp/billing/tests.py::BillingSettlementConcurrencyTests`，`allapp/billing/test_p1_receivables.py`，盘点/移库/补货相关模块。
4. 正式发布前执行一次干净MySQL真实迁移链与关键业务并发门。此次评估代理没有执行数据库用例，不能把其列成已通过。
