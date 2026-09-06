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
      if (options.url.endsWith('/api/pda/token/')) {
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
      if (options.url.endsWith('/api/pda/token/refresh/')) {
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
      if (options.url.endsWith('/api/pda/token/refresh/')) {
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

    const { BASE_URL, request } = await import('@/utils/request')
    const first = request({ url: '/api/one/' })
    const second = request({ url: '/api/two/' })

    await vi.waitFor(() => expect(finishRefresh).toBeTypeOf('function'))
    expect(
      uniMock.request.mock.calls.filter(([options]) =>
        options.url.endsWith('/api/pda/token/refresh/'),
      ),
    ).toHaveLength(1)
    finishRefresh()

    await expect(Promise.all([first, second])).resolves.toEqual([
      { url: `${BASE_URL}/api/one/` },
      { url: `${BASE_URL}/api/two/` },
    ])
  })

  it('clears tokens and returns to login when refresh is invalid', async () => {
    vi.useFakeTimers()
    storage.set('access', 'expired-access')
    storage.set('refresh', 'expired-refresh')
    uniMock.request.mockImplementation((options) => {
      if (options.url.endsWith('/api/pda/token/refresh/')) {
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

  it('restores persisted tokens after an app restart without logging in again', async () => {
    storage.set('access', 'persisted-access')
    storage.set('refresh', 'persisted-refresh')
    uniMock.request.mockImplementation((options) => {
      respond(options, 200, {
        user: { id: 4, username: 'operator' },
        perms: [],
        capabilities: {},
      })
    })

    const { useAuth } = await import('@/store/auth')
    const auth = useAuth()

    expect(auth.access).toBe('persisted-access')
    await auth.ensureProfile()
    expect(auth.profileLoaded).toBe(true)
    expect(uniMock.request).toHaveBeenCalledTimes(1)
    expect(uniMock.request.mock.calls[0][0].url).toContain('/api/auth/profile/')
  })
})
