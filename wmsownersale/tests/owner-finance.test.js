import { describe, expect, it } from 'vitest'
import { ownerAccess, buildOwnerMenu } from '@/utils/ownerAccess'
import { financePageBlocked } from '@/utils/financeAccess'

describe('货主财务只读访问', () => {
  const context = {roles:['owner_finance'], capabilities:{
    can_view_owner_inventory:true, can_export_owner_inventory:true,
    can_view_owner_operations:true, can_export_operations:true,
    can_manage_owner_inventory_alerts:true,
  }}
  it('保留全部查询和导出，不能管理预警或进入下单', () => {
    const access = ownerAccess(context)
    expect(access).toMatchObject({finance:true, manager:false, salesperson:false,
      readOnly:true, canViewStockHistory:true, canExportStockHistory:true,
      canManageInventoryAlerts:false})
    const menu = buildOwnerMenu(context)
    expect(menu.orders.map(r => r.key)).toEqual(['order_list'])
    expect(menu.administration.map(r => r.text)).toEqual(['审批记录','计费总览'])
    expect(menu.reports.map(r => r.key)).toEqual(expect.arrayContaining([
      'customer_addresses','arrival','customer_returns','inventory','stock_history',
    ]))
  })
  it.each(['pages/orders/cart','pages/orders/reorder','pages/orders/import_drop_ship',
    'pages/warehouses/select','pages/customers/select','pages/products/search',
    'pages/inventory/alert-settings'])('阻止直接打开操作页面 %s', path => {
    expect(financePageBlocked(path)).toBe(true)
  })
  it('同一页面区分新增入口和只读详情，保留密码修改', () => {
    expect(financePageBlocked('pages/inbound/index',{create:'1'})).toBe(true)
    expect(financePageBlocked('pages/inbound/index',{id:'1'})).toBe(false)
    expect(financePageBlocked('pages/returns/index',{order_id:'1'})).toBe(true)
    expect(financePageBlocked('pages/returns/index',{id:'1'})).toBe(false)
    expect(financePageBlocked('pages/approval/index')).toBe(false)
    expect(financePageBlocked('pages/me/change_password')).toBe(false)
  })
})
