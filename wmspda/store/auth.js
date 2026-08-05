import { defineStore } from 'pinia'
import { api, getToken, setToken } from '@/utils/request'

let profilePromise = null
let profileSequence = 0

function emptyState() {
  return {
    user: null,
    access: getToken(),
    profile: null,
    perms: [],
    profileLoaded: false,
    profileLoading: false,
  }
}

export const useAuth = defineStore('auth', {
  state: emptyState,

  getters: {
    canProcessAssistedOutbound: (state) =>
      state.profileLoaded === true &&
      state.profile?.capabilities?.can_process_warehouse_assisted_outbound === true,
    canImportProducts: (state) =>
      state.profileLoaded === true &&
      state.profile?.capabilities?.can_import_products === true,
    canRequestReplenishment: (state) =>
      state.profileLoaded === true &&
      state.profile?.capabilities?.can_request_replenishment === true,
    canApproveReplenishment: (state) =>
      state.profileLoaded === true &&
      state.profile?.capabilities?.can_approve_replenishment === true,
    canRetryReplenishmentPosting: (state) =>
      state.profileLoaded === true &&
      state.profile?.capabilities?.can_retry_replenishment_posting === true,
  },

  actions: {
    clearProfile() {
      profileSequence += 1
      profilePromise = null
      this.user = null
      this.profile = null
      this.perms = []
      this.profileLoaded = false
      this.profileLoading = false
    },

    async loadProfile({ force = false } = {}) {
      if (!getToken()) {
        this.clearProfile()
        return null
      }
      if (this.profileLoaded && !force) return this.profile
      if (profilePromise) return profilePromise

      const previousState = {
        user: this.user,
        profile: this.profile,
        perms: [...this.perms],
        profileLoaded: this.profileLoaded,
      }
      this.profileLoading = true
      this.profileLoaded = false
      this.perms = []
      const requestSequence = ++profileSequence
      const currentPromise = (async () => {
        try {
          const result = await api.profile()
          const perms = Array.isArray(result?.perms) ? result.perms : []
          const capabilities = result?.capabilities
          if (!result?.user || !capabilities || typeof capabilities !== 'object') {
            throw new Error('权限资料格式不正确')
          }
          if (requestSequence !== profileSequence) {
            throw new Error('权限资料请求已失效')
          }
          this.profile = result
          this.user = result.user
          this.perms = perms
          this.profileLoaded = true
          return result
        } catch (error) {
          if (requestSequence === profileSequence) {
            const statusCode = Number(error?.statusCode || error?.code)
            if (statusCode === 401) {
              this.clearProfile()
              this.access = ''
              setToken('')
            } else if (statusCode === 403) {
              // 403 只撤销代办能力；保留 token 和已经加载的普通功能资料。
              this.user = previousState.user
              this.profile = previousState.profile
              this.perms = previousState.perms
              this.profileLoaded = previousState.profileLoaded
              this.invalidateAssistedCapability()
            } else {
              // 网络、5xx 和响应格式异常均 fail closed，并允许下次重试。
              this.clearProfile()
            }
          }
          throw error
        } finally {
          if (requestSequence === profileSequence) this.profileLoading = false
          if (profilePromise === currentPromise) profilePromise = null
        }
      })()
      profilePromise = currentPromise
      return currentPromise
    },

    async ensureProfile() {
      if (this.profileLoaded) return this.profile
      return this.loadProfile()
    },

    invalidateAssistedCapability() {
      if (!this.profile) {
        this.perms = []
        this.profileLoaded = false
        return
      }
      this.profile = {
        ...this.profile,
        capabilities: {
          ...(this.profile.capabilities || {}),
          can_process_warehouse_assisted_outbound: false,
        },
      }
      this.perms = this.perms.filter(
        (permission) => permission !== 'outbound.process_warehouse_assisted_outbound',
      )
    },

    async login(username, password) {
      this.clearProfile()
      this.access = ''
      setToken('')
      const result = await api.login(username, password)
      this.access = result?.access || ''
      setToken(this.access)
      this.user = { username }

      // 登录成功与权限资料加载分开处理；profile 暂时不可用时仍可使用普通仓库功能。
      try {
        await this.loadProfile({ force: true })
      } catch (error) {
        console.warn('权限资料加载失败，代办出库入口保持关闭', error)
      }
      return result
    },

    logout() {
      this.clearProfile()
      this.access = ''
      setToken('')
    },
  },
})
