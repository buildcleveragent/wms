import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ workbench: vi.fn() }))
vi.mock('@/utils/request', () => ({ api: { ownerWorkbench: mocks.workbench } }))
import OwnerWorkbench from '@/components/OwnerWorkbench.vue'
const payload = (count = 3) => ({ role: 'owner_salesperson', scope_label: '我创建的订单', as_of: '2026-10-20T12:00:00+08:00', notes: ['不含未设置预计发货时间的订单。'], categories: [
  { code: 'pending', label: '待审核', count, available: true }, { code: 'returned', label: '被退回', count: 0, available: true },
  { code: 'overdue', label: '超期未发货', count: 1, available: true }, { code: 'shortage', label: '库存不足', count: 1, available: true },
] })
const wrappers = []
function render(props = {}) { const w = mount(OwnerWorkbench, { props: { active: true, identity: 'sales-1', ...props } }); wrappers.push(w); return w }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
describe('首页待办提醒', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    mocks.workbench.mockReset().mockResolvedValue(payload())
    vi.stubGlobal('uni', { navigateTo: vi.fn() })
  })
  afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers(); vi.restoreAllMocks() })
  it('展示角色范围、四类数量、时间与清单入口', async () => {
    const w = render(); await flushPromises()
    expect(w.findAll('.reminder-card')).toHaveLength(4)
    expect(w.text()).toContain('我创建的订单')
    expect(w.text()).toContain('2026-10-20 12:00:00（北京时间）')
    await w.find('.overdue').trigger('click')
    expect(uni.navigateTo).toHaveBeenCalledWith({ url: '/pages/workbench/items?category=overdue' })
  })
  it('无审核权限不显示假零且不能导航', async () => {
    const data = payload(); data.categories[0] = { code: 'pending', label: '待审核', count: null, available: false }
    mocks.workbench.mockResolvedValue(data)
    const w = render(); await flushPromises()
    expect(w.find('.pending').text()).toContain('无审核权限')
    expect(w.find('.pending .reminder-count').text()).toBe('—')
    await w.find('.pending').trigger('click'); expect(uni.navigateTo).not.toHaveBeenCalled()
  })
  it('30秒轮询及手动刷新更新已处理数量', async () => {
    const w = render(); await flushPromises()
    mocks.workbench.mockResolvedValue(payload(0))
    await vi.advanceTimersByTimeAsync(30000); await flushPromises()
    expect(w.find('.pending .reminder-count').text()).toBe('0')
    await w.find('.workbench-heading button').trigger('click'); await flushPromises()
    expect(mocks.workbench).toHaveBeenCalledTimes(3)
  })
  it('页面和浏览器隐藏暂停，恢复立即刷新，卸载清理', async () => {
    const w = render(); await flushPromises()
    await w.setProps({ active: false }); await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.workbench).toHaveBeenCalledTimes(1)
    await w.setProps({ active: true }); await flushPromises()
    expect(mocks.workbench).toHaveBeenCalledTimes(2)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.workbench).toHaveBeenCalledTimes(2)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange')); await flushPromises()
    expect(mocks.workbench).toHaveBeenCalledTimes(3)
    w.unmount(); await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.workbench).toHaveBeenCalledTimes(3)
  })
  it('首次失败重试，后续失败保留数量不冒充零', async () => {
    mocks.workbench.mockRejectedValueOnce(new Error('offline'))
    const w = render(); await flushPromises()
    expect(w.findAll('.reminder-card')).toHaveLength(0)
    await w.find('.workbench-error button').trigger('click'); await flushPromises()
    expect(w.find('.pending .reminder-count').text()).toBe('3')
    mocks.workbench.mockRejectedValueOnce(new Error('offline'))
    await vi.advanceTimersByTimeAsync(30000); await flushPromises()
    expect(w.text()).toContain('更新失败，当前为上次数据')
    expect(w.find('.pending .reminder-count').text()).toBe('3')
  })
  it('请求不重叠，切换账号丢弃旧响应并排队刷新', async () => {
    const pending = deferred(); mocks.workbench.mockReturnValueOnce(pending.promise)
    const w = render()
    await w.setProps({ identity: 'manager-2' })
    expect(mocks.workbench).toHaveBeenCalledTimes(1)
    const next = { ...payload(7), scope_label: '本货主全部订单' }
    mocks.workbench.mockResolvedValue(next)
    pending.resolve(payload(99)); await flushPromises()
    expect(w.text()).not.toContain('99')
    expect(w.text()).toContain('本货主全部订单')
    expect(w.find('.pending .reminder-count').text()).toBe('7')
  })
  it.each([401, 403, 409])('权限失效 %s 清空并停止轮询', async (statusCode) => {
    const w = render(); await flushPromises()
    mocks.workbench.mockRejectedValue({ statusCode })
    await vi.advanceTimersByTimeAsync(30000); await flushPromises()
    expect(w.findAll('.reminder-card')).toHaveLength(0)
    expect(w.text()).toContain('权限已失效')
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.workbench).toHaveBeenCalledTimes(2)
  })
  it('后端接口缺失显示不可用，可手动重试', async () => {
    mocks.workbench.mockRejectedValueOnce({ statusCode: 404 })
    const w = render(); await flushPromises()
    expect(w.text()).toContain('待办暂不可用')
    await w.find('.workbench-error button').trigger('click'); await flushPromises()
    expect(w.findAll('.reminder-card')).toHaveLength(4)
  })
})
