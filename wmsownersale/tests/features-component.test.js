import { reactive } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = reactive({ roles: [], capabilities: {}, ensureAuth: vi.fn() })
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))
vi.mock('@/utils/request.js', () => ({ downloadAuthenticatedFile: vi.fn() }))

import Features from '@/pages/features/index.vue'

function render() {
  return mount(Features, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: (tag) => ['view', 'text'].includes(tag),
        },
      },
    },
  })
}

describe('功能页权限组件', () => {
  beforeEach(() => {
    auth.roles = []
    auth.capabilities = {}
    auth.ensureAuth.mockClear()
    vi.stubGlobal('uni', { navigateTo: vi.fn(), switchTab: vi.fn(), showToast: vi.fn() })
  })

  it('业务员与管理员渲染各自菜单', async () => {
    auth.roles = ['owner_salesperson']
    const salesperson = render()
    expect(salesperson.text()).toContain('访销下单')
    expect(salesperson.text()).toContain('下载一件代发模板')
    expect(salesperson.text()).not.toContain('订单审批')
    salesperson.unmount()

    auth.roles = ['owner_manager']
    const manager = render()
    expect(manager.text()).toContain('订单审批')
    expect(manager.text()).toContain('计费总览')
    expect(manager.text()).not.toContain('访销下单')
  })

  it('无货主角色不渲染功能磁贴', () => {
    const wrapper = render()
    expect(wrapper.findAll('.tile')).toHaveLength(0)
  })
})
