export function qtyText(value) {
  const n = Number(value || 0)
  if (Number.isInteger(n)) return String(n)
  return n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

export function clampQty(value, min = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.max(n, min) : min
}
