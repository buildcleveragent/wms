import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'


describe('refresh and business retry boundaries', () => {
  let storage
  beforeEach(() => {
    vi.resetModules()
    storage = { access: 'old-access', refresh: 'old-refresh', user: '{"id":1}' }
    vi.stubGlobal('uni', {
      getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
      getStorageSync: (key) => storage[key] || '',
      setStorageSync: (key, value) => { storage[key] = value },
      removeStorageSync: (key) => { delete storage[key] },
      showToast: vi.fn(), reLaunch: vi.fn(),
    })
  })
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals() })

  it.each([400, 403, 409, 500])('preserves the new session when business retry returns %s', async (statusCode) => {
    uni.request = vi.fn((opts) => {
      if (opts.url.endsWith('/api/auth/refresh/')) {
        opts.success({ statusCode: 200, data: { access: 'new-access', refresh: 'new-refresh' } })
      } else if (opts.header.Authorization === 'Bearer old-access') {
        opts.success({ statusCode: 401, data: {} })
      } else {
        opts.success({ statusCode, data: { detail: 'business rejection' } })
      }
    })
    const { request } = await import('../utils/request')
    await expect(request({ url: '/business/' })).rejects.toMatchObject({ statusCode })
    expect(storage).toMatchObject({ access: 'new-access', refresh: 'new-refresh', user: '{"id":1}' })
    expect(uni.reLaunch).not.toHaveBeenCalled()
    
  })

  it.each([0, 500, 503])('preserves recoverable credentials when refresh fails with %s', async (statusCode) => {
    uni.request = vi.fn((opts) => {
      if (!opts.url.endsWith('/api/auth/refresh/')) opts.success({ statusCode: 401, data: {} })
      else if (statusCode === 0) opts.fail({ errMsg: 'offline' })
      else opts.success({ statusCode, data: {} })
    })
    const { request } = await import('../utils/request')
    await expect(request({ url: '/business/' })).rejects.toMatchObject({ statusCode })
    expect(storage.access).toBe('old-access')
    expect(storage.refresh).toBe('old-refresh')
    
  })

  it('shares one in-flight refresh across five expired requests', async () => {
    let finishRefresh
    let refreshCount = 0
    uni.request = vi.fn((opts) => {
      if (opts.url.endsWith('/api/auth/refresh/')) { refreshCount++; finishRefresh = opts.success }
      else opts.success({ statusCode: opts.header.Authorization === 'Bearer old-access' ? 401 : 200, data: { ok: true } })
    })
    const { request } = await import('../utils/request')
    const pending = Array.from({ length: 5 }, () => request({ url: '/business/' }))
    await vi.waitFor(() => expect(finishRefresh).toBeTypeOf('function'))
    finishRefresh({ statusCode: 200, data: { access: 'new-access', refresh: 'new-refresh' } })
    await expect(Promise.all(pending)).resolves.toHaveLength(5)
    expect(refreshCount).toBe(1)
  })
  it.each([200, 401])('ignores old account refresh response %s after account switch', async (statusCode) => {
    vi.useFakeTimers()
    let finish
    uni.request = vi.fn((opts) => {
      if (opts.url.endsWith('/api/auth/refresh/')) finish = opts.success
      else opts.success({ statusCode: 401, data: {} })
    })
    const { request, setTokens } = await import('../utils/request')
    const pending = request({ url: '/business/', method: 'POST' }).catch(error => error)
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
    setTokens('account-b-access', 'account-b-refresh')
    finish({ statusCode, data: { access: 'late-account-a', refresh: 'late-refresh-a' } })
    await pending
    expect(storage.access).toBe('account-b-access')
    expect(storage.refresh).toBe('account-b-refresh')
    expect(uni.request).toHaveBeenCalledTimes(2)
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it.each([200, 401])('rejects old account response %s without replaying the write', async (statusCode) => {
    let finish
    uni.request = vi.fn(opts => { finish = opts.success })
    const { request, setTokens } = await import('../utils/request')
    const pending = request({ url: '/business/', method: 'POST' }).catch(error => error)
    setTokens('account-b-access', 'account-b-refresh')
    finish({ statusCode, data: { oldAccount: true } })
    const result = await pending
    expect(result.statusCode).toBe(409)
    expect(uni.request).toHaveBeenCalledTimes(1)
    expect(storage.access).toBe('account-b-access')
  })

})
