import { defineStore } from 'pinia'
import {
  api,
  clearSessionStorage,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from '@/utils/request'
import { useCart } from '@/store/cart'

function storedUser() {
  try {
    return uni.getStorageSync('user') || null
  } catch (error) {
    return null
  }
}

function storedAccess() {
  return getAccessToken()
}

function persistUser(user) {
  if (user) uni.setStorageSync('user', user)
  else uni.removeStorageSync('user')
}

function commitSession(access, refresh, user) {
  const previous = {
    access: getAccessToken(),
    refresh: getRefreshToken(),
    user: storedUser(),
  }
  try {
    setTokens(access, refresh)
    persistUser(user)
  } catch (error) {
    // uni storage has no transaction primitive, so restore the complete prior
    // session if any write in the logical commit fails.
    setTokens(previous.access, previous.refresh)
    persistUser(previous.user)
    throw error
  }
}

export const useAuth = defineStore('auth', {
  state: () => ({
    user: storedUser(),
    access: storedAccess(),
    refresh: getRefreshToken(),
    capabilities: storedUser()?.capabilities || {},
  }),
  getters: {
    roles: (state) => state.user?.roles || [],
    isOwnerManager() {
      return this.roles.includes('owner_manager')
    },
    isOwnerSalesperson() {
      return this.roles.includes('owner_salesperson')
    },
  },
  actions: {
    ensureAuth() {
      this.access = storedAccess()
      this.refresh = getRefreshToken()
      this.user = storedUser()
      this.capabilities = this.user?.capabilities || {}
      return !!this.access
    },
    async bootstrap() {
      this.ensureAuth()
      if (!this.access && !this.refresh) return { status: 'anonymous' }
      try {
        const profile = await api.authProfile()
        this.access = getAccessToken()
        this.refresh = getRefreshToken()
        this.capabilities = profile?.capabilities || {}
        this.user = { ...(profile?.user || {}), capabilities: this.capabilities }
        persistUser(this.user)
        return { status: 'authenticated' }
      } catch (error) {
        if (error?.statusCode === 0) return { status: 'offline', error }
        this.clearLocalSession()
        return { status: 'anonymous', error }
      }
    },
    async login(username, password) {
      const res = await api.login(username, password)
      const access = res?.access || ''
      const refresh = res?.refresh || ''
      if (!access || !refresh) throw new Error('登录响应缺少令牌。')

      const profile = await api.authProfileWithAccess(access)
      const capabilities = profile?.capabilities || {}
      const user = {
        ...(profile?.user || { username }),
        capabilities,
      }
      commitSession(access, refresh, user)

      this.access = access
      this.refresh = refresh
      this.capabilities = capabilities
      this.user = user
      try { useCart().resetOrder() } catch (error) {}
      return user
    },
    clearLocalSession() {
      useCart().resetOrder()
      this.user = null
      this.access = ''
      this.refresh = ''
      this.capabilities = {}
      clearSessionStorage()
      persistUser(null)
    },
    async logout() {
      const refresh = getRefreshToken()
      try {
        if (refresh) await api.logout()
      } catch (error) {
        // Local logout must always complete even when the network is down.
      } finally {
        this.clearLocalSession()
      }
    },
  },
})
