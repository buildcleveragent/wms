import { buildQuery } from './request'

export const activityCategories = [
  { value: '', label: '全部业务' }, { value: 'order', label: '出库单' },
  { value: 'pos', label: 'POS 销售' }, { value: 'inventory', label: '库存增减' },
  { value: 'task', label: '仓内作业' }, { value: 'billing', label: '计费' },
  { value: 'receipt', label: '回款' }, { value: 'alert', label: '异常' },
]

export function openActivity(target, scope = {}) {
  if (!target) return
  const routes = { bill: '/pages/billing/bill_detail', accrual: '/pages/billing/accrual_detail' }
  const url = routes[target.kind] || '/pages/activity/detail'
  const query = buildQuery({ ...scope, kind: target.kind, id: target.id })
  uni.navigateTo({ url: `${url}?${query}` })
}

export function fieldText(value) {
  if (value === null || value === undefined || value === '') return '未记录'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'string' && /^\d{4}-\d\d-\d\dT/.test(value)) return new Date(value).toLocaleString()
  return String(value)
}
