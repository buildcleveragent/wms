# POS 使用手册

本文面向仓库管理员、门店收银员和实施人员，说明当前系统已实现的门店 POS 能力。示例地址使用本地后端 `http://127.0.0.1:8000`。

## 1. 功能概述

当前 POS 可以完成：

- 扫码或搜索商品。
- 查看商品建议售价、最低价、最高折扣限制和当前仓库可售库存。
- 记录收款方式、实收金额和找零。
- 提交收银结账请求并生成 POS 销售单。
- 结账成功后按商品货主创建一张或多张销售出库单 `OutboundOrder`。
- 按 FEFO 自动扣减当前仓库库存，写入 `InventoryTransaction` 并刷新 `InventorySummary`。
- 返回小票数据用于前端展示或后续打印。
- 支持整单撤销，回补库存并撤销收款状态。
- PDA 前端提供 `POS收银` 页面入口。

当前暂不包含：

- 微信、支付宝、银行卡网关联机支付；当前为手工确认已收款。
- 物理小票打印机驱动和打印队列。
- 部分退款、部分退货、换货流程。
- 普通仓库出库的拣货、复核、打包流程。

因此，当前 POS “结账”代表门店即时销售完成：系统已记录收款、创建销售出库单、扣减库存并生成库存流水；但不代表已经完成第三方支付通道确认或物理打印。

## 2. 使用前准备

使用 POS API 前，需要确认以下基础数据已维护：

- POS 用户不需要绑定 `owner`，但必须绑定 `warehouse`，用于查询和结账时计算当前仓库可售库存。
- 如需指定客户，客户资料需提前维护；客户只会应用到与其货主一致的销售出库单。
- 如果结账时未指定客户，或某个货主与所选客户不一致，系统会自动使用该货主下 `code=CASH` 的散客客户；不存在时自动创建 `CASH / 散客`。
- 商品资料已维护，状态为启用。
- 商品编码、SKU、GTIN、单品条码、箱条码或包装条码已按实际扫码需要维护。
- 商品价格字段已维护：
  - `price`：建议售价。
  - `min_price`：最低成交价，可为空。
  - `max_discount`：最高折扣百分比，可为空。
- 当前仓库已有可售库存，系统按 `InventoryDetail.available_qty` 汇总并在结账时扣减。

如果用户未绑定仓库，商品查询和结账都会失败。用户未绑定货主也可以搜索商品和提交结账。

## 3. 启动后端

首次使用前，按项目环境准备后端：

```bash
cp .env.example .env
```

在 `.env` 中确认至少配置：

- `SECRET_KEY`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `ALLOWED_HOSTS`

确认 MySQL 可连接后执行：

```bash
python manage.py migrate --noinput
python manage.py runserver
```

默认本地服务地址为：

```text
http://127.0.0.1:8000
```

## 4. 登录与认证

POS API 需要登录后访问。可以调用：

```http
POST /api/auth/login/
```

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"pos-admin","password":"your-password"}'
```

成功后返回 `access` 和 `refresh`。后续请求在 Header 中携带：

```http
Authorization: Bearer <access>
```

PDA 前端当前的登录封装使用 `/api/token/`，同样返回 JWT token。

## 5. 商品查询

### 5.1 按条码查询

接口：

```http
GET /api/pos/products/?barcode=...
```

条码会匹配以下字段：

- `Product.code`
- `Product.sku`
- `Product.gtin`
- `Product.unit_barcode`
- `Product.carton_barcode`
- `ProductPackage.barcode`

示例：

```bash
curl "http://127.0.0.1:8000/api/pos/products/?barcode=6901234567892" \
  -H "Authorization: Bearer <access>"
```

### 5.2 按关键字搜索

接口：

```http
GET /api/pos/products/?search=...&page=1&page_size=20
```

`search` 支持按商品编码、SKU、名称、GTIN、单品条码、箱条码模糊查询。

示例：

```bash
curl "http://127.0.0.1:8000/api/pos/products/?search=牛奶&page=1&page_size=20" \
  -H "Authorization: Bearer <access>"
