import { describe, expect, it } from 'vitest'
import { presetRange, validRange } from '../utils/date-ranges'
describe('server business-date ranges', () => {
  it('accepts multi-year ranges without a day cap', () => {
    expect(validRange('2000-01-01', '2026-11-02', '2026-11-02')).toBe(true)
    expect(validRange('2026-02-30', '2026-11-02', '2026-11-02')).toBe(false)
    expect(validRange('2026-11-02', '2026-11-03', '2026-11-02')).toBe(false)
    expect(validRange('2026-11-02', '2026-11-01', '2026-11-02')).toBe(false)
  })
  it('uses Monday week boundaries across years', () => {
    expect(presetRange('this_week', '2025-01-01')).toEqual({ date_from: '2024-12-30', date_to: '2025-01-01' })
    expect(presetRange('last_week', '2025-01-01')).toEqual({ date_from: '2024-12-23', date_to: '2024-12-29' })
    expect(presetRange('yesterday', '2024-03-01')).toEqual({ date_from: '2024-02-29', date_to: '2024-02-29' })
    expect(presetRange('this_year', '2026-11-02')).toEqual({ date_from: '2026-01-01', date_to: '2026-11-02' })
  })
})
