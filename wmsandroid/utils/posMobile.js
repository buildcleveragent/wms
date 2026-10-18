import { createIdempotencyUuid } from '@/utils/request'

export const POS_PAYMENT_METHODS = Object.freeze([
  { value: 'WECHAT', label: '微信' },
  { value: 'ALIPAY', label: '支付宝' },
  { value: 'CASH', label: '现金' },
  { value: 'BANK_CARD', label: '银行卡' },
  { value: 'OTHER', label: '其他' },
])

export function normalizeRows(data) {
  if (Array.isArray(data)) return data
  return Array.isArray(data?.results) ? data.results : []
}

export function toNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

export function roundMoney(value) {
  return Math.round((toNumber(value) + Number.EPSILON) * 100) / 100
}

export function money(value) {
  return `¥${roundMoney(value).toFixed(2)}`
}

export function productName(product = {}) {
  return product.name || product.sku || product.code || String(product.id || '')
}

export function productSpec(product = {}) {
  return product.spec || product.specification || product.product_spec || product.package_spec || ''
}

export function availableQty(product = {}) {
  return toNumber(product.available_qty ?? product.available_stock ?? product.stock_qty, 0)
}

export function unitOptions(product = {}) {
  const options = Array.isArray(product.unit_options) ? product.unit_options : []
  const result = options.length ? options.map((option, index) => ({
    ...option,
    label: option.label || option.name || option.code || (index === 0 ? '基本单位' : '包装'),
    multiplier: Math.max(toNumber(option.multiplier, 1), 0.000001),
    barcode: String(option.barcode || '').trim(),
  })) : [{
    kind: 'base', package_id: null,
    label: product.base_unit?.name || product.base_unit?.code || '基本单位',
    multiplier: 1,
    barcode: String(product.unit_barcode || product.gtin || '').trim(),
  }]
  const matched = product.matched_barcode
  if (matched && Number(matched.multiplier) > 0 && !result.some((unit) => unit.package_id === matched.package_id && Number(unit.multiplier) === Number(matched.multiplier))) {
    result.push({ ...matched, kind: matched.package_id ? 'package' : 'base', multiplier: Number(matched.multiplier), barcode: '' })
  }
  return result
}

function normalizedCode(value) { return String(value || '').trim().toLowerCase() }

export function unitIndexForBarcode(product, barcode) {
  const code = normalizedCode(barcode)
  if (!code) return 0
  const options = unitOptions(product)
  const matched = product.matched_barcode
  if (!matched || !(Number(matched.multiplier) > 0)) return -1
  return options.findIndex((option) => option.package_id === matched.package_id && Number(option.multiplier) === Number(matched.multiplier))
}

export function cartLineKey(item) {
  const unit = item.unit_options?.[item.unit_index] || {}
  return `${item.owner_id}:${item.product_id}:${unit.package_id || unit.code || 'base'}:${Number(unit.multiplier)}`
}

export function lineBaseQty(item) {
  const unit = item.unit_options?.[item.unit_index] || { multiplier: 1 }
  const value = toNumber(item.qty, 0) * toNumber(unit.multiplier, 0)
  return Math.round((value + Number.EPSILON) * 1000) / 1000
}

export function lineAmount(item) {
  return roundMoney(lineBaseQty(item) * Math.max(toNumber(item.price, 0), 0))
}

export function createCartItem(product, barcode = '') {
  const options = unitOptions(product)
  const unitIndex = unitIndexForBarcode(product, barcode)
  return {
    cart_id: createIdempotencyUuid(),
    product_id: product.id,
    owner_id: product.owner_id ?? null,
    owner_name: product.owner_name || '',
    base_unit: product.base_unit,
    code: product.code || '',
    sku: product.sku || '',
    name: productName(product),
    spec: productSpec(product),
    available_qty: availableQty(product),
    price: toNumber(product.price, 0).toFixed(2),
    min_price: product.min_price ?? null,
    max_discount: product.max_discount ?? null,
    list_price: product.price ?? null,
    qty: 1,
    unit_options: options,
    unit_index: unitIndex,
  }
}

