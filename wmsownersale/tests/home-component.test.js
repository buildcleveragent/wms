import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  downloadAuthenticatedFile: vi.fn(),
  onShow: vi.fn(),
  ownerWorkbench: vi.fn(),
}))
const auth = reactive({ roles: [], capabilities: {}, bootstrap: vi.fn(), logout: vi.fn() })

vi.mock('@dcloudio/uni-app', () => ({ onShow: mocks.onShow, onHide: vi.fn(), onUnload: vi.fn() }))
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))
vi.mock('@/utils/request.js', () => ({
  downloadAuthenticatedFile: mocks.downloadAuthenticatedFile,
  api: { ownerWorkbench: mocks.ownerWorkbench },
}))

import HomePage from '@/pages/home/index.vue'
const wrappers = []
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))

async function render() {
  const wrapper = mount(HomePage, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: (tag) => ['view', 'text'].includes(tag),
        },
      },
    },
  })
  wrappers.push(wrapper)
  mocks.onShow.mock.calls.at(-1)[0]()
  await flushPromises()
  return wrapper
}

describe('货主端首页功能卡片', () => {
  beforeEach(() => {
    auth.roles = []
    auth.capabilities = {can_view_owner_inventory: true, can_export_owner_inventory: true}
    auth.bootstrap.mockReset().mockResolvedValue({ status: 'authenticated' })
    auth.logout.mockReset().mockResolvedValue()
    mocks.onShow.mockReset()
    mocks.downloadAuthenticatedFile.mockReset()
    mocks.ownerWorkbench.mockReset().mockResolvedValue({ scope_label: '我创建的订单', categories: [{ code: 'pending', label: '待审核', count: 2, available: true }], notes: [] })
    vi.stubGlobal('uni', {
      navigateTo: vi.fn(),
      switchTab: vi.fn(),
      showToast: vi.fn(),
      reLaunch: vi.fn(),
    })
  })

  it('业务员按权限显示带图标的功能卡片', async () => {
    auth.roles = ['owner_salesperson']
    const wrapper = await render()

    const cards = wrapper.findAll('.quick-card')
    expect(cards).toHaveLength(10)
    expect(wrapper.text()).toContain('到货预报／入库申请')
    expect(wrapper.findAll('.quick-icon')).toHaveLength(cards.length)
    expect(wrapper.text()).toContain('访销下单')
    expect(wrapper.text()).toContain('实时库存')
    expect(wrapper.text()).toContain('库存预警')
    expect(wrapper.text()).toContain('客户地址簿')
    expect(wrapper.text()).toContain('退货与异常')
    expect(wrapper.text()).not.toContain('订单审批')
    expect(wrapper.text()).toContain('待办与异常')
    expect(wrapper.findAll('.reminder-card')).toHaveLength(1)
  })

  it('管理员只显示查询、报表和管理入口', async () => {
    auth.roles = ['owner_manager']
    const wrapper = await render()

    expect(wrapper.text()).toContain('访销订单')
    expect(wrapper.text()).toContain('订单审批')
    expect(wrapper.text()).toContain('计费总览')
    expect(wrapper.text()).not.toContain('访销下单')
    expect(wrapper.text()).not.toContain('一件代发导入')
  })

  it('普通页面和 Tab 页面使用正确导航方式', async () => {
    auth.roles = ['owner_salesperson']
    const wrapper = await render()

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
    const wrapper = await render()

    const templateCard = wrapper.findAll('.quick-card')
      .find((card) => card.text().includes('下载一件代发模板'))
    await templateCard.trigger('click')
    await flushPromises()

    expect(mocks.downloadAuthenticatedFile).toHaveBeenCalledWith(
      '/api/outbound/orders/import-drop-ship-template/',
      '一件代发导入模板.xlsx',
    )
  })

  it('已登录但无货主端权限时允许切换账号', async () => {
    const wrapper = await render()
    expect(wrapper.findAll('.quick-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('当前账号没有货主端功能权限')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(auth.logout).toHaveBeenCalledOnce()
    expect(uni.reLaunch).toHaveBeenCalledWith({ url: '/pages/login' })
  })

  it('直接打开首页时先验证会话，不提前显示无权限', async () => {
    let resolveSession
    auth.bootstrap.mockImplementation(() => new Promise(resolve => { resolveSession = resolve }))
    const wrapper = await render()
    expect(wrapper.text()).toContain('正在验证登录状态')
    expect(wrapper.text()).not.toContain('当前账号没有货主端功能权限')
    auth.roles = ['owner_salesperson']
    resolveSession({ status: 'authenticated' })
    await flushPromises()
    expect(wrapper.text()).toContain('访销下单')
    expect(uni.reLaunch).not.toHaveBeenCalled()
  })

  it('未登录或会话失效时跳转登录页', async () => {
    auth.bootstrap.mockResolvedValue({ status: 'anonymous' })
    const wrapper = await render()
    expect(uni.reLaunch).toHaveBeenCalledWith({ url: '/pages/login' })
    expect(wrapper.text()).not.toContain('当前账号没有货主端功能权限')
    expect(wrapper.findAll('.quick-card')).toHaveLength(0)
  })

  it('断网时提供重试，恢复后显示货主菜单', async () => {
    auth.roles = ['owner_salesperson']
    auth.bootstrap.mockResolvedValueOnce({ status: 'offline' })
    const wrapper = await render()
    expect(wrapper.text()).toContain('登录信息已保留')
    expect(wrapper.findAll('.quick-card')).toHaveLength(0)
    expect(auth.logout).not.toHaveBeenCalled()
    expect(uni.reLaunch).not.toHaveBeenCalled()
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(auth.bootstrap).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('访销下单')
  })

  it('再次显示首页时重新验证会话', async () => {
    auth.roles = ['owner_salesperson']
    const wrapper = await render()
    auth.bootstrap.mockResolvedValueOnce({ status: 'anonymous' })
    await mocks.onShow.mock.calls[0][0]()
    await flushPromises()
    expect(auth.bootstrap).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.quick-card')).toHaveLength(0)
    expect(uni.reLaunch).toHaveBeenCalledWith({ url: '/pages/login' })
  })
})
