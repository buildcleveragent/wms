import { getBaseUrl, getToken } from '../utils/request'
import { orderService } from './order'

function storageKey() {
  const user = uni.getStorageSync('sales_user')
  return user?.id && getToken() ? `sale_mini_checkout:${getBaseUrl()}:${user.id}` : null
}

export function pendingCheckout() {
  const key = storageKey()
  const intent = key && uni.getStorageSync(key)
  return intent ? { ...intent, scope: key } : null
}

function requestKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  // Some mini-program runtimes lack Web Crypto. The server scopes keys by account.
  return [Date.now(), Math.random().toString(36).slice(2), Math.random().toString(36).slice(2)].join('-')
}

function saveIntent(key, intent) {
  uni.setStorageSync(key, intent)
  if (uni.getStorageSync(key)?.requestKey !== intent.requestKey) {
    throw new Error('无法保存本次提交编号，请检查设备存储后重试。')
  }
}

export function checkoutIsCurrent(receipt) {
  return Boolean(receipt && receipt.scope === storageKey()
    && uni.getStorageSync(receipt.scope)?.requestKey === receipt.requestKey)
}

export function finishCheckout(receipt) {
  if (checkoutIsCurrent(receipt)) uni.removeStorageSync(receipt.scope)
}

export async function submitCheckout(prepare) {
  const key = storageKey()
  if (!key) throw new Error('请登录商城账号后继续结算。')
  let intent = pendingCheckout()
  const recovering = Boolean(intent)
  if (!intent) {
    const payload = await prepare()
    if (key !== storageKey()) throw new Error('登录账号已变化，请重新结算。')
    // A second active page can finish preparing while the first persists its request.
    intent = pendingCheckout()
    if (intent) throw new Error('有一笔订单待确认，请先查看上次提交。')
    intent = { requestKey: requestKey(), payload: JSON.parse(JSON.stringify(payload)) }
    saveIntent(key, intent)
  }
  let order
  if (intent.orderId) {
    order = await orderService.detail(intent.orderId)
  } else {
    try {
      order = await orderService.create(intent.payload, intent.requestKey)
    } catch (error) {
      if (error.statusCode === 400 || error.statusCode === 422) {
        finishCheckout({ scope: key, requestKey: intent.requestKey })
      }
      throw error
    }
    if (!order?.id) throw new Error('订单回执不完整，请确认上次提交。')
    if (!checkoutIsCurrent({ scope: key, requestKey: intent.requestKey })) {
      throw new Error('当前结算已变化，请在我的订单中查看原订单。')
    }
    // Release contact information once the receipt is known. Keep the ID until navigation succeeds.
    saveIntent(key, { requestKey: intent.requestKey, orderId: order.id })
  }
  const receipt = { order, requestKey: intent.requestKey, scope: key, recovering }
  if (!checkoutIsCurrent(receipt)) throw new Error('请切回原账号确认上次提交。')
  return receipt
}
