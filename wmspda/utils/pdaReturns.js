import { request } from './request'

export const RETURN_BASE = '/api/pda/return-orders/'
export const returnRequest = (path = '', method = 'GET', data = {}) =>
  request({ url: `${RETURN_BASE}${path}`, method, data, silentError: true })

export function quantityUnits(value) {
  const text = String(value ?? '').trim() || '0'
  if (!/^\d{1,14}(\.\d{1,4})?$/.test(text)) throw new Error('数量须为非负数，最多四位小数')
  const [integer, decimal = ''] = text.split('.')
  return BigInt(integer) * 10000n + BigInt(decimal.padEnd(4, '0'))
}

export function returnLineInput(row) {
  const keys = ['available_qty', 'damaged_qty', 'new_damage_qty']
  const values = keys.map(key => quantityUnits(row[key]))
  const [normal, damaged, discovered] = values
  if (normal + damaged + discovered <= 0n) throw new Error('每行退货数量必须大于零')
  if (row.stock_available != null && normal + discovered > quantityUnits(row.stock_available)) throw new Error('正常退货与现场破损合计超过可用库存')
  if (row.stock_damaged != null && damaged > quantityUnits(row.stock_damaged)) throw new Error('退货数量超过已登记破损库存')
  if (row.serial_no && normal + damaged + discovered !== 10000n) throw new Error('每个序列号只能退一件')
  return { inventory_id: row.inventory_id, ...Object.fromEntries(keys.map(key => [key, String(row[key] || '0')])) }
}

export function commonSupplier(lines) {
  if (!lines.length || !lines[0].supplier_id) return null
  const id = lines[0].supplier_id
  return lines.every(row => row.supplier_id === id) ? { id, name: lines[0].supplier_name } : null
}

export function returnSession(user) {
  return user?.id && user?.warehouse_id ? `${user.id}:${user.warehouse_id}` : ''
}

export function pendingStorageKey(session) {
  if (!session) throw new Error('无法确定账号和仓库')
  return `pda-return-pending:${session}`
}
