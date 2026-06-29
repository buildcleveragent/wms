# wmssalemini

独立的买家商城 uni-app 小程序工程，用于客户/门店/采购方浏览已上架商品、加入购物车、使用优惠券和积分、提交订单、微信支付、退款和售后。订单进入 WMS 出库履约链路，小程序不直接扣库存。

完整使用教程见：

- [`../docs/sales-miniapp-user-guide.md`](../docs/sales-miniapp-user-guide.md)

## Scope

- 独立于 `wmspda/`，可以参考但不要修改 `wmspda/`。
- 使用 uni-app CLI 构建微信小程序和 H5，不接 DCloud 云服务。
- 后端商城接口使用 `/api/sale-mini/`。
- 项目只保留普通客户商城页面，不包含旧销售员移动开单工作台。
- 商品只展示 `SaleProductConfig` 已上架配置。
- 下单创建 `OutboundOrder / OutboundOrderLine`，由后端重新计算价格、单位、金额、优惠和库存。

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

默认后端地址是 `http://192.168.1.6:8001`，也可以在登录页修改服务地址。
