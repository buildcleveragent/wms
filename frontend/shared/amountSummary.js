export function amountSummaryLabel(summary = {}) {
  const items = summary.items || summary.lines || []
  const unpriced = summary.unpriced_line_count ?? items.filter(item => item.is_priced === false).length
  if (summary.amount_status === 'UNPRICED' || (items.length && unpriced === items.length)) return '未计价'
  const amount = Number(summary.priced_amount ?? summary.total_amount ?? 0).toFixed(2)
  return unpriced ? `已计价小计 ¥${amount}，另有 ${unpriced} 行未计价` : `¥ ${amount}`
}