```

### 5.3 返回字段

商品查询返回分页结构，核心字段如下：

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 101,
      "code": "SKU-001",
      "sku": "SKU-001",
      "name": "示例商品",
      "gtin": "6901234567892",
      "unit_barcode": "UNIT-001",
      "carton_barcode": "CTN-001",
      "base_unit": {
        "id": 1,
        "code": "PCS",
        "name": "件"
      },
      "price": "10.00",
      "min_price": "8.00",
      "max_discount": "20.00",
      "available_qty": "12.0000",
      "unit_options": [
        {
          "kind": "base",
          "package_id": null,
          "label": "件",
          "multiplier": "1",
          "barcode": "UNIT-001"
        },
        {
          "kind": "package",
          "package_id": 55,
          "label": "箱",
          "multiplier": "12.0000",
          "barcode": "CTN-001"
        }
      ]
    }
  ]
}
```

字段说明：

- `price`：建议售价。
- `min_price`：最低成交价；为空表示未配置最低价。
- `max_discount`：最高折扣百分比；为空表示未配置折扣限制。
- `available_qty`：当前用户绑定仓库内该商品的可售库存汇总。
- `unit_options`：可选销售单位。结账接口当前按基本数量提交，选择包装单位时前端或调用方需要按 `multiplier` 换算为基本数量。

## 6. 收银结账

接口：

```http
POST /api/pos/checkout/
```

请求体字段：

- `customer_id`：可选，客户 ID；不传时按每个商品货主使用散客客户 `CASH`。
- `src_bill_no`：可选，POS 小票号或外部单号；同一货主下不能重复。
- `idempotency_key`：建议填写，重试同一笔销售时避免重复建单和重复扣库存。
- `remark`：可选，备注。
- `payment`：必填，收款信息。
- `payment.method`：必填，支持 `CASH`、`WECHAT`、`ALIPAY`、`BANK_CARD`、`OTHER`。
- `payment.amount_received`：现金必填且必须大于或等于应收金额；非现金可不传，传入时必须等于应收金额。
- `payment.reference_no`：可选，支付流水号或备注。
- `items`：必填，购物车明细。
- `items[].product_id`：必填，商品 ID。
- `items[].qty`：必填，基本单位数量，必须大于 `0`。
- `items[].price`：必填，成交单价。

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/pos/checkout/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access>" \
  -d '{
    "customer_id": 2001,
    "src_bill_no": "POS-RECEIPT-001",
    "idempotency_key": "POS-RECEIPT-001",
    "remark": "门店收银",
    "payment": {
      "method": "CASH",
      "amount_received": "20.00",
      "reference_no": ""
    },
    "items": [
      {
        "product_id": 101,
        "qty": "2.000",
        "price": "9.0000"
      }
    ]
  }'
