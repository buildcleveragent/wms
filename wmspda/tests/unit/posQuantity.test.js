import { describe, expect, it } from 'vitest'
import { basicUnit, groupBasicQuantities, groupedQuantityText, quantityWithUnit } from '../../utils/posQuantity'

describe('POS basic quantity presentation', () => {
  it('sums by identity, preserving different units with the same name', () => {
    const rows = groupBasicQuantities([
      { base_unit_id: 1, base_unit: '袋', qty: '0.1' },
      { base_unit_id: 1, base_unit: '袋', qty: '0.2' },
      { base_unit_id: 2, base_unit: '千克', qty: '1.250' },
      { base_unit_id: 3, base_unit: '袋', qty: '2' },
    ])
    expect(rows).toHaveLength(3)
    expect(groupedQuantityText(rows)).toBe('0.3 袋、1.25 千克、2 袋')
  })
  it('renders real count units, zero and negative return quantities', () => {
    expect(quantityWithUnit('2.000', { base_unit: '件' })).toBe('2 件')
    expect(quantityWithUnit('0.000', { base_unit: '袋' })).toBe('0 袋')
    expect(groupedQuantityText([{ base_unit: '千克', net_qty: '-0.125' }], 'net_qty')).toBe('-0.125 千克')
    expect(groupedQuantityText([])).toBe('0')
  })
  it('does not fabricate units or mixed totals with older responses', () => {
    expect(groupedQuantityText(undefined)).toBe('单位明细不可用')
    expect(quantityWithUnit('10', {})).toBe('10 单位未设置')
    expect(basicUnit({ base_unit: { id: 1, code: 'KG', name: '千克' } })).toBe('千克')
    expect(groupBasicQuantities([{ unit: '袋', qty: 2 }, { unit: '袋', qty: 3 }])).toHaveLength(2)
  })
  it('keeps large precise decimal totals and basic-unit labels for packaged sales', () => {
    const rows = groupBasicQuantities([
      { base_unit_id: 1, base_unit_name: '瓶', qty: '9007199254740.001', unit: '箱' },
      { base_unit_id: 1, base_unit_name: '瓶', qty: '0.002' },
    ])
    expect(groupedQuantityText(rows)).toBe('9007199254740.003 瓶')
    expect(quantityWithUnit(24, { base_unit_name: '瓶', unit: '箱' })).toBe('24 瓶')
  })
})
