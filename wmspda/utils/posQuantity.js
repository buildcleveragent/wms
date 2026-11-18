// POS persists basic quantities to three decimal places. Sum scaled integers so
// mixed-unit presentation never introduces floating-point accumulation errors.
export function quantityNumber(value) {
  const text = String(value ?? '0')
  if (!/^-?\d+(\.\d+)?$/.test(text)) return '0'
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '').replace(/^-0$/, '0') : text
}

export function basicUnit(row = {}) {
  const unit = row.base_unit
  return (typeof unit === 'object' && unit !== null ? unit.name || unit.code : unit) ||
    row.base_unit_name || row.base_unit_code || row.unit || '单位未设置'
}

export function quantityWithUnit(value, row) {
  return `${quantityNumber(value)} ${basicUnit(row)}`
}

export function groupedQuantityText(rows, field = 'qty') {
  if (!Array.isArray(rows)) return '单位明细不可用'
  return rows.map(row => quantityWithUnit(row[field], row)).join('、') || '0'
}

export function groupBasicQuantities(lines) {
  const groups = new Map()
  lines.forEach((line, index) => {
    const id = line.base_unit_id ?? line.base_unit?.id
    // Old local drafts may lack identity. Keep them separate rather than merge
    // unrelated dictionary units just because their display labels match.
    const key = id == null ? `missing-${index}` : String(id)
    const value = String(line.qty ?? 0)
    const normalized = /^-?\d+(\.\d{1,3})?$/.test(value)
      ? value : Number.isFinite(Number(value)) ? Number(value).toFixed(3) : '0'
    const [whole, fraction = ''] = normalized.replace(/^-/, '').split('.')
    const scaled = (BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, '0'))) * (normalized.startsWith('-') ? -1n : 1n)
    const current = groups.get(key) || { base_unit_id: id, base_unit: basicUnit(line), scaled: 0n }
    current.scaled += scaled
    groups.set(key, current)
  })
  return [...groups.values()].map(({ scaled, ...row }) => {
    const magnitude = scaled < 0n ? -scaled : scaled
    return { ...row, qty: `${scaled < 0n ? '-' : ''}${magnitude / 1000n}.${String(magnitude % 1000n).padStart(3, '0')}` }
  })
}
