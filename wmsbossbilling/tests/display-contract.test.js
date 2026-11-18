import { describe, expect, it } from 'vitest'
import { compareAmounts, decimalText, hasWarning, inventoryAvailable, money, percent, productName, qty, sumAmounts } from '../utils/billing'

describe('boss business data presentation', () => {
  it.each([null, undefined, '', '  ', 'bad', NaN, Infinity, false, {}])('does not turn missing/invalid %s into zero', value => {
    expect(money(value, 'CNY')).toBe('-')
    expect(qty(value)).toBe('-')
    expect(percent(value)).toBe('--')
    expect(decimalText(value)).toBe('-')
  })
  it('preserves real zeros, signed reversals, precision and currency', () => {
    expect(money(0, 'CNY')).toBe('¥0.00')
    expect(qty('0.0000')).toBe('0')
    expect(percent(0)).toBe('0.00%')
    expect(money('-110.20', 'USD')).toBe('$-110.20')
    expect(money(12, null)).toBe('UNKNOWN 12.00')
    expect(qty('0.1234')).toBe('0.1234')
  })
  it('does not report a complete bill total when a line amount is missing', () => {
    expect(money(sumAmounts(100, -40, 0), 'CNY')).toBe('¥60.00')
    expect(sumAmounts(100, null)).toBeNull()
    expect(sumAmounts(null, 5)).toBeNull()
  })
  it('distinguishes product models and supports older responses', () => {
    expect(productName({ product_name: '汽水', brand_model: '甲·橙味' })).toBe('汽水 · 甲·橙味')
    expect(productName({ product_name: '汽水', brand_model: '乙·原味' })).toBe('汽水 · 乙·原味')
    expect(productName({ product_name: '旧名称' })).toBe('旧名称')
    expect(productName({ product_name: '旧名称', product_display_name: '新名称 · 型号' })).toBe('新名称 · 型号')
    expect(productName({ product_code: 'SKU-A' })).toBe('SKU-A')
  })
  it('distinguishes missing inventory snapshots from valid financial data', () => {
    const meta = { data_status: 'UNAVAILABLE', warnings: [{ code: 'HISTORICAL_INVENTORY_MISSING', count: 1 }] }
    expect(inventoryAvailable(meta)).toBe(false)
    expect(hasWarning(meta, 'HISTORICAL_STALE_METRIC_UNAVAILABLE')).toBe(false)
    expect(inventoryAvailable({ data_status: 'COMPLETE' })).toBe(true)
  })

  it('preserves model-supported decimal precision beyond JavaScript safe integers', () => {
    expect(money('9007199254740993.01', 'CNY')).toBe('¥9007199254740993.01')
    expect(qty('99999999999999.1234')).toBe('99999999999999.1234')
    expect(decimalText('99999999999999.1234', 4)).toBe('99999999999999.1234')
    expect(money(sumAmounts('9007199254740993.01', '-9007199254740993.00'), 'CNY')).toBe('¥0.01')
    expect(money(sumAmounts('9007199254740993.01', '0.09'), 'CNY')).toBe('¥9007199254740993.10')
    expect(compareAmounts('9007199254740993.01', '9007199254740993.02')).toBe(-1)
    expect(compareAmounts(null, '0')).toBe(-1)
  })

  it('rounds decimal ties consistently without binary floating-point artifacts', () => {
    expect(money('1.005', 'CNY')).toBe('¥1.01')
    expect(money('-1.005', 'CNY')).toBe('¥-1.01')
    expect(qty('0.00005')).toBe('0.0001')
  })
})
