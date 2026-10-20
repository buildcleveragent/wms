export function ownerAccess({ roles = [], capabilities = {} } = {}) {
  const roleSet = new Set(Array.isArray(roles) ? roles : [])
  const salesperson = roleSet.has('owner_salesperson')
  const manager = roleSet.has('owner_manager')
  const ownerRole = salesperson || manager
  return {
    salesperson,
    manager,
    ownerRole,
    canViewInventory: ownerRole && capabilities?.can_view_owner_inventory === true,
    canExportInventory: ownerRole && capabilities?.can_export_owner_inventory === true,
    canManageInventoryAlerts: manager && capabilities?.can_manage_owner_inventory_alerts === true,
    canViewOperations: ownerRole && Boolean(capabilities?.can_view_owner_operations),
  }
}

export function buildOwnerMenu(context = {}) {
  const access = ownerAccess(context)
  const orders = []
  const reports = []
  const administration = []

  if (access.salesperson) {
    orders.push(
      { key: 'order_create', text: '访销下单', path: '/pages/warehouses/select', emoji: '📝', color: 'blue' },
      { key: 'drop_ship_import', text: '一件代发导入', path: '/pages/orders/import_drop_ship', emoji: '📥', color: 'orange' },
      { key: 'drop_ship_template', text: '下载一件代发模板', action: 'download_template', emoji: '⬇️', color: 'orange' },
    )
  }
  if (access.ownerRole) {
    orders.splice(access.salesperson ? 1 : 0, 0,
      { key: 'order_list', text: '访销订单', path: '/pages/orders/index', emoji: '📄', color: 'blue' },
    )
    reports.push(
      { key: 'customer_addresses', text: '客户地址簿', path: '/pages/customers/addresses', emoji: '📍', color: 'blue' },
      { key: 'arrival', text: '到货预报／入库申请', path: '/pages/inbound/index', emoji: '📥', color: 'blue' },
      { key: 'customer_returns', text: '退货与异常', path: '/pages/returns/index', emoji: '↩️', color: 'orange' },
      { key: 'sales_reports', text: '销售报表', path: '/pages/reports/index', navigation: 'tab', emoji: '🗂️', color: 'blue' },
    )
  }
  if (access.canViewInventory) {
    reports.push(
      { key: 'inventory', text: '实时库存', path: '/pages/inventory/index', emoji: '📦', color: 'green' },
      { key: 'inventory_alerts', text: '库存预警', path: '/pages/inventory/alerts', emoji: '🔔', color: 'orange' },
    )
  }
  if (access.canViewOperations) {
    reports.splice(1, 0,
      { key: 'operations', text: '入出库履约', path: '/pages/reports/operations', emoji: '📈', color: 'green' },
    )
  }
  if (access.manager) {
    administration.push(
      { key: 'approval', text: '订单审批', path: '/pages/approval/index', emoji: '✅', color: 'orange' },
      { key: 'billing', text: '计费总览', path: '/pages/billing/overview', emoji: '💳', color: 'green' },
    )
  }
  return { orders, reports, administration }
}
