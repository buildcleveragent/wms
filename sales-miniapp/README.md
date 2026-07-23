# wmssalemini

独立的买家商城 uni-app 小程序工程，用于客户/门店/采购方浏览已上架商品、加入购物车、使用优惠券和积分、提交订单、微信支付、退款、售后和评价已购商品。订单进入 WMS 出库履约链路，小程序不直接扣库存。

完整使用教程见：

- [`../docs/sales-miniapp-user-guide.md`](../docs/sales-miniapp-user-guide.md)

## Scope

- 独立于 `wmspda/`，可以参考但不要修改 `wmspda/`。
- 使用 uni-app CLI 构建微信小程序和 H5，不接 DCloud 云服务。
- 后端商城接口使用 `/api/sale-mini/`。
- 项目只保留普通客户商城页面，不包含旧销售员移动开单工作台。
- 商品只展示 `SaleProductConfig` 已上架配置。
- 下单创建 `OutboundOrder / OutboundOrderLine`，由后端重新计算价格、单位、金额、优惠和库存。
- 购物车可统一提交多个后台履约包裹；后端拆成多张 WMS 出库单，前台展示为一个客户订单。多包裹订单当前走线下付款/平台确认，单包裹订单支持微信支付和优惠权益。
- 已完成且未退款的订单商品可以评价；评价经过后台审核后展示，支持三项评分、文字、匿名和最多 6 张图片。

## Product Reviews

部署评价功能前执行：

```bash
python manage.py migrate
```

评价图片保存到 Django `MEDIA_ROOT/sale-mini/reviews/`。生产环境需要由 Web 服务器正确提供 `MEDIA_URL`，并限制上传目录只作为静态媒体访问。

运营人员在 Django Admin 的“商城商品评价”中审核：

- “审核通过并发布”会让评价进入商品详情和全部评价列表。
- “驳回”允许用户修改后重新提交。
- “隐藏”会立即从商城评价和评分统计中移除，但保留原始记录。

## Quick Start

HBuilderX 使用方式：

- 打开目录：`/wms/sales-miniapp`
- 不要打开：`/wms/sales-miniapp/src` 或 `dist`
- 项目根目录已经包含 `App.vue`、`main.js`、`manifest.json`、`pages.json` 和 `pages/`
- 运行到微信小程序时，HBuilderX 会从项目根读取页面入口

命令行使用方式：

```bash
cd /wms/sales-miniapp
npm install
npm run dev:mp-weixin
```

常用命令：

```bash
npm run dev:h5
npm run test:structure
npm run build:h5
npm run build:mp-weixin
```

微信开发者工具导入路径：

- 开发预览：`/wms/sales-miniapp/dist/dev/mp-weixin`
- 发布构建：`/wms/sales-miniapp/dist/build/mp-weixin`

默认后端地址是 `http://192.168.1.6:8001`，由 `utils/request.js` 统一配置，登录页不会向用户显示服务器地址。
