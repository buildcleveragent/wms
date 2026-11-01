import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAssistedOutbound } from '@/store/assistedOutbound'

describe('assisted delivery note printing preference', () => {
  beforeEach(() => {
    globalThis.uni = { getStorageSync: vi.fn(), setStorageSync: vi.fn() }
    setActivePinia(createPinia())
  })

  it('defaults to no printing and retains it when choosing a customer', () => {
    const draft = useAssistedOutbound()
    expect(draft.form.print_after_create).toBe(false)
    expect(draft.loadPrintPreference(7, 1)).toBe(false)
    draft.setOwner({ id: 1 })
    draft.setCustomer({ id: 2 })
    expect(draft.form.print_after_create).toBe(false)
  })

  it('honors an explicit opt-in only for the matching user and warehouse', () => {
    const draft = useAssistedOutbound()
    draft.setPrintAfterCreate(true, 7, 1)
    const [key, value] = uni.setStorageSync.mock.calls[0]
    uni.getStorageSync.mockImplementation(requested => requested === key ? value : '')
    expect(draft.loadPrintPreference(7, 1)).toBe(true)
    expect(draft.loadPrintPreference(8, 1)).toBe(false)
    expect(draft.loadPrintPreference(7, 2)).toBe(false)
  })

  it('keeps printing disabled when preference storage is unavailable', () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {})
    uni.getStorageSync.mockImplementation(() => { throw new Error('storage unavailable') })
    expect(useAssistedOutbound().loadPrintPreference(7, 1)).toBe(false)
    warning.mockRestore()
  })
})
