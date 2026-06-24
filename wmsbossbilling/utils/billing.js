export const CHARGE_TYPE_LABELS = {
  RECEIVE: '收货',
  PUTAWAY: '上架',
  RELOC: '移库',
  PICK: '拣货',
  REVIEW: '复核',
  PACK: '打包',
  LOAD: '装车',
  DISPATCH: '发运/订单处理',
  COUNT: '盘点',
  ADJUST: '调整',
  STORAGE: '仓储/保管',
}

export const ACCRUAL_STATUS_LABELS = {
  OPEN: '未锁定',
  LOCKED: '已锁定',
  INVOICED: '已开票',
  VOID: '作废',
}

export const BILL_STATUS_LABELS = {
  DRAFT: '草稿',
  ISSUED: '已开票',
  PAID: '已收款',
  VOID: '作废',
}

export function asList(value) {
  if (Array.isArray(value)) return value
  if (Array.isArray(value?.results)) return value.results
  return []
}

export function toNumber(value) {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

export function money(value) {
  return `¥${toNumber(value).toFixed(2)}`
}

// export function qty(value) {
//   return toNumber(value).toFixed(4)
// }

export function qty(value) {
  if (value === null || value === undefined || value === '') return '-'

  const num = Number(value)
  if (!Number.isFinite(num)) return String(value)

  return String(Number(num.toFixed(4)))
}

export function percent(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '--'
  return `${num.toFixed(2)}%`
}

export function chargeTypeLabel(code) {
  return CHARGE_TYPE_LABELS[code] || code || '-'
}

export function accrualStatusLabel(code) {
  return ACCRUAL_STATUS_LABELS[code] || code || '-'
}

export function billStatusLabel(code) {
  return BILL_STATUS_LABELS[code] || code || '-'
}

export function formatDate(date) {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function defaultDateRange(days = 30) {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - (days - 1))
  return {
    start: formatDate(start),
    end: formatDate(end),
  }
}
