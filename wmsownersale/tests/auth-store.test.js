import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  storage: {},
  login: vi.fn(),
  authProfileWithAccess: vi.fn(),
  setTokens: vi.fn(),
  clearSessionStorage: vi.fn(),
  resetOrder: vi.fn(),
}))

vi.mock('@/utils/request', () => ({
  api: {
    login: mocks.login,
    authProfileWithAccess: mocks.authProfileWithAccess,
    authProfile: vi.fn(),
    logout: vi.fn(),
  },
  getAccessToken: () => mocks.storage.access || '',
  getRefreshToken: () => mocks.storage.refresh || '',
  setTokens: mocks.setTokens,
  clearSessionStorage: mocks.clearSessionStorage,
}))

vi.mock('@/store/cart', () => ({
  useCart: () => ({ resetOrder: mocks.resetOrder }),
}))

describe('登录会话原子提交', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mocks.storage = {}
    mocks.login.mockReset()
    mocks.authProfileWithAccess.mockReset()
    mocks.setTokens.mockReset().mockImplementation((access, refresh) => {
      mocks.storage.access = access
      mocks.storage.refresh = refresh
    })
    mocks.resetOrder.mockReset()
    vi.stubGlobal('uni', {
      getStorageSync: (key) => mocks.storage[key] || '',
      setStorageSync: (key, value) => { mocks.storage[key] = value },
      removeStorageSync: (key) => { delete mocks.storage[key] },
    })
  })

  it('profile 失败时不持久化刚签发的令牌', async () => {
    mocks.login.mockResolvedValue({ access: 'new-access', refresh: 'new-refresh' })
    mocks.authProfileWithAccess.mockRejectedValue({ statusCode: 503 })
    const { useAuth } = await import('@/store/auth')
    const auth = useAuth()

    await expect(auth.login('owner', 'password')).rejects.toMatchObject({ statusCode: 503 })

    expect(mocks.authProfileWithAccess).toHaveBeenCalledWith('new-access')
    expect(mocks.setTokens).not.toHaveBeenCalled()
    expect(mocks.storage).toEqual({})
    expect(auth.access).toBe('')
    expect(auth.user).toBeNull()
  })

  it('profile 成功后一次提交令牌、用户和能力', async () => {
    mocks.login.mockResolvedValue({ access: 'new-access', refresh: 'new-refresh' })
    mocks.authProfileWithAccess.mockResolvedValue({
      user: { id: 7, username: 'owner' },
      capabilities: { can_view_owner_operations: true },
    })
    const { useAuth } = await import('@/store/auth')
    const auth = useAuth()

    await expect(auth.login('owner', 'password')).resolves.toMatchObject({ id: 7 })

    expect(mocks.setTokens).toHaveBeenCalledOnce()
    expect(mocks.storage).toMatchObject({
      access: 'new-access',
      refresh: 'new-refresh',
      user: {
        id: 7,
        capabilities: { can_view_owner_operations: true },
      },
    })
    expect(mocks.resetOrder).toHaveBeenCalledOnce()
  })
})
