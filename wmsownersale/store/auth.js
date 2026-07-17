import { defineStore } from 'pinia'
import { api, setToken } from '@/utils/request'

function storedUser() {
  try {
    return uni.getStorageSync('user') || null
  } catch (error) {
    return null
  }
}

function storedAccess() {
  try {
    return uni.getStorageSync('access') || ''
  } catch (error) {
    return ''
  }
}

function persistUser(user) {
  if (user) uni.setStorageSync('user', user)
  else uni.removeStorageSync('user')
}

export const useAuth = defineStore('auth', {
  state: () => ({
    user: storedUser(),
    access: storedAccess(),
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
      this.user = storedUser()
      this.capabilities = this.user?.capabilities || {}
      return !!this.access
    },
    async login(username, password) {
      const res = await api.login(username, password)
      this.access = res?.access || ''
      setToken(this.access)
      const profile = await api.authProfile()
      this.capabilities = profile?.capabilities || {}
      this.user = {
        ...(profile?.user || { username }),
        capabilities: this.capabilities,
      }
      persistUser(this.user)
      return this.user
    },
    logout() {
      this.user = null
      this.access = ''
      this.capabilities = {}
      setToken('')
      persistUser(null)
    },
  },
})
