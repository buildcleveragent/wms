import { describe, expect, it } from 'vitest'

import { buildOwnerMenu } from '@/utils/ownerAccess'

const keys = (items) => items.map((item) => item.key)

describe('货主端共享权限矩阵', () => {
  it('纯业务员显示开单、导入、模板和查询，不显示管理功能', () => {
    const menu = buildOwnerMenu({ roles: ['owner_salesperson'] })
    expect(keys(menu.orders)).toEqual([
      'order_create',
      'order_list',
      'drop_ship_import',
      'drop_ship_template',
    ])
    expect(menu.administration).toEqual([])
  })

  it('纯管理员显示查询、审核和账单，不显示开单或导入', () => {
    const menu = buildOwnerMenu({ roles: ['owner_manager'] })
    expect(keys(menu.orders)).toEqual(['order_list'])
    expect(keys(menu.administration)).toEqual(['approval', 'billing'])
  })

  it('无货主角色时不暴露任何货主功能', () => {
    const menu = buildOwnerMenu({
      roles: ['warehouse_manager'],
      capabilities: { can_view_owner_operations: true },
    })
    expect([...menu.orders, ...menu.reports, ...menu.administration]).toEqual([])
  })

  it('履约报表同时要求货主角色和后端能力', () => {
    const enabled = buildOwnerMenu({
      roles: ['owner_manager'],
      capabilities: { can_view_owner_operations: true },
    })
    const disabled = buildOwnerMenu({ roles: ['owner_manager'] })
    expect(keys(enabled.reports)).toContain('operations')
    expect(keys(disabled.reports)).not.toContain('operations')
  })
})
