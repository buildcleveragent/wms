# 商城小程序使用教程

本文档说明 `sales-miniapp` 买家商城小程序做了什么、包含哪些文件、如何配置和使用。这个小程序是一个独立的 uni-app 工程，不修改 `wmspda`，它作为 WMS 的线上商城入口，把客户订单接入现有出库、库存、支付和售后链路。

## 1. 项目定位

`sales-miniapp` 是面向客户、门店、采购方的多商家商城小程序。

它负责：

- 展示可售商品、分类、首页横幅和推荐商品。
- 按商家/货主浏览已上架商品。
- 管理客户收货地址。
- 使用服务端购物车。
- 预览订单金额、库存和购买规则。
- 使用优惠券、积分抵扣。
- 提交商城订单。
- 创建 WMS 出库单 `OutboundOrder / OutboundOrderLine`。
- 发起微信支付、处理支付回调。
- 申请退款、接收退款回调。
- 取消未支付订单、释放库存和优惠资源。
- 发起售后申请。
- 查询订单状态。

它不负责直接扣减库存。库存事实仍由 WMS 出库、分配、拣货、复核和过账链路负责。

核心链路：

```text
客户浏览商品
  -> 加入购物车
  -> 服务端校验价格、库存、单位、起订量
  -> 预览优惠券/积分后的应付金额
  -> 提交订单
  -> 创建 OutboundOrder
  -> 分配库存
  -> 微信支付或线下付款
  -> 仓库履约
  -> 退款/售后按状态进入后端流程
```

## 2. 我完成的主要工作

前端部分：

- 新建独立 uni-app 工程 `sales-miniapp/`。
- 实现商城首页、分类、商品列表、商品详情、购物车、确认订单、订单列表、订单详情、地址、我的、登录页面。
- 实现商家列表、商家店铺、优惠/积分、售后、收藏、浏览足迹等商城常用页面。
- 地址簿支持按已开通商家切换；从确认订单选择地址时锁定当前结算商家，避免跨商家选错地址。
- 订单列表和售后列表支持“全部商家”和单商家筛选，适合多商家客户查看自己的交易记录。
- “我的”页面的积分、优惠券摘要支持按已开通商家切换，进入权益页时延续同一商家口径。
- 实现服务端请求封装、JWT token 存储、服务地址配置。
- 实现账号登录和微信登录入口。
- 微信登录后保留后端返回的全部商家绑定 `bindings`，多商家客户不会退化成单商家会话。
- 实现服务端购物车同步，本地响应式状态只负责页面展示。
- 实现搜索历史、热门搜索、商家筛选、分类筛选、品牌筛选、价格筛选、只看有货和价格排序。
- 实现收藏、浏览足迹、订单详情“再来一单”，并保留 `owner_id / config_id`，避免跨商家或错上架配置。
- 订单详情展示买家可读的配送进度，不直接暴露 WMS 审核、分配、拣货等内部状态。
- 实现优惠券列表、积分余额、订单预览时优惠抵扣。
- 实现微信支付发起、继续支付、退款申请、售后申请。
- 清理旧销售员移动开单页面，当前前端只保留普通客户商城入口。

后端部分：

- 新增 `/api/sale-mini/` 接口分组。
- 新增小程序用户、地址、首页 Banner、商品上架配置、购物车、订单映射、支付、退款、支付事件、优惠券、积分流水、分销记录、售后申请等模型。
- 下单时创建现有 WMS `OutboundOrder / OutboundOrderLine`，不让小程序直接扣库存。
- 支付前使用 `SaleMiniOrderMapping.payable_amount`，避免优惠影响 WMS 出库行单价。
- 优惠券、积分、分销记录支持锁定、确认、释放、退款反冲。
- 未支付超时订单支持管理命令自动取消并释放资源。
- 微信支付、支付回调、退款、退款回调有幂等事件记录。
- 修复旧销售移动接口在 `USE_TZ=False` 下 `timezone.localdate()` 报错的问题。

数据准确性保障：

- 服务端每次预览和下单都重新计算价格、金额、单位换算和库存。
- 下单后走库存分配，不依赖前端库存展示。
- 优惠不污染 WMS 出库行价格，订单映射单独保存 `goods_amount / adjustment_amount / payable_amount`。
- 支付成功、取消、过期、退款回调都会处理优惠券、积分、分销、库存占用的状态一致性。
- 当前已通过商城结构测试、前端 H5/微信构建、后端基础检查、纯单元测试、真实库只读数据准确性校验，以及数据库级 `SaleMiniApiTests` 快速 DB 模式业务验收。

