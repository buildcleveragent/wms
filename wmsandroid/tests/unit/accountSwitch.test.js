import { afterEach, beforeEach, expect, it, vi } from 'vitest'
let storage
beforeEach(() => {
  vi.resetModules()
  vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8001')
  storage = { access: 'account-a', refresh: 'refresh-a' }
  vi.stubGlobal('uni', {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: key => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value },
    removeStorageSync: key => { delete storage[key] },
    showToast: vi.fn(), reLaunch: vi.fn(),
  })
})
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })
it.each([200, 401])('rejects late refresh %s after switching accounts', async statusCode => {
  vi.useFakeTimers()
  let finish
  uni.request = vi.fn(opts => {
    if (opts.url.endsWith('/refresh/')) finish = opts.success
    else opts.success({ statusCode: 401, data: {} })
  })
  const { request, setAuthTokens } = await import('../../utils/request')
  const pending = request({ url: '/business/', method: 'POST' }).catch(error => error)
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
  setAuthTokens({ access: 'account-b', refresh: 'refresh-b' })
  finish({ statusCode, data: { access: 'late-account-a', refresh: 'late-refresh-a' } })
  expect((await pending).statusCode).toBe(409)
  expect(storage).toMatchObject({ access: 'account-b', refresh: 'refresh-b' })
  expect(uni.request).toHaveBeenCalledTimes(2)
  expect(uni.reLaunch).not.toHaveBeenCalled()
})
it.each([200, 401])('does not reuse a new account for an old request response %s', async statusCode => {
  let finish
  uni.request = vi.fn(opts => { finish = opts.success })
  const { request, setAuthTokens } = await import('../../utils/request')
  const pending = request({ url: '/business/', method: 'POST' }).catch(error => error)
  setAuthTokens({ access: 'account-b', refresh: 'refresh-b' })
  finish({ statusCode, data: { oldAccount: true } })
  expect((await pending).statusCode).toBe(409)
  expect(uni.request).toHaveBeenCalledTimes(1)
  expect(storage.access).toBe('account-b')
})
