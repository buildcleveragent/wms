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
  if (options.length) return options.map((option, index) => ({
    ...option,
    label: option.label || option.name || option.code || (index === 0 ? '基本单位' : '包装'),
    multiplier: Math.max(toNumber(option.multiplier, 1), 0.000001),
    barcode: String(option.barcode || '').trim(),
  }))
  return [{
    kind: 'base', package_id: null,
    label: product.base_unit?.name || product.base_unit?.code || '基本单位',
    multiplier: 1,
    barcode: String(product.unit_barcode || product.gtin || '').trim(),
  }]
}

function normalizedCode(value) { return String(value || '').trim().toLowerCase() }

export function unitIndexForBarcode(product, barcode) {
  const code = normalizedCode(barcode)
  if (!code) return 0
  const options = unitOptions(product)
  const matched = options.findIndex((option) => normalizedCode(option.barcode) === code)
  if (matched >= 0) return matched
  const baseCodes = [product.barcode, product.unit_barcode, product.gtin, product.code, product.sku]
  return baseCodes.some((value) => normalizedCode(value) === code) ? 0 : 0
}

export function cartLineKey(item) {
  const unit = item.unit_options?.[item.unit_index] || {}
  return `${item.product_id}:${unit.package_id || unit.code || item.unit_index || 0}`
}

export function lineBaseQty(item) {
  const unit = item.unit_options?.[item.unit_index] || { multiplier: 1 }
  return Math.max(toNumber(item.qty, 0), 0) * Math.max(toNumber(unit.multiplier, 1), 0.000001)
}

export function lineAmount(item) {
  return roundMoney(lineBaseQty(item) * Math.max(toNumber(item.price, 0), 0))
}

export function createCartItem(product, barcode = '') {
  const options = unitOptions(product)
  const unitIndex = unitIndexForBarcode(product, barcode)
  return {
    product_id: product.id,
    owner_id: product.owner_id ?? null,
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
  const key = cartLineKey(incoming)
  const existing = items.find((item) => cartLineKey(item) === key)
  const addBaseQty = lineBaseQty(incoming)
  if (existing) {
    if (lineBaseQty(existing) + addBaseQty > availableQty(product)) {
      return { items, error: '数量超过全仓可用库存' }
    }
    existing.available_qty = availableQty(product)
    existing.qty = toNumber(existing.qty, 0) + 1
    return { items: [...items], item: existing }
  }
  if (addBaseQty > incoming.available_qty) return { items, error: '该包装数量超过全仓可用库存' }
  return { items: [...items, incoming], item: incoming }
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