## 3. 文件结构

### 3.1 小程序前端

核心目录：

```text
sales-miniapp/
  package.json
  vite.config.js
  index.html
  App.vue
  main.js
  manifest.json
  pages.json
  uni.scss
  pages/
  components/
  services/
  stores/
  utils/
```

页面文件：

```text
sales-miniapp/pages/login/login.vue
sales-miniapp/pages/index/index.vue
sales-miniapp/pages/category/category.vue
sales-miniapp/pages/product-list/product-list.vue
sales-miniapp/pages/product-detail/product-detail.vue
sales-miniapp/pages/cart/cart.vue
sales-miniapp/pages/order-confirm/order-confirm.vue
sales-miniapp/pages/order-list/order-list.vue
sales-miniapp/pages/order-detail/order-detail.vue
sales-miniapp/pages/merchants/merchants.vue
sales-miniapp/pages/merchant-detail/merchant-detail.vue
sales-miniapp/pages/benefits/benefits.vue
sales-miniapp/pages/after-sales/after-sales.vue
sales-miniapp/pages/favorites/favorites.vue
sales-miniapp/pages/history/history.vue
sales-miniapp/pages/address/address.vue
sales-miniapp/pages/address-edit/address-edit.vue
sales-miniapp/pages/user/user.vue
```

组件：

```text
sales-miniapp/components/ProductCard.vue
sales-miniapp/components/PriceText.vue
sales-miniapp/components/QuantityStepper.vue
sales-miniapp/components/EmptyState.vue
sales-miniapp/components/AddressCard.vue
sales-miniapp/components/OrderStatusTag.vue
sales-miniapp/components/CartBar.vue
```

服务层：

```text
sales-miniapp/services/auth.js
sales-miniapp/services/product.js
sales-miniapp/services/cart.js
sales-miniapp/services/order.js
sales-miniapp/services/address.js
sales-miniapp/services/benefit.js
sales-miniapp/services/payment.js
```

状态和工具：

```text
sales-miniapp/stores/session.js
sales-miniapp/stores/cart.js
sales-miniapp/stores/browse.js
sales-miniapp/utils/request.js
sales-miniapp/utils/money.js
sales-miniapp/utils/qty.js
```

测试脚本：

```text
sales-miniapp/scripts/verify-mall-structure.mjs
sales-miniapp/scripts/run-quality-gate.mjs
```

### 3.2 后端文件

销售小程序接口：

```text
allapp/salesapp/salemini_api.py
allapp/salesapp/salemini_urls.py
```

小程序支付和优惠服务：

```text
allapp/salesapp/services_wechat_pay.py
allapp/salesapp/services_salemini_adjustments.py
```

模型和管理：

```text
allapp/salesapp/models.py
allapp/salesapp/admin.py
```

超时取消命令：

```text
allapp/salesapp/management/commands/bootstrap_sale_mini_catalog.py
allapp/salesapp/management/commands/expire_sale_mini_orders.py
allapp/salesapp/management/commands/validate_sale_mini_data_accuracy.py
```

迁移：

```text
allapp/salesapp/migrations/0002_miniprogramuser_minicustomeraddress_saleminibanner_and_more.py
allapp/salesapp/migrations/0003_saleminicart_saleminicartitem_and_more.py
allapp/salesapp/migrations/0004_saleminipayment_saleminipaymentevent_saleminirefund_and_more.py
allapp/salesapp/migrations/0005_saleminicoupon_saleminicoupontemplate_and_more.py
allapp/salesapp/migrations/0006_saleminiaftersalerequest.py
allapp/salesapp/migrations/0007_allow_multi_owner_mini_program_user.py
```

测试：

```text
allapp/salesapp/tests.py
allapp/salesapp/test_salemini_unit.py
allapp/salesapp/test_mobile_api_unit.py
allapp/salesapp/test_services_pricing_unit.py
```

配置：

```text
.env.example
wmsmaster/settings.py
wmsmaster/urls.py
```

## 4. 后端接口

小程序主接口都挂在：

```text
/api/sale-mini/
```

核心接口：

