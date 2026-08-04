const MIN_BASE_QUANTITY = 0.001

function hasAtMostThreeDecimals(value) {
  return /^(?:\d+|\d*\.\d{1,3})$/.test(value)
}

/**
 * Validate a quantity already expressed in the product's base unit.
 *
 * Keeping this check in a shared helper makes the page and Pinia store enforce
 * the same contract. `availableValue` is optional for edit contexts that do
 * not expose stock, but is authoritative whenever it is supplied.
 */
export function validateCartQuantity(rawValue, availableValue = null) {
  if (typeof rawValue === 'number' && (!Number.isFinite(rawValue) || rawValue < MIN_BASE_QUANTITY)) {
    return { valid: false, value: null, error: '基本数量不能小于 0.001' }
  }
  const text = rawValue == null ? '' : String(rawValue).trim()
  if (!text) {
    return { valid: false, value: null, error: '请输入基本数量' }
  }
  if (!hasAtMostThreeDecimals(text)) {
    return { valid: false, value: null, error: '数量最多保留三位小数' }
  }

  const value = Number(text)
  if (!Number.isFinite(value) || value < MIN_BASE_QUANTITY) {
    return { valid: false, value: null, error: '基本数量不能小于 0.001' }
  }

  const hasAvailable = availableValue !== null && availableValue !== undefined && availableValue !== ''
  const available = hasAvailable ? Number(availableValue) : null
  if (hasAvailable && (!Number.isFinite(available) || available < MIN_BASE_QUANTITY)) {
    return { valid: false, value: null, available, error: '该商品当前没有可用库存' }
  }
  if (hasAvailable && value > available) {
    return {
      valid: false,
      value: null,
      available,
      overAvailable: true,
      error: `基本数量不能超过可用库存 ${available}`,
    }
  }

  return { valid: true, value, available, error: '' }
}

export function validateDesiredQuantity(rawValue, multiplierValue = 1) {
  const text = rawValue == null ? '' : String(rawValue).trim()
  if (!text) {
    return { valid: false, error: '请输入大于 0 的出货数量' }
  }

  const saleQty = Number(text)
  const multiplier = Number(multiplierValue)
  if (!Number.isFinite(saleQty) || saleQty <= 0) {
    return { valid: false, error: '出货数量必须是大于 0 的数字' }
  }
  if (!hasAtMostThreeDecimals(text)) {
    return { valid: false, error: '出货数量最多保留三位小数' }
  }
  if (!Number.isFinite(multiplier) || multiplier <= 0) {
    return { valid: false, error: '商品单位换算配置无效' }
  }

  const rawBaseQty = saleQty * multiplier
  if (!Number.isFinite(rawBaseQty) || rawBaseQty < MIN_BASE_QUANTITY) {
    return { valid: false, error: '换算后的基本数量不能小于 0.001' }
  }
  const baseQty = Number(rawBaseQty.toFixed(3))
  if (baseQty < MIN_BASE_QUANTITY) {
    return { valid: false, error: '换算后的基本数量不能小于 0.001' }
  }
  return { valid: true, saleQty, multiplier, baseQty, error: '' }
}

export function previewBaseQuantity(rawValue, multiplierValue = 1) {
  const result = validateDesiredQuantity(rawValue, multiplierValue)
  return result.valid ? result.baseQty : 0
}
