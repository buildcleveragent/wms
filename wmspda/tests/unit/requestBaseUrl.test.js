import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.resetModules()
  vi.unstubAllGlobals()
})

describe('PDA API base URL', () => {
  it('always uses the hard-coded develop endpoint', async () => {
    vi.stubGlobal('uni', {
      getAccountInfoSync: vi.fn(() => ({ miniProgram: { envVersion: 'onsite' } })),
    })

    const { BASE_URL } = await import('@/utils/request')

    expect(BASE_URL).toBe('http://192.168.1.6:8001')
  })
})
