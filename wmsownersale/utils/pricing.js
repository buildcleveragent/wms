const PRICE_SCALE = 4

function incrementDigits(digits) {
  const chars = digits.split('')
  for (let index = chars.length - 1; index >= 0; index -= 1) {
    if (chars[index] !== '9') {
      chars[index] = String(Number(chars[index]) + 1)
      return chars.join('')
    }
    chars[index] = '0'
  }
  return `1${chars.join('')}`
}

function parseDecimal(value, { round = false } = {}) {
  if (value === null || value === undefined || value === '') return null
  const source = String(value).trim()
  const match = source.match(/^\+?(\d+)(?:\.(\d*))?$/)
  if (!match) return null

  const whole = match[1].replace(/^0+(?=\d)/, '') || '0'
  const fraction = match[2] || ''
  if (!round && fraction.length > PRICE_SCALE) return null

  let scaled = `${whole}${fraction.slice(0, PRICE_SCALE).padEnd(PRICE_SCALE, '0')}`
    .replace(/^0+(?=\d)/, '') || '0'
  if (round && fraction.length > PRICE_SCALE && Number(fraction[PRICE_SCALE]) >= 5) {
    scaled = incrementDigits(scaled)
  }
  return scaled
}

function compareScaled(left, right) {
  const normalizedLeft = left.replace(/^0+(?=\d)/, '')
  const normalizedRight = right.replace(/^0+(?=\d)/, '')
  if (normalizedLeft.length !== normalizedRight.length) {
    return normalizedLeft.length > normalizedRight.length ? 1 : -1
  }
  if (normalizedLeft === normalizedRight) return 0
  return normalizedLeft > normalizedRight ? 1 : -1
}

function formatScaled(scaled) {
  const padded = scaled.padStart(PRICE_SCALE + 1, '0')
  const splitAt = padded.length - PRICE_SCALE
  return `${padded.slice(0, splitAt)}.${padded.slice(splitAt)}`
}

export function normalizePrice4(value) {
  const scaled = parseDecimal(value, { round: true })
  return scaled === null ? null : formatScaled(scaled)
}

export function comparePrice4(left, right) {
  const leftScaled = parseDecimal(left)
  const rightScaled = parseDecimal(right)
  if (leftScaled === null || rightScaled === null) return null
  return compareScaled(leftScaled, rightScaled)
}

export function initializePriceGuard(item) {
  const serverMinimum = item.minimum_sale_price ?? item.min_price
  if (serverMinimum === null || serverMinimum === undefined || serverMinimum === '') {
    item.minimum_sale_price = null
    item.min_price = null
    item.price_rule_error = ''
    return item
  }

  const minimum = normalizePrice4(serverMinimum)
  if (minimum === null) {
    item.min_price = null
    item.price_rule_error = '服务端最低价配置不正确'
    return item
  }

  item.minimum_sale_price = minimum
  item.min_price = Number(minimum)
  item.price_rule_error = ''
  return item
}

export function enforceMinimumPrice(item) {
  initializePriceGuard(item)
  if (item.price_rule_error) {
    return { valid: false, adjusted: false, error: item.price_rule_error }
  }

  const priceScaled = parseDecimal(item.price)
  const minimum = item.minimum_sale_price
  if (priceScaled === null) {
    return {
      valid: false,
      adjusted: false,
      minimum,
      error: '成交价必须是大于 0 且最多四位小数的数字',
    }
  }

  if (compareScaled(priceScaled, '0') <= 0) {
    if (minimum && comparePrice4(minimum, '0') > 0) item.price = Number(minimum)
    return {
      valid: false,
      adjusted: Boolean(minimum),
      minimum,
      error: '成交价必须大于 0',
    }
  }

  if (minimum && comparePrice4(formatScaled(priceScaled), minimum) < 0) {
    item.price = Number(minimum)
    return {
      valid: false,
      adjusted: true,
      minimum,
      error: `单价不得低于 ¥${minimum}`,
    }
  }

  item.price = Number(formatScaled(priceScaled))
  return { valid: true, adjusted: false, minimum, error: '' }
}

export function isPriceAllowed(item) {
  initializePriceGuard(item)
  if (item.price_rule_error) return false
  const priceScaled = parseDecimal(item.price)
  if (priceScaled === null || compareScaled(priceScaled, '0') <= 0) return false
  if (!item.minimum_sale_price) return true
  return comparePrice4(formatScaled(priceScaled), item.minimum_sale_price) >= 0
}
