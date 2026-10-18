import { describe, it, expect, vi } from 'vitest'
vi.mock('../../utils/request', () => ({ request: vi.fn() }))
import { commonSupplier, quantityUnits, returnLineInput, returnSession, pendingStorageKey } from '../../utils/pdaReturns'

describe('physical return input invariants', () => {
  it('uses exact decimal quantities and excludes reserved/locked amounts', () => {
    expect(quantityUnits('1.0001')).toBe(10001n)
    const line = { inventory_id: 1, stock_available: '10', stock_damaged: '5', available_qty: '2', damaged_qty: '3', new_damage_qty: '1' }
    expect(returnLineInput(line)).toEqual({ inventory_id: 1, available_qty: '2', damaged_qty: '3', new_damage_qty: '1' })
    expect(() => returnLineInput({ ...line, new_damage_qty: '9' })).toThrow('超过可用库存')
    expect(() => returnLineInput({ ...line, damaged_qty: '6' })).toThrow('已登记破损')
    expect(() => returnLineInput({ ...line, serial_no: 'SN1' })).toThrow('一件')
  })
  it.each(['-1', '0.00001', 'NaN', 'Infinity', '1e3'])('rejects invalid quantity %s', value => {
    expect(() => quantityUnits(value)).toThrow()
  })
  it('requires one reliable supplier across all selected inventory', () => {
    expect(commonSupplier([{ supplier_id: 1, supplier_name: '原供货方' }, { supplier_id: 1 }])).toEqual({ id: 1, name: '原供货方' })
    expect(commonSupplier([{ supplier_id: 1 }, { supplier_id: null }])).toBeNull()
    expect(commonSupplier([{ supplier_id: 1 }, { supplier_id: 2 }])).toBeNull()
  })
  it('isolates pending requests by account and warehouse', () => {
    expect(pendingStorageKey(returnSession({ id: 1, warehouse_id: 2 }))).not.toBe(pendingStorageKey(returnSession({ id: 2, warehouse_id: 2 })))
    expect(pendingStorageKey(returnSession({ id: 1, warehouse_id: 2 }))).not.toBe(pendingStorageKey(returnSession({ id: 1, warehouse_id: 3 })))
    expect(() => pendingStorageKey(returnSession(null))).toThrow()
  })
})
