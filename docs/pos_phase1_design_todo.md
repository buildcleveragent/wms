# POS 收银系统第一阶段设计与 TODO

## 目标

第一阶段提供仓库/门店收银台的基础能力：

- 扫码或搜索商品，返回商品、价格和当前仓库可售库存。
- 录入购物车后结账，生成一张销售出库单。
- 复用现有 `Owner`、`Customer`、`Product`、`InventoryDetail`、`OutboundOrder`、`OutboundOrderLine`。
- 不新增无依据字段，不新增数据库表。

## 现有模型复用口径

- 登录用户：从 `request.user.owner_id` 和 `request.user.warehouse_id` 推断收银作用域。
- 商品：使用 `Product.code`、`Product.sku`、`Product.gtin`、`Product.unit_barcode`、`Product.carton_barcode` 做检索。
- 售价：优先使用 `Product.price`；最低价使用 `Product.min_price`；最高折扣使用 `Product.max_discount`。
- 库存：按 `InventoryDetail.owner/product/warehouse` 汇总 `available_qty`。
- 客户：结账必须传 `customer_id`；客户必须属于当前用户货主。
- 销售单：结账创建 `OutboundOrder(outbound_type="SALES", submit_status="SUBMITTED")`。
- 销售明细：每个购物车条目创建 `OutboundOrderLine(base_qty, base_price)`。

## 第一阶段 API

### `GET /api/pos/products/`

参数：

- `search`：商品编码、SKU、条码或名称关键字。
- `barcode`：精确扫码字段，匹配 `gtin/unit_barcode/carton_barcode/ProductPackage.barcode`。
- `page`、`page_size`：分页。

返回：

- 商品 ID、编码、SKU、名称、基本单位。
- 建议售价、最低价、最高折扣。
- 当前仓库可售库存。
- 可用销售单位选项：基本单位和已有 `ProductPackage`。

### `POST /api/pos/checkout/`

输入：

- `customer_id`：客户 ID。
- `src_bill_no`：可选，POS 小票号/外部单号；同货主下重复会拒绝。
- `remark`：可选备注。
- `items`：商品、数量、成交单价。

校验：

- 用户必须绑定货主和仓库。
- 客户和商品必须属于当前货主。
- 数量必须大于 0。
- 成交单价不得低于 `Product.min_price`。
- 如 `Product.max_discount` 有值，成交单价不得低于 `Product.price * (1 - max_discount / 100)`。
- 可售库存必须满足购物车数量。

结果：

- 创建并返回 `OutboundOrder` 读模型。

## 暂不包含

- 现金/微信/支付宝等支付流水表。
- 退款、退货、换货。
- 小票打印模板。
- 自动拣货、自动过账、自动扣减库存。
- 前端完整收银页面。

这些能力需要明确业务字段和流程后进入后续阶段。

## TODO

- [x] 阅读现有模型、序列化器、视图、URL 和前端请求结构。
- [x] 形成第一阶段设计。
- [x] 新增 POS 商品查询接口。
- [x] 新增 POS 结账接口。
- [x] 在主 URL 中挂载 POS API。
- [x] 在 `wmspda/utils/request.js` 中加入 POS 请求方法。
- [x] 补后端测试覆盖商品查询、价格校验、库存校验、结账建单。
- [x] 运行 `python3 manage.py check` 和 POS 测试。
