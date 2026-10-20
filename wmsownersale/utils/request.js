import { useCart } from '@/store/cart'
export { BASE_URL } from './api-base'
import { BASE_URL } from './api-base'

let sessionEpoch = 0
function checkSession(epoch) {
  if (epoch !== sessionEpoch) throw { statusCode: 409, message: '登录账号已变化，请重新操作' }
}

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
  sessionEpoch++
  persistTokens(access, refresh)
}

function persistTokens(access, refresh) {
  uni.setStorageSync('access', access || '')
  if (refresh !== undefined) uni.setStorageSync('refresh', refresh || '')
}

// Kept for old callers while making the two-token contract explicit.
export function setToken(access) {
  setTokens(access)
}

export function clearSessionStorage() {
  sessionEpoch++
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
  const expiredEpoch = sessionEpoch

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
      if (expiredEpoch === sessionEpoch) uni.reLaunch({ url: '/pages/login' })
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

function decodeDownloadError(data) {
  if (Object.prototype.toString.call(data) !== '[object ArrayBuffer]') return data
  try {
    const bytes = new Uint8Array(data)
    let text = ''
    if (typeof TextDecoder !== 'undefined') text = new TextDecoder().decode(bytes)
    else text = decodeURIComponent(Array.from(bytes, byte => '%' + byte.toString(16).padStart(2, '0')).join(''))
    return JSON.parse(text)
  } catch { return { detail: '下载失败，请重试' } }
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
        const data = opts.responseType === 'arraybuffer' && !(statusCode >= 200 && statusCode < 300) ? decodeDownloadError(res.data) : res.data

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
let refreshEpoch = -1

export function refreshSession() {
  const epoch = sessionEpoch
  if (refreshPromise && refreshEpoch === epoch) return refreshPromise
  const refresh = getRefreshToken()
  if (!refresh) {
    return Promise.reject({ statusCode: 401, message: '缺少刷新令牌' })
  }
  refreshEpoch = epoch
  const flight = rawRequest({
    url: '/api/auth/refresh/',
    method: 'POST',
    data: { refresh },
    silent: true,
  }).then((data) => {
    checkSession(epoch)
    if (!data?.access) throw { statusCode: 401, message: '刷新令牌无效' }
    persistTokens(data.access, data.refresh || refresh)
    return data.access
  }).catch(error => {
    checkSession(epoch)
    throw error
  }).finally(() => {
    if (refreshPromise === flight) refreshPromise = null
  })
  refreshPromise = flight
  return flight
}

async function recoverRequest(send, credential = false) {
  const epoch = sessionEpoch
  const originalAccess = getAccessToken()
  try {
    const result = await send(originalAccess)
    checkSession(epoch)
    return result
  } catch (error) {
    checkSession(epoch)
    if (error?.statusCode !== 401 || credential) throw error
    let access
    try {
      access = getAccessToken() !== originalAccess ? getAccessToken() : await refreshSession()
      checkSession(epoch)
    } catch (refreshError) {
      checkSession(epoch)
      // Offline refresh failures keep the session so the startup page can
      // offer an explicit retry instead of destroying a valid login.
      if ([401, 403].includes(refreshError?.statusCode)) expireLocalSession()
      throw refreshError
    }
    try {
      const result = await send(access)
      checkSession(epoch)
      return result
    } catch (retryError) {
      checkSession(epoch)
      if (retryError?.statusCode === 401) expireLocalSession()
      throw retryError
    }
  }
}

export function request(opts = {}) {
  return recoverRequest(access => rawRequest(opts, access), isLoginRequest(opts.url) || opts._retried)
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

function rawDownload(url, access, excel = false) {
  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url: BASE_URL + url,
      header: access ? { Authorization: `Bearer ${access}` } : {},
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          if (!excel) {resolve(res.tempFilePath);return}
          if (!uni.getFileSystemManager) {reject({message:'当前环境不支持校验下载文件'});return}
          uni.getFileSystemManager().readFile({filePath:res.tempFilePath,
            success(result) {
              const bytes=new Uint8Array(result.data)
              if(bytes[0]===0x50 && bytes[1]===0x4b) resolve(res.tempFilePath)
              else reject({message:getFriendlyMessage(decodeDownloadError(result.data),'导出响应不是有效的 Excel 文件')})
            }, fail:()=>reject({message:'下载文件读取失败，请重试'}),
          })
        }
        else {
          const fallback = { statusCode: res.statusCode, message: '下载失败' }
          if (!res.tempFilePath || !uni.getFileSystemManager) { reject(fallback); return }
          uni.getFileSystemManager().readFile({filePath: res.tempFilePath, encoding: 'utf8',
            success(result) { let data; try {data=JSON.parse(result.data)} catch {data=null} reject({...fallback, message:getFriendlyMessage(data, '下载失败')}) },
            fail() {reject(fallback)},
          })
        }
      },
      fail: (error) => reject({ statusCode: 0, message: '网络异常，请稍后重试', data: error }),
    })
  })
}