```text
POST /api/sale-mini/auth/wechat-login/
GET  /api/sale-mini/me/
GET  /api/sale-mini/home/
GET  /api/sale-mini/merchants/
GET  /api/sale-mini/categories/
GET  /api/sale-mini/brands/
GET  /api/sale-mini/products/
GET  /api/sale-mini/products/{id}/
GET  /api/sale-mini/cart/
POST /api/sale-mini/cart/add/
POST /api/sale-mini/cart/update/
POST /api/sale-mini/cart/remove/
POST /api/sale-mini/cart/clear/
GET  /api/sale-mini/addresses/
POST /api/sale-mini/addresses/
PUT  /api/sale-mini/addresses/{id}/
DELETE /api/sale-mini/addresses/{id}/
GET  /api/sale-mini/coupons/
GET  /api/sale-mini/points/
POST /api/sale-mini/orders/preview/
GET  /api/sale-mini/orders/
POST /api/sale-mini/orders/
GET  /api/sale-mini/orders/{id}/
POST /api/sale-mini/orders/{id}/cancel/
POST /api/sale-mini/payments/wechat/prepay/
POST /api/sale-mini/payments/wechat/callback/
POST /api/sale-mini/payments/wechat/refund/
POST /api/sale-mini/payments/wechat/refund-callback/
POST /api/sale-mini/after-sales/
```

`GET /api/sale-mini/home/` 返回首页首屏数据，包括 `banners`、`categories`、
`merchants`、`recommend_products`、`hot_products` 和 `new_products`。独立
`GET /api/sale-mini/merchants/` 用于商品列表、分类页商家筛选，以及兼容旧首页数据。

登录页也支持后台账号登录，使用：

```text
POST /api/auth/login/
```

## 5. 运行前准备

### 5.1 后端环境变量

参考 `.env.example`，至少需要配置：

```text
SECRET_KEY=
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=
```

微信登录和支付相关配置：

```text
WECHAT_MINI_APPID=
WECHAT_MINI_SECRET=
WECHAT_JSCODE2SESSION_URL=https://api.weixin.qq.com/sns/jscode2session

WECHAT_PAY_API_BASE_URL=https://api.mch.weixin.qq.com
WECHAT_PAY_MCH_ID=
WECHAT_PAY_MCH_SERIAL_NO=
WECHAT_PAY_PRIVATE_KEY=
WECHAT_PAY_PRIVATE_KEY_PATH=
WECHAT_PAY_APIV3_KEY=
WECHAT_PAY_NOTIFY_URL=
WECHAT_PAY_REFUND_NOTIFY_URL=
WECHAT_PAY_PLATFORM_PUBLIC_KEY=
WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH=
WECHAT_PAY_VERIFY_CALLBACK_SIGNATURE=False

SALE_MINI_PAY_TIMEOUT_MINUTES=30
SALE_MINI_POINT_EXCHANGE_RATE=100
SALE_MINI_DISTRIBUTION_COMMISSION_RATE=0
```

说明：

- 开发阶段可以先用账号登录和线下付款。
- 真正使用微信登录必须配置 `WECHAT_MINI_APPID / WECHAT_MINI_SECRET`。
- 真正使用微信支付必须配置商户号、证书序列号、私钥、APIv3 key、回调地址。
- `WECHAT_PAY_VERIFY_CALLBACK_SIGNATURE=False` 只适合开发和测试环境，生产环境应开启签名校验。

### 5.2 应用数据库迁移

```bash
cd /wms
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py migrate
```

只检查迁移是否已应用：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py migrate --check
```

查看销售小程序迁移：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py showmigrations salesapp
```

### 5.3 基础业务数据

小程序要能下单，至少要有这些数据：

1. 货主 `Owner`。
2. 仓库 `Warehouse`。
3. 客户 `Customer`。
4. 系统用户 `User`，并绑定同一货主和出库仓库。
5. 商品 `Product`，包含单位、价格、规格等基础信息。
6. 库存明细或库存汇总中有可用库存。
7. 小程序买家 `MiniProgramUser`，绑定 `Owner + User + Customer`。
8. 商品上架配置 `SaleProductConfig`，且 `is_listed=True`。

重要规则：

- 不是所有 `Product` 都会展示，只有 `SaleProductConfig` 已上架商品才会出现在小程序。
- 小程序用户必须绑定客户，否则不能下单。
- 绑定的系统用户必须有出库仓库，否则不能从小程序下单。
- 微信登录时，`MiniProgramUser` 需要预先绑定 `openid`，或通过 `unionid` 绑定后首次登录写入 `openid`。

