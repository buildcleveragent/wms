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
      this.access = res?.access || ''
      this.refresh = res?.refresh || ''
      setTokens(this.access, this.refresh)
      const profile = await api.authProfile()
      useCart().resetOrder()
      this.capabilities = profile?.capabilities || {}
      this.user = {
        ...(profile?.user || { username }),
        capabilities: this.capabilities,
      }
      persistUser(this.user)
      return this.user
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
