import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let storage
let uniMock

function respond(options, statusCode, data) {
  queueMicrotask(() => options.success({ statusCode, data }))
}

beforeEach(() => {
  vi.resetModules()
  storage = new Map()
  uniMock = {
    getAccountInfoSync: vi.fn(() => ({ miniProgram: { envVersion: 'develop' } })),
    getStorageSync: vi.fn((key) => storage.get(key) || ''),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    request: vi.fn(),
    uploadFile: vi.fn(),
    showToast: vi.fn(),
    reLaunch: vi.fn(),
  }
  vi.stubGlobal('uni', uniMock)
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('PDA token refresh', () => {
  it('stores both tokens returned by login', async () => {
    uniMock.request.mockImplementation((options) => {
      if (options.url.endsWith('/api/token/')) {
        respond(options, 200, {
          access: 'login-access',
          refresh: 'login-refresh',
          user: { id: 4, username: 'operator' },
        })
        return
      }
      if (options.url.endsWith('/api/auth/logout/')) {
        expect(options.data).toEqual({ refresh: 'login-refresh' })
        expect(options.header.Authorization).toBeUndefined()
        respond(options, 204, null)
        return
      }
      respond(options, 200, {
        user: { id: 4, username: 'operator' },
        perms: [],
        capabilities: {},
      })
    })

    const { useAuth } = await import('@/store/auth')
    const auth = useAuth()
    await auth.login('operator', 'secret')

    expect(storage.get('access')).toBe('login-access')
    expect(storage.get('refresh')).toBe('login-refresh')

    await auth.logout()
    expect(storage.has('access')).toBe(false)
    expect(storage.has('refresh')).toBe(false)
  })

  it('refreshes a rejected access token and retries the original request', async () => {
    storage.set('access', 'expired-access')
    storage.set('refresh', 'refresh-one')
    uniMock.request.mockImplementation((options) => {
      if (options.url.endsWith('/api/token/refresh/')) {
        expect(options.data).toEqual({ refresh: 'refresh-one' })
        respond(options, 200, { access: 'fresh-access', refresh: 'refresh-two' })
        return
      }
      if (options.header.Authorization === 'Bearer expired-access') {
        respond(options, 401, { code: 'token_not_valid' })
        return
      }
      respond(options, 200, { ok: true })
    })

    const { request } = await import('@/utils/request')
    await expect(request({ url: '/api/auth/profile/' })).resolves.toEqual({ ok: true })

    expect(storage.get('access')).toBe('fresh-access')
    expect(storage.get('refresh')).toBe('refresh-two')
    expect(uniMock.request).toHaveBeenCalledTimes(3)
  })

  it('shares one refresh across concurrent 401 responses', async () => {
    storage.set('access', 'expired-access')
    storage.set('refresh', 'refresh-one')
    let finishRefresh
    uniMock.request.mockImplementation((options) => {
      if (options.url.endsWith('/api/token/refresh/')) {
        finishRefresh = () =>
          options.success({
            statusCode: 200,
            data: { access: 'fresh-access', refresh: 'refresh-two' },
          })
        return
      }
      if (options.header.Authorization === 'Bearer expired-access') {
        respond(options, 401, { code: 'token_not_valid' })
        return
      }
      respond(options, 200, { url: options.url })
    })

    const { request } = await import('@/utils/request')
    const first = request({ url: '/api/one/' })
    const second = request({ url: '/api/two/' })

    await vi.waitFor(() => expect(finishRefresh).toBeTypeOf('function'))
    expect(
      uniMock.request.mock.calls.filter(([options]) =>
        options.url.endsWith('/api/token/refresh/'),
      ),
    ).toHaveLength(1)
    finishRefresh()

    await expect(Promise.all([first, second])).resolves.toEqual([
      { url: '/api/one/' },
      { url: '/api/two/' },
    ])
  })

  it('retries product import upload with the refreshed token', async () => {
    storage.set('access', 'expired-access')
    storage.set('refresh', 'refresh-one')
    uniMock.request.mockImplementation((options) => {
      respond(options, 200, { access: 'fresh-access', refresh: 'refresh-two' })
    })
    uniMock.uploadFile.mockImplementation((options) => {
      if (options.header.Authorization === 'Bearer expired-access') {
        respond(options, 401, JSON.stringify({ code: 'token_not_valid' }))
        return
      }
      respond(options, 200, JSON.stringify({ created_count: 1 }))
    })

    const { uploadProductImportExcel } = await import('@/utils/request')
    await expect(uploadProductImportExcel('/tmp/products.xlsx', 1)).resolves.toEqual({
      created_count: 1,
    })

    expect(uniMock.uploadFile).toHaveBeenCalledTimes(2)
    expect(uniMock.uploadFile.mock.calls[1][0]).toMatchObject({
      timeout: 600000,
      header: { Authorization: 'Bearer fresh-access' },
    })
  })

  it('clears tokens and returns to login when refresh is invalid', async () => {
    vi.useFakeTimers()
    storage.set('access', 'expired-access')
    storage.set('refresh', 'expired-refresh')
    uniMock.request.mockImplementation((options) => {
      if (options.url.endsWith('/api/token/refresh/')) {
        respond(options, 401, { code: 'token_not_valid' })
        return
      }
      respond(options, 401, { code: 'token_not_valid' })
    })

    const { request } = await import('@/utils/request')
    const pending = request({ url: '/api/auth/profile/' })
    const rejection = expect(pending).rejects.toMatchObject({
      authExpired: true,
      statusCode: 401,
    })
    await vi.runAllTimersAsync()
    await rejection

    expect(storage.has('access')).toBe(false)
    expect(storage.has('refresh')).toBe(false)
    expect(uniMock.showToast).toHaveBeenCalledWith(
      expect.objectContaining({ title: '登录已超时，需要重新登录' }),
    )
    expect(uniMock.reLaunch).toHaveBeenCalledWith({ url: '/pages/login' })
  })
})
