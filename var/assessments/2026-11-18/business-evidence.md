# WMS 业务链与历史整改核对

2026-11-18，工作区基线 `836ce7c`。本分项通过路由、模型、服务、岗位手册和既有测试断言核对业务范围，并对传统 PDA 的 POS 退货做了两次离线契约探针。本分项没有连接数据库。全报告应以同一轮主报告中的运行记录判断 MySQL、浏览器和发布验证状态。

## 岗位、入口与业务链

| 使用者与入口 | 已定位的主要业务链 | 判断落点 |
| --- | --- | --- |
| 货主业务员、货主管理员，货主端 | 指定授权仓库与客户、开单提交、货主审核冻结库存、退回或撤销、到货预报、客户退货、履约与账单查询 | 保留货主、仓库、客户关系、原创建人和单价口径。货主审核与仓库确认分开。 |
| 仓库操作员，PDA / Android 的对应菜单 | 扫码与包装换算、无单收货、分批验收、上架、拣选复核、盘点、代办出库、退货出库与冲销 | 检查合格/破损/拒收、重复扫码、不同操作员复核、过账失败重试和原维度还原。不同客户端的同名菜单须逐端验证。 |
| 仓库管理员，Web Console / Admin、已开放的 PDA 管理页 | 仓库确认并发布任务、盘点与调整、差异审核、移库、补货和异常恢复 | 当前代码中七项移库/补货开关默认关闭。运行现场开关、角色和实际授权决定可用范围。 |
| 收银员及 POS 管理岗位，PDA / Android | 开班、收银、跨货主成单、赊销、客户级还款、部分退货、作废、交班、打印和对账 | 分别核对实物数量、资金方式、同一请求的流水数和班次对账。 |
| 仓库老板、财务角色，老板端 / Web | 经营与预警、库存与库容、计费事件/快照、应计、锁账、出账、收款分配与冲销 | 将经营指标下钻到业务来源，将仓储服务费和商品货款分别核对。 |
| 商城买家，微信小程序 | 商品与购物车、下单、支付回调与对账、取消、退款及异常重试 | 测试回调可以确认应用的签名或状态逻辑。商户真实回款、退款回单需有对应外部凭证。 |
| 系统管理员，Web Admin | 货主、仓库、库位、商品标识与包装、客户关系、角色与权限、审计 | 核对角色、动作和数据范围三者组合。拥有附加 POS 或财务权限会改变可见入口。 |
| 仓库发运与承运交接 | 仓内发运有任务、数量与异常进度；司机域有任务、站点和预签收数据模型 | 仓库发运与承运签收的责任边界见下方改进项。 |

岗位边界来自 [角色模板](/wms/allapp/accounts/roles.py:18)、[角色范围](/wms/allapp/accounts/models.py:60)、[岗位矩阵](/wms/docs/manuals/ROLE_MATRIX.md:7)、[功能开关](/wms/wmsmaster/settings.py:103) 和 [项目路由](/wms/wmsmaster/urls.py:25)。已定位 12 个编号的跨模块流程，加上可选定价用例。手工烟测清单只列其中前 8 个。当前代码还另有客户退货、盘点、跨货主转货与销售、POS 并发和资金核销专项测试。[业务测试](/wms/allapp/test_business_flows.py:822) · [人工清单](/wms/docs/business-flow-smoke-checklist.md:3) · [收款核销](/wms/allapp/billing/test_p1_receivables.py:56)

## 优先改进与验收

