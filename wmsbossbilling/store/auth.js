import { defineStore } from 'pinia'
import {
  api,
  clearToken,
  getStoredRefreshToken,
  getStoredToken,
  getStoredUser,
  setStoredUser,
  setTokens,
} from '@/utils/request'

function fallbackUser(username = '') {
  return username
    ? {
        username,
        display_name: username,
      }
    : null
}

export const useAuth = defineStore('auth', {
  state: () => ({
    user: getStoredUser(),
    access: getStoredToken(),
    refresh: getStoredRefreshToken(),
  }),
  actions: {
    restore() {
      this.user = getStoredUser()
      this.access = getStoredToken()
      this.refresh = getStoredRefreshToken()
    },
    load() {
      this.restore()
    },
    ensureAuth() {
      this.restore()
      return !!this.access
    },
    async login(username, password) {
      const res = await api.login(username, password)
      this.access = res?.access || ''
      this.refresh = res?.refresh || ''
      setTokens(this.access, this.refresh)

      let profileUser = null
      try {
        const profile = await api.authProfile()
        profileUser = profile?.user
          ? { ...profile.user, capabilities: profile.capabilities || {} }
          : null
      } catch (error) {
        profileUser = fallbackUser(username)
      }

      this.user = profileUser || fallbackUser(username)
      setStoredUser(this.user)
      return this.user
    },
    async logout() {
      const refresh = this.refresh || getStoredRefreshToken()
      try {
        if (refresh) await api.logout(refresh)
      } catch (error) {
        // Local cleanup is mandatory even when the network is unavailable.
      }
      this.user = null
      this.access = ''
      this.refresh = ''
      clearToken()
    },
  },
})
