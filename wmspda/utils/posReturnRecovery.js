// Each sale has its own durable slot. Finishing one return cannot erase another sale's request.
export function returnStorageScope(server, user) {
  if (!server || !user?.id || !user?.warehouse_id) return ''
  return `wmspda:pos-return:v1:${encodeURIComponent(server)}:${user.warehouse_id}:${user.id}:`
}

function validId(value) {
  return /^[1-9]\d*$/.test(String(value))
}

function storageKey(scope, saleId) {
  if (!scope || !validId(saleId)) throw new Error('无法确定原收银员、仓库或销售单。')
  return scope + saleId
}

export function readPendingReturn(storage, scope, saleId) {
  const key = storageKey(scope, saleId)
  const record = storage.getStorageSync(key)
  // H5 also returns an empty string when the underlying read fails.
  if ((record === '' || record == null) && !storage.getStorageInfoSync().keys.includes(key)) return null
  const body = record?.payload
  const linesValid = Array.isArray(body?.lines) && body.lines.length &&
    body.lines.every(line => validId(line?.sale_line_id) && Number.isFinite(Number(line.qty)) && Number(line.qty) > 0)
  const refundsValid = Array.isArray(body?.refunds) && body.refunds.every(line =>
    typeof line?.method === 'string' && line.method && typeof line.reference_no === 'string' &&
    typeof line.amount === 'string' && /^\d+\.\d{2}$/.test(line.amount))
  if (record?.version !== 1 || String(body?.sale_id) !== String(saleId) ||
      !body.idempotency_key || typeof body.idempotency_key !== 'string' || !body.idempotency_key.trim() ||
      typeof body.reason !== 'string' || !body.reason.trim() || !linesValid || !refundsValid) {
    throw new Error('待确认退货记录不完整，请保留本机数据并核对原退货单。')
  }
  return record
}

export function listPendingReturns(storage, scope) {
  if (!scope) return {}
  const keys = storage.getStorageInfoSync().keys
  if (!Array.isArray(keys)) throw new Error('无法读取待确认退货记录。')
  const found = {}
  for (const key of keys.filter(key => key.startsWith(scope))) {
    const saleId = key.slice(scope.length)
    if (!validId(saleId)) throw new Error('待确认退货的销售单编号无效。')
    try {
      const record = readPendingReturn(storage, scope, saleId)
      if (record) found[saleId] = { saleId, record }
    } catch (error) {
      found[saleId] = { saleId, error: error.message || '待确认记录读取失败。' }
    }
  }
  return found
}

export function storePendingReturn(storage, scope, payload, saleNo, lineLabels = {}) {
  if (readPendingReturn(storage, scope, payload.sale_id)) {
    throw new Error('这张销售单已有待确认退货，请继续确认原请求。')
  }
  const record = { version: 1, sale_no: saleNo, line_labels: lineLabels, payload }
  storage.setStorageSync(storageKey(scope, payload.sale_id), record)
  return record
}

export function clearPendingReturn(storage, scope, payload) {
  const record = readPendingReturn(storage, scope, payload.sale_id)
  if (record?.payload.idempotency_key === payload.idempotency_key) {
    storage.removeStorageSync(storageKey(scope, payload.sale_id))
  }
}

export function definitelyRejectedReturn(error) {
  const status = Number(error?.statusCode || error?.code)
  if (error?.sessionChanged || ![400, 422].includes(status)) return false
  const data = error.data
  // Django returns a list for business-rule errors and a field map for invalid input.
  if (Array.isArray(data)) return data.length > 0 && data.every(msg => typeof msg === 'string' && msg.trim())
  if (!data || typeof data !== 'object' || 'idempotency_key' in data) return false
  return ['sale_id', 'reason', 'lines', 'items', 'refunds', 'non_field_errors'].some(field => field in data)
}