| 优先级与事项 | 当前证据与业务影响 | 改进与可观察验收 |
| --- | --- | --- |
| P1　传统 PDA 部分退货的重复确认 | 真实提交函数在每次点击确认时用当前时间重新生成请求键。模拟首次没有回执，两次相同业务内容生成不同键。服务端按键去重且允许同一销售行分次退货；若首笔已过账且仍有足够可退数量，第二笔可能形成重复部分退货与退款。后续 MySQL 合成订单已观察到两张退货单，记录 18 元退款并回补两件，见[集成记录](/wms/var/assessments/2026-11-18/backend-evidence.json)。[PDA 提交](/wms/wmspda/pages/pos/index.vue:2974) · [部分退货数量校验](/wms/allapp/pos/services.py:1725) · [现有部分退货用例](/wms/allapp/pos/tests.py:2899) | 参照 [Android 已有的待确认状态](/wms/wmsandroid/pages/pos/history.vue:204)，在发出前保存请求及键，未取得确定结果时继续查询或复用原请求。用一张可退两件的订单申请退一件，模拟服务端已提交、客户端丢回执及重开页面，验收仅一笔退货、一条对应退款、一次库存恢复，同时仍可另发起真实第二次退货。 |
| P2　传统 PDA 赠品不能单独退货 | 该端总是传一条退款明细，金额 0.00 被 DRF 最低 0.01 的校验拒绝。实际运行当前提交函数并将其退款形状交给当前序列化器已确认。后端与 Android 已支持零价退货。[PDA](/wms/wmspda/pages/pos/index.vue:2979) · [退款输入](/wms/allapp/pos/serializers.py:600) · [Android](/wms/wmsandroid/pages/pos/history.vue:199) | 零应退金额发送空退款数组。两端分别验收赠品单独退、赠品加有价商品混退及重复提交，核对库存恢复和资金总额。 |
| P2　客户销售退货的财务交接 | 货主端标准客户退货可回到原销售行，按实收良品/破损/拒收登记库存；退货状态表示实物处理进度。手册明确退款、核销及账单调整需另行办理。外部财务如何接单尚无现场证据。[退货模型](/wms/allapp/inbound/customer_return_models.py:35) · [操作手册](/wms/docs/manuals/src/owner_manager.md:75) | 先让货主、仓管和财务确认谁负责哪类售后。对需要跟踪的渠道，提供待财务处理清单或外部凭证回填，按退货号和分批验收行区分免退款、待处理与已结清。抽查一笔分批退货，能追到对应结算凭证或明确免结算原因。 |
| P2　按真实工作量安排日计费 | 默认调度遍历日期、全部货主、全部仓库的乘积，然后为每个组合调用作业逻辑并在内存保留结果。这是确定的执行规模，尚无生产超时或漏计费证据。[计费调度](/wms/allapp/billing/services/metrics.py:513) · [循环与结果收集](/wms/allapp/billing/services/metrics.py:542) | 先统计有业务的组合比例、日结时长、作业记录和锁等待。若主要成本来自空组合，依历史库存、快照、合同、事件及人工指标选取应计费范围。并行对照现有方式逐货主逐仓逐日核对金额，包含解除绑定但仍有历史费用的组合。 |
| 条件优先　发运至承运签收的交接 | 标准订单进度包含短发异常和部分/全部发运。独立司机域有任务与站点/预签收模型；其 views、serializers、admin、urls 四个文件均为零字节，总路由无司机模块挂载。仓外可能有独立运输系统，本次无相应现场证据。[发运进度](/wms/allapp/outbound/progress.py:464) · [配送任务](/wms/allapp/driverapp/models.py:65) · [预签收](/wms/allapp/driverapp/models.py:185) | 仓库承诺止于交给承运商时，明确运单与交接凭证来源；若客户合同以签收闭环，则从承运系统接收交接、签收和异常凭证，允许从出库单追溯。用分批发运及一笔拒收验收部分签收、拒收和发运三个可区分结果。 |

### 离线探针结果

Node 的 VM 直接加载本工作区 `confirmReturnSale` 函数体，使用合成销售单 42、销售行 2、退货数量 1.000，替代确认对话框和网络发送器。两次确认的金额均为 10.00，发送键分别为 `42-100` 和 `42-101`。改为赠品后捕获一行金额为 `0.00` 的 CASH 退款。独立 Python 进程禁止 socket 连接，调用真实 DRF 序列化器，单行零值结果为 `valid=False / min_value`，相同单据的空退款数组为 `valid=True`，领域归一化返回空数组。两项离线检查成功退出。这些结果验证请求契约。数据库流水、现场打印和实际退款以集成或现场证据为准。

## 9 月业务与权限问题逐项对照

9 月综合评估基线为 `8e46144`，同日另一份整改报告的起始基线为 `c9175bf`，其中部分结果是多个候选的复验组合。以下为本轮源码与现有回归定位。表中的“有回归”仅表示找到断言；本轮实际执行结果见主报告。[9 月原始评估](/wms/var/assessments/2026-09-06/wms-assessment-2026-09-06.md:9) · [9 月整改范围及证据组合](/wms/docs/remediation-execution-2026-09-06.md:23)

