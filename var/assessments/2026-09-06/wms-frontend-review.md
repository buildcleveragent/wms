# WMS 前端与跨端架构评估

评估日期：2026-09-06。范围：`/wms/templates`、`/wms/static`、`/wms/frontend`、`/wms/wmspda`、`/wms/wmsandroid`、`/wms/wmsownersale`、`/wms/wmsbossbilling`、`/wms/sales-miniapp`，同时读取相关 API/JWT 配置和 CI。

方式：源码和测试审查、现有本地依赖下运行 JS 单测/结构检查/H5 构建、离线 mock 复现。已读 `/wms/AGENTS.md`。没有修改业务代码、安装依赖、调用生产 API、连接数据库或进行正式安全扫描。没有浏览器渲染、微信真机、Android APK、PDA 硬件扫码/打印或真实交易联调验证，因此不把 H5 编译通过当作跨端验收通过。

保留评估开始时的未提交改动：`allapp/pos/{exports.py,tests.py,views.py}`、`wmspda/pages/pos/index.vue`、`wmspda/utils/request.js`，未跟踪 `wmsandroid/`、`wmspda/tests/unit/posExportContracts.test.js`。结束时 `git status --short` 与开始清单一致。下述源码结论针对当前工作区，包含这些在制修改。

## 总体判断

系统具有可辨认的角色分工：Web 管理后台、PDA 仓库作业/POS、货主销售、老板经营分析、消费者商城，以及新增 Android 手机作业端。不是只有界面壳：存在令牌轮换、货主/客户上下文、分页一致性、价格数量校验、POS 幂等恢复、打印失败隔离、微信支付后端确认等实际业务设计，且部分已有行为测试。

成熟度不均衡。货主端的组件行为测试相对扎实；老板端只有请求分类等少量单测；商城以源码结构契约和支付服务测试为主；Android 尚未纳入现有 CI。统一能力主要共享了 API 地址策略，身份恢复、业务错误处理、请求包装和部分仓库业务仍分别实现，已经出现可复现的跨端错误处理差异。不能仅凭当前单测全部通过判定现场可稳定使用。

## 规模和组成

下表源码文件数排除了 node_modules、dist、unpackage、test-results、playwright-report 和 .git；页面数按 pages.json 的 path 注册项统计。

| 模块 | 页面注册项 | 源码/配置等文件数 | 本次重点 |
| --- | ---: | ---: | --- |
| wmspda | 39 | 120 | 登录、收货/上架、扫码、POS、导入导出 |
| wmsandroid | 31 | 89 | 手机工作台、相机扫码、POS 状态恢复 |
| wmsownersale | 20 | 75 | 启动鉴权、客户上下文、选品/购物车、库存、账单 |
| wmsbossbilling | 12 | 37 | 经营报表/账单、请求错误分类、登录 |
| sales-miniapp | 19 | 68 | 消费者选品、购物车、确认订单、支付 |
| templates | — | 61 | 普通 Web、运营台、Django Admin 定制 |
| static | — | 36 | CSS、Admin JS、表单行为 |
| frontend | — | 1 | 共享 API 地址/微信 manifest 构建校验 |

五端使用 uni-app/Vue；四端使用 Pinia，商城使用 Vue reactive store。各端独立 package.json/package-lock.json，依赖版本总体协调。PDA 和 Android 存在 56 份同路径 `.vue/.js/.json/.scss` 文件，其中 20 份逐字一致，另外 36 份已分叉。这是重复维护成本的实测依据，不能仅凭重复断言存在功能错误。

## 确认的问题

### F1 / P1：货主端令牌刷新后遇到业务校验失败会清空登录及当前订单状态

证据：`/wms/wmsownersale/utils/request.js:183` 的 request() 外层捕获 401；`:188` 刷新成功后 `:189` 重试业务请求，但这次重试仍在同一个 try 块中。`:190-194` 将业务请求的 400/403/500 同样视作刷新失败，只要 statusCode 非 0 就调用 expireLocalSession()。`:74-82` 清理 access/refresh/user 并调用 `useCart().resetOrder()`。

已离线复现：原请求 401 → refresh 200（返回新 access/refresh）→ 原业务请求重试 400，结果返回 400，同时移除 access、refresh、user，并执行购物车 reset。用户在令牌恰好到期时提交含错误的订单，将面对重新登录和丢失当前选品上下文，而不是留在表单纠错。没有真实网络/数据库访问。