### 5.4 可选运营数据

如果要使用优惠和运营功能，可以配置：

- `SaleMiniBanner`：首页横幅。
- `SaleMiniCouponTemplate`：优惠券模板。
- `SaleMiniCoupon`：发给客户或买家的优惠券。
- `SaleMiniPointLedger`：客户积分流水。
- `SALE_MINI_DISTRIBUTION_COMMISSION_RATE`：分销佣金比例。

## 6. 启动后端

```bash
cd /wms
SECRET_KEY=test-secret-key ALLOWED_HOSTS=192.168.1.6,localhost,127.0.0.1 CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.33:5173 .venv/bin/python manage.py runserver 0.0.0.0:8001
```

小程序默认请求地址：

```text
http://192.168.1.6:8001
```

后端运行在 VMware Ubuntu 虚拟机时，需要确保：

- Django 监听 `0.0.0.0:8001`，不要只监听 `127.0.0.1`。
- 虚拟机 IP 是 `192.168.1.6`，Windows 主机和真机手机能访问这个 IP。
- Ubuntu 防火墙放行 `8001` 端口。
- Django 的 `ALLOWED_HOSTS` 包含 `192.168.1.6`。
- Django 的 `CORS_ALLOWED_ORIGINS` 包含 HBuilderX H5 地址，例如 `http://192.168.1.33:5173`。

也可以在小程序登录页的“服务地址”输入框里改成实际后端地址。该地址会保存到本地缓存 `sales_base_url`。

## 7. 启动和构建小程序

进入前端工程：

```bash
cd /wms/sales-miniapp
```

HBuilderX 编译方式：

- 打开 `/wms/sales-miniapp` 项目根目录。
- 不要打开 `/wms/sales-miniapp/src`，当前项目没有 `src` 源码根。
- 项目根目录应直接看到 `App.vue`、`main.js`、`manifest.json`、`pages.json`、`pages/`。
- 运行到微信小程序即可，或使用下方命令行构建后导入微信开发者工具。

安装依赖：

```bash
npm install
```

H5 开发预览：

```bash
npm run dev:h5
```

微信小程序开发预览：

```bash
npm run dev:mp-weixin
```

微信小程序发布构建：

```bash
npm run build:mp-weixin
```

H5 构建：

```bash
npm run build:h5
```

微信开发者工具导入路径：

```text
sales-miniapp/dist/dev/mp-weixin
sales-miniapp/dist/build/mp-weixin
```

注意：

- 这是 uni-app CLI 工程，不使用 DCloud 云服务。
- 构建输出里可能出现 uni-app 自带的 DCloud/uniCloud 提示，那只是工具链提示，不代表项目接入了 DCloud 云。

## 8. 客户采购使用流程

### 8.1 登录

打开小程序后，如果没有 token，会进入：

```text
pages/login/login
```

可选登录方式：

- 微信登录：调用 `uni.login` 获取 code，再请求 `/api/sale-mini/auth/wechat-login/`。
- 账号登录：请求 `/api/auth/login/`，适合开发测试或内部用户。

登录成功后进入：

```text
pages/index/index
```

### 8.2 浏览商品

首页展示：

- 商城名称或当前登录客户名称。
- 全货主商品提示，登录后可显示当前结算绑定信息。
- 搜索入口。
- Banner，可按后台配置跳转到商品、分类、商家、搜索结果、商品列表或内部页面。
- 分类。
- 精选商家，点击商家进入该货主的商品列表。
- 品牌筛选、价格筛选、只看有货和排序。
- 热卖商品。
- 推荐商品。
- 底部购物车栏。

商品来源：

- 后端只返回所有 active 货主下已上架的 `SaleProductConfig` 商品。
- 商品列表和分类页都可以按商家/货主筛选，筛选参数是 `owner_id`。
- 商品列表可以按品牌筛选，筛选参数是 `brand_id`，品牌也只来自已上架可售商品。
- 库存、价格和购买规则由服务端计算；前端只展示结果，不信任本地价格。
- 下单时必须按单一商家结算，购物车内跨商家商品需要拆成多笔订单。
- 如果当前账号还未开通某个商家的购买权限，仍然可以浏览商品和进店，商品详情会提示“该商家暂未对你的账号开通购买权限”，加购/购买仍由后端最终拦截。

### 8.3 加入购物车

