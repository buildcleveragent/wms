const prefix = 'pos_repayment_pending_v1:'

export function getPendingRepayment(scope, storage = uni) {
  return storage.getStorageSync(prefix + scope) || null
}

export async function recordRepayment({ scope, payload, send, makeKey, storage = uni }) {
  if (!scope) throw new Error('无法确认收银员及仓库，请重新加载班次。')
  const key = prefix + scope
  let pending = getPendingRepayment(scope, storage)
  if (pending && JSON.stringify(pending.payload) !== JSON.stringify(payload)) {
    throw new Error('尚有结果待确认的还款，请先恢复原客户及金额并重试。')
  }
  if (!pending) {
    pending = { payload, idempotency_key: makeKey() }
    // Persist before sending: failure here must not create an unrepeatable payment.
    storage.setStorageSync(key, pending)
  }
  try {
    const result = await send({ ...pending.payload, idempotency_key: pending.idempotency_key })
    storage.removeStorageSync(key)
    return result
  } catch (error) {
    const status = Number(error?.statusCode || error?.status || 0)
    if (!error?.sessionChanged && [400, 403, 404, 409, 422].includes(status)) {
      storage.removeStorageSync(key)
    }
    throw error
  }
}
