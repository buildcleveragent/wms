import { beforeEach, describe, expect, it, vi } from 'vitest'

const cartReset = vi.fn()
vi.mock('@/store/cart', () => ({ useCart: () => ({ resetOrder: cartReset }) }))

function makeUni({ storage, onRequest, onUpload }) {
  return {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getStorageSync: (key) => storage[key] || '',
    setStorageSync: (key, value) => { storage[key] = value },
    removeStorageSync: (key) => { delete storage[key] },
    request: onRequest,
    uploadFile: onUpload,
    showToast: vi.fn(),
    reLaunch: vi.fn(),
  }
}

describe('认证上传与轮换令牌', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.useFakeTimers()
    cartReset.mockReset()
  })

  it('上传第一次 401 后单飞刷新并仅重试一次', async () => {
    const storage = { access: 'old-access', refresh: 'old-refresh' }
    const uploadHeaders = []
    let uploadCount = 0
    const uni = makeUni({
      storage,
      onRequest: (options) => options.success({
        statusCode: 200,
        data: { access: 'new-access', refresh: 'new-refresh' },
      }),
      onUpload: (options) => {
        uploadHeaders.push(options.header.Authorization)
        uploadCount += 1
        options.success(uploadCount === 1
          ? { statusCode: 401, data: '{"detail":"expired"}' }
          : { statusCode: 200, data: '{"ok":true}' })
      },
    })
    vi.stubGlobal('uni', uni)
    const { uploadAuthenticatedFile } = await import('@/utils/request')

    await expect(uploadAuthenticatedFile({ url: '/upload/', filePath: '/tmp/a.xlsx' }))
      .resolves.toEqual({ ok: true })
    expect(uploadHeaders).toEqual(['Bearer old-access', 'Bearer new-access'])
    expect(storage).toMatchObject({ access: 'new-access', refresh: 'new-refresh' })
  })

  it('刷新令牌失效时统一清理会话', async () => {
    const storage = { access: 'old-access', refresh: 'bad-refresh', user: { id: 1 } }
    const uni = makeUni({
      storage,
      onRequest: (options) => options.success({ statusCode: 401, data: { detail: 'invalid' } }),
      onUpload: (options) => options.success({ statusCode: 401, data: '{}' }),
    })
    vi.stubGlobal('uni', uni)
    const { uploadAuthenticatedFile } = await import('@/utils/request')

    await expect(uploadAuthenticatedFile({ url: '/upload/', filePath: '/tmp/a.xlsx' }))
      .rejects.toMatchObject({ statusCode: 401 })
    expect(storage).toEqual({})
    expect(cartReset).toHaveBeenCalledOnce()
  })

  it('上传 500 不刷新也不重试', async () => {
    const storage = { access: 'access', refresh: 'refresh' }
    const request = vi.fn()
    const upload = vi.fn((options) => options.success({ statusCode: 500, data: '{}' }))
    vi.stubGlobal('uni', makeUni({ storage, onRequest: request, onUpload: upload }))
    const { uploadAuthenticatedFile } = await import('@/utils/request')

    await expect(uploadAuthenticatedFile({ url: '/upload/', filePath: '/tmp/a.xlsx' }))
      .rejects.toMatchObject({ statusCode: 500 })
    expect(upload).toHaveBeenCalledOnce()
    expect(request).not.toHaveBeenCalled()
  })

  it('退出在 401 刷新轮换后提交新 refresh token', async () => {
    const storage = { access: 'expired-access', refresh: 'refresh-one' }
    const logoutBodies = []
    let logoutAttempts = 0
    const uni = makeUni({
      storage,
      onUpload: vi.fn(),
      onRequest: (options) => {
        if (options.url.endsWith('/api/auth/refresh/')) {
          options.success({
            statusCode: 200,
            data: { access: 'fresh-access', refresh: 'refresh-two' },
          })
          return
        }
        logoutBodies.push(options.data)
        logoutAttempts += 1
        options.success(logoutAttempts === 1
          ? { statusCode: 401, data: {} }
          : { statusCode: 204, data: {} })
      },
    })
    vi.stubGlobal('uni', uni)
    const { api } = await import('@/utils/request')

    await api.logout()
    expect(logoutBodies).toEqual([
      { refresh: 'refresh-one' },
      { refresh: 'refresh-two' },
    ])
  })

  it('临时 access 校验 profile 不覆盖既有会话', async () => {
    const storage = { access: 'old-access', refresh: 'old-refresh' }
    let authorization
    const uni = makeUni({
      storage,
      onUpload: vi.fn(),
      onRequest: (options) => {
        authorization = options.header.Authorization
        options.success({ statusCode: 200, data: { user: { id: 7 } } })
      },
    })
    vi.stubGlobal('uni', uni)
    const { api } = await import('@/utils/request')

    await expect(api.authProfileWithAccess('temporary-access'))
      .resolves.toEqual({ user: { id: 7 } })
    expect(authorization).toBe('Bearer temporary-access')
    expect(storage).toEqual({ access: 'old-access', refresh: 'old-refresh' })
  })
})
