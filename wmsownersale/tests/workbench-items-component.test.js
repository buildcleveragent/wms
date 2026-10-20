import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ items: vi.fn(), hooks: {} }))
const auth = reactive({ user: { id: 1 }, roles: ['owner_salesperson'] })
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))
vi.mock('@/utils/request', () => ({ api: { ownerWorkbenchItems: mocks.items } }))
vi.mock('@dcloudio/uni-app', () => ({ onLoad: cb => { mocks.hooks.load = cb }, onShow: cb => { mocks.hooks.show = cb }, onHide: cb => { mocks.hooks.hide = cb }, onUnload: cb => { mocks.hooks.unload = cb }, onReachBottom: cb => { mocks.hooks.bottom = cb }, onPullDownRefresh: cb => { mocks.hooks.refresh = cb } }))
import WorkbenchItems from '@/pages/workbench/items.vue'
const row = (id = 1, action = 'view') => ({ id, order_no: `SO-${id}`, customer_name: '客户', warehouse_name: '仓库', status_label: '待拣货', etd: '2026-10-19T12:00:00+08:00', reason: '订单缺货', action, shortages: [{ product_name: '商品', quantity: '3.500', unit: '瓶', reason: '等待补货' }] })
const payload = (category = 'shortage') => ({ category, scope_label: '我创建的订单', count: 1, next_page: null, as_of: '2026-10-20T12:00:00+08:00', results: [row()] })
const wrappers = []
function render(category = 'shortage') { const w = mount(WorkbenchItems); wrappers.push(w); mocks.hooks.load({ category }); mocks.hooks.show(); return w }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
describe('待办清单', () => {
  beforeEach(() => { mocks.items.mockReset().mockResolvedValue(payload()); auth.user = { id: 1 }; vi.stubGlobal('uni', { navigateTo: vi.fn(), stopPullDownRefresh: vi.fn() }) })
  afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))
  it('显示商品缺口、原因、时间和进度入口', async () => {
    const w = render(); await flushPromises()
    expect(w.text()).toContain('缺口 3.5 瓶')
    expect(w.text()).toContain('等待补货')
    await w.find('.todo-order').trigger('click')
    expect(uni.navigateTo).toHaveBeenCalledWith({ url: '/pages/orders/detail?id=1' })
  })
  it('管理员待审核前往审核详情', async () => {
    mocks.items.mockResolvedValue({ ...payload('pending'), results: [row(1, 'review')] })
    const w = render('pending'); await flushPromises()
    await w.find('.todo-order').trigger('click')
    expect(uni.navigateTo).toHaveBeenCalledWith({ url: '/pages/approval/approvedetail?id=1' })
  })
  it('分页失败重试同一页，搜索重置页码', async () => {
    mocks.items.mockResolvedValueOnce({ ...payload(), next_page: 2, count: 2 }).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ...payload(), results: [row(2)] })
    const w = render(); await flushPromises(); mocks.hooks.bottom(); await flushPromises()
    expect(w.findAll('.todo-order')).toHaveLength(1)
    await w.find('.error button').trigger('click'); await flushPromises()
    expect(mocks.items).toHaveBeenLastCalledWith({ category: 'shortage', search: '', page: 2, page_size: 20 })
    expect(w.findAll('.todo-order')).toHaveLength(2)
    await w.find('input').setValue('SO-1'); await w.find('.search-row button').trigger('click'); await flushPromises()
    expect(mocks.items).toHaveBeenLastCalledWith({ category: 'shortage', search: 'SO-1', page: 1, page_size: 20 })
  })
  it('返回刷新后已处理订单消失', async () => {
    const w = render(); await flushPromises(); mocks.hooks.hide()
    mocks.items.mockResolvedValue({ ...payload(), count: 0, results: [] })
    mocks.hooks.show(); await flushPromises()
    expect(w.findAll('.todo-order')).toHaveLength(0)
    expect(w.text()).toContain('暂无符合条件的订单')
  })
  it('首次失败重试和刷新失败保留旧结果', async () => {
    mocks.items.mockRejectedValueOnce(new Error('offline'))
    const w = render(); await flushPromises()
    await w.find('.error button').trigger('click'); await flushPromises()
    mocks.items.mockRejectedValueOnce(new Error('offline')); mocks.hooks.refresh(); await flushPromises()
    expect(w.text()).toContain('更新失败，当前为上次数据')
    expect(w.findAll('.todo-order')).toHaveLength(1)
  })
  it('过期响应不覆盖新搜索，隐藏后也不应用结果', async () => {
    const old = deferred(); mocks.items.mockReturnValueOnce(old.promise)
    const w = render(); await w.find('input').setValue('new'); await w.find('.search-row button').trigger('click'); await flushPromises()
    old.resolve({ ...payload(), results: [row(99)] }); await flushPromises()
    expect(w.text()).not.toContain('SO-99')
    const hidden = deferred(); mocks.items.mockReturnValueOnce(hidden.promise)
    mocks.hooks.refresh(); mocks.hooks.hide(); hidden.resolve({ ...payload(), results: [row(98)] }); await flushPromises()
    expect(w.text()).not.toContain('SO-98')
  })
  it('权限失效清空数据，切换账号不保留旧结果', async () => {
    const w = render(); await flushPromises()
    mocks.items.mockRejectedValueOnce({ statusCode: 403 }); mocks.hooks.refresh(); await flushPromises()
    expect(w.findAll('.todo-order')).toHaveLength(0)
    expect(w.text()).toContain('权限已失效')
    mocks.items.mockResolvedValue({ ...payload(), results: [row(2)] })
    auth.user = { id: 2 }; await flushPromises()
    expect(w.text()).toContain('SO-2')
  })
})