| 历史项目 | 当前实现及回归定位 |
| --- | --- |
| B1　冻结、取消释放后货主汇总滞后 | 冻结和释放都刷新相关货主与商品汇总。有冻结、过账介入后释放的回归。[冻结](/wms/allapp/outbound/services.py:801) · [释放](/wms/allapp/outbound/services.py:1736) · [回归](/wms/allapp/pos/test_cross_flow_acceptance.py:55) |
| B2　POS 同一还款重复记账及并发超收 | 事务内锁仓库、客户行；稳定请求键与载荷指纹处理重试，历史凭证续传也有防重复校验。有双收银员用例。[服务](/wms/allapp/pos/services.py:1294) · [旧凭证/重放回归](/wms/allapp/pos/test_cross_flow_acceptance.py:140) · [双收银员](/wms/allapp/pos/test_repayment_concurrency.py:34) |
| B3　已收还款后直接作废赊销 | 当前阻止直接作废，验证余额保留。现有还款按客户汇总，因此只要同仓该客户存在已完成还款，其其他未结赊销单也会触发阻止。若门店常遇到误开新单，下一步可按销售单配款，覆盖老单已还、新单未还及部分核销。[服务](/wms/allapp/pos/services.py:1865) · [回归](/wms/allapp/pos/test_cross_flow_acceptance.py:151) |
| B4　零价赠品退货 | 后端在零金额接受空退款数组，有回归；Android 采用匹配形状。传统 PDA 仍存在入口契约缺陷，见本轮探针。[后端](/wms/allapp/pos/services.py:479) · [回归](/wms/allapp/pos/test_cross_flow_acceptance.py:209) |
| B5　标准出库与 POS 对空效期的排序 | 标准出库显式使用空效期置后，POS 将空效期映射到最大日期。有标准出库对照回归。[标准出库](/wms/allapp/outbound/services.py:652) · [POS](/wms/allapp/pos/services.py:555) · [回归](/wms/allapp/pos/test_cross_flow_acceptance.py:85) |
| POS 商品目录的角色与仓库范围 | 目录要求 POS 能力并限定仓库，缺货候选限定仓库授权货主。授权收银员可在业务仓查看多货主库存是现行语义。有买家拒绝与授权收银员正例。[服务](/wms/allapp/pos/views.py:151) · [回归](/wms/allapp/core/test_security_acceptance.py:56) |
| 任务行和指派后台范围 | 两个后台类采用统一子对象范围控制，存在列表、外键和对象回归。[任务行](/wms/allapp/tasking/admin.py:1810) · [指派](/wms/allapp/tasking/admin.py:2367) · [回归](/wms/allapp/core/test_security_acceptance.py:200) |
| 委派用户管理员重置超级管理员密码 | 当前按目标对象保护超级管理员，另有正常委派账户的正例。[对象权限](/wms/allapp/accounts/admin.py:455) · [回归](/wms/allapp/core/test_security_acceptance.py:104) |
| 打印改密撤销 | 票据校验密码版本、当前账户状态、文档路径和时效。[服务](/wms/allapp/core/print_tickets.py:42) · [回归](/wms/allapp/core/test_security_acceptance.py:231) |
| 打印链接携带完整 API 凭据 | 指定文档改用短时专用签名票据；收货打印只取票据参数。有票据绑定文档和 API 凭据隔离的回归。[签发](/wms/allapp/core/print_tickets.py:26) · [收货入口](/wms/allapp/inbound/export_print.py:28) · [回归](/wms/allapp/core/test_security_acceptance.py:283) |
| XLSX 解压预算 | 公共归档校验在相关标识导入与无单入库的工作簿解析前调用，有预检预算回归。此结论限已核对入口。[公共校验](/wms/allapp/core/xlsx_safety.py:11) · [标识导入](/wms/allapp/products/identifier_excel.py:166) · [无单入库](/wms/allapp/inbound/excel_import.py:260) · [回归](/wms/allapp/core/test_security_acceptance.py:33) |
| 商品标识导出单元格 | 导出将文本显式保存为字符串，并有重新打开工作簿核验类型的回归。[输出](/wms/allapp/products/identifier_excel.py:143) · [回归](/wms/allapp/core/test_security_acceptance.py:307) |
| 多个会话登录页限流 | Session 限流中间件已安装，有共享额度回归。[安装配置](/wms/wmsmaster/settings.py:271) · [回归](/wms/allapp/accounts/test_session_login_throttle.py:20) |
| Docker 构建上下文环境文件 | 忽略规则已覆盖根目录和子目录环境文件，保留示例。本分项未解包候选镜像核验内容。[忽略规则](/wms/.dockerignore:19) |

9 月其他有代表性的历史变化也应按当前范围表述。货主和老板请求层已将续期失败和业务重试分开处理，商城加入续期，Android 进入项目 CI 的单测、浏览器和 App 资源构建；移动真机及签名包仍需相应实测。传统 PDA 基础地址仍固定选开发配置，旧 Web 模板仍对可能匹配不到的元素调用 `.closest()`。发布脚本已显式停止、恢复并检查支付定时器与服务，真实环境中的调度效果仍需运行证据。[货主](/wms/wmsownersale/utils/request.js:225) · [老板](/wms/wmsbossbilling/utils/request.js:200) · [商城](/wms/sales-miniapp/utils/request.js:104) · [Android CI](/wms/.github/workflows/ci.yml:57) · [传统 PDA 地址](/wms/wmspda/utils/request.js:15) · [Web 模板](/wms/templates/base.html:108) · [发布脚本](/wms/docker/production_release.sh:75)
