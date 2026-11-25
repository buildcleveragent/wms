// Finance shares query pages with managers, never purchasing/editing pages.
const writePages = new Set([
  'pages/warehouses/select', 'pages/customers/select', 'pages/products/search',
  'pages/orders/cart', 'pages/orders/reorder', 'pages/orders/import_drop_ship',
  'pages/inventory/alert-settings',
])

export function financePageBlocked(route = '', options = {}) {
  route = route.replace(/^\//, '').split('?')[0]
  return writePages.has(route) ||
    (route === 'pages/inbound/index' && options.create === '1') ||
    (route === 'pages/returns/index' && Boolean(Number(options.order_id))) ||
    (route === 'pages/customers/addresses' && options.select === '1')
}

export function financeIdentity(auth) {
  return JSON.stringify([auth.user?.id, auth.user?.owner_id, auth.roles || [], auth.capabilities || {}])
}
