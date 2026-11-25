// Drafts saved before dual-zone sales have no stock_scope field.
export const DUAL_ZONE = 'pick_storage'
export const PICK_ONLY = 'pick'

export function queryStockScope(scope) {
  return scope === PICK_ONLY ? { zone_type: 1 } : {}
}

export function checkoutStockScope(scope) {
  return scope === PICK_ONLY ? { stock_zone_type: 1 } : {}
}

export function posDraftKey(user) {
  if (!user?.warehouse_id || !user?.id) return ''
  return `pos_sale_draft_v2:${user.warehouse_id}:${user.id}`
}

export function checkoutDefinitelyRejected(error) {
  const status = Number(error?.statusCode || error?.code)
  return [400, 422].includes(status) && !error?.data?.idempotency_key
}
