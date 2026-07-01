import { reactive } from 'vue'
import { api, clearToken, getBaseUrl, setBaseUrl, setToken } from '../utils/request'

const sessionStore = reactive({
  baseUrl: getBaseUrl(),
  user: uni.getStorageSync('sales_user') || null,
  profile: uni.getStorageSync('sale_mini_profile') || null,
  home: null,
  loading: false,
  async login(username, password, serverUrl) {
    if (serverUrl) {
      setBaseUrl(serverUrl)
      this.baseUrl = getBaseUrl()
    }
    const data = await api.login(username, password)
    setToken(data.access, data.refresh)
    this.user = data.user || null
    uni.setStorageSync('sales_user', this.user)
    await this.fetchProfile()
    await this.fetchHome()
  },
  async wechatLogin(code, serverUrl, extra = {}) {
    if (serverUrl) {
      setBaseUrl(serverUrl)
      this.baseUrl = getBaseUrl()
    }
    const data = await api.saleMiniWechatLogin({ code, ...extra })
    setToken(data.access, data.refresh)
    this.user = data.user || null
    this.profile = {
      buyer: data.buyer,
      customer: data.customer,
      bindings: data.bindings || [],
    }
    uni.setStorageSync('sales_user', this.user)
    uni.setStorageSync('sale_mini_profile', this.profile)
    await this.fetchHome()
    return data
  },
  async fetchProfile() {
    this.profile = await api.saleMiniMe()
    uni.setStorageSync('sale_mini_profile', this.profile)
    return this.profile
  },
  async fetchHome() {
    this.loading = true
    try {
      this.home = await api.saleMiniHome()
    } finally {
      this.loading = false
    }
  },
  logout() {
    clearToken()
    uni.removeStorageSync('sales_user')
    uni.removeStorageSync('sale_mini_profile')
    this.user = null
    this.profile = null
    this.home = null
    uni.reLaunch({ url: '/pages/login/login' })
  },
})

export function useSessionStore() {
  return sessionStore
}
