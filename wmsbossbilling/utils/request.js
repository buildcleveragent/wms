export { BASE_URL } from './api-base'
import { BASE_URL } from './api-base'

const ACCESS_KEY = 'access'
const REFRESH_KEY = 'refresh'
const USER_KEY = 'user'
let sessionEpoch = 0
function checkSession(epoch) {
  if (epoch !== sessionEpoch) throw classifyError(409, { detail: '登录账号已变化，请重新操作' })
}

function storageGet(key) {
  try {
    return uni.getStorageSync(key) || ''
  } catch (error) {
    return ''
  }
}

export function getStoredToken() {
  return storageGet(ACCESS_KEY)
}

export function getStoredRefreshToken() {
  return storageGet(REFRESH_KEY)
}

export function setTokens(access, refresh) {
  sessionEpoch++
  persistTokens(access, refresh)
}

function persistTokens(access, refresh) {
  try {
    if (access) uni.setStorageSync(ACCESS_KEY, access)
    else uni.removeStorageSync(ACCESS_KEY)
    if (refresh) uni.setStorageSync(REFRESH_KEY, refresh)
    else uni.removeStorageSync(REFRESH_KEY)
  } catch (error) {}
}

export function setToken(token) {
  setTokens(token, getStoredRefreshToken())
}

