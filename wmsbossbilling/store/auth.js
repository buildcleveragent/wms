import { defineStore } from 'pinia'
import { api, clearToken, getStoredToken, getStoredUser, setStoredUser, setToken } from '@/utils/request'

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
  }),
  actions: {
    restore() {
      this.user = getStoredUser()
      this.access = getStoredToken()
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
      setToken(this.access)

      let profileUser = null
      try {
        const profile = await api.authProfile()
        profileUser = profile?.user || null
      } catch (error) {
        profileUser = fallbackUser(username)
      }

      this.user = profileUser || fallbackUser(username)
      setStoredUser(this.user)
      return this.user
    },
    logout() {
      this.user = null
      this.access = ''
      clearToken()
    },
  },
})
