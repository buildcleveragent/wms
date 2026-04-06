const ENV =
  (uni.getAccountInfoSync && uni.getAccountInfoSync().miniProgram?.envVersion) ||
  'develop'

const BASE_MAP = {
  develop: 'http://192.168.1.6:8001',
  trial: 'https://trial.example.com',
  mobilephone: 'http://8.148.198.200:8080',
  owner: 'http://8.148.198.200:8080',
  onsite: 'http://192.168.2.6:8001',
}

// export const BASE_URL = BASE_MAP[ENV] || BASE_MAP.develop
export const BASE_URL = BASE_MAP.develop

function getToken() {
  try {
    return uni.getStorageSync('access') || ''
  } catch (e) {
    return ''
  }
}

export function setToken(t) {
  uni.setStorageSync('access', t || '')
}

export function clearToken() {
  try {
    uni.removeStorageSync('access')
    uni.removeStorageSync('user')
  } catch (e) {}
}

let redirectingToLogin = false

function isLoginRequest(url = '') {
  return url === '/api/token/' || url.includes('/api/token/')
}

function getFriendlyMessage(data, fallback = '请求失败') {
  if (!data) return fallback

  if (typeof data === 'string') return data

  if (Array.isArray(data)) {
    return data[0] || fallback
  }

  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail) && data.detail.length) return data.detail[0]

  if (typeof data.message === 'string') return data.message
  if (Array.isArray(data.message) && data.message.length) return data.message[0]

  if (Array.isArray(data.non_field_errors) && data.non_field_errors.length) {
    return data.non_field_errors[0]
  }

  for (const key in data) {
    const v = data[key]
    if (Array.isArray(v) && v.length) return v[0]
    if (typeof v === 'string') return v
  }

  return fallback
}

function redirectToLogin() {
  if (redirectingToLogin) return
  redirectingToLogin = true

  clearToken()

  uni.showToast({
    title: '登录已超时，需要重新登录',
    icon: 'none',
    duration: 1500,
  })

  setTimeout(() => {
    try {
      uni.reLaunch({ url: '/pages/login' })
    } finally {
      setTimeout(() => {
        redirectingToLogin = false
      }, 500)
    }
  }, 500)
}

function buildQuery(params = {}) {
  return Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&')
}

export function request(opts = {}) {
  const token = getToken()

  return new Promise((resolve, reject) => {
    uni.request({
      url: BASE_URL + (opts.url || ''),
      method: opts.method || 'GET',
      data: opts.data || {},
      header: {
        'Content-Type': 'application/json',
        ...(opts.header || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },

      success: (res) => {
        const statusCode = res.statusCode
        const data = res.data

        if (statusCode >= 200 && statusCode < 300) {
          resolve(data)
          return
        }

        // 登录接口自己的 401，不做“超时跳登录”处理
        if (statusCode === 401 && !isLoginRequest(opts.url)) {
          const err = {
            code: 401,
            statusCode,
            message: '登录已超时，需要重新登录',
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

      fail: (err) => {
        const message = '网络异常，请稍后重试'

        uni.showToast({
          title: message,
          icon: 'none',
        })

        reject({
          code: 0,
          statusCode: 0,
          message,
          data: err,
        })
      },
    })
  })
}

export const api = {
  // 登录
  login: (username, password) =>
    request({
      url: '/api/token/',
      method: 'POST',
      data: { username, password },
    }),

  // 目录
  customers: (q = '', page = 1, owner_id, mine) => {
    const qs = buildQuery({
      search: q,
      page,
      owner_id,
      mine: mine ? 1 : undefined,
    })
    return request({
      url: `/api/catalog/customers?${qs}`,
    })
  },

  myOwners: (q = '', page = 1) => {
    const qs = buildQuery({
      search: q,
      page,
    })
    return request({
      url: `/api/catalog/owners/?${qs}`,
    })
  },

  products: (q = '', page = 1, warehouse_id) => {
    const qs = buildQuery({
      search: q,
      page,
      warehouse_id,
    })
    return request({
      url: `/api/catalog/products?${qs}`,
    })
  },

  inventorySummary: (params = {}) => {
    const qs = buildQuery({
      search: params.search || '',
      page: params.page || 1,
      page_size: params.page_size || 10,
    })
    return request({
      url: `/api/inventory/summary/?${qs}`,
    })
  },

  billingPeriods: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs ? `/api/billing/periods/?${qs}` : '/api/billing/periods/',
    })
  },

  billingPeriodPreview: (id) =>
    request({
      url: `/api/billing/periods/${id}/preview/`,
    }),

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

  // 出库单创建
  createOutboundOrder: (payload) =>
    request({
      url: '/api/outbound/orders/',
      method: 'POST',
      data: payload,
    }),

  // 兼容两种调用：
  // 1) api.orders('关键字')
  // 2) api.orders({ approval_status:'OWNER_PENDING', page:1 })
  orders: (arg1 = '', page = 1) => {
    if (arg1 && typeof arg1 === 'object' && !Array.isArray(arg1)) {
      const qs = buildQuery(arg1)
      const url = qs ? `/api/outbound/orders/?${qs}` : `/api/outbound/orders/`
      return request({ url })
    }

    const q = arg1 || ''
    return request({
      url: `/api/outbound/orders?search=${encodeURIComponent(q)}&page=${page}`,
    })
  },

  orderDetail: (id) =>
    request({
      url: `/api/outbound/orders/${id}/`,
    }),

  pendingOrders: (page = 1, search = '') =>
    request({
      url: `/api/outbound/orders?approval_status=OWNER_PENDING&page=${page}${
        search ? `&search=${encodeURIComponent(search)}` : ''
      }`,
    }),

  ownerApprove: (id) =>
    request({
      url: `/api/outbound/orders/${id}/owner-approve/`,
      method: 'POST',
    }),

  // 这两个接口如果你后端还没实现，会返回后端错误
  ownerUnapprove: (id) =>
    request({
      url: `/api/outbound/orders/${id}/owner-unapprove/`,
      method: 'POST',
    }),

  cancelOrder: (id) =>
    request({
      url: `/api/outbound/orders/${id}/cancel/`,
      method: 'POST',
    }),

  // 上传一件代发 Excel
  importDropShipExcel(filePath) {
    const access = uni.getStorageSync('access') || ''

    return new Promise((resolve, reject) => {
      uni.uploadFile({
        url: `${BASE_URL}/api/outbound/orders/import-drop-ship-excel/`,
        filePath,
        name: 'file',
        header: access ? { Authorization: `Bearer ${access}` } : {},
        success: (res) => {
          try {
            const data = typeof res.data === 'string' ? JSON.parse(res.data) : res.data
            if (res.statusCode >= 200 && res.statusCode < 300) {
              resolve(data)
            } else {
              reject({ statusCode: res.statusCode, data })
            }
          } catch (e) {
            reject(e)
          }
        },
        fail: (err) => reject(err),
      })
    })
  },
}
