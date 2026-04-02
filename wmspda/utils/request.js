const ENV =
  (uni.getAccountInfoSync && uni.getAccountInfoSync().miniProgram?.envVersion) ||
  'develop'

const BASE_MAP = {
  develop: 'http://192.168.1.6:8001',
  trial: 'https://trial.example.com',
  pda: 'http://8.148.198.200:8080',
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

  // 兜底取第一个字段错误
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
  const qs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&')
  return qs
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
  // Auth
  login: (username, password) =>
    request({
      url: '/api/token/',
      method: 'POST',
      data: { username, password },
    }),

  // Catalog
  customers: (q = '', page = 1) =>
    request({
      url: `/api/catalog/customers?search=${encodeURIComponent(q)}&page=${page}`,
    }),

  owners: (q = '', page = 1) =>
    request({
      url: `/api/catalog/owners?search=${encodeURIComponent(q)}&page=${page}`,
    }),

  suppliers: (q = '', page = 1, owner) =>
    request({
      url: `/api/catalog/suppliers?search=${encodeURIComponent(q)}&page=${page}&owner=${owner}`,
    }),

  products: (q = '', page = 1) =>
    request({
      url: `/api/catalog/products?search=${encodeURIComponent(q)}&page=${page}`,
    }),

  receive_products: (q = '', page = 1, owner) =>
    request({
      url: `/api/catalog/receive_products?search=${encodeURIComponent(q)}&page=${page}&owner=${owner}`,
    }),

  receive_without_order: (q = '', page = 1, owner) =>
    request({
      url: `/api/inbound/receive_without_order?search=${encodeURIComponent(q)}&page=${page}&owner=${owner}`,
    }),

  submitReceiveWithoutOrder: (payload) =>
    request({
      url: '/api/inbound/receive_without_order/',
      method: 'POST',
      data: payload,
    }),

  // Orders
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

  // PDA 拣货
  pickTasks: (params = {}) =>
    request({
      url: '/api/pda/pick-tasks/',
      method: 'GET',
      data: params,
    }),

  pickTaskDetail: (id) =>
    request({
      url: `/api/pda/pick-tasks/${id}/`,
      method: 'GET',
    }),

  pickTaskLines: (id) =>
    request({
      url: `/api/pda/pick-tasks/${id}/lines/`,
      method: 'GET',
    }),

  scanPick: (id, payload) =>
    request({
      url: `/api/pda/pick-tasks/${id}/scan/`,
      method: 'POST',
      data: payload,
    }),

  postPickTask: (id) =>
    request({
      url: `/api/pda/pick-tasks/${id}/post/`,
      method: 'POST',
    }),

  createPickReviewTask: (id) =>
    request({
      url: `/api/pda/pick-tasks/${id}/create-review-task/`,
      method: 'POST',
    }),

  reviewPickTasks: (params = {}) =>
    request({
      url: '/api/pda/pick-tasks/',
      method: 'GET',
      data: {
        status: 'COMPLETED',
        review_status: 'PENDING',
        for_review: 1,
        ...params,
      },
    }),

  adjustPickLineQty: (taskId, data) =>
    request({
      url: `/api/pda/pick-tasks/${taskId}/adjust-line-qty/`,
      method: 'POST',
      data,
    }),
	
	
  companyInventorySummary: (params = {}) => {
    const qs = buildQuery({
      mode: params.mode || 'warehouse',
      warehouse_id: params.warehouse_id || '',
      owner_id: params.owner_id || '',
      search: params.search || '',
      page: params.page || 1,
      page_size: params.page_size || 10,
    })
    return request({
      url: `/api/inventory/company-summary/?${qs}`,
    })
  },
	  
	  
}