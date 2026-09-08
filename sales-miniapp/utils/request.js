import { BASE_URL } from './api-base'

let sessionEpoch = 0
function checkSession(epoch) {
  if (epoch !== sessionEpoch) throw { statusCode: 409, message: '登录账号已变化，请重新操作' }
}

try {
  uni.removeStorageSync('sales_base_url')
} catch (error) {}

export function getBaseUrl() {
  return BASE_URL
}

export function setBaseUrl(url) {
  const requested = String(url || '').trim().replace(/\/$/, '')
  if (requested && requested !== BASE_URL) {
    throw new Error('服务地址由发布构建配置，运行时不可覆盖。')
  }
}

export function getToken() {
  return uni.getStorageSync('sales_access') || ''
}

export function setToken(access, refresh) {
  sessionEpoch++
  persistTokens(access, refresh)
}

function persistTokens(access, refresh) {
  uni.setStorageSync('sales_access', access || '')
  uni.setStorageSync('sales_refresh', refresh || '')
}

export function clearToken() {
  sessionEpoch++
  uni.removeStorageSync('sales_access')
  uni.removeStorageSync('sales_refresh')
}

export function query(params = {}) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
}

function firstMessage(value) {
  if (!value) return ''
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return firstMessage(value[0])
  if (typeof value === 'object') {
    return firstMessage(value.detail) || firstMessage(value.message) || firstMessage(Object.values(value)[0])
  }
  return String(value)
}

function isDebugPage(message) {
  const text = String(message || '').toLowerCase()
  return (
    text.includes('<!doctype html') ||
    text.includes('<html') ||
    text.includes('<body') ||
    text.includes('traceback') ||
    text.includes('django/db/backends')
  )
}

function messageFrom(data, fallback = '请求失败', statusCode = 0) {
  if (statusCode >= 500) return '服务器处理失败，请稍后重试'
  const raw = firstMessage(data)
  if (!raw || isDebugPage(raw)) return fallback
  const message = mallMessage(raw.replace(/\s+/g, ' ').trim())
  return message.length > 120 ? `${message.slice(0, 117)}...` : message
}

function mallMessage(message) {
  const text = String(message || '')
  if (text.includes('未绑定') && text.includes('客户')) {
    return '当前商品暂未对你的账号开通购买权限，可先浏览或联系客服。'
  }
  if (text.includes('购买权限')) {
    return text
  }
  if (text.includes('购物车包含多个配送包裹')) {
    return '购物车已按配送包裹拆分，可统一提交。'
  }
  if (text.includes('商城履约配置')) {
    return text
  }
  return text
}

let refreshFlight = null
let refreshEpoch = -1

function expireSession(authRedirect) {
  clearToken()
  if (authRedirect) uni.reLaunch({ url: '/pages/login/login' })
}

function refreshSession() {
  const epoch = sessionEpoch
  if (refreshFlight && refreshEpoch === epoch) return refreshFlight
  const refresh = uni.getStorageSync('sales_refresh') || ''
  if (!refresh) return Promise.reject({ statusCode: 401, message: '请重新登录' })
  refreshEpoch = epoch
  const flight = rawRequest({ url: '/api/auth/refresh/', method: 'POST', data: { refresh }, silent: true })
    .then((data) => {
      checkSession(epoch)
      if (!data?.access) throw { statusCode: 401, message: '登录已失效' }
      if (uni.getStorageSync('sales_refresh') !== refresh) {
        throw { statusCode: 409, message: '登录状态已变化，请重试' }
      }
      persistTokens(data.access, data.refresh || refresh)
    }).catch(error => {
      checkSession(epoch)
      throw error
    }).finally(() => { if (refreshFlight === flight) refreshFlight = null })
  refreshFlight = flight
  return flight
}

async function withAuthRecovery(send, { authRedirect = true, credential = false } = {}) {
  const epoch = sessionEpoch
  const originalAccess = getToken()
  try {
    const result = await send()
    checkSession(epoch)
    return result
  } catch (error) {
    checkSession(epoch)
    if (error?.statusCode !== 401 || credential) throw error
  }
  try {
    if (getToken() === originalAccess) await refreshSession()
    checkSession(epoch)
  } catch (error) {
    checkSession(epoch)
    if ([401, 403].includes(error?.statusCode)) expireSession(authRedirect)
    throw error
  }
  try {
    const result = await send()
    checkSession(epoch)
    return result
  } catch (error) {
    checkSession(epoch)
    if (error?.statusCode === 401) expireSession(authRedirect)
    throw error
  }
}

export function request(options = {}) {
  const credential = ['/api/auth/login/', '/api/auth/refresh/', '/api/sale-mini/auth/wechat-login/'].includes(options.url)
  return withAuthRecovery(() => rawRequest(options), { authRedirect: options.authRedirect !== false, credential })
}

export function uploadFile(options) {
  return withAuthRecovery(() => rawUploadFile(options))
}

function rawRequest({ url, method = 'GET', data, header = {}, silent = false }) {
  const token = getToken()
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${getBaseUrl()}${url}`,
      method,
      data,
      header: {
        'Content-Type': 'application/json',
        ...header,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        const msg = messageFrom(res.data, '请求失败', res.statusCode)
        if (!silent && res.statusCode !== 401) uni.showToast({ title: msg, icon: 'none' })
        reject({ statusCode: res.statusCode, message: msg, data: res.data, notified: true })
      },
      fail(err) {
        if (!silent) uni.showToast({ title: '网络异常，请检查服务地址', icon: 'none' })
        reject({ statusCode: 0, message: '网络异常', data: err, notified: true })
      },
    })
  })
}

function rawUploadFile({ url, filePath, name = 'image', formData = {} }) {
  const token = getToken()
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${getBaseUrl()}${url}`,
      filePath,
      name,
      formData,
      header: token ? { Authorization: `Bearer ${token}` } : {},
      success(res) {
        let data = res.data
        if (typeof data === 'string') {
          try {
            data = JSON.parse(data)
          } catch (err) {
            data = { detail: data }
          }
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(data)
          return
        }
        const msg = messageFrom(data, '图片上传失败', res.statusCode)
        if (res.statusCode !== 401) uni.showToast({ title: msg, icon: 'none' })
        reject({ statusCode: res.statusCode, message: msg, data, notified: true })
      },
      fail(err) {
        uni.showToast({ title: '图片上传失败，请检查网络', icon: 'none' })
        reject({ statusCode: 0, message: '图片上传失败', data: err, notified: true })
      },
    })
  })
}

export const api = {
  login: (username, password) =>
    request({
      url: '/api/auth/login/',
      method: 'POST',
      data: { username, password },
    }),
  saleMiniWechatLogin: (payload) =>
    request({
      url: '/api/sale-mini/auth/wechat-login/',
      method: 'POST',
      data: payload,
    }),
  saleMiniMe: () => request({ url: '/api/sale-mini/me/', authRedirect: false }),
  saleMiniHome: (params = {}) => request({ url: `/api/sale-mini/home/?${query(params)}` }),
  saleMiniCategories: (params = {}) => request({ url: `/api/sale-mini/categories/?${query(params)}` }),
  saleMiniBrands: (params = {}) => request({ url: `/api/sale-mini/brands/?${query(params)}` }),
  saleMiniCoupons: (params = {}) => request({ url: `/api/sale-mini/coupons/?${query(params)}` }),
  saleMiniPoints: (params = {}) => request({ url: `/api/sale-mini/points/?${query(params)}` }),
  saleMiniAfterSales: (params = {}) => request({ url: `/api/sale-mini/after-sales/?${query(params)}` }),
  saleMiniProducts: (params = {}) => request({ url: `/api/sale-mini/products/?${query(params)}` }),
  saleMiniProduct: (id, params = {}) => request({ url: `/api/sale-mini/products/${id}/?${query(params)}` }),
  saleMiniProductReviews: (id, params = {}) => request({ url: `/api/sale-mini/products/${id}/reviews/?${query(params)}` }),
  createSaleMiniReviewDraft: (payload) =>
    request({ url: '/api/sale-mini/reviews/drafts/', method: 'POST', data: payload }),
  saleMiniReviewDraft: (id) => request({ url: `/api/sale-mini/reviews/${id}/draft/` }),
  updateSaleMiniReviewDraft: (id, payload) =>
    request({ url: `/api/sale-mini/reviews/${id}/draft/`, method: 'PUT', data: payload }),
  deleteSaleMiniReviewImage: (reviewId, imageId) =>
    request({ url: `/api/sale-mini/reviews/${reviewId}/images/${imageId}/`, method: 'DELETE', data: {} }),
  submitSaleMiniReview: (id) =>
    request({ url: `/api/sale-mini/reviews/${id}/submit/`, method: 'POST', data: {} }),
  saleMiniCart: (params = {}) => request({ url: `/api/sale-mini/cart/?${query(params)}` }),
  addSaleMiniCart: (payload) =>
    request({
      url: '/api/sale-mini/cart/add/',
      method: 'POST',
      data: payload,
    }),
  updateSaleMiniCart: (payload) =>
    request({
      url: '/api/sale-mini/cart/update/',
      method: 'POST',
      data: payload,
    }),
  removeSaleMiniCart: (payload) =>
    request({
      url: '/api/sale-mini/cart/remove/',
      method: 'POST',
      data: payload,
    }),
  clearSaleMiniCart: (payload = {}) =>
    request({
      url: '/api/sale-mini/cart/clear/',
      method: 'POST',
      data: payload,
    }),
  saleMiniAddresses: (params = {}) => request({ url: `/api/sale-mini/addresses/?${query(params)}` }),
  createSaleMiniAddress: (payload) =>
    request({
      url: '/api/sale-mini/addresses/',
      method: 'POST',
      data: payload,
    }),
  updateSaleMiniAddress: (id, payload) =>
    request({
      url: `/api/sale-mini/addresses/${id}/`,
      method: 'PUT',
      data: payload,
    }),
  deleteSaleMiniAddress: (id) =>
    request({
      url: `/api/sale-mini/addresses/${id}/`,
      method: 'DELETE',
      data: {},
    }),
  previewSaleMiniOrder: (payload) =>
    request({
      url: '/api/sale-mini/orders/preview/',
      method: 'POST',
      data: payload,
    }),
  createSaleMiniOrder: (payload) =>
    request({
      url: '/api/sale-mini/orders/',
      method: 'POST',
      data: payload,
    }),
  saleMiniOrders: (params = {}) => request({ url: `/api/sale-mini/orders/?${query(params)}` }),
  saleMiniOrder: (id, params = {}) => request({ url: `/api/sale-mini/orders/${id}/?${query(params)}` }),
  cancelSaleMiniOrder: (id) =>
    request({
      url: `/api/sale-mini/orders/${id}/cancel/`,
      method: 'POST',
      data: {},
    }),
  prepaySaleMiniWechat: (payload) =>
    request({
      url: '/api/sale-mini/payments/wechat/prepay/',
      method: 'POST',
      data: payload,
    }),
  querySaleMiniWechat: (payload) =>
    request({
      url: '/api/sale-mini/payments/wechat/query/',
      method: 'POST',
      data: payload,
    }),
  refundSaleMiniWechat: (payload) =>
    request({
      url: '/api/sale-mini/payments/wechat/refund/',
      method: 'POST',
      data: payload,
    }),
  createSaleMiniAfterSale: (payload) =>
    request({
      url: '/api/sale-mini/after-sales/',
      method: 'POST',
      data: payload,
    }),
}
