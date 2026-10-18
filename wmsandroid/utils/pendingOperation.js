import { BASE_URL, getSessionEpoch } from '@/utils/request'

// Business requests belong to a verified identity, never an anonymous draft.
export function businessScope(auth) {
  const user = auth?.user
  const warehouse = user?.warehouse_id || user?.scopes?.warehouse_ids?.[0]
  if (!auth?.profileLoaded || !user?.id || !warehouse) return ''
  return `${encodeURIComponent(BASE_URL)}:${user.id}:${warehouse}`
}

export function captureSession(auth) {
  return { scope: businessScope(auth), epoch: getSessionEpoch() }
}

export function isCurrentSession(auth, captured) {
  return Boolean(captured?.scope) && captured.scope === businessScope(auth) && captured.epoch === getSessionEpoch()
}

export function operationKey(auth, kind, objectId = '') {
  const scope = businessScope(auth)
  return scope ? `wmsandroid:pending:${kind}:${scope}:${objectId}` : ''
}

export function readPending(key) {
  if (!key) return null
  const value = uni.getStorageSync(key)
  if (!value) return null
  if (value.version !== 1 || !value.payload) throw new Error('待确认记录无法读取，请保留本机数据并联系管理员')
  return JSON.parse(JSON.stringify(value))
}

export function writePending(key, payload, display = {}) {
  if (!key) throw new Error('请先登录并加载仓库资料')
  const value = { version: 1, created_at: Date.now(), payload: JSON.parse(JSON.stringify(payload)), display: JSON.parse(JSON.stringify(display)) }
  // Storage failure must stop the request: otherwise a restart loses its identity.
  try { uni.setStorageSync(key, value) }
  catch { throw new Error('无法保存待确认记录，请检查手机存储后重试') }
  return value
}

export function clearPending(key) { if (key) uni.removeStorageSync(key) }

export function definitelyRejected(error) {
  // Permission loss / expiry cannot establish whether an earlier attempt committed.
  return !error?.sessionChanged && Number(error?.statusCode) === 400
}
