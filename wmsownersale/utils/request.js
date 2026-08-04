import { useCart } from '@/store/cart'

const ENV =
  (uni.getAccountInfoSync && uni.getAccountInfoSync().miniProgram?.envVersion) ||
  'develop'

const BASE_MAP = {
  develop: 'http://192.168.1.6:8001',
  develop2: 'http://192.168.1.9:8001',
  mobilephone: 'http://8.148.198.200:8080',
  owner: 'http://8.148.198.200:8080',
  onsite: 'http://192.168.2.6:8001',
}

// export const BASE_URL = BASE_MAP[ENV] || BASE_MAP.develop
export const BASE_URL = BASE_MAP.develop

export function getAccessToken() {
  try {
    return uni.getStorageSync('access') || ''
  } catch (e) {
    return ''
  }
}

export function getRefreshToken() {
  try {
    return uni.getStorageSync('refresh') || ''
  } catch (e) {
    return ''
  }
}

export function setTokens(access, refresh) {
  uni.setStorageSync('access', access || '')
  if (refresh !== undefined) uni.setStorageSync('refresh', refresh || '')
}

// Kept for old callers while making the two-token contract explicit.
export function setToken(access) {
  setTokens(access)
}

export function clearSessionStorage() {
  try {
    uni.removeStorageSync('access')
    uni.removeStorageSync('refresh')
    uni.removeStorageSync('user')
  } catch (e) {}
}

let redirectingToLogin = false

function isLoginRequest(url = '') {
  return url.includes('/api/token/') || url.includes('/api/auth/login/') || url.includes('/api/auth/refresh/')
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

export function expireLocalSession({ notify = true } = {}) {
  if (redirectingToLogin) return
  redirectingToLogin = true

  clearSessionStorage()

  try { useCart().resetOrder() } catch (e) {}

  if (notify) {
    uni.showToast({
      title: '登录已超时，需要重新登录',
      icon: 'none',
      duration: 1500,
    })
  }

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

function rawRequest(opts = {}, access = '') {
  return new Promise((resolve, reject) => {
    uni.request({
      url: BASE_URL + (opts.url || ''),
      method: opts.method || 'GET',
      data: typeof opts.data === 'function' ? opts.data() : (opts.data || {}),
      responseType: opts.responseType || 'text',
      header: {
        'Content-Type': 'application/json',
        ...(opts.header || {}),
        ...(access ? { Authorization: `Bearer ${access}` } : {}),
      },

      success: (res) => {
        const statusCode = res.statusCode
        const data = res.data

        if (statusCode >= 200 && statusCode < 300) {
          resolve(data)
          return
        }

        const message = getFriendlyMessage(data, '请求失败')

        if (!opts.silent && statusCode !== 401) {
          uni.showToast({ title: message, icon: 'none' })
        }

        reject({
          code: statusCode,
          statusCode,
          message,
          data,
        })
      },

      fail: (err) => {
        const message = '网络异常，请稍后重试'

        if (!opts.silent) uni.showToast({ title: message, icon: 'none' })

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

let refreshPromise = null

export function refreshSession() {
  if (refreshPromise) return refreshPromise
  const refresh = getRefreshToken()
  if (!refresh) {
    return Promise.reject({ statusCode: 401, message: '缺少刷新令牌' })
  }
  refreshPromise = rawRequest({
    url: '/api/auth/refresh/',
    method: 'POST',
    data: { refresh },
    silent: true,
  }).then((data) => {
    if (!data?.access) throw { statusCode: 401, message: '刷新令牌无效' }
    setTokens(data.access, data.refresh || refresh)
    return data.access
  }).finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

export async function request(opts = {}) {
  try {
    return await rawRequest(opts, getAccessToken())
  } catch (error) {
    if (error?.statusCode !== 401 || isLoginRequest(opts.url) || opts._retried) throw error
    try {
      const access = await refreshSession()
      return await rawRequest({ ...opts, _retried: true }, access)
    } catch (refreshError) {
      // Offline refresh failures keep the session so the startup page can
      // offer an explicit retry instead of destroying a valid login.
      if (refreshError?.statusCode !== 0) expireLocalSession()
      throw refreshError
    }
  }
}

export async function fetchAllPages(fetchPage, params = {}) {
  const all = []
  let page = 1
  while (true) {
    const response = await fetchPage({ ...params, page })
    const rows = Array.isArray(response) ? response : (response?.results || [])
    all.push(...rows)
    if (Array.isArray(response) || !response?.next) return all
    page += 1
  }
}

function rawDownload(url, access) {
  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url: BASE_URL + url,
      header: access ? { Authorization: `Bearer ${access}` } : {},
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.tempFilePath)
        else reject({ statusCode: res.statusCode, message: '下载失败' })
      },
      fail: (error) => reject({ statusCode: 0, message: '网络异常，请稍后重试', data: error }),
    })
  })
}

export async function downloadAuthenticatedFile(url, filename) {
  // #ifdef H5
  const data = await request({ url, responseType: 'arraybuffer', silent: true })
  const blob = new Blob([data])
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(objectUrl)
  return
  // #endif

  // #ifndef H5
  let path
  try {
    path = await rawDownload(url, getAccessToken())
  } catch (error) {
    if (error?.statusCode !== 401) throw error
    const access = await refreshSession().catch((refreshError) => {
      if (refreshError?.statusCode !== 0) expireLocalSession()
      throw refreshError
    })
    path = await rawDownload(url, access)
  }
  return new Promise((resolve, reject) => {
    uni.openDocument({ filePath: path, showMenu: true, success: resolve, fail: reject })
  })
  // #endif
}

function rawUpload(opts, access = '') {
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: BASE_URL + opts.url,
      filePath: opts.filePath,
      name: opts.name || 'file',
      formData: opts.formData || {},
      header: {
        ...(opts.header || {}),
        ...(access ? { Authorization: `Bearer ${access}` } : {}),
      },
      success: (response) => {
        let data = response.data
        if (typeof data === 'string') {
          try {
            data = JSON.parse(data)
          } catch (error) {
            data = { detail: data || '上传响应格式不正确' }
          }
        }
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(data)
          return
        }
        reject({
          code: response.statusCode,
          statusCode: response.statusCode,
          message: getFriendlyMessage(data, '上传失败'),
          data,
        })
      },
      fail: (error) => reject({
        code: 0,
        statusCode: 0,
        message: '网络异常，请稍后重试',
        data: error,
      }),
    })
  })
}

