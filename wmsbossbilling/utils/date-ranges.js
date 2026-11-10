export const datePresets = [
  { key: 'today', label: '今天' }, { key: 'yesterday', label: '昨天' },
  { key: 'this_week', label: '本周' }, { key: 'last_week', label: '上周' },
  { key: 'this_month', label: '本月' }, { key: 'this_year', label: '本年' },
]
export const granularities = [
  { key: 'auto', label: '自动' }, { key: 'day', label: '按日' }, { key: 'week', label: '按周' },
  { key: 'month', label: '按月' }, { key: 'year', label: '按年' },
]
const iso = date => date.toISOString().slice(0, 10)
export function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return false
  const date = new Date(`${value}T00:00:00Z`)
  return Number(value.slice(0, 4)) >= 1000 && Number.isFinite(date.getTime()) && iso(date) === value
}
export function validRange(from, to, today) {
  return validDate(from) && validDate(to) && from <= to && to <= today
}
export function presetRange(key, today) {
  if (!validDate(today)) throw new Error('业务日期尚未加载')
  const end = new Date(`${today}T00:00:00Z`)
  const start = new Date(end)
  const offset = (end.getUTCDay() + 6) % 7
  if (key === 'yesterday') { start.setUTCDate(start.getUTCDate() - 1); end.setUTCDate(end.getUTCDate() - 1) }
  else if (key === 'this_week') start.setUTCDate(start.getUTCDate() - offset)
  else if (key === 'last_week') { start.setUTCDate(start.getUTCDate() - offset - 7); end.setUTCDate(end.getUTCDate() - offset - 1) }
  else if (key === 'this_month') start.setUTCDate(1)
  else if (key === 'this_year') start.setUTCMonth(0, 1)
  else if (key !== 'today') throw new Error('未知快捷日期')
  return { date_from: iso(start), date_to: iso(end) }
}
