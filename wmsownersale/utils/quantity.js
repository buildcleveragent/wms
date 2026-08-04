const MIN_BASE_QUANTITY = 0.001

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