在首页、商品列表、商品详情点击加入购物车。

购物车特点：

- 商品写入后端 `SaleMiniCart / SaleMiniCartItem`。
- 前端本地状态只做页面展示，不是库存和金额事实来源。
- 修改数量时会请求服务端重新校验。
- 起订量、倍数、库存不足等错误由服务端返回。

### 8.4 确认订单

进入：

```text
pages/order-confirm/order-confirm
```

确认内容：

- 收货地址。
- 配送方式：配送、自提、快递/小包。
- 支付方式：微信支付、线下付款。
- 优惠券。
- 积分抵扣。
- 商品明细。
- 商品金额、优惠金额、应付金额。
- 备注。

地址规则：

- 从“我的 - 收货地址”进入时，多商家客户可以切换已开通商家，维护各商家的地址簿。
- 从确认订单进入地址选择时，地址页锁定当前结算商家，只返回该商家的客户地址。
- 新增和编辑地址都会带上当前 `owner_id`，后端仍按 `Owner + Customer` 保存。

点击提交时，前端会先用：

```text
POST /api/sale-mini/orders/preview/
```

服务端重新计算：

- 商品价格。
- 商品金额。
- 优惠券。
- 积分抵扣。
- 应付金额。
- 库存是否足够。
- 单位换算和购买规则。

然后用：

```text
POST /api/sale-mini/orders/
```

创建订单。

### 8.5 微信支付

如果选择微信支付：

1. 创建小程序订单。
2. 后端创建 WMS 出库单和小程序订单映射。
3. 后端锁定优惠券和积分。
4. 前端请求 `/api/sale-mini/payments/wechat/prepay/`。
5. 前端调用 `uni.requestPayment`。
6. 微信异步回调 `/api/sale-mini/payments/wechat/callback/`。
7. 回调确认支付、优惠券、积分、分销记录。

如果前端支付中断，可以在订单详情点击“继续支付”。

### 8.6 线下付款

如果选择线下付款：

- 订单会以线下付款状态创建。
- 不调用微信支付。
- 适合货到付款、月结、人工确认付款等场景。

### 8.7 查看订单

订单列表：

```text
pages/order-list/order-list
```

订单详情：

```text
pages/order-detail/order-detail
```

订单详情展示：

- 订单号。
- 买家状态。
- 支付状态。
- 收货信息。
- 商品明细。
- 商品金额。
- 优惠明细。
- 应付金额。
- 备注。
- 售后申请状态。
- 再来一单入口。

买家状态使用普通商城文案：

- `WAIT_PAY`：待付款。
- `WAIT_SHIP`：待发货。
- `COMPLETED`：已完成。
- `REFUNDING`：退款中。
- `REFUNDED`：已退款。
- `CANCELLED`：已取消。

WMS 内部的货主审核、仓库确认、拣货等状态不会直接展示给普通买家。

多商家客户可以在订单列表切换“全部商家”或某一个已开通商家。订单查询会把 `owner_id` 传给后端，后端仍按当前登录客户的商家绑定过滤。

“再来一单”会把原订单商品重新加入当前服务端购物车，但下单前仍然重新预览当前价格、库存、优惠和购买规则，不复用旧订单金额。

### 8.8 取消订单

未支付、未进入仓库深度作业的订单可以取消。

取消会处理：

- 关闭或取消微信支付单。
- 释放库存分配。
- 释放优惠券。
- 释放冻结积分。
- 反冲分销记录。
- 标记订单取消。

### 8.9 退款和售后

订单已支付且还未进入拣货深度流程时，可以在订单详情申请退款。

退款成功后会处理：

- WMS 订单取消。
- 库存释放。
- 优惠券/积分/分销反冲。
- 小程序支付状态变为已退款。

订单进入拣货、处理中或完成后，可以申请售后。售后申请只创建 `SaleMiniAfterSaleRequest`，不直接改库存和支付，需要后续人工或业务流程处理。

售后列表同样支持按商家筛选，方便客户只查看某个商家的售后记录。

## 9. 商城结构测试

项目提供一个不依赖数据库和浏览器的结构测试，用来防止旧销售工作台页面重新混入商城小程序：

```bash
cd /wms/sales-miniapp
npm run test:structure
```

它会检查：