建议：分开“refresh 请求失败”与“刷新后业务请求失败”的错误处理，只对确定的认证失效清会话；普通业务失败保留草稿、上下文和新令牌。补充 401→刷新成功→400/403/500 的行为测试。

### F2 / P2：老板端在刷新网络断连和刷新后业务失败时无条件登出

证据：`/wms/wmsbossbilling/utils/request.js:154-166`，特别是 `:162-164` 的 catch 无条件 redirectToLogin；`:118-120` 会清令牌和用户信息。

已离线复现两条路径：① 原请求 401，refresh 网络 fail（status 0）；② 原请求 401，refresh 200，业务重试 400。两者都移除 access/refresh/user 并显示“登录已失效，请重新登录”。网络问题被误报为身份问题。当前 `/wms/wmsbossbilling/tests/request.test.js:5-14` 只验证错误类型映射，没有覆盖刷新/重试过程。

建议：网络/服务故障保留会话并提供重试，业务错误传给当前页面；只有刷新令牌失效或再次明确 401 才强制登录。

### F3 / P2：商城持有 refresh token，但没有续期路径，默认约 15 分钟后将被要求重新登录

证据：`/wms/sales-miniapp/utils/request.js:22-29` 保存并清理 sales_refresh，却没有读取它/调用刷新接口的路径；`:103-107` 在任何 401 时清理令牌并跳到登录。上传文件同样如此（`:143-145`）。`/wms/wmsmaster/settings.py:195-199` 的 access 有效期为 15 分钟、refresh 为 7 天。

这是静态确认的行为，没有等待 15 分钟做真机验证。其准确表述是：使用仓库默认 JWT 配置且未再次登录的会话，在 access 到期后的下一次受保护请求会被清理并要求重新登录；并非声称线上配置必然相同。长时间购物/评价/支付流程会被打断。建议实现与其他端一致的单次刷新、并发刷新合并、一次重试及草稿保留。

### F4 / P2：PDA 构建地址固定到开发机，无法通过构建环境配置切换

证据：`/wms/wmspda/utils/request.js:5-13` 定义地址映射后仍硬编码 `BASE_URL = BASE_MAP.develop`，对应 `http://192.168.1.6:8001`；`/wms/wmspda/vite.config.js:3-8` 只检查微信 manifest，没有接入统一 validateApiBase；`/wms/wmspda/tests/unit/requestBaseUrl.test.js:9-16` 甚至明确保证始终使用硬编码开发地址。

这个行为可能满足当前特定仓库的局域网使用，但属于确认的配置可移植性问题：把同一构建部署到另一现场/远程域名不会自动选择其 API，必须改源码。在多环境发版和故障排查中容易误连开发服务。没有访问该 IP，也不以此推断线上实际网络暴露。建议用受校验的发布环境参数并保留明确的现场内网配置选项；测试应该验证各环境边界，而不是锁死开发地址。

### F5 / P3：普通 Web 基础模板包含必然可触发的 null DOM 调用

证据：`/wms/templates/base.html:108` 直接调用 `document.querySelector('a[href="/admin/accounts/"]').closest(...)`，没有判断 querySelector 是否返回 null。当前 `/wms/templates/_sidebar.html` 是空文件，`/wms/templates/home.html` 等继承 base 的页面没有提供该链接，因此这些页面使用该脚本时会出现 TypeError。此项为代码确认，未进行浏览器复现；该错误发生于独立 DOMContentLoaded 回调，不能夸大为整页所有脚本失效。

另外，基础模板的移动侧栏事件在 extra_js block 内（`:95-103`），而 `/wms/templates/inbound/order_form.html:204` 等模板重写该 block 时不调用 block.super，可能丢失侧栏行为。由于当前侧栏本身为空，是否影响在用流程应先确认 Web 路由使用情况。建议保留为低优先级遗留 UI 清理，不阻断主要 PDA 流程。

## 可保留的设计优点