```

成功后返回 POS 销售单、收款、小票数据和销售出库单列表，HTTP 状态码为 `201`。返回结构为：

```json
{
  "sale": {},
  "payment": {},
  "orders": [],
  "order_count": 1,
  "src_bill_no": "POS-RECEIPT-001",
  "receipt": {}
}
```

业务结果为：

- 创建一张 `PosSale`，代表本次门店销售。
- 创建一张 `PosPayment`，代表本次总收款。
- 按商品货主拆分创建 `OutboundOrder`；一笔 POS 结账可能生成一张或多张销售出库单。
- `outbound_type` 为 `SALES`。
- `delivery_method` 为 `PICKUP`。
- `submit_status` 为 `SUBMITTED`。
- `approval_status` 为 `WHS_APPROVED`，订单按 POS 即时销售关闭。
- 每个购物车条目创建一条 `OutboundOrderLine`，写入 `base_qty` 和 `base_price`。
- 按 FEFO 自动扣减库存，写入 `InventoryTransaction(ISSUE)`。

如果未传 `customer_id`，系统会按每个商品货主自动使用 `code=CASH` 的散客客户；如果该客户不存在，会自动创建 `CASH / 散客`。

### 6.1 查询小票与撤销

```http
GET /api/pos/sales/{id}/
GET /api/pos/sales/{id}/receipt/
POST /api/pos/sales/{id}/void/
```

`void` 为整单撤销：系统会将 POS 销售单和收款标记为已撤销，销售出库单标记为取消，并按原库存流水写入反向 `InventoryTransaction(RECEIVE)` 回补库存。

## 7. 结账校验规则

提交结账时系统会进行以下校验：

- 当前用户必须绑定仓库 `warehouse`。
- `items` 不能为空。
- `payment.method` 必须有效。
- 现金实收金额不能小于应收金额；非现金实收金额如传入，必须等于应收金额。
- `customer_id` 如果传入，对应客户必须存在；它只应用到客户货主一致的销售出库单，其他货主使用散客客户。
- `src_bill_no` 如果传入，同一货主下不能重复。
- 每个 `product_id` 对应商品必须存在且启用。
- `qty` 必须大于 `0`。
- `price` 不能低于商品 `min_price`。
- 如果商品同时配置了 `price` 和 `max_discount`，成交价不能低于 `price * (1 - max_discount / 100)`。
- 当前仓库可售库存必须大于或等于购物车内该商品合计数量。

同一商品在购物车中出现多行时，库存校验会按商品汇总数量。

## 8. 常见错误

### 当前用户未绑定仓库

现象：

```json
{"detail": "当前用户未绑定仓库(warehouse)，无法查询 POS 商品。"}
```

或：

```json
["当前用户未绑定仓库(warehouse)，无法收银。"]
```

处理：在用户资料中绑定正确的 `warehouse`。

### 客户不存在

现象：

```json
{"customer_id": ["客户不存在。"]}
```

处理：确认客户资料存在；普通散客收银可以不传 `customer_id`。

### 商品不存在或已停用

现象：

```json
{"items": ["商品不存在或已停用：[101]"]}
```

处理：确认商品 ID 正确，并且商品已启用。

### 成交价低于最低价

现象：

```json
{"price": ["SKU-001 成交价不能低于最低价 8.00。"]}
```

处理：提高成交价，或由有权限人员调整商品最低价。

### 成交价超过最高折扣限制

现象：

```json
{"price": ["SKU-001 成交价超过最高折扣限制，最低可售 8.0000。"]}
```

处理：提高成交价，或由有权限人员调整商品建议售价和最高折扣。

### 可售库存不足

现象：

```json
{"items": ["SKU-001 可售库存不足，可售 10.0000，需要 11.000。"]}
```

处理：减少销售数量，或先完成入库、库存调整、释放占用等库存处理。

### 实收金额不足

现象：

```json
{"payment": ["现金实收金额不能小于应收金额。"]}
```

处理：重新输入实收金额，或选择正确支付方式。

### 小票号或外部单号重复

现象：

```json
{"src_bill_no": ["POS 小票号/外部单号已存在。"]}
```

处理：更换唯一的 `src_bill_no`，或检查是否重复提交了同一笔销售。

## 9. PDA 前端使用

PDA 首页已提供 `POS收银` 入口，页面路径为：

```text
/pages/pos/index
```

页面当前支持：

- 可选搜索并选择客户；未选客户时按散客结账。
- 扫码或搜索商品。
- 查看商品售价和可售库存。
- 将商品加入购物车。
- 选择销售单位，并按单位换算为基本数量提交。
- 修改销售数量和基本单位成交价。
- 选择支付方式、输入实收金额并显示找零。
- 提交结账，生成 POS 销售单、收款记录、销售出库单和库存流水。
- 展示最近一次小票摘要。

`wmspda/utils/request.js` 中对应请求方法为：

```js
api.posProducts(params)
api.posCheckout(payload)
api.posSaleDetail(id)
api.posSaleReceipt(id)
api.posSaleVoid(id, payload)
```

示例：

```js
await api.posProducts({
  barcode: '6901234567892',
  page: 1,
  page_size: 20,
})

await api.posCheckout({
  customer_id: 2001,
  src_bill_no: 'POS-RECEIPT-001',
  idempotency_key: 'POS-RECEIPT-001',
  remark: '门店收银',
  payment: {
    method: 'CASH',
    amount_received: '20.00',
    reference_no: '',
  },
  items: [
    {
      product_id: 101,
      qty: '2.000',
      price: '9.0000',
    },
  ],
})
```

注意：当前 POS 页面已完成门店即时销售闭环；物理打印、部分退款退货和第三方支付联机仍需后续扩展。

## 10. 验证方式

如果只阅读或更新本文档，不需要运行测试。

如后续同步修改 POS 接口、序列化字段或业务逻辑，建议运行：

```bash
python3 manage.py check
pytest -q allapp/pos/tests.py --create-db
```
