export const historyCategories = {
  inbound: '正常入库', outbound: '正常出库', customer_return: '销售退货入库',
  supplier_return: '退货出库（退供／退货主）', reversal: '撤销回补',
  adjust_gain: '盘点／调整增加', adjust_loss: '盘点／调整减少',
  transfer_in: '货权转入', transfer_out: '货权转出', unknown: '待核对',
}

export const blankHistoryFilters = () => ({
  date_from: '', date_to: '', warehouse_id: '', product_id: '', document_key: '',
  search: '', document_no: '', batch: '', category: '', grain: 'all', order: 'desc',
})

export function historyQuery(filters) {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== '' && value != null))
}

export function historyURL(filters, view = 'events', eventId) {
  const params = { ...historyQuery(filters), view, ...(eventId ? {event_id: eventId} : {}) }
  return '/pages/reports/stock-history?' + Object.entries(params)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join('&')
}

export function periodFilters(filters, period) {
  const next = { ...filters, grain: 'all' }
  if (!period.period_start) return { ...next, date_from: '', date_to: '', undated: true }
  next.date_from = filters.date_from && filters.date_from > period.period_start ? filters.date_from : period.period_start
  next.date_to = filters.date_to && filters.date_to < period.period_end ? filters.date_to : period.period_end
  delete next.undated
  return next
}

export function historyTime(value) {
  return value ? value.replace('T', ' ').replace(/\.\d+/, '').replace(/\+08:00$/, '') : '时间未记录'
}

export function historyTimes(count, unverified = false) {
  if (unverified) return Number(count) > 0 ? `${count} 次，另有次数待核对` : '次数待核对'
  return `${count || 0} 次`
}
