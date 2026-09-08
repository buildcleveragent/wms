import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.resetModules()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('PDA API base URL', () => {
  it('uses an explicit build endpoint', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://wms.example.test/')
    vi.stubGlobal('uni', {})
    const { BASE_URL } = await import('@/utils/request')
    expect(BASE_URL).toBe('https://wms.example.test')
  })

  it('retains the onsite default when no build endpoint is supplied', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    vi.stubGlobal('uni', {
      getAccountInfoSync: vi.fn(() => ({ miniProgram: { envVersion: 'onsite' } })),
    })

    const { BASE_URL, api, downloadMissingGtinProducts } = await import('@/utils/request')

    expect(BASE_URL).toBe('http://192.168.1.6:8001')
    expect(downloadMissingGtinProducts).toBeTypeOf('function')
    expect(api.downloadMissingGtinProducts).toBe(downloadMissingGtinProducts)
  })
})