- `pages.json` 的首页和底部 Tab 是否是商城结构。
- 旧销售工作台页面文件是否不存在。
- 请求封装是否只暴露 `/api/sale-mini/` 商城接口。
- 首页 Banner 是否保留可点击跳转逻辑。
- 首页是否有精选商家入口，并能通过 `owner_id` 跳到对应商品列表。
- 分类页是否保留“全部”分类，并支持商家筛选和 `owner_id` 商品过滤。
- 商品列表是否支持商家、品牌、搜索历史、热门搜索、只看有货和价格排序。
- 商品详情是否支持收藏、浏览足迹、同店热卖和商家店铺入口。
- 微信登录是否保留多商家 `bindings`。
- 商品详情是否有未开通商家的购买权限提示。
- “我的”页积分/优惠券和权益页是否能按已开通商家切换，并保持同一个 `owner_id`。
- 地址页是否支持多商家地址切换，且订单选择地址时锁定当前商家。
- 订单列表和售后列表是否支持按商家筛选。
- 订单详情是否有配送进度，且页面不出现 WMS、PDA、销售员等后台术语。
- 后端权限错误是否翻译为普通客户能理解的商城文案。
- 购物车和确认订单是否按 `owner_id` 做商家分组和单商家结算。
- 订单详情是否保留 `config_id / owner_id`，用于再来一单重新加购。

完整质量门禁：

```bash
cd /wms/sales-miniapp
npm run test:quality
```

快速门禁，不跑 H5/微信构建：

```bash
npm run test:quality -- --skip-build
```

带真实库只读数据准确性校验的快速门禁：

```bash
npm run test:quality -- --skip-build --data-accuracy
```

带数据库 API 端到端测试的完整 DB 门禁：

```bash
npm run test:quality -- --db
```

本地快速 DB 门禁，跳过迁移回放但保留 API 业务断言和真实库只读数据准确性校验：

```bash
npm run test:quality -- --skip-build --db --fast-db
```

注意：标准 `--db` 会回放完整迁移，需要干净的 Django MySQL 测试库，当前本机 MySQL 初始化整套 WMS 测试库较慢。已经验证过的快速 DB 模式使用 pytest-django `--no-migrations` 建表并通过 `SaleMiniApiTests`。

## 10. 超时订单处理

未支付微信订单超时后，需要定时执行：

```bash
cd /wms
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py expire_sale_mini_orders
```

如果要尝试关闭微信支付单：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py expire_sale_mini_orders --close-wechat
```

建议生产环境用 cron、supervisor、celery beat 或其他调度器定期执行。

## 11. 管理后台常用维护项

建议在 Django admin 或后端管理页面维护这些对象：

- 小程序买家：`MiniProgramUser`
- 客户地址：`MiniCustomerAddress`
- 首页横幅：`SaleMiniBanner`
- 商品上架配置：`SaleProductConfig`
- 服务端购物车：`SaleMiniCart / SaleMiniCartItem`
- 小程序订单映射：`SaleMiniOrderMapping`
- 支付单：`SaleMiniPayment`
- 退款单：`SaleMiniRefund`
- 支付事件：`SaleMiniPaymentEvent`
- 优惠券模板：`SaleMiniCouponTemplate`
- 优惠券：`SaleMiniCoupon`
- 订单调整：`SaleMiniOrderAdjustment`
- 积分流水：`SaleMiniPointLedger`
- 分销记录：`SaleMiniDistributionRecord`
- 售后申请：`SaleMiniAfterSaleRequest`

## 12. 数据准确性说明

这个项目最重要的原则是：前端只展示和提交意图，后端负责事实。

具体规则：

- 商品是否可卖由 `SaleProductConfig.is_listed` 控制。
- 商品价格由后端服务端计算。
- 库存由后端在预览和下单时重新校验。
- 下单会创建 WMS 出库单，并通过 WMS 分配库存。
- 微信支付金额使用 `SaleMiniOrderMapping.payable_amount`。
- WMS 出库行仍保存商品原始成交行金额，不被优惠券和积分污染。
- 优惠、积分、分销、退款都有独立流水和状态。
- 支付回调、退款回调有事件表用于幂等处理。

关键金额字段：

```text
goods_amount       商品金额
adjustment_amount 订单级优惠或加价金额，优惠为负数
payable_amount     最终应付金额
```

不要直接把前端传来的价格、金额、库存作为最终事实。

只读数据准确性校验命令：

```bash
cd /wms
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py validate_sale_mini_data_accuracy --fail-on-issues --limit 20
```

它会检查：

- 上架配置的货主和商品货主是否一致。
- 已上架商品是否指向启用的货主和启用的商品。
- 服务端购物车是否存在跨货主商品、下架商品或非正数数量。
- 小程序订单映射和 WMS 出库单的货主、客户、商品金额、应付金额是否一致。
- 支付/退款金额和分值金额是否一致。
- 优惠券、积分调整和订单映射的归属是否一致。
- 库存明细和库存汇总的可用库存是否为负数。

## 13. 验证命令

后端检查：

```bash
cd /wms
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py check
```

迁移检查：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py makemigrations --check --dry-run
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py migrate --check
```

