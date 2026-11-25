import { reactive } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = reactive({ roles: [], capabilities: {}, ensureAuth: vi.fn() })
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))

import Reports from '@/pages/reports/index.vue'

describe('报表入口能力控制', () => {
  beforeEach(() => {
    auth.roles = []
    auth.capabilities = {}
    vi.stubGlobal('uni', { navigateTo: vi.fn() })
  })

  it.each([
    ['owner_finance', true, true],
    ['owner_manager', true, true],
    ['owner_salesperson', true, false],
    ['owner_manager', false, false],
  ])('完整档案入口按角色和导出权限显示 %s %s', (role, granted, visible) => {
    auth.roles = [role]
    auth.capabilities = { can_export_owner_archive: granted }
    const wrapper = mount(Reports)
    expect(wrapper.text().includes('导出完整 Excel')).toBe(visible)
  })

  it('没有履约能力时隐藏履约入口，管理员仍可查看财务', () => {
    auth.roles = ['owner_manager']
    const wrapper = mount(Reports)
    expect(wrapper.text()).not.toContain('出入库记录')
    expect(wrapper.text()).toContain('实时库存')
    expect(wrapper.text()).toContain('计费总览')
  })

  it('有履约能力时展示入口，普通业务员不展示财务', () => {
    auth.roles = ['owner_salesperson']
    auth.capabilities = { can_view_owner_operations: true, can_view_owner_inventory: true }
    const wrapper = mount(Reports)
    expect(wrapper.text()).toContain('出入库记录')
    expect(wrapper.text()).toContain('实时库存')
    expect(wrapper.text()).not.toContain('计费总览')
  })
})
