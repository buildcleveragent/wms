const ENV =
  (uni.getAccountInfoSync && uni.getAccountInfoSync().miniProgram?.envVersion) ||
  'develop'

const BASE_MAP = {
  develop: 'http://192.168.1.6:8001',
  develop2: 'http://192.168.1.9:8001',
  pda: 'http://8.148.198.200:8080',
  onsite: 'http://192.168.2.6:8001',
}

// export const BASE_URL = BASE_MAP[ENV] || BASE_MAP.develop
export const BASE_URL = String(import.meta.env.VITE_API_BASE_URL || BASE_MAP.develop).trim().replace(/\/$/, '')
if (!/^https?:\/\/[^/?#@]+(?::\d+)?(?:\/[^?#]*)?$/.test(BASE_URL)) {
  throw new Error('VITE_API_BASE_URL 必须是无凭据、查询参数和片段的 HTTP(S) 地址。')
}

const PRODUCT_IMPORT_UPLOAD_TIMEOUT_MS = 10 * 60 * 1000
const ACCESS_TOKEN_STORAGE_KEY = 'access'
const REFRESH_TOKEN_STORAGE_KEY = 'refresh'
let sessionEpoch = 0
function sessionChangedError() {
  return { code: 409, statusCode: 409, sessionChanged: true, message: '登录账号已变化，请重新操作' }
}

export function getToken() {
  try {
    return uni.getStorageSync(ACCESS_TOKEN_STORAGE_KEY) || ''
  } catch (e) {
    return ''
  }
}

export function setToken(t) {
  sessionEpoch++
  uni.setStorageSync(ACCESS_TOKEN_STORAGE_KEY, t || '')
}

export function getRefreshToken() {
  try {
    return uni.getStorageSync(REFRESH_TOKEN_STORAGE_KEY) || ''
  } catch (e) {
    return ''
  }
}

export function setAuthTokens(tokens = {}) {
  sessionEpoch++
  persistAuthTokens(tokens)
}

function persistAuthTokens(tokens = {}) {
  const access = tokens?.access || ''
  const refresh = tokens?.refresh

  if (access) uni.setStorageSync(ACCESS_TOKEN_STORAGE_KEY, access)
  else uni.removeStorageSync(ACCESS_TOKEN_STORAGE_KEY)

  if (refresh !== undefined) {
    if (refresh) uni.setStorageSync(REFRESH_TOKEN_STORAGE_KEY, refresh)
    else uni.removeStorageSync(REFRESH_TOKEN_STORAGE_KEY)
  }
}

export function clearAuthTokens() {
  sessionEpoch++
  try {
    uni.removeStorageSync(ACCESS_TOKEN_STORAGE_KEY)
    uni.removeStorageSync(REFRESH_TOKEN_STORAGE_KEY)
  } catch (e) {}
}

export function clearToken() {
  clearAuthTokens()
}

let redirectingToLogin = false
let refreshPromise = null
let refreshEpoch = -1

function isAuthenticationRequest(url = '') {
  return (
    url === '/api/pda/token/' ||
    url.includes('/api/pda/token/') ||
    url === '/api/token/' ||
    url.includes('/api/token/') ||
    url === '/api/auth/login/' ||
    url === '/api/auth/refresh/'
  )
}

const HTML_ERROR_MESSAGE = '后端服务错误，请联系管理员查看日志'

function sanitizeMessage(value, fallback = '请求失败') {
  if (typeof value !== 'string') return fallback

  const text = value.trim()
  if (!text) return fallback

  if (/^<!doctype\s+html/i.test(text) || /^<html[\s>]/i.test(text)) {
    return HTML_ERROR_MESSAGE
  }

  return text.length > 120 ? `${text.slice(0, 117)}...` : text
}

function getFriendlyMessage(data, fallback = '请求失败') {
  if (!data) return fallback

  if (typeof data === 'string') return sanitizeMessage(data, fallback)

  if (Array.isArray(data)) {
    return sanitizeMessage(data[0], fallback)
  }

  if (typeof data.detail === 'string') return sanitizeMessage(data.detail, fallback)
  if (Array.isArray(data.detail) && data.detail.length) {
    return sanitizeMessage(data.detail[0], fallback)
  }

  if (typeof data.message === 'string') return sanitizeMessage(data.message, fallback)
  if (Array.isArray(data.message) && data.message.length) {
    return sanitizeMessage(data.message[0], fallback)
  }

  if (Array.isArray(data.non_field_errors) && data.non_field_errors.length) {
    return sanitizeMessage(data.non_field_errors[0], fallback)
  }

  // 兜底取第一个字段错误
  for (const key in data) {
    const v = data[key]
    if (Array.isArray(v) && v.length) return sanitizeMessage(v[0], fallback)
    if (typeof v === 'string') return sanitizeMessage(v, fallback)
  }

  return fallback
}

function redirectToLogin() {
  if (redirectingToLogin) return
  redirectingToLogin = true

  clearToken()
  const expiredEpoch = sessionEpoch

  uni.showToast({
    title: '登录已超时，需要重新登录',
    icon: 'none',
    duration: 1500,
  })

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

function expiredLoginError(data = null) {
  return {
    code: 401,
    statusCode: 401,
    authExpired: true,
    message: '登录已超时，需要重新登录',
    data,
  }
}

export function refreshAccessToken() {
  const epoch = sessionEpoch
  if (refreshPromise && refreshEpoch === epoch) return refreshPromise
  refreshEpoch = epoch

  const refresh = getRefreshToken()
  if (!refresh) return Promise.reject(expiredLoginError())

  const pending = new Promise((resolve, reject) => {
    uni.request({
      url: `${BASE_URL}/api/pda/token/refresh/`,
      method: 'POST',
      data: { refresh },
      header: { 'Content-Type': 'application/json' },
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        const statusCode = Number(res.statusCode || 0)
        const data = res.data || {}
        if (statusCode >= 200 && statusCode < 300 && data.access) {
          persistAuthTokens({
            access: data.access,
            refresh: data.refresh || refresh,
          })
          resolve(data.access)
          return
        }
        if (statusCode === 400 || statusCode === 401 || statusCode === 403) {
          reject(expiredLoginError(data))
          return
        }
        reject({
          code: statusCode,
          statusCode,
          message: getFriendlyMessage(data, '刷新登录状态失败，请稍后重试'),
          data,
        })
      },
      fail: (error) =>
        reject({
          code: 0,
          statusCode: 0,
          message: '刷新登录状态失败，请检查网络后重试',
          data: error,
        }),
    })
  })

  const flight = pending.finally(() => {
    if (refreshPromise === flight) refreshPromise = null
  })
  refreshPromise = flight
  return flight
}

function retryAfterUnauthorized(tokenUsed, retry, alreadyRetried = false, epoch) {
  if (epoch !== sessionEpoch) return Promise.reject(sessionChangedError())
  if (alreadyRetried) {
    const error = expiredLoginError()
    clearAuthTokens()
    redirectToLogin()
    return Promise.reject(error)
  }

  const currentToken = getToken()
  const refreshOrReuse =
    tokenUsed && currentToken && tokenUsed !== currentToken
      ? Promise.resolve(currentToken)
      : refreshAccessToken()

  return refreshOrReuse.then(() => {
    if (epoch !== sessionEpoch) throw sessionChangedError()
    return retry()
  }).catch((error) => {
    if (error?.authExpired && epoch === sessionEpoch) redirectToLogin()
    throw error
  })
}

function buildQuery(params = {}) {
  const qs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&')
  return qs
}

/**
 * Create a client request identifier suitable for an idempotent PDA write.
 *
 * The inbound PDA endpoints retain this identifier together with a payload
 * fingerprint.  A caller must keep the value when retrying the same write,
 * and create a new one only after a successful response or when the entered
 * data changes.
 */
export function createIdempotencyUuid() {
  const bytes = new Uint8Array(16)
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : null

  if (cryptoApi && typeof cryptoApi.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }

  // RFC 4122 version 4 / variant bits.  The server only requires a safe,
  // stable identifier, but a conventional UUID also makes support tracing
  // much easier.
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export async function withAuthToken(url) {
  if (!url.startsWith(`${BASE_URL}/`)) throw new Error('打印地址不属于当前服务。')
  const relative = url.slice(BASE_URL.length)
  const path = relative.split('?')[0]
  const result = await request({ url: '/api/print-tickets/', method: 'POST', data: { path } })
  if (!result?.ticket) throw new Error('未取得打印票据，请重试。')
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}ticket=${encodeURIComponent(result.ticket)}`
}

export function request(opts = {}, alreadyRetried = false) {
  const epoch = sessionEpoch
  const token = opts.auth === false ? '' : getToken()

  return new Promise((resolve, reject) => {
    uni.request({
      url: BASE_URL + (opts.url || ''),
      method: opts.method || 'GET',
      data: opts.data || {},
      ...(opts.responseType ? { responseType: opts.responseType } : {}),
      header: {
        'Content-Type': 'application/json',
        ...(opts.header || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },

      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        const statusCode = res.statusCode
        const data = res.data

        if (statusCode >= 200 && statusCode < 300) {
          resolve(data)
          return
        }

        // 业务请求的 access token 过期时刷新一次并自动重试原请求。
        if (
          statusCode === 401 &&
          opts.auth !== false &&
          !isAuthenticationRequest(opts.url)
        ) {
          retryAfterUnauthorized(
            token,
            () => request(opts, true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }

        const message = getFriendlyMessage(data, '请求失败')

        if (!opts.silentError) {
          uni.showToast({
            title: message,
            icon: 'none',
          })
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

        if (!opts.silentError) {
          uni.showToast({
            title: message,
            icon: 'none',
          })
        }

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

export function downloadProductImportTemplate(alreadyRetried = false) {
  const epoch = sessionEpoch
  const token = getToken()
  const url = `${BASE_URL}/api/products/import-template/`

  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    return window.fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async (response) => {
      if (epoch !== sessionEpoch) throw sessionChangedError()
      if (response.status === 401) {
        return retryAfterUnauthorized(
          token,
          () => downloadProductImportTemplate(true),
          alreadyRetried,
          epoch,
        )
      }
      if (!response.ok) {
        let data = null
        try {
          data = await response.json()
        } catch (e) {}
        throw {
          statusCode: response.status,
          data,
          message:
            response.status === 403
              ? '无商品导入权限'
              : getFriendlyMessage(data, '模板下载失败'),
        }
      }
      const blob = await response.blob()
      if (epoch !== sessionEpoch) throw sessionChangedError()
      const objectUrl = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = '商品批量导入模板.xlsx'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(objectUrl)
      return { opened: true, tempFilePath: '' }
    })
  }
  // #endif

  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ tempFilePath: res.tempFilePath || '', opened: false })
          return
        }
        if (res.statusCode === 401) {
          retryAfterUnauthorized(
            token,
            () => downloadProductImportTemplate(true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }
        reject({
          statusCode: res.statusCode,
          data: res.data,
          message:
            res.statusCode === 403
              ? '无商品导入权限'
              : getFriendlyMessage(res.data, '模板下载失败'),
        })
      },
      fail: (error) => reject({ statusCode: 0, data: error, message: '模板下载失败' }),
    })
  })
}

export function downloadProductArchive(ownerId, ownerCode = '', alreadyRetried = false) {
  const url = `${BASE_URL}/api/products/export-excel/?owner_id=${encodeURIComponent(ownerId)}`
  return downloadProductArchiveFile(url, `商品档案-${ownerCode || ownerId}.xlsx`, alreadyRetried)
}

export function downloadAllProductArchives(alreadyRetried = false) {
  const url = `${BASE_URL}/api/products/export-all-excel/`
  return downloadProductArchiveFile(url, '全部商品档案.xlsx', alreadyRetried)
}

function downloadProductArchiveFile(url, fallbackFilename, alreadyRetried) {
  const epoch = sessionEpoch
  const token = getToken()

  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    return window.fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async (response) => {
      if (epoch !== sessionEpoch) throw sessionChangedError()
      if (response.status === 401) {
        return retryAfterUnauthorized(
          token,
          () => downloadProductArchiveFile(url, fallbackFilename, true),
          alreadyRetried,
          epoch,
        )
      }
      if (!response.ok) {
        let data = null
        try {
          data = await response.json()
        } catch (e) {}
        throw {
          statusCode: response.status,
          data,
          message:
            response.status === 403
              ? '无商品档案导出权限'
              : getFriendlyMessage(data, '商品档案导出失败'),
        }
      }
      const blob = await response.blob()
      if (epoch !== sessionEpoch) throw sessionChangedError()
      const disposition = response.headers.get('content-disposition') || ''
      const matched = disposition.match(/filename\*=UTF-8''([^;]+)/i)
      const filename = matched
        ? decodeURIComponent(matched[1])
        : fallbackFilename
      const objectUrl = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(objectUrl)
      return { opened: true, tempFilePath: '', filename }
    })
  }
  // #endif

  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ tempFilePath: res.tempFilePath || '', opened: false })
          return
        }
        if (res.statusCode === 401) {
          retryAfterUnauthorized(
            token,
            () => downloadProductArchiveFile(url, fallbackFilename, true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }
        reject({
          statusCode: res.statusCode,
          data: res.data,
          message: res.statusCode === 403 ? '无商品档案导出权限' : '商品档案导出失败',
        })
      },
      fail: (error) =>
        reject({ statusCode: 0, data: error, message: '商品档案下载失败' }),
    })
  })
}

export function downloadMissingGtinProducts(alreadyRetried = false) {
  const epoch = sessionEpoch
  const token = getToken()
  const url = `${BASE_URL}/api/products/export-missing-gtin/`

  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    return window.fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async (response) => {
      if (epoch !== sessionEpoch) throw sessionChangedError()
      if (response.status === 401) {
        return retryAfterUnauthorized(
          token,
          () => downloadMissingGtinProducts(true),
          alreadyRetried,
          epoch,
        )
      }
      if (!response.ok) {
        let data = null
        try {
          data = await response.json()
        } catch (e) {}
        throw {
          statusCode: response.status,
          data,
          message:
            response.status === 403
              ? '无商品档案导出权限'
              : getFriendlyMessage(data, '无标准条码商品导出失败'),
        }
      }
      const blob = await response.blob()
      if (epoch !== sessionEpoch) throw sessionChangedError()
      const disposition = response.headers.get('content-disposition') || ''
      const matched = disposition.match(/filename\*=UTF-8''([^;]+)/i)
      const filename = matched
        ? decodeURIComponent(matched[1])
        : '无标准条码商品.xlsx'
      const objectUrl = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(objectUrl)
      return { opened: true, tempFilePath: '', filename }
    })
  }
  // #endif

  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ tempFilePath: res.tempFilePath || '', opened: false })
          return
        }
        if (res.statusCode === 401) {
          retryAfterUnauthorized(
            token,
            () => downloadMissingGtinProducts(true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }
        reject({
          statusCode: res.statusCode,
          data: res.data,
          message:
            res.statusCode === 403 ? '无商品档案导出权限' : '无标准条码商品导出失败',
        })
      },
      fail: (error) =>
        reject({ statusCode: 0, data: error, message: '无标准条码商品下载失败' }),
    })
  })
}

export function uploadProductImportExcel(
  filePath,
  warehouseId = null,
  alreadyRetried = false,
) {
  const epoch = sessionEpoch
  const token = getToken()
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${BASE_URL}/api/products/import-excel/`,
      filePath,
      name: 'file',
      timeout: PRODUCT_IMPORT_UPLOAD_TIMEOUT_MS,
      formData: warehouseId ? { warehouse_id: String(warehouseId) } : {},
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        let data = res.data
        try {
          data = typeof data === 'string' ? JSON.parse(data) : data
        } catch (error) {
          reject({
            statusCode: res.statusCode,
            data,
            message:
              res.statusCode >= 500
                ? '服务器异常，请稍后重试'
                : '服务器返回了无法识别的结果',
          })
          return
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(data)
          return
        }
        if (res.statusCode === 401) {
          retryAfterUnauthorized(
            token,
            () => uploadProductImportExcel(filePath, warehouseId, true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }
        reject({
          statusCode: res.statusCode,
          data,
          message:
            res.statusCode === 403
              ? '无商品导入权限'
              : getFriendlyMessage(data, '商品导入失败'),
        })
      },
      fail: (error) =>
        reject({
          statusCode: 0,
          data: error,
          message: '网络异常，请稍后重试',
        }),
    })
  })
}