export async function downloadAuthenticatedFile(url, filename, { isCurrent = () => true, excel = false } = {}) {
  const epoch = sessionEpoch
  const current = () => { checkSession(epoch); return isCurrent() }

  // #ifdef H5
  const data = await request({ url, responseType: 'arraybuffer', silent: true })
  if (!current()) return false
  if (excel) {
    const bytes = new Uint8Array(data)
    if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) throw {message: getFriendlyMessage(decodeDownloadError(data), '导出响应不是有效的 Excel 文件')}
  }
  const blob = new Blob([data], {type: excel ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'application/octet-stream'})
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  return true
  // #endif

  // #ifndef H5
  const discard = path => uni.getFileSystemManager?.().unlink({filePath:path,fail:()=>{}})
  const path = await recoverRequest(async access => {
    const result = await rawDownload(url, access, excel)
    try { checkSession(epoch) } catch (error) { discard(result); throw error }
    return result
  })
  let valid
  try { valid = current() } catch (error) { discard(path); throw error }
  if (!valid) {
    discard(path)
    return false
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

export function uploadAuthenticatedFile(opts) {
  return recoverRequest(access => rawUpload(opts, access))
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
      page_size: params.page_size || 50,
    })
    return request({
      url: `/api/inventory/summary/?${qs}`,
    })
  },

  inventoryAlerts: () => request({ url: '/api/inventory/owner-alerts/', silent: true }),
  inventoryAlertItems: (params = {}) => request({ url: '/api/inventory/owner-alerts/items/', data: params, silent: true }),
  inventoryAlertSettings: (productId) => request({ url: productId ? `/api/inventory/owner-alerts/products/${productId}/settings/` : '/api/inventory/owner-alerts/settings/', silent: true }),
  saveInventoryAlertSettings: (productId, data) => request({ url: productId ? `/api/inventory/owner-alerts/products/${productId}/settings/` : '/api/inventory/owner-alerts/settings/', method: 'PATCH', data, silent: true }),
  inventoryAlertProducts: (params = {}) => request({ url: '/api/inventory/owner-alerts/products/', data: params, silent: true }),
  inventoryDetail: (id, params = {}) => request({
    url: `/api/inventory/summary/${id}/detail/?${buildQuery(params)}`, silent: true,
  }),
  inventoryTransactions: (id, params = {}) => request({
    url: `/api/inventory/summary/${id}/transactions/?${buildQuery(params)}`, silent: true,
  }),

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
      const qs = buildQuery({ ...arg1, owner_portal: 1 })
      const url = qs ? `/api/outbound/orders/?${qs}` : `/api/outbound/orders/`
      return request({ url })
    }

    const q = arg1 || ''
    return request({
      url: `/api/outbound/orders/?owner_portal=1&search=${encodeURIComponent(q)}&page=${page}`,
    })
  },

  orderDetail: (id) =>
    request({
      url: `/api/outbound/orders/${id}/`,
    }),

  orderProgress: (id) => request({
    url: `/api/outbound/orders/${id}/progress/`,
    silent: true,
  }),

  ownerWorkbench: () => request({ url: '/api/outbound/owner-workbench/', silent: true }),
  ownerWorkbenchItems: (params = {}) => request({
    url: `/api/outbound/owner-workbench/items/?${buildQuery(params)}`, silent: true,
  }),

  orderEditContext: (id) =>
    request({ url: `/api/outbound/orders/${id}/edit-context/` }),

  reorderContext: (id, params = {}) => request({
    url: `/api/outbound/orders/${id}/reorder-context/?${buildQuery(params)}`, silent: true,
  }),
  validateOrder: payload => request({
    url: '/api/outbound/orders/validate/', method: 'POST', data: payload, silent: true,
  }),
  frequentProducts: (customerId, params = {}) => request({
    url: `/api/outbound/customers/${customerId}/frequent-products/?${buildQuery(params)}`,
    silent: true,
  }),
  customerAddresses: customerId => request({
    url: `/api/outbound/customers/${customerId}/addresses/`, silent: true,
  }),
  saveCustomerAddress: (customerId, addressId, data, key) => request({
    url: `/api/outbound/customers/${customerId}/addresses/${addressId ? `${addressId}/` : ''}`,
    method: addressId ? 'PATCH' : 'POST', data, silent: true,
    header: key ? { 'Idempotency-Key': key } : {},
  }),
  removeCustomerAddress: (customerId, addressId, version) => request({
    url: `/api/outbound/customers/${customerId}/addresses/${addressId}/`,
    method: 'DELETE', data: { version }, silent: true,
  }),

  updateOutboundOrder: (id, payload) =>
    request({
      url: `/api/outbound/orders/${id}/`,
      method: 'PUT',
      data: payload,
    }),

  submitOutboundOrder: (id, payload = {}, key) =>
    request({
      url: `/api/outbound/orders/${id}/submit/`,
      method: 'POST',
      data: payload,
      header: key ? { 'Idempotency-Key': key } : {},
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

// Authenticated evidence never uses a public media URL.
export async function returnPhotoURL(url) {
  // #ifdef H5
  const bytes = await request({url,responseType:'arraybuffer',silent:true})
  const source = URL.createObjectURL(new Blob([bytes],{type:'image/jpeg'}))
  setTimeout(()=>URL.revokeObjectURL(source),60000)
  return source
  // #endif
  // #ifndef H5
  return recoverRequest(access => rawDownload(url, access))
  // #endif
}