销售小程序集成测试：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests
```

销售小程序快速 DB 集成测试：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q --reuse-db --no-migrations --disable-warnings allapp/salesapp/tests.py::SaleMiniApiTests
```

销售小程序无数据库单元测试：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python -m pytest -q allapp/salesapp/test_salemini_unit.py allapp/salesapp/test_mobile_api_unit.py allapp/salesapp/test_services_pricing_unit.py
```

数据准确性校验：

```bash
SECRET_KEY=test-secret-key CORS_ALLOWED_ORIGINS=http://localhost .venv/bin/python manage.py validate_sale_mini_data_accuracy --fail-on-issues --limit 20
```

前端构建：

```bash
cd /wms/sales-miniapp
npm run build:mp-weixin
npm run build:h5
```

已验证结果：

```text
manage.py check: passed
sale-mini pure unit tests: 15 passed
validate_sale_mini_data_accuracy: ok, issue_count=0
npm run test:structure: passed
npm run test:quality -- --skip-build: passed
npm run test:quality -- --skip-build --data-accuracy: passed
npm run test:quality -- --skip-build --db --fast-db: passed, including 35 SaleMiniApiTests and live data validation
npm run test:quality: passed, including build:mp-weixin and build:h5
SaleMiniApiTests fast DB mode: 35 passed
```

## 14. 常见问题

### 登录后提示未绑定客户

检查：

- 是否存在 `MiniProgramUser`。
- `MiniProgramUser.owner` 是否正确。
- `MiniProgramUser.customer` 是否正确且启用。
- 账号登录时，`MiniProgramUser.user` 是否绑定当前登录用户。

### 微信登录提示未绑定客户

检查：

- `.env` 是否配置 `WECHAT_MINI_APPID / WECHAT_MINI_SECRET`。
- 微信 code 是否来自正确小程序。
- `MiniProgramUser.openid` 是否已预绑定。
- 如果用 unionid 绑定，微信接口是否返回 unionid。
- 同一 openid 是否绑定了多个小程序用户。

### 下单提示未绑定仓库

检查：

- `MiniProgramUser.user` 绑定的系统用户是否设置了 `warehouse`。
- 该仓库是否属于当前货主业务范围。

### 商品不显示

检查：

- `SaleProductConfig.is_listed=True`。
- `SaleProductConfig.is_active=True`。
- `Product.is_active=True`。
- 商品货主是否和当前用户货主一致。
- 是否有库存，或库存展示规则是否允许显示。

### 购物车或下单提示库存不足

这是正常保护。小程序库存展示不是最终事实，服务端会按当前仓库库存重新校验。需要检查库存明细、库存分配和是否已有其他订单占用库存。

### 微信支付预下单失败

检查：

- 商户号、证书序列号、私钥、APIv3 key 是否配置。
- 私钥路径是否可读。
- 回调地址是否是微信可访问的 HTTPS 地址。
- 订单是否仍是未支付状态。
- `payable_amount` 是否大于 0。

### 退款申请失败

检查：

- 订单是否已支付。
- 订单状态是否允许退款。
- 微信支付原交易号是否存在。
- 微信支付配置是否完整。

## 15. 上线前检查清单

上线前至少确认：

- 数据库迁移全部应用。
- `.env` 中生产环境微信配置完整。
- `WECHAT_PAY_VERIFY_CALLBACK_SIGNATURE=True`。
- 微信支付和退款回调地址可公网访问。
- 已配置订单超时取消定时任务。
- 已配置至少一个测试客户、测试买家、测试仓库、测试商品、测试库存。
- 上架商品只包含允许对外销售的商品。
- 已用测试账号跑通：登录、浏览、加购、预览、下单、支付、取消、退款、售后。
- WMS PDA 端可以看到对应出库任务并完成履约。