export function downloadNoOrderReceiveImportTemplate(ownerId, alreadyRetried = false) {
  const epoch = sessionEpoch
  const token = getToken()
  const url = `${BASE_URL}/api/inbound/receive_without_order/import_template/?owner_id=${encodeURIComponent(ownerId)}`

  // #ifdef H5
  if (typeof window !== 'undefined' && typeof window.fetch === 'function') {
    return window.fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async (response) => {
      if (epoch !== sessionEpoch) throw sessionChangedError()
      if (response.status === 401) {
        return retryAfterUnauthorized(
          token,
          () => downloadNoOrderReceiveImportTemplate(ownerId, true),
          alreadyRetried,
          epoch,
        )
      }
      if (!response.ok) {
        let data = null
        try {
          data = await response.json()
        } catch (e) {}
        throw {
          statusCode: response.status,
          data,
          message: getFriendlyMessage(data, '模板下载失败'),
        }
      }
      const blob = await response.blob()
      if (epoch !== sessionEpoch) throw sessionChangedError()
      const objectUrl = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = '无订单批量入库模板.xlsx'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(objectUrl)
      return { opened: true, tempFilePath: '' }
    })
  }
  // #endif

  return new Promise((resolve, reject) => {
    uni.downloadFile({
      url,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ tempFilePath: res.tempFilePath || '', opened: false })
          return
        }
        if (res.statusCode === 401) {
          retryAfterUnauthorized(
            token,
            () => downloadNoOrderReceiveImportTemplate(ownerId, true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }
        reject({
          statusCode: res.statusCode,
          data: res.data,
          message: getFriendlyMessage(res.data, '模板下载失败'),
        })
      },
      fail: (error) => reject({ statusCode: 0, data: error, message: '模板下载失败' }),
    })
  })
}