- **提交和打印分开处理**：`/wms/wmspda/utils/posCheckoutFlow.js:8-32` 明确区分结账失败、交易已提交但 UI 处理失败、打印失败，避免简单把“打印失败”等同于“交易未成功”。`/wms/wmspda/tests/unit/posCheckoutFlow.test.js:5` 的 6 个行为测试均通过。
- **Android 对不确定交易保留可重试快照**：`/wms/wmsandroid/utils/posMobile.js:147-176` 保存稳定幂等键和 payload；`/wms/wmsandroid/pages/pos/index.vue:335-373` 在网络不确定时保留 pendingCheckout、锁定购物车、用同一 payload 重试；400/403 才释放为可纠错状态。这里评估的是前端恢复设计，服务器端幂等/库存一致性仍由后端审查确定。
- **货主端已积累实际组件行为测试**：搜索、审批、库存和账期页面测试覆盖迟到响应、不正确分页重复触发、加载失败重试、数量错误及订单详情失败状态。示例 `/wms/wmsownersale/tests/inventory-component.test.js:1`、`/wms/wmsownersale/tests/product-search-component.test.js:1`、`/wms/wmsownersale/tests/billing-overview-component.test.js:1`。本次 81 个测试全部通过。
- **支付以服务端确认为准**：`/wms/sales-miniapp/services/payment-core.mjs:20-30` 查询确认并有限轮询，`:49-71` 处理客户端取消但服务端已成功，以及支付成功后查询暂时失败；零金额内部结算路径 `:34-43` 不再调用微信支付。相比直接信任客户端成功回调更稳妥。真实微信支付、回调和退款未在此子评估验证。
- **消费者错误信息有基本收敛**：`/wms/sales-miniapp/utils/request.js:49-65` 过滤 HTML/traceback 并限制消息长度；PDA 也有 HTML 错误页兜底。货主/老板端的消息策略尚未完全统一。
- **API 构建策略可复用**：`/wms/frontend/api-base-policy.mjs:22-50` 校验发布 URL、HTTPS、内部地址允许项；`:53-64` 要求微信 AppID 和 URL 检查。货主、老板、商城均使用它。缺少配置时主动阻断发布是优点。
- **已有自动化基础**：`/wms/.github/workflows/ci.yml:26-87` 对 PDA/货主/老板有独立质量任务；`:454-479` 将商城质量/发布检查纳入流水线。不是完全依赖人工验收。

## 待验证风险和工程债务

1. **单文件业务聚集**：`/wms/wmspda/pages/pos/index.vue` 5834 行；`/wms/wmspda/utils/request.js` 1472 行；`/wms/wmspda/pages/products/search.vue` 1578 行；货主账单概览 1057 行。大量 UI、状态、请求和业务计算集中，增加每次修改的回归范围。建议围绕 POS 草稿/结账状态、金额数量、请求认证等已有边界渐进拆分，不做全面重写。
2. **重复客户端已分叉**：PDA/Android 20 份同路径文件逐字重复，36 份存在差异；多端 request、auth、金额、分页策略分别实现。F1/F2/F3 是出现差异的实际证据。适合共享契约和纯业务函数，扫码、布局、权限入口仍按设备保持独立。
3. **测试层次不均**：老板单测仅 2 文件 9 项，现有 E2E `/wms/wmsbossbilling/e2e/login.spec.js:3` 主要为登录 UI 冒烟，不能证明报表筛选/分组金额/账单下载正确。商城结构脚本（`/wms/sales-miniapp/scripts/verify-mall-structure.mjs:21-26`）和多份 PDA/Android contract 测试做源码字符串断言，有回归提醒价值，但不等同真实交互。PDA `/wms/wmspda/tests/e2e/app-smoke.spec.js:53-88` 的打印失败用例还会自行替换 document.body，因此它证明辅助流程函数，不是 POS 页实际完整点击链路。
4. **跨端发布覆盖不足**：新增 `wmsandroid/` 尚未出现在 CI；`/wms/wmsandroid/package.json:11` 的 quality script 也未执行 build:app。H5 成功不能验证相机权限、native network config 合并、Android 原生插件和 APK 签名。
5. **小程序发布环境待补**：本地三端 build:mp-weixin 都因缺少 VITE_API_BASE_URL 在配置阶段失败；仓库默认 manifest 的 mp-weixin.appid 也均为空，urlCheck=true。此为当前检出与环境尚未提供发布配置，不是已部署系统缺陷。未注入示例/虚假 AppID 以绕过发布校验。
6. **Web 样式构建文档与目录存在偏差**：AGENTS.md 描述 `frontend/npm ci/build_tailwind.sh`，当前 frontend 只有 api-base-policy.mjs；静态 app.css 已存在。复建 Tailwind 的具体来源需维护者确认，不能声称本次重建过 Web CSS。
7. **未做浏览器/真机视觉与可访问性验收**：源码存在响应式、加载/错误状态、部分 ARIA/键盘导航设计，但没有实测窄屏长文本、扫码焦点、移动键盘、弱网、Android 权限拒绝、打印机断连和大列表性能。此处不打伪精确的视觉/体验分数。