export async function uploadAuthenticatedFile(opts) {
  try {
    return await rawUpload(opts, getAccessToken())
  } catch (error) {
    if (error?.statusCode !== 401) throw error
  }

  let access
  try {
    access = await refreshSession()
  } catch (refreshError) {
    if (refreshError?.statusCode !== 0) expireLocalSession()
    throw refreshError
  }

  try {
    return await rawUpload(opts, access)
  } catch (retryError) {
    if (retryError?.statusCode === 401) expireLocalSession()
    throw retryError
  }
}

export const api = {
  // 登录
  login: (username, password) =>
    request({
      url: '/api/token/',
      method: 'POST',
      data: { username, password },
      silent: true,
    }),

  authProfile: () =>
    request({
      url: '/api/auth/profile/',
    }),

  // Validate a just-issued access token without mutating the persisted session.
  authProfileWithAccess: (access) =>
    rawRequest({
      url: '/api/auth/profile/',
      silent: true,
    }, access),

  logout: () => request({
    url: '/api/auth/logout/',
    method: 'POST',
    // Evaluate on every attempt so a refresh-token rotation during a 401 retry
    // cannot leave the newly issued token unblacklisted.
    data: () => ({ refresh: getRefreshToken() }),
    silent: true,
  }),

  changePassword: (oldPassword, newPassword1, newPassword2) =>
    request({
      url: '/api/auth/password/change/',
      method: 'POST',
      data: {
        old_password: oldPassword,
        new_password1: newPassword1,
        new_password2: newPassword2,
      },
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

  warehouses: () =>
    request({
      url: '/api/catalog/warehouses/',
    }),

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

  operationsSummary: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs
        ? `/api/reports/v2/operations/summary/?${qs}`
        : '/api/reports/v2/operations/summary/',
    })
  },

  operationsDetails: (params = {}) => {
    const qs = buildQuery(params)
    return request({
      url: qs
        ? `/api/reports/v2/operations/details/?${qs}`
        : '/api/reports/v2/operations/details/',
    })
  },

  // 出库单创建
  createOutboundOrder: (payload, idempotencyKey) => {
    const key = String(idempotencyKey || '').trim()
    if (!key) {
      return Promise.reject({
        code: 'MISSING_IDEMPOTENCY_KEY',
        message: '缺少订单幂等键，请重新进入开单流程',
      })
    }
    return request({
      url: '/api/outbound/orders/',
      method: 'POST',
      data: payload,
      header: { 'Idempotency-Key': key },
    })
  },

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

  orderEditContext: (id) =>
    request({ url: `/api/outbound/orders/${id}/edit-context/` }),

  updateOutboundOrder: (id, payload) =>
    request({
      url: `/api/outbound/orders/${id}/`,
      method: 'PUT',
      data: payload,
    }),

  submitOutboundOrder: (id) =>
    request({
      url: `/api/outbound/orders/${id}/submit/`,
      method: 'POST',
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
  ownerReject: (id, reason) =>
    request({
      url: `/api/outbound/orders/${id}/owner-reject/`,
      method: 'POST',
	  data: { reason: String(reason || '').trim() },
    }),

  cancelOrder: (id) =>
    request({
      url: `/api/outbound/orders/${id}/cancel/`,
      method: 'POST',
    }),
	
  // 上传一件代发 Excel
  importDropShipExcel(filePath, warehouseId) {
    return uploadAuthenticatedFile({
      url: '/api/outbound/orders/import-drop-ship-excel/',
      filePath,
      name: 'file',
      formData: {
        warehouse_id: String(warehouseId || ''),
      },
    })
  },
}
