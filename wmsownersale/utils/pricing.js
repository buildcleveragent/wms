const CENTS = 100

function optionalNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function configuredNumber(value, label) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  if (!Number.isFinite(number)) throw new RangeError(`${label}配置不正确`)
  return number
}

function ceilToCent(value) {
  return Math.ceil((value - Number.EPSILON) * CENTS) / CENTS
}

export function calculateMinimumSalePrice(origPrice, productMinPrice, maxDiscount) {
  const original = optionalNumber(origPrice)
  const configuredMinimum = configuredNumber(productMinPrice, '商品最低价')
  const discount = configuredNumber(maxDiscount, '最高折扣')
  const candidates = []

  if (configuredMinimum !== null) {
    if (configuredMinimum < 0) throw new RangeError('商品最低价不能小于 0')
    candidates.push(configuredMinimum)
  }

  if (discount !== null) {
    if (discount < 0 || discount > 100) throw new RangeError('最高折扣必须在 0 到 100 之间')
    if (original === null || original < 0) throw new RangeError('商品原价配置不正确')
    candidates.push(original * (100 - discount) / 100)
  }

  return candidates.length ? ceilToCent(Math.max(...candidates)) : null
}

export function initializePriceGuard(item) {
  const currentPrice = optionalNumber(item.price)
  const existingOriginal = optionalNumber(item.orig_price)
  item.orig_price = existingOriginal !== null ? existingOriginal : (currentPrice ?? 0)

  try {
    item.min_price = calculateMinimumSalePrice(
      item.orig_price,
      item.product_min_price,
      item.max_discount
    )
    item.price_rule_error = ''
  } catch (error) {
    item.min_price = null
    item.price_rule_error = error?.message || '商品价格配置不正确'
  }
  return item
}

export function enforceMinimumPrice(item) {
  initializePriceGuard(item)
  if (item.price_rule_error) {
    return { valid: false, adjusted: false, error: item.price_rule_error }
  }

  const minimum = item.min_price
  const value = optionalNumber(item.price)
  if (value === null || value <= 0) {
    if (minimum !== null && minimum > 0) item.price = minimum
    return { valid: false, adjusted: minimum !== null, minimum, error: '成交价必须大于 0' }
  }
  if (minimum !== null && value < minimum) {
    item.price = minimum
    return { valid: false, adjusted: true, minimum, error: `单价不得低于 ¥${minimum.toFixed(2)}` }
  }

  item.price = Math.round((value + Number.EPSILON) * CENTS) / CENTS
  return { valid: true, adjusted: false, minimum, error: '' }
}

export function isPriceAllowed(item) {
  initializePriceGuard(item)
  const value = optionalNumber(item.price)
  return !item.price_rule_error && value !== null && value > 0 && (
    item.min_price === null || value >= item.min_price
  )
}
