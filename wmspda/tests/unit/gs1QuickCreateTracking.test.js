import { describe, expect, it } from 'vitest'

import { inferGs1TrackingControls } from '@/utils/gs1QuickCreate'

describe('GS1 quick-create tracking inference', () => {
  it('keeps batch and expiry management disabled when tracking fields are blank', () => {
    expect(inferGs1TrackingControls({
      lot_no: '   ',
      shelf_life_days: '',
      inbound_valid_days: '',
      expiry_warning_days: '',
      mfg_date: '',
      exp_date: '',
    })).toEqual({ batch_control: false, lot_no: '', expiry_control: false })
  })

  it('enables batch management only when a batch number is entered', () => {
    expect(inferGs1TrackingControls({ lot_no: ' LOT-2026 ' })).toEqual({
      batch_control: true,
      lot_no: 'LOT-2026',
      expiry_control: false,
    })
  })

  it.each([
    { shelf_life_days: '365' },
    { inbound_valid_days: '30' },
    { expiry_warning_days: '15' },
    { mfg_date: '2026-08-16' },
    { exp_date: '2027-08-16' },
  ])('enables expiry management when an expiry field is entered: %o', (fields) => {
    expect(inferGs1TrackingControls(fields).expiry_control).toBe(true)
  })
})
