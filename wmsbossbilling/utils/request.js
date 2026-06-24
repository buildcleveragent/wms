function resolveRuntimeEnv() {
  try {
    const systemInfo = uni.getSystemInfoSync ? uni.getSystemInfoSync() : {}
    const uniPlatform = systemInfo?.uniPlatform || ''
    if (uniPlatform === 'app' || uniPlatform === 'app-plus') {
      return 'mobilephone'
    }
  } catch (error) {}

  try {
    const envVersion = uni.getAccountInfoSync
      ? uni.getAccountInfoSync().miniProgram?.envVersion
      : ''
    if (envVersion) {
      return envVersion
    }
  } catch (error) {}

  return 'develop'
}

export const ENV = resolveRuntimeEnv()

const BASE_MAP = {
  develop: 'http://192.168.1.6:8001',
  develop2: 'http://192.168.1.9:8001',
  mobilephone: 'http://8.148.198.200:8080',
  owner: 'http://8.148.198.200:8080',
  onsite: 'http://192.168.2.6:8001',
}

// export const BASE_URL = BASE_MAP[ENV] || BASE_MAP.develop
export const BASE_URL =BASE_MAP.owner

export function getStoredToken() {
  try {
    return uni.getStorageSync('access') || ''
  } catch (error) {
    return ''
  }
}

export function setToken(token) {
  try {
    uni.setStorageSync('access', token || '')
  } catch (error) {}
}

export function getStoredUser() {
  try {
    const raw = uni.getStorageSync('user')
    return raw ? JSON.parse(raw) : null
  } catch (error) {
    return null
  }
}

export function setStoredUser(user) {
  try {
    if (!user) {
      uni.removeStorageSync('user')
      return
    }
    uni.setStorageSync('user', JSON.stringify(user))
  } catch (error) {}
}

export function clearToken() {
  try {
    uni.removeStorageSync('access')
    uni.removeStorageSync('user')
  } catch (error) {}
}

function isLoginRequest(url = '') {
  return url === '/api/token/' || url.includes('/api/token/')
}

function getFriendlyMessage(data, fallback = '请求失败') {
  if (!data) return fallback
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data[0] || fallback
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail) && data.detail.length) return data.detail[0]
  if (typeof data.message === 'string') return data.message
  if (Array.isArray(data.message) && data.message.length) return data.message[0]
  if (Array.isArray(data.non_field_errors) && data.non_field_errors.length) {
    return data.non_field_errors[0]
  }

  for (const key in data) {
    const value = data[key]
    if (Array.isArray(value) && value.length) return value[0]
    if (typeof value === 'string') return value
  }

  return fallback
}

let redirectingToLogin = false

function redirectToLogin() {
  if (redirectingToLogin) return
  redirectingToLogin = true
  clearToken()

  uni.showToast({
    title: '登录已失效，请重新登录',
    icon: 'none',
    duration: 1500,
  })

  setTimeout(() => {
    try {
      uni.reLaunch({ url: '/pages/login' })
    } finally {
      setTimeout(() => {
        redirectingToLogin = false
      }, 300)
    }
  }, 300)
}

export function buildQuery(params = {}) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
}

export function request(options = {}) {
  const token = getStoredToken()

  return new Promise((resolve, reject) => {
    uni.request({
      url: BASE_URL + (options.url || ''),
      method: options.method || 'GET',
      data: options.data || {},
      header: {
        'Content-Type': 'application/json',
        ...(options.header || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      success: (res) => {
        const { statusCode, data } = res
        if (statusCode >= 200 && statusCode < 300) {
          resolve(data)
          return
        }

        if (statusCode === 401 && !isLoginRequest(options.url)) {
          const err = {
            code: 401,
            statusCode,
            message: '登录已失效，请重新登录',
            data,
          }
          redirectToLogin()
          reject(err)
          return
        }

        const message = getFriendlyMessage(data, '请求失败')
        uni.showToast({
          title: message,
          icon: 'none',
        })
        reject({
          code: statusCode,
          statusCode,
          message,
          data,
        })
      },
      fail: (error) => {
        const message = '网络异常，请稍后重试'
        uni.showToast({
          title: message,
          icon: 'none',
        })
        reject({
          code: 0,
          statusCode: 0,
          message,
          data: error,
        })
      },
    })
  })
}

export const api = {
  login: (username, password) =>
    request({
      url: '/api/token/',
      method: 'POST',
      data: { username, password },
    }),

  authProfile: () =>
    request({
      url: '/api/auth/profile/',
    }),

  billingWarehouseOverview: (params = {}) => {
    const qs = buildQuery({
      ...params,
      scope_mode: 'warehouse_boss',
    })
    return request({
      url: qs
        ? `/api/billing/dashboard/warehouse-overview/?${qs}`
        : '/api/billing/dashboard/warehouse-overview/',
    })
  },

  bossHome: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/reports/boss/home/?${qs}` : '/api/reports/boss/home/',
    })
  },

  bossInventory: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/reports/boss/inventory/?${qs}` : '/api/reports/boss/inventory/',
    })
  },

  bossAlerts: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/reports/boss/alerts/?${qs}` : '/api/reports/boss/alerts/',
    })
  },

  billingPeriods: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/billing/periods/?${qs}` : '/api/billing/periods/',
    })
  },

  billingBills: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/billing/bills/?${qs}` : '/api/billing/bills/',
    })
  },

  billingBillDetail: (id) =>
    request({
      url: `/api/billing/bills/${id}/`,
    }),

  billingAccruals: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/billing/accruals/?${qs}` : '/api/billing/accruals/',
    })
  },

  billingAccrualDetail: (id) =>
    request({
      url: `/api/billing/accruals/${id}/`,
    }),
	
  bossInventoryDetail: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs
        ? `/api/inventory/company-summary/?${qs}`
        : '/api/inventory/company-summary/',
    })
  },
}
