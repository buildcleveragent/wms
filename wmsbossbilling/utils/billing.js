import Decimal from 'decimal.js'

const Amount = Decimal.clone({ precision: 40, rounding: Decimal.ROUND_HALF_UP })

export const CHARGE_TYPE_LABELS = {
  RECEIVE: '收货',
  PUTAWAY: '上架',
  RELOC: '移库',
  PICK: '拣货',
  REVIEW: '复核',
  PACK: '打包',
  LOAD: '装车',
  DISPATCH: '发运/订单处理',
  COUNT: '盘点',
  ADJUST: '调整',
  STORAGE: '仓储/保管',
}

export const ACCRUAL_STATUS_LABELS = {
  OPEN: '未锁定',
  LOCKED: '已锁定',
  INVOICED: '已开票',
  VOID: '作废',
}

export const BILL_STATUS_LABELS = {
  DRAFT: '草稿',
  ISSUED: '已开票',
  PAID: '已收款',
  VOID: '作废',
}

export function asList(value) {
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.results)) return value.results
  return []
}

// Missing or invalid data must never become a business zero.
export function toNumber(value) {
  if (value == null || typeof value === 'boolean' || typeof value === 'object') return null
  if (typeof value === 'string' && !value.trim()) return null
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

export function decimalText(value, digits = 3) {
  const num = toDecimal(value)
  return num == null ? '-' : num.toFixed(digits)
}

function toDecimal(value) {
  try {
    if (toNumber(value) == null) return null
    return new Amount(typeof value === 'string' ? value.trim() : value)
  } catch { return null }
}

// Keep decimal strings through aggregation; Number cannot preserve all model-supported amounts.
export function sumAmounts(...values) {
  const numbers = values.map(toDecimal)
  return numbers.some(value => value == null) ? null
    : numbers.reduce((total, value) => total.plus(value), new Amount(0)).toFixed()
}

export function compareAmounts(left, right) {
  const a = toDecimal(left), b = toDecimal(right)
  return a && b ? a.comparedTo(b) : a ? 1 : b ? -1 : 0
}

export function money(value, currency) {
  const num = toDecimal(value)
  if (num == null) return '-'
  const code = String(currency || 'UNKNOWN').trim().toUpperCase() || 'UNKNOWN'
  const amount = num.toFixed(2)
  const symbols = { CNY: '¥', USD: '$', EUR: '€', GBP: '£' }
  return symbols[code] ? `${symbols[code]}${amount}` : `${code} ${amount}`
}

export function qty(value) {
  const num = toDecimal(value)
  return num == null ? '-' : num.toDecimalPlaces(4).toFixed()
}

export function percent(value) {
  const num = toNumber(value)
  return num == null ? '--' : `${num.toFixed(2)}%`
}

export function productName(item = {}) {
  return item.product_display_name || [item.product_name, item.brand_model].filter(Boolean).join(' · ')
    || item.product_code || item.product_sku || '-'
}

export function hasWarning(meta, code) {
  return (meta?.warnings || []).some(row => row.code === code)
}

export function inventoryAvailable(meta) {
  return !hasWarning(meta, 'HISTORICAL_INVENTORY_MISSING')
}

export function chargeTypeLabel(code) {
  return CHARGE_TYPE_LABELS[code] || code || '-'
}

export function accrualStatusLabel(code) {
  return ACCRUAL_STATUS_LABELS[code] || code || '-'
}

export function billStatusLabel(code) {
  return BILL_STATUS_LABELS[code] || code || '-'
}

export function formatDate(date) {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function defaultDateRange(days = 30) {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - (days - 1))
  return {
    start: formatDate(start),
    end: formatDate(end),
  }
}