export function uploadNoOrderReceiveImportPreview(filePath, ownerId, alreadyRetried = false) {
  const epoch = sessionEpoch
  const token = getToken()
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${BASE_URL}/api/inbound/receive_without_order/import_preview/`,
      filePath,
      name: 'file',
      formData: { owner_id: String(ownerId) },
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success: (res) => {
        if (epoch !== sessionEpoch) { reject(sessionChangedError()); return }
        let data = res.data
        try {
          data = typeof data === 'string' ? JSON.parse(data) : data
        } catch (error) {
          reject({
            statusCode: res.statusCode,
            data,
            message: res.statusCode >= 500 ? '服务器异常，请稍后重试' : '服务器返回了无法识别的结果',
          })
          return
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(data)
          return
        }
        if (res.statusCode === 401) {
          retryAfterUnauthorized(
            token,
            () => uploadNoOrderReceiveImportPreview(filePath, ownerId, true),
            alreadyRetried,
            epoch,
          ).then(resolve, reject)
          return
        }
        reject({
          statusCode: res.statusCode,
          data,
          message: getFriendlyMessage(data, 'Excel 校验失败'),
        })
      },
      fail: (error) => reject({ statusCode: 0, data: error, message: '网络异常，请稍后重试' }),
    })
  })
}

export const api = {
  homeTodos: () => request({ url: '/api/pda/home-todos/', silentError: true }),
  // Auth
  login: (username, password) =>
    request({
      url: '/api/pda/token/',
      method: 'POST',
      data: { username, password },
      auth: false,
    }),

  logout: (refresh) =>
    request({
      url: '/api/auth/logout/',
      method: 'POST',
      data: { refresh },
      auth: false,
      silentError: true,
    }),

  profile: () =>
    request({
      url: '/api/auth/profile/',
      method: 'GET',
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

  downloadProductImportTemplate,
  importProductsExcel: uploadProductImportExcel,
  productImportWarehouses: () =>
    request({
      url: '/api/products/import-warehouses/',
      method: 'GET',
    }),
  productExportOwners: (q = '', page = 1) =>
    request({
      url: `/api/products/export-owners/?search=${encodeURIComponent(q)}&page=${page}`,
    }),
  downloadProductArchive,
  downloadMissingGtinProducts,
  downloadNoOrderReceiveImportTemplate,
  previewNoOrderReceiveImport: uploadNoOrderReceiveImportPreview,
  confirmNoOrderReceiveImport: (payload) =>
    request({
      url: '/api/inbound/receive_without_order/import_confirm/',
      method: 'POST',
      data: payload,
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
      silentError: true,
    }),

  gs1ProductLookup: (ownerId, barcode) =>
    request({
      url: '/api/inbound/gs1-products/lookup/',
      method: 'POST',
      data: { owner_id: ownerId, barcode },
      silentError: true,
    }),

  gs1ProductOptions: (ownerId, search = '') =>
    request({
      url: `/api/inbound/gs1-products/options/?${buildQuery({ owner_id: ownerId, search })}`,
    }),

  gs1ProductQuickCreate: (payload) =>
    request({
      url: '/api/inbound/gs1-products/quick-create/',
      method: 'POST',
      data: payload,
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

  // Ordered inbound PDA tasks.  These endpoints deliberately use the server
  // scope inferred from the logged-in operator; do not accept owner or
  // warehouse IDs from the page.
  inboundPdaTasks: (params = {}) =>
    request({
      url: `/api/inbound/pda/tasks/?${buildQuery({
        task_type: params.task_type || '',
        search: params.search || '',
        status: params.status || '',
        page: params.page || 1,
      })}`,
    }),

  inboundPdaTask: (taskId) =>
    request({
      url: `/api/inbound/pda/tasks/${taskId}/`,
    }),

  claimInboundPdaTask: (taskId) =>
    request({
      url: `/api/inbound/pda/tasks/${taskId}/claim/`,
      method: 'POST',
    }),

  startInboundPdaTask: (taskId) =>
    request({
      url: `/api/inbound/pda/tasks/${taskId}/start/`,
      method: 'POST',
    }),

  inboundPdaLocations: (taskId, search = '') =>
    request({
      url: `/api/inbound/pda/tasks/${taskId}/locations/?${buildQuery({ search })}`,
    }),

  recordInboundReceipt: (taskId, payload) =>
    request({
      url: `/api/inbound/pda/tasks/${taskId}/record-receipt/`,
      method: 'POST',
      data: payload,
    }),

  recordInboundPutaway: (taskId, payload) =>
    request({
      url: `/api/inbound/pda/tasks/${taskId}/record-putaway/`,
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

  assistedOwners: (q = '', page = 1) =>
    request({
      url: `/api/outbound/assisted-orders/owners/?${buildQuery({ search: q, page })}`,
    }),

  assistedCustomers: (ownerId, q = '', page = 1) =>
    request({
      url: `/api/outbound/assisted-orders/customers/?${buildQuery({ owner_id: ownerId, search: q, page })}`,
    }),

  assistedProducts: (ownerId, q = '', page = 1) =>
    request({
      url: `/api/outbound/assisted-orders/products/?${buildQuery({ owner_id: ownerId, search: q, page })}`,
    }),

  createAssistedOutboundOrder: (payload) =>
    request({
      url: '/api/outbound/assisted-orders/',
      method: 'POST',
      data: payload,
    }),

  assistedOutboundHistory: (params = {}) => {
    const qs = buildQuery({
      search: params.search || '',
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      owner_id: params.owner_id || '',
      operator_id: params.operator_id || '',
      status: params.status || '',
      page: params.page || 1,
      page_size: params.page_size || 20,
    })
    return request({ url: `/api/outbound/assisted-orders/history/?${qs}` })
  },

  assistedOutboundHistoryOptions: () =>
    request({ url: '/api/outbound/assisted-orders/history-options/' }),

  assistedOutboundStats: (params = {}) => {
    const qs = buildQuery({
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      owner_id: params.owner_id || '',
      operator_id: params.operator_id || '',
      top_n: params.top_n || 10,
    })
    return request({ url: `/api/outbound/assisted-orders/stats/?${qs}` })
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

  warehouseReviewOrders: (params = {}) =>
    request({ url: `/api/outbound/warehouse-review/?${buildQuery(params)}` }),
  warehouseReviewOptions: () =>
    request({ url: '/api/outbound/warehouse-review/options/' }),
  warehouseReviewDetail: (id) =>
    request({ url: `/api/outbound/warehouse-review/${id}/` }),
  warehouseConfirm: (id, data) =>
    request({ url: `/api/outbound/orders/${id}/warehouse-confirm/`, method: 'POST', data }),
  warehouseReject: (id, data) =>
    request({ url: `/api/outbound/orders/${id}/warehouse-reject/`, method: 'POST', data }),

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

  systemSettings: () =>
    request({
      url: '/api/core/settings/',
    }),

  printConfigs: (params = {}) => {
    const qs = buildQuery({
      module: params.module || '',
    })
    return request({
      url: `/api/core/print-configs/?${qs}`,
    })
  },

  defaultPrintConfig: (module = '') =>
    request({
      url: `/api/core/print-configs/default/?${buildQuery({ module })}`,
    }),

  posProducts: (params = {}) => {
    const qs = buildQuery({
      ...params,
      search: params.search || '',
      barcode: params.barcode || '',
      page: params.page || 1,
      page_size: params.page_size || 20,
    })
    return request({
      url: `/api/pos/products/?${qs}`,
    })
  },

  posReceiptWarehouseInfos: () =>
    request({
      url: '/api/pos/receipt-warehouse-infos/',
    }),

  posCheckout: (payload) =>
    request({
      url: '/api/pos/checkout/',
      method: 'POST',
      data: payload,
    }),

  posSales: (params = {}) => {
    const qs = buildQuery({
      search: params.search || '',
      status: params.status || '',
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      shift: params.shift || '',
      page: params.page || 1,
      page_size: params.page_size || 20,
    })
    return request({
      url: `/api/pos/sales/?${qs}`,
    })
  },

  posSalesExport: (params = {}) => {
    const qs = buildQuery({
      search: params.search || '',
      status: params.status || '',
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      shift: params.shift || '',
      include_all_sheet: params.include_all_sheet || '',
    })
    return `${BASE_URL}/api/pos/sales/export/?${qs}`
  },

  posStats: (params = {}) => {
    const qs = buildQuery({
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      owner_id: params.owner_id || '',
      cashier_id: params.cashier_id || '',
      top_n: params.top_n || 10,
    })
    return request({
      url: `/api/pos/stats/?${qs}`,
    })
  },

  posStatsExport: (params = {}) => {
    const qs = buildQuery({
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      owner_id: params.owner_id || '',
      cashier_id: params.cashier_id || '',
      top_n: params.top_n || 10,
      include_all_sheet: params.include_all_sheet || '',
    })
    return `${BASE_URL}/api/pos/stats/export/?${qs}`
  },

  posAccuracy: (params = {}) => {
    const qs = buildQuery({
      start_date: params.start_date || '',
      end_date: params.end_date || '',
    })
    return request({
      url: `/api/pos/accuracy/?${qs}`,
    })
  },

  posSaleDetail: (id) =>
    request({
      url: `/api/pos/sales/${id}/`,
    }),

  posSaleReceipt: (id) =>
    request({
      url: `/api/pos/sales/${id}/receipt/`,
    }),

  posSalePrintLog: (id, payload = {}) =>
    request({
      url: `/api/pos/sales/${id}/print-log/`,
      method: 'POST',
      data: payload,
    }),

  posSalePrintUrl: (id) => `${BASE_URL}/api/pos/sales/${id}/print/`,

  posSaleVoid: (id, payload = {}) =>
    request({
      url: `/api/pos/sales/${id}/void/`,
      method: 'POST',
      data: payload,
    }),

  posCustomers: (q = '', page = 1) =>
    request({
      url: `/api/pos/customers/?search=${encodeURIComponent(q)}&page=${page}`,
    }),

  posCustomerCreate: (payload = {}) =>
    request({
      url: '/api/pos/customers/',
      method: 'POST',
      data: payload,
    }),

  posCustomerUpdate: (id, payload = {}) =>
    request({
      url: `/api/pos/customers/${id}/`,
      method: 'PATCH',
      data: payload,
    }),

  posCustomerDebt: (customerId) =>
    request({
      url: `/api/pos/customer-debts/${customerId}/`,
    }),

  posRepayments: (params = {}) => {
    const qs = buildQuery({
      customer_id: params.customer_id || '',
      page: params.page || 1,
      page_size: params.page_size || 20,
    })
    return request({
      url: `/api/pos/repayments/?${qs}`,
    })
  },

  posRepaymentCreate: (payload = {}) =>
    request({
      url: '/api/pos/repayments/',
      method: 'POST',
      data: payload,
    }),

  posReturns: (params = {}) => {
    const qs = buildQuery({
      sale_id: params.sale_id || '',
      page: params.page || 1,
      page_size: params.page_size || 20,
    })
    return request({
      url: `/api/pos/returns/?${qs}`,
    })
  },

  posReturnCreate: (payload = {}) =>
    request({
      url: '/api/pos/returns/',
      method: 'POST',
      data: payload,
    }),

  posReturnDetail: (id) =>
    request({
      url: `/api/pos/returns/${id}/`,
    }),

  posShiftCurrent: () =>
    request({
      url: '/api/pos/shifts/current/',
    }),

  posShiftOpen: (payload = {}) =>
    request({
      url: '/api/pos/shifts/open/',
      method: 'POST',
      data: payload,
    }),

  posShifts: (params = {}) => {
    const qs = buildQuery({
      page: params.page || 1,
      page_size: params.page_size || 20,
    })
    return request({
      url: `/api/pos/shifts/?${qs}`,
    })
  },

  posShiftClose: (id, payload = {}) =>
    request({
      url: `/api/pos/shifts/${id}/close/`,
      method: 'POST',
      data: payload,
    }),

  posShiftReopen: (id, payload = {}) =>
    request({
      url: `/api/pos/shifts/${id}/reopen/`,
      method: 'POST',
      data: payload,
    }),

  posShiftPrintUrl: (id) => `${BASE_URL}/api/pos/shifts/${id}/print/`,

  posShiftExportUrl: (id, params = {}) => {
    const qs = buildQuery({ include_all_sheet: params.include_all_sheet || '' })
    return `${BASE_URL}/api/pos/shifts/${id}/export/${qs ? `?${qs}` : ''}`
  },

  authUrl: (url) => withAuthToken(url),

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
      url: `/api/pda/pick-tasks/?${buildQuery({ ...params, for_review: 1 })}`,
      method: 'GET',
    }),

  pickReviewContext: (id) =>
    request({ url: `/api/pda/pick-tasks/${id}/review-context/`, method: 'GET' }),

  adjustPickLineQty: (taskId, data) =>
    request({
      url: `/api/pda/pick-tasks/${taskId}/adjust-line-qty/`,
      method: 'POST',
      data,
    }),

  // 补货策略、申请与 PDA 作业
  replenishmentPolicies: () =>
    request({ url: '/api/replenishment/policies/', method: 'GET' }),

  replenishmentRequests: () =>
    request({ url: '/api/replenishment/requests/', method: 'GET' }),

  createReplenishmentRequest: (payload) =>
    request({ url: '/api/replenishment/requests/', method: 'POST', data: payload }),

  approveReplenishmentRequest: (id, payload = {}) =>
    request({ url: `/api/replenishment/requests/${id}/approve/`, method: 'POST', data: payload }),

  rejectReplenishmentRequest: (id, payload = {}) =>
    request({ url: `/api/replenishment/requests/${id}/reject/`, method: 'POST', data: payload }),

  replenishmentTasks: (params = {}) =>
    request({ url: `/api/replenishment/pda/tasks/?${buildQuery(params)}`, method: 'GET' }),

  replenishmentTask: (id) =>
    request({ url: `/api/replenishment/pda/tasks/${id}/`, method: 'GET' }),

  claimReplenishmentTask: (id) =>
    request({ url: `/api/replenishment/pda/tasks/${id}/claim/`, method: 'POST' }),

  startReplenishmentTask: (id) =>
    request({ url: `/api/replenishment/pda/tasks/${id}/start/`, method: 'POST' }),

  recordReplenishment: (id, payload) =>
    request({ url: `/api/replenishment/pda/tasks/${id}/record/`, method: 'POST', data: payload }),

  retryReplenishmentPosting: (id) =>
    request({ url: `/api/replenishment/pda/tasks/${id}/retry-posting/`, method: 'POST' }),

  // 移库申请与 PDA 作业
  relocationOptions: (params = {}) =>
    request({ url: `/api/relocation/options/?${buildQuery(params)}`, method: 'GET' }),
  relocationRequests: () =>
    request({ url: '/api/relocation/requests/', method: 'GET' }),
  createRelocationRequest: (payload) =>
    request({ url: '/api/relocation/requests/', method: 'POST', data: payload }),
  approveRelocationRequest: (id, payload = {}) =>
    request({ url: `/api/relocation/requests/${id}/approve/`, method: 'POST', data: payload }),
  rejectRelocationRequest: (id, payload = {}) =>
    request({ url: `/api/relocation/requests/${id}/reject/`, method: 'POST', data: payload }),
  cancelRelocationRequest: (id) =>
    request({ url: `/api/relocation/requests/${id}/cancel/`, method: 'POST' }),
  directReleaseRelocation: (payload) =>
    request({ url: '/api/relocation/direct-release/', method: 'POST', data: payload }),
  relocationTasks: (params = {}) =>
    request({ url: `/api/relocation/pda/tasks/?${buildQuery(params)}`, method: 'GET' }),
  relocationTask: (id) =>
    request({ url: `/api/relocation/pda/tasks/${id}/`, method: 'GET' }),
  claimRelocationTask: (id) =>
    request({ url: `/api/relocation/pda/tasks/${id}/claim/`, method: 'POST' }),
  startRelocationTask: (id) =>
    request({ url: `/api/relocation/pda/tasks/${id}/start/`, method: 'POST' }),
  recordRelocation: (id, payload) =>
    request({ url: `/api/relocation/pda/tasks/${id}/record/`, method: 'POST', data: payload }),
  reportRelocationException: (id, payload) =>
    request({ url: `/api/relocation/pda/tasks/${id}/report-exception/`, method: 'POST', data: payload }),
  resumeRelocation: (id) =>
    request({ url: `/api/relocation/pda/tasks/${id}/resume/`, method: 'POST' }),
  voidRelocation: (id, payload) =>
    request({ url: `/api/relocation/pda/tasks/${id}/void/`, method: 'POST', data: payload }),
  retryRelocationPosting: (id) =>
    request({ url: `/api/relocation/pda/tasks/${id}/retry-posting/`, method: 'POST' }),

  // PDA 盘点
  countTasks: (params = {}) =>
    request({ url: `/api/pda/count-tasks/?${buildQuery(params)}`, method: 'GET' }),

  countTaskDetail: (id) =>
    request({ url: `/api/pda/count-tasks/${id}/`, method: 'GET' }),

  countTaskLines: (id) =>
    request({ url: `/api/pda/count-tasks/${id}/lines/`, method: 'GET' }),

  claimCountTask: (id) =>
    request({ url: `/api/pda/count-tasks/${id}/claim/`, method: 'POST' }),

  recordCount: (id, payload) =>
    request({ url: `/api/pda/count-tasks/${id}/record/`, method: 'POST', data: payload }),

  submitCountTask: (id) =>
    request({ url: `/api/pda/count-tasks/${id}/submit/`, method: 'POST' }),

  approveCountTask: (id, payload = {}) =>
    request({ url: `/api/pda/count-tasks/${id}/approve/`, method: 'POST', data: payload }),

  rejectCountTask: (id, payload = {}) =>
    request({ url: `/api/pda/count-tasks/${id}/reject/`, method: 'POST', data: payload }),

  postCountTask: (id, payload = {}) =>
    request({ url: `/api/pda/count-tasks/${id}/post/`, method: 'POST', data: payload }),


  companyInventorySummary: (params = {}) => {
    const qs = buildQuery({
      ...params,
      mode: params.mode || 'warehouse',
      warehouse_id: params.warehouse_id || '',
      owner_id: params.owner_id || '',
      search: params.search || '',
      page: params.page || 1,
      page_size: params.page_size || 100,
    })
    return request({
      url: `/api/inventory/company-summary/?${qs}`,
    })
  },

  pdaThroughputStats: (params = {}) => {
    const qs = buildQuery({
      mode: params.mode || 'month',
      month: params.month || '',
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      owner: params.owner || '',
      warehouse: params.warehouse || '',
    })
    return request({
      url: `/api/reports/pda/throughput/?${qs}`,
    })
  },

  pdaThroughputDetails: (params = {}) => {
    const qs = buildQuery({
      mode: params.mode || 'month',
      month: params.month || '',
      start_date: params.start_date || '',
      end_date: params.end_date || '',
      owner: params.owner || '',
      warehouse: params.warehouse || '',
      metric: params.metric || 'all',
    })
    return request({
      url: `/api/reports/pda/throughput/details/?${qs}`,
    })
  },


}

export function downloadReturnOrders(filters = {}) {
  const query = Object.entries(filters).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join('&')
  return downloadProductArchiveFile(`${BASE_URL}/api/pda/return-orders/export/?${query}`, '退货出库.xlsx')
}

// Customer-return photos remain authenticated and account-isolated.
export function uploadAuthenticatedFile(opts, retried = false) {
  const epoch = sessionEpoch, token = getToken()
  return new Promise((resolve,reject)=>uni.uploadFile({
    url: BASE_URL + opts.url, filePath: opts.filePath, name: 'file', formData:opts.formData || {},
    header: {Authorization:`Bearer ${token}`},
    success(res) {
      if(epoch!==sessionEpoch) {reject(sessionChangedError());return}
      if(res.statusCode===401) {retryAfterUnauthorized(token,()=>uploadAuthenticatedFile(opts,true),retried,epoch).then(resolve,reject);return}
      let data;try{data=typeof res.data==='string'?JSON.parse(res.data):res.data}catch{reject({message:'上传响应无效'});return}
      if(res.statusCode>=200 && res.statusCode<300) resolve(data)
      else reject({statusCode:res.statusCode,message:getFriendlyMessage(data,'照片上传失败')})
    },fail:()=>reject({message:'照片上传失败，请重试'})
  }))
}
export async function returnPhotoURL(url, retried = false) {
  // #ifdef H5
  const bytes = await request({url, responseType: 'arraybuffer', silentError: true})
  const source = URL.createObjectURL(new Blob([bytes], {type: 'image/jpeg'}))
  setTimeout(() => URL.revokeObjectURL(source), 60000)
  return source
  // #endif
  // #ifndef H5
  const epoch=sessionEpoch, token=getToken()
  return new Promise((resolve,reject)=>uni.downloadFile({url:BASE_URL+url,header:{Authorization:`Bearer ${token}`},
    success(res) {
      if(epoch!==sessionEpoch){reject(sessionChangedError());return}
      if(res.statusCode===401){retryAfterUnauthorized(token,()=>returnPhotoURL(url,true),retried,epoch).then(resolve,reject);return}
      if(res.statusCode===200)resolve(res.tempFilePath)
      else reject({statusCode:res.statusCode,message:'无权查看照片或照片已失效'})
    },fail:()=>reject({message:'照片加载失败'})
  }))
  // #endif
}
