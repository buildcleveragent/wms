import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => { vi.resetModules(); vi.unstubAllGlobals() })

describe('document-specific print URLs', () => {
  it('exchanges the bearer token for a ticket without putting it into the URL', async () => {
    let sent
    vi.stubGlobal('uni', {
      getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
      getStorageSync: (key) => key === 'access' ? 'full-access-secret' : '',
      request: (options) => { sent = options; options.success({ statusCode: 200, data: { ticket: 'document-only-ticket' } }) },
    })
    const { BASE_URL, withAuthToken } = await import('@/utils/request')
    const url = await withAuthToken(`${BASE_URL}/api/pos/sales/7/print/`)
    expect(url).toBe(`${BASE_URL}/api/pos/sales/7/print/?ticket=document-only-ticket`)
    expect(url).not.toContain('full-access-secret')
    expect(sent.url).toBe(`${BASE_URL}/api/print-tickets/`)
    expect(sent.data).toEqual({ path: '/api/pos/sales/7/print/' })
    expect(sent.header.Authorization).toBe('Bearer full-access-secret')
  })

  it('refuses to send credentials to a different server', async () => {
    vi.stubGlobal('uni', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }) })
    const { withAuthToken } = await import('@/utils/request')
    await expect(withAuthToken('https://unrelated.invalid/print/')).rejects.toThrow('当前服务')
  })
})