export function addProductToCart(items, product, barcode = '') {
  if (availableQty(product) <= 0) return { items, error: '库存不足，不能加入购物车' }
  const incoming = createCartItem(product, barcode)
  if (incoming.unit_index < 0) return { items, error: product.barcode_error || '无法确认条码包装，请更新服务端或手工选择商品和单位' }
  const key = cartLineKey(incoming)
  const next = items.map((item) => ({ ...item, available_qty: item.product_id === incoming.product_id && item.owner_id === incoming.owner_id ? incoming.available_qty : item.available_qty }))
  const existing = next.find((item) => cartLineKey(item) === key)
  if (existing) {
    existing.qty = toNumber(existing.qty, 0) + 1
  } else next.push(incoming)
  const error = validateCartStock(next)
  return error ? { items, error } : { items: next, item: existing || incoming }
}

export function validateCartStock(items) {
  const totals = new Map()
  for (const item of items) {
    const amount = lineBaseQty(item)
    if (!Number.isFinite(Number(item.qty)) || amount <= 0) return `${item.name} 数量必须大于 0 且为有效数字`
    const key = `${item.owner_id}:${item.product_id}`
    const previous = totals.get(key) || { qty: 0, stock: Infinity }
    const total = { qty: previous.qty + Math.round(amount * 1000), stock: Math.min(previous.stock, Math.floor(toNumber(item.available_qty, 0) * 1000 + 0.000001)) }
    totals.set(key, total)
    if (total.qty > total.stock) return `${item.name} 各包装合计超过全仓可用库存`
  }
  return ''
}

export function minimumPrice(item) {
  const candidates = []
  const minPrice = toNumber(item.min_price, NaN)
  if (Number.isFinite(minPrice) && minPrice >= 0) candidates.push(minPrice)
  const listPrice = toNumber(item.list_price, NaN)
  const maxDiscount = toNumber(item.max_discount, NaN)
  if (Number.isFinite(listPrice) && Number.isFinite(maxDiscount) && maxDiscount >= 0 && maxDiscount <= 100) {
    candidates.push(roundMoney(listPrice * (100 - maxDiscount) / 100))
  }
  return candidates.length ? Math.max(...candidates) : null
}

export function validateCart(items) {
  if (!items.length) return '购物车不能为空'
  const stockError = validateCartStock(items)
  if (stockError) return stockError
  for (const item of items) {
    if (lineBaseQty(item) <= 0) return `${item.name} 数量必须大于 0`
    if (lineBaseQty(item) > toNumber(item.available_qty, 0)) return `${item.name} 全仓可用库存不足`
    const price = toNumber(item.price, -1)
    if (price < 0) return `${item.name} 单价不能小于 0`
    const lowest = minimumPrice(item)
    if (lowest !== null && price + 0.00005 < lowest) return `${item.name} 最低单价 ${lowest.toFixed(2)}`
  }
  return ''
}

export function buildCheckoutSnapshot({ items, paymentMethod, amountReceived, referenceNo = '', remark = '' }) {
  const total = items.reduce((sum, item) => sum + lineAmount(item), 0)
  const received = paymentMethod === 'CASH' ? toNumber(amountReceived, 0) : total
  return {
    version: 1,
    created_at: Date.now(),
    idempotency_key: createIdempotencyUuid(),
    payload: {
      src_bill_no: '',
      idempotency_key: '',
      remark: String(remark || '').trim(),
      items: items.map((item) => ({
        product_id: item.product_id,
        qty: lineBaseQty(item).toFixed(3),
        price: toNumber(item.price, 0).toFixed(4),
        amount: lineAmount(item).toFixed(2),
      })),
      payment: {
        method: paymentMethod,
        amount_received: received.toFixed(2),
        reference_no: String(referenceNo || '').trim(),
      },
    },
  }
}

export function finalizeCheckoutSnapshot(snapshot) {
  const copy = JSON.parse(JSON.stringify(snapshot))
  copy.payload.idempotency_key = copy.idempotency_key
  return copy
}

export function draftStorageKey(user = {}) {
  const userId = user.id || 'anonymous'
  const warehouseId = user.warehouse_id || user.scopes?.warehouse_ids?.[0] || 'no-warehouse'
  return `wmsandroid:pos:draft:${userId}:${warehouseId}`
}

export function pendingStorageKey(user = {}) {
  return `${draftStorageKey(user)}:pending-checkout`
}
