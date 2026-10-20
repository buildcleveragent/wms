import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ progress: vi.fn() }))
vi.mock('@/utils/request', () => ({ api: { orderProgress: mocks.progress } }))
import OrderProgress from '@/components/OrderProgress.vue'

const payload = (id = 7, status = '待审核') => ({
  order_id: id, current_status_label: status, as_of: '2026-10-20T10:30:00+08:00',
  stages: [
    { code: 'SUBMIT', name: '订单提交', state: 'completed', completed_at: null, tasks: [] },
    { code: 'OWNER', name: '货主审核', state: 'active', tasks: [] },
  ],
  quantities: [{ product_id: 1, product_name: '瓶装商品', unit_id: 1, unit: '瓶', planned_qty: '10.000', shipped_qty: '4.000', remaining_qty: '6.000' }],
  exceptions: [{ code: 'short_pick', message: '短拣 6：库存不足', product_id: 1 }],
  history: [{ message: '请核对地址', occurred_at: null }],
})
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const wrappers = []
function render(props = {}) {
  const wrapper = mount(OrderProgress, { props: { orderId: 7, active: true, ...props } })
  wrappers.push(wrapper)
  return wrapper
}

describe('订单履约进度展示与轮询', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    mocks.progress.mockReset().mockResolvedValue(payload())
  })
  afterEach(() => {
    wrappers.splice(0).forEach(w => w.unmount())
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('显示真实节点、北京时间、单位数量和历史退回', async () => {
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain('待审核')
    expect(wrapper.text()).toContain('时间未记录')
    expect(wrapper.text()).toContain('2026-10-20 10:30:00')
    expect(wrapper.text()).toContain('单位：瓶')
    expect(wrapper.findAll('.quantity-grid text').map(el => el.text())).toEqual(['计划', '10', '已发运', '4', '未发运', '6'])
    expect(wrapper.text()).toContain('短拣 6：库存不足')
    expect(wrapper.text()).toContain('历史退回记录')
  })

  it('每30秒刷新，手动刷新重置计时且不清空旧内容', async () => {
    const wrapper = render()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(29999)
    expect(mocks.progress).toHaveBeenCalledTimes(1)
    mocks.progress.mockResolvedValue(payload(7, '拣货中'))
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(wrapper.text()).toContain('拣货中')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(mocks.progress).toHaveBeenCalledTimes(3)
  })

  it('首次失败可重试，后台失败保留上次数据', async () => {
    mocks.progress.mockRejectedValueOnce(new Error('offline'))
    const wrapper = render()
    await flushPromises()
    expect(wrapper.text()).toContain('进度暂不可用')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('待审核')
    mocks.progress.mockRejectedValue(new Error('offline'))
    await vi.advanceTimersByTimeAsync(30000)
    await flushPromises()
    expect(wrapper.text()).toContain('更新失败，当前为上次数据')
    expect(wrapper.text()).toContain('待审核')
  })

  it('页面隐藏暂停，返回立即刷新，卸载后停止', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.setProps({ active: false })
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.progress).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(mocks.progress).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.progress).toHaveBeenCalledTimes(2)
  })

  it('浏览器隐藏暂停，恢复可见立即刷新', async () => {
    render()
    await flushPromises()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.progress).toHaveBeenCalledTimes(1)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(mocks.progress).toHaveBeenCalledTimes(2)
  })

  it('慢请求不重叠，切换订单后忽略旧响应', async () => {
    const old = deferred()
    mocks.progress.mockReturnValueOnce(old.promise).mockResolvedValue(payload(8, '已发运'))
    const wrapper = render()
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.progress).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ orderId: 8 })
    expect(mocks.progress).toHaveBeenCalledTimes(1)
    old.resolve(payload(7, '旧订单状态'))
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧订单状态')
    expect(wrapper.text()).toContain('已发运')
    expect(mocks.progress).toHaveBeenLastCalledWith(8)
  })

  it('隐藏期间到达的响应不更新页面', async () => {
    const response = deferred()
    mocks.progress.mockReturnValueOnce(response.promise)
    const wrapper = render()
    await wrapper.setProps({ active: false })
    response.resolve(payload())
    await flushPromises()
    expect(wrapper.text()).not.toContain('待审核')
  })

  it.each([401, 403, 404, 409])('权限或资源失效 %s 清除旧数据并停止轮询', async (statusCode) => {
    const wrapper = render()
    await flushPromises()
    mocks.progress.mockRejectedValue({ statusCode })
    await vi.advanceTimersByTimeAsync(30000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('瓶装商品')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.progress).toHaveBeenCalledTimes(2)
  })

  it('无发运记录显示未记录，不显示零发运', async () => {
    const data = payload()
    data.quantities[0].shipped_qty = null
    data.quantities[0].remaining_qty = null
    mocks.progress.mockResolvedValue(data)
    const wrapper = render()
    await flushPromises()
    expect(wrapper.findAll('.quantity-grid text').map(el => el.text())).toEqual(['计划', '10', '已发运', '未记录', '未发运', '未记录'])
  })
})