## 实际执行结果

执行环境 Node v22.22.0，CI 配置 Node 20；因此本地通过不是 CI Node 20 复测证明。以下全部使用已有 node_modules，没有安装。单测无数据库连接，MySQL 未复用也未重建。

| 工作目录 | 命令 | 本次实际结果 |
| --- | --- | --- |
| /wms/wmspda | npm run test:unit | 14 文件、51 测试通过，exit 0 |
| /wms/wmsandroid | npm run test:unit | 9 文件、35 测试通过，exit 0 |
| /wms/wmsownersale | npm run test:unit | 18 文件、81 测试通过，exit 0 |
| /wms/wmsbossbilling | npm run test:unit | 2 文件、9 测试通过，exit 0 |
| /wms/sales-miniapp | npm run test:payment | Node runner 报告 1 文件项通过、exit 0；该文件源码定义 8 个 test。报告不虚报 runner 输出为“8 项通过” |
| /wms/wmsownersale | npm run test:contracts | 通过；输出 owner client contract OK: 20 registered pages |
| /wms/sales-miniapp | npm run test:structure | 通过；输出 sales-miniapp mall structure ok |
| /wms/wmspda | npm run build:h5 | 通过、exit 0 |
| /wms/wmsandroid | npm run build:h5 | 通过、exit 0 |
| /wms/wmsownersale | npm run build:h5 | 通过、exit 0 |
| /wms/wmsbossbilling | npm run build:h5 | 通过、exit 0 |
| /wms/sales-miniapp | npm run build:h5 | 通过、exit 0 |
| /wms/wmsownersale | npm run build:mp-weixin | 执行失败/环境配置阻断，缺 VITE_API_BASE_URL，exit 1 |
| /wms/wmsbossbilling | npm run build:mp-weixin | 执行失败/环境配置阻断，缺 VITE_API_BASE_URL，exit 1 |
| /wms/sales-miniapp | npm run build:mp-weixin | 执行失败/环境配置阻断，缺 VITE_API_BASE_URL，exit 1 |

四套 Vitest 合计 **176 项通过**，另有商城支付测试文件通过以及两个结构/契约命令通过。没有失败的已运行 JS 行为单测；3 个微信构建确实失败，原因是当前发布配置缺失，不计为通过或跳过。5 个 H5 构建均出现 `[PLUGIN_TIMINGS]` 插件耗时警告，无编译失败；因为同时运行多个构建，不能根据本次耗时断言常规构建性能退化。

未执行：Playwright、Android app/APK 打包、微信真机、全部后端、真实 API 合约联调、生产数据准确性/负载测试。没有进行数据库任何操作。

## 复现和证据文件

- 报告：`/tmp/wms-frontend-review.md`。
- 可重复运行的 F1/F2 离线脚本：`/tmp/wms-frontend-auth-repro.mjs`，命令 `node /tmp/wms-frontend-auth-repro.mjs`。脚本从真实 request.js 读取函数，仅替换模块导入为 mock，保留请求/错误控制流；所有 uni.request 响应由内存回调提供，setTimeout 被禁止执行，不触发网络/登录页面。
- 脚本完整输出：`/tmp/wms-frontend-auth-repro-output.json`。
- 测试/构建汇总摘录：`/tmp/wms-frontend-test-results.txt`。该文件为本次工具输出的人工摘录，不是原始完整逐行日志；以上命令和工作目录可重跑。

优先安排 F1、F2、F3 的会话恢复与草稿保留，再统一环境配置与关键 API 契约。新增 Android 在发布前需要自己的构建门禁和真机验收；将源码字符串测试逐步补为重要异常路径的行为测试。无需以整体重写作为前提。
