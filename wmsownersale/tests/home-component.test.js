import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  downloadAuthenticatedFile: vi.fn(),
}))
const auth = reactive({ roles: [], capabilities: {}, ensureAuth: vi.fn() })

vi.mock('@/store/auth', () => ({ useAuth: () => auth }))
vi.mock('@/utils/request.js', () => ({
  downloadAuthenticatedFile: mocks.downloadAuthenticatedFile,
}))

import HomePage from '@/pages/home/index.vue'

function render() {
  return mount(HomePage, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: (tag) => ['view', 'text'].includes(tag),
        },
      },
    },
  })
}

describe('货主端首页功能卡片', () => {
  beforeEach(() => {
    auth.roles = []
    auth.capabilities = {}
    auth.ensureAuth.mockClear()
    mocks.downloadAuthenticatedFile.mockReset()
    vi.stubGlobal('uni', {
      navigateTo: vi.fn(),
      switchTab: vi.fn(),
      showToast: vi.fn(),
    })
  })

  it('业务员按权限显示带图标的功能卡片', () => {
    auth.roles = ['owner_salesperson']
    const wrapper = render()

    const cards = wrapper.findAll('.quick-card')
    expect(cards).toHaveLength(6)
    expect(wrapper.findAll('.quick-icon')).toHaveLength(cards.length)
    expect(wrapper.text()).toContain('访销下单')
    expect(wrapper.text()).toContain('实时库存')
    expect(wrapper.text()).not.toContain('订单审批')
  })

  it('管理员只显示查询、报表和管理入口', () => {
    auth.roles = ['owner_manager']
    const wrapper = render()

    expect(wrapper.text()).toContain('访销订单')
    expect(wrapper.text()).toContain('订单审批')
    expect(wrapper.text()).toContain('计费总览')
    expect(wrapper.text()).not.toContain('访销下单')
    expect(wrapper.text()).not.toContain('一件代发导入')
  })

  it('普通页面和 Tab 页面使用正确导航方式', async () => {
    auth.roles = ['owner_salesperson']
    const wrapper = render()

    const cards = wrapper.findAll('.quick-card')
    await cards.find((card) => card.text().includes('访销下单')).trigger('click')
    await cards.find((card) => card.text().includes('销售报表')).trigger('click')

    expect(uni.navigateTo).toHaveBeenCalledWith(expect.objectContaining({
      url: '/pages/warehouses/select',
    }))
    expect(uni.switchTab).toHaveBeenCalledWith(expect.objectContaining({
      url: '/pages/reports/index',
    }))
  })

  it('模板卡片继续使用认证下载', async () => {
    auth.roles = ['owner_salesperson']
    mocks.downloadAuthenticatedFile.mockResolvedValue()
    const wrapper = render()

    const templateCard = wrapper.findAll('.quick-card')
      .find((card) => card.text().includes('下载一件代发模板'))
    await templateCard.trigger('click')
    await flushPromises()

    expect(mocks.downloadAuthenticatedFile).toHaveBeenCalledWith(
      '/api/outbound/orders/import-drop-ship-template/',
      '一件代发导入模板.xlsx',
    )
  })

  it('无货主端权限时显示明确空状态', () => {
    const wrapper = render()
    expect(wrapper.findAll('.quick-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('当前账号没有货主端功能权限')
  })
})
