// POS quantities have three decimal places, unit prices four, and money two.
// Use decimal integers so the displayed charge matches Django's ROUND_HALF_UP.
function scaledDecimal(value, places) {
  const match = /^(\d*)(?:\.(\d*))?$/.exec(String(value ?? '').trim())
  if (!match) return 0n
  const fraction = match[2] || ''
  const digits = (match[1] || '0') + fraction.slice(0, places).padEnd(places, '0')
  return BigInt(digits) + (Number(fraction[places] || 0) >= 5 ? 1n : 0n)
}

function divideHalfUp(numerator, denominator) {
  return denominator ? (numerator * 2n + denominator) / (2n * denominator) : 0n
}

export function saleLineAmount(qty, price) {
  const cents = divideHalfUp(scaledDecimal(qty, 3) * scaledDecimal(price, 4), 100000n)
  return Number(cents) / 100
}

export function negotiatedUnitPrice(qty, amount) {
  const price = divideHalfUp(scaledDecimal(amount, 2) * 100000n, scaledDecimal(qty, 3))
  return Number(price) / 10000
}

export function hasCatalogPrice(price) {
  return price !== null && price !== undefined && price !== ''
}
