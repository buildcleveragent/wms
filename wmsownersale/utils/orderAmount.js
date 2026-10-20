// Match the order's Decimal calculation: round each line to cents using
// ROUND_HALF_EVEN, then sum. Integer arithmetic keeps decimal ties exact.
function decimalParts(value) {
  const text = String(value ?? '').trim()
  if (text.length > 64 || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) return null
  const [whole, fraction = ''] = text.split('.')
  return { coefficient: BigInt((whole || '0') + fraction), scale: fraction.length }
}

function lineCents(qty, price) {
  const quantity = decimalParts(qty), unitPrice = decimalParts(price)
  if (!quantity || !unitPrice) return 0n
  const product = quantity.coefficient * unitPrice.coefficient
  const scale = quantity.scale + unitPrice.scale
  if (scale <= 2) return product * 10n ** BigInt(2 - scale)
  const divisor = 10n ** BigInt(scale - 2)
  const cents = product / divisor, twiceRemainder = (product % divisor) * 2n
  return cents + (twiceRemainder > divisor || (twiceRemainder === divisor && cents % 2n === 1n) ? 1n : 0n)
}

function formatCents(cents) {
  return `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`
}

export function lineAmount(qty, price) {
  return formatCents(lineCents(qty, price))
}

export function orderAmount(items) {
  return formatCents(items.reduce((total, item) => total + lineCents(item.qty, item.price), 0n))
}