export function getStoredUser() {
  try {
    const raw = uni.getStorageSync(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch (error) {
    return null
  }
}

export function setStoredUser(user) {
  try {
    if (user) uni.setStorageSync(USER_KEY, JSON.stringify(user))
    else uni.removeStorageSync(USER_KEY)
  } catch (error) {}
}

export function clearToken() {
  sessionEpoch++
  try {
    uni.removeStorageSync(ACCESS_KEY)
    uni.removeStorageSync(REFRESH_KEY)
    uni.removeStorageSync(USER_KEY)
  } catch (error) {}
}

function credentialEndpoint(url = '') {
  return ['/api/auth/login/', '/api/auth/refresh/', '/api/auth/logout/'].includes(url)
}

function friendlyMessage(data, fallback = '请求失败') {
  if (!data) return fallback
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data[0] || fallback
  if (typeof data.detail === 'string') return data.detail
  if (typeof data.message === 'string') return data.message
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return value[0]
    if (typeof value === 'string') return value
  }
  return fallback
}

export function classifyError(statusCode, data) {
  let kind = 'BUSINESS_ERROR'
  if (!statusCode) kind = 'NETWORK_ERROR'
  else if (statusCode === 401) kind = 'UNAUTHENTICATED'
  else if (statusCode === 403) kind = 'FORBIDDEN'
  else if (statusCode >= 500) kind = 'SERVER_ERROR'
  return {
    kind,
    code: data?.code || statusCode || 0,
    statusCode: statusCode || 0,
    message: friendlyMessage(data, kind === 'NETWORK_ERROR' ? '网络异常，请稍后重试' : '请求失败'),
    data,
  }
}

function rawRequest(options, token = '') {
  return new Promise((resolve, reject) => {
    uni.request({
      url: BASE_URL + (options.url || ''),
      method: options.method || 'GET',
      data: options.data || {},
      responseType: options.responseType || 'text',
      header: {
        'Content-Type': 'application/json',
        ...(options.header || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(options.rawResponse ? res : res.data)
        else reject(classifyError(res.statusCode, decodeErrorData(res.data)))
      },
      fail: (error) => reject(classifyError(0, error)),
    })
  })
}

function decodeErrorData(data) {
  if (Object.prototype.toString.call(data) !== '[object ArrayBuffer]') return data
  // Download requests still receive JSON error bodies; decode them on H5 and mini-programs.
  try {
    const encoded = Array.from(new Uint8Array(data), byte => `%${byte.toString(16).padStart(2, '0')}`).join('')
    return JSON.parse(decodeURIComponent(encoded))
  } catch (error) {
    return { detail: '下载失败，请重试' }
  }
}

let refreshFlight = null
let refreshEpoch = -1
let redirectingToLogin = false

function redirectToLogin() {
  if (redirectingToLogin) return
  redirectingToLogin = true
  clearToken()
  const expiredEpoch = sessionEpoch
  uni.showToast({ title: '登录已失效，请重新登录', icon: 'none', duration: 1500 })
  setTimeout(() => {
    if (expiredEpoch === sessionEpoch) uni.reLaunch({ url: '/pages/login' })
    setTimeout(() => { redirectingToLogin = false }, 300)
  }, 300)
}

async function refreshAccessToken() {
  const epoch = sessionEpoch
  if (refreshFlight && refreshEpoch === epoch) return refreshFlight
  const refresh = getStoredRefreshToken()
  if (!refresh) throw classifyError(401, { detail: 'Refresh token is missing.' })

  refreshEpoch = epoch
  const flight = rawRequest({
    url: '/api/auth/refresh/',
    method: 'POST',
    data: { refresh },
  }).then((data) => {
    checkSession(epoch)
    if (!data?.access) throw classifyError(401, { detail: 'Refresh response is invalid.' })
    persistTokens(data.access, data.refresh || refresh)
    return data.access
  }).catch(error => {
    checkSession(epoch)
    throw error
  }).finally(() => {
    if (refreshFlight === flight) refreshFlight = null
  })
  refreshFlight = flight
  return flight
}

export function buildQuery(params = {}, { keepEmpty = [] } = {}) {
  return Object.entries(params)
    .filter(([key, value]) => value !== undefined && value !== null && (value !== '' || keepEmpty.includes(key)))
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
}

export async function request(options = {}) {
  const epoch = sessionEpoch
  const originalAccess = getStoredToken()
  try {
    const result = await rawRequest(options, originalAccess)
    checkSession(epoch)
    return result
  } catch (error) {
    checkSession(epoch)
    if (error.statusCode !== 401 || credentialEndpoint(options.url) || options._retried) throw error
    let access
    try {
      access = getStoredToken() !== originalAccess ? getStoredToken() : await refreshAccessToken()
      checkSession(epoch)
    } catch (refreshError) {
      checkSession(epoch)
      if ([401, 403].includes(refreshError?.statusCode)) redirectToLogin()
      throw refreshError
    }
    try {
      const result = await rawRequest({ ...options, _retried: true }, access)
      checkSession(epoch)
      return result
    } catch (retryError) {
      checkSession(epoch)
      if (retryError?.statusCode === 401) redirectToLogin()
      throw retryError
    }
  }
}

export async function downloadBinary({ url, method = 'GET', data = {}, filename = 'report.xlsx' }) {
  const response = await request({ url, method, data, responseType: 'arraybuffer', rawResponse: true })
  const disposition = response.header?.['content-disposition'] || response.header?.['Content-Disposition'] || ''
  const matched = disposition.match(/filename="?([^";]+)"?/i)
  const resolvedName = matched?.[1] || filename
  // #ifdef H5
  const blob = new Blob([response.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
  const anchor = document.createElement('a')
  anchor.href = URL.createObjectURL(blob)
  anchor.download = resolvedName
  anchor.click()
  URL.revokeObjectURL(anchor.href)
  // #endif
  // #ifndef H5
  const fs = uni.getFileSystemManager()
  const filePath = `${uni.env.USER_DATA_PATH}/${resolvedName}`
  fs.writeFileSync(filePath, response.data, 'binary')
  uni.openDocument({ filePath, showMenu: true })
  // #endif
  return { filename: resolvedName }
}

function get(url, params = {}) {
  const query = buildQuery(params)
  return request({ url: query ? `${url}?${query}` : url })
}

function post(url, data = {}) {
  return request({ url, method: 'POST', data })
}

export const api = {
  login: (username, password) => request({
    url: '/api/auth/login/', method: 'POST', data: { username, password },
  }),
  refresh: (refresh) => rawRequest({
    url: '/api/auth/refresh/', method: 'POST', data: { refresh },
  }),
  logout: (refresh) => request({
    url: '/api/auth/logout/', method: 'POST', data: { refresh },
  }),
  authProfile: () => get('/api/auth/profile/'),
  bossContext: (params = {}) => get('/api/reports/boss/context/', params),
  bossHome: (params = {}) => get('/api/reports/boss/home/', params),
  bossInventory: (params = {}) => get('/api/reports/boss/inventory/', params),
  bossInventoryDetails: (params = {}) => get('/api/reports/boss/inventory/details/', params),
  bossAlerts: (params = {}) => get('/api/reports/boss/alerts/', params),
  bossAlertSection: (section, params = {}) => get(
    `/api/reports/boss/alerts/sections/${encodeURIComponent(section)}/`, params,
  ),
  bossAlertDetail: (section, itemType, id, params = {}) => get(
    `/api/reports/boss/alerts/sections/${encodeURIComponent(section)}/${encodeURIComponent(itemType)}/${id}/`,
    params,
  ),
  bossRevenueAssurance: (params = {}) => get('/api/reports/boss/revenue-assurance/', params),
  bossReceivables: (params = {}) => get('/api/reports/boss/receivables/', params),
  bossReceivableBills: (params = {}) => get('/api/reports/boss/receivables/bills/', params),
  bossOperations: (params = {}) => get('/api/reports/boss/operations/', params),
  bossResourceYield: (params = {}) => get('/api/reports/boss/resource-yield/', params),
  bossPerformance: (params = {}) => get('/api/reports/boss/performance/', params),
  bossInventoryRisk: (params = {}) => get('/api/reports/boss/inventory-risk/', params),
  bossAlertCases: (params = {}) => get('/api/reports/boss/alert-cases/', params),
  createBusinessReviewSnapshot: (data = {}) => post('/api/reports/boss/review-snapshots/', data),
  billingWarehouseOverview: (params = {}) => get('/api/billing/dashboard/warehouse-overview/', params),
  billingPeriods: (params = {}) => get('/api/billing/periods/', params),
  billingBills: (params = {}) => get('/api/billing/bills/', params),
  billingBillDetail: (id) => get(`/api/billing/bills/${id}/`),
  billingAccruals: (params = {}) => get('/api/billing/accruals/', params),
  billingAccrualDetail: (id) => get(`/api/billing/accruals/${id}/`),
}
