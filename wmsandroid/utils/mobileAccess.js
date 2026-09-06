const PUBLIC_PATHS = new Set(['/pages/login'])
const TAB_PATHS = new Set(['/pages/index/index', '/pages/features/index', '/pages/me/index'])

const RULES = [
  { prefix: '/pages/pos/history', permissions: ['pos.view_possale'] },
  { prefix: '/pages/pos/index', permissions: ['pos.add_possale'] },
  { prefix: '/pages/outbound/assisted', capability: 'can_process_warehouse_assisted_outbound' },
  { prefix: '/pages/inbound/createwithoutorder', capability: 'can_receive_without_order' },
  { prefix: '/pages/products/search', capability: 'can_receive_without_order' },
  { prefix: '/pages/orders/cart', capability: 'can_receive_without_order' },
  { prefix: '/pages/inbound/receive_task', anyPermission: ['tasking.view_wmstask', 'tasking.claim_task_as_wh_operator'] },
  { prefix: '/pages/inbound/putaway_task', anyPermission: ['tasking.view_wmstask', 'tasking.claim_task_as_wh_operator'] },
  { prefix: '/pages/picking/', anyPermission: ['tasking.view_wmstask', 'tasking.claim_task_as_wh_operator'] },
  { prefix: '/pages/review/', anyPermission: ['tasking.view_wmstask', 'tasking.claim_task_as_wh_operator'] },
  { prefix: '/pages/inventory/company', anyPermission: ['inventory.view_inventorydetail', 'inventory.view_inventorysummary', 'tasking.claim_task_as_wh_operator'] },
  { prefix: '/pages/inventory/stocktake', anyPermission: ['tasking.view_wmstask', 'tasking.claim_task_as_wh_operator'] },
  { prefix: '/pages/replenishment/request', capability: 'can_request_replenishment' },
  { prefix: '/pages/inventory/replenish', capability: 'can_execute_replenishment' },
  { prefix: '/pages/inventory/move/request', anyCapability: ['can_request_relocation', 'can_manage_relocation'] },
  { prefix: '/pages/inventory/move', anyCapability: ['can_execute_relocation', 'can_manage_relocation_tasks'] },
]

const WAREHOUSE_PATH_PREFIXES = [
  '/pages/inbound/', '/pages/products/search', '/pages/orders/cart',
  '/pages/picking/', '/pages/review/', '/pages/inventory/',
]

function cleanPath(url = '') {
  const path = String(url || '').split('?')[0]
  return path.startsWith('/') ? path : `/${path}`
}

function hasWarehouseScope(auth) {
  const ids = auth?.user?.scopes?.warehouse_ids
  return Boolean(auth?.user?.warehouse_id || (Array.isArray(ids) && ids.length))
}

export function hasPermission(auth, permission) {
  return Array.isArray(auth?.perms) && auth.perms.includes(permission)
}

export function canAccessPath(auth, url) {
  const path = cleanPath(url)
  if (PUBLIC_PATHS.has(path)) return true
  if (!auth?.access) return false
  if (!auth.profileLoaded) return true

  const rule = RULES.find((item) => path.startsWith(item.prefix))
  if (rule?.permissions && !rule.permissions.every((permission) => hasPermission(auth, permission))) return false
  if (rule?.anyPermission && !rule.anyPermission.some((permission) => hasPermission(auth, permission))) return false
  if (rule?.capability && auth.profile?.capabilities?.[rule.capability] !== true) return false
  if (rule?.anyCapability && !rule.anyCapability.some((item) => auth.profile?.capabilities?.[item] === true)) {
    return false
  }
  if (WAREHOUSE_PATH_PREFIXES.some((prefix) => path.startsWith(prefix))) return hasWarehouseScope(auth)
  return true
}

let installed = false

export function installMobileRouteGuards(getAuth) {
  if (installed || typeof uni === 'undefined' || typeof uni.addInterceptor !== 'function') return
  installed = true
  ;['navigateTo', 'redirectTo', 'reLaunch', 'switchTab'].forEach((name) => {
    uni.addInterceptor(name, {
      invoke(args = {}) {
        const auth = getAuth?.()
        if (!args.url || !auth || canAccessPath(auth, args.url)) return true
        uni.showToast({ title: auth.access ? '没有此功能的操作权限' : '请先登录', icon: 'none' })
        return false
      },
    })
  })
}

export async function guardCurrentPage(auth) {
  if (!auth) return true
  if (auth.access && !auth.profileLoaded && !auth.profileLoading) await auth.loadProfile().catch(() => null)
  const pages = typeof getCurrentPages === 'function' ? getCurrentPages() : []
  const route = pages[pages.length - 1]?.route
  if (!route || PUBLIC_PATHS.has(cleanPath(route)) || canAccessPath(auth, route)) return true
  uni.showToast({ title: auth.access ? '没有此功能的操作权限' : '请先登录', icon: 'none' })
  if (!auth.access) uni.reLaunch({ url: '/pages/login' })
  else if (!TAB_PATHS.has(cleanPath(route))) uni.switchTab({ url: '/pages/index/index' })
  return false
}
