import { makeInventoryAuth, inventoryCapabilities } from './inventoryAuth'
const inventoryAuth = makeInventoryAuth()
vi.mock('@/store/auth', () => ({useAuth: () => inventoryAuth}))
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ detail: vi.fn(), transactions: vi.fn(), hooks: {} }))
vi.mock('@dcloudio/uni-app', () => ({
  onLoad: cb => { mocks.hooks.load = cb }, onShow: cb => { mocks.hooks.show = cb },
  onHide: cb => { mocks.hooks.hide = cb }, onUnload: cb => { mocks.hooks.unload = cb },
}))
vi.mock('@/utils/request', () => ({ api: { inventoryDetail: mocks.detail, inventoryTransactions: mocks.transactions } }))
import InventoryDetail from '@/pages/inventory/detail.vue'

const quantities = { onhand_qty: '12.2500', available_qty: '9.2500', allocated_qty: '1.0000', locked_qty: '1.0000', damaged_qty: '1.0000', base_unit: '瓶' }
const stock = { warehouse_id: 1, warehouse_name: '一号仓库', batch_no: 'B001', production_date: null, expiry_date: '2026-12-31', ...quantities }
const detail = () => ({ summary_id: 7, as_of: '2026-10-19T12:00:00+08:00', product: { name: '测试商品', code: 'TEST', base_unit: '瓶' }, warehouses: [{ id: 1, name: '一号仓库' }], totals: [quantities], results: [stock], next_page: null, count: 1 })
const tx = (id = 1) => ({ id, type_label: '出库（库存扣减）', qty_delta: '-2.5000', base_unit: '瓶', warehouse_name: '一号仓库', batch_no: '', posted_at: null, document: { label: '出库单 SO001', order_id: 9 }, reason: '原因未记录' })
const ledger = () => ({ summary_id: 7, as_of: '2026-10-19T12:00:00+08:00', results: [tx()], next_cursor: null })
const wrappers = []
function render() {
  const w = mount(InventoryDetail)
  wrappers.push(w)
  mocks.hooks.load({ id: '7' }); mocks.hooks.show()
  return w
}
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
async function transactions(w) { await w.findAll('.tabs button')[1].trigger('click'); await flushPromises() }

describe('库存明细与流水', () => {
  beforeEach(() => {
    inventoryAuth.capabilities=inventoryCapabilities()
    mocks.detail.mockReset().mockResolvedValue(detail())
    mocks.transactions.mockReset().mockResolvedValue(ledger())
    vi.stubGlobal('uni', { navigateTo: vi.fn() })
  })
  afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers() })

  it('显示商品、单位、分项数量及可用公式，保留小数精度', async () => {
    const w = render(); await flushPromises()
    expect(w.text()).toContain('单位：瓶')
    expect(w.text()).toContain('可用＝现有－分配－锁定－残次')
    expect(w.find('.stock-row').text()).toContain('12.25')
    expect(w.text()).toContain('2026-10-19 12:00:00（北京时间）')
    expect(mocks.transactions).not.toHaveBeenCalled()
  })

  it('按仓库、批次、效期筛选并重置，旧库存不会冒充新筛选结果', async () => {
    const w = render(); await flushPromises()
    await w.find('input').setValue('B001')
    w.findAll('picker')[0].element.dispatchEvent(new CustomEvent('change', { detail: { value: 1 } }))
    w.findAll('picker')[1].element.dispatchEvent(new CustomEvent('change', { detail: { value: 1 } }))
    await w.find('.search').trigger('click'); await flushPromises()
    expect(mocks.detail).toHaveBeenLastCalledWith(7, { warehouse_id: 1, batch: 'B001', expiry: 'expired', page_size: 50 })
    await w.findAll('.actions button')[1].trigger('click'); await flushPromises()
    expect(mocks.detail).toHaveBeenLastCalledWith(7, { page_size: 50 })
  })

  it('显示流水正负数量、缺失时间、受限单据和单位差异', async () => {
    const data = ledger()
    data.results.push({ ...tx(2), qty_delta: '1.1250', unit_mismatch: true, document: { label: '关联单据受权限限制' } })
    mocks.transactions.mockResolvedValue(data)
    const w = render(); await flushPromises(); await transactions(w)
    expect(w.text()).toContain('-2.5 瓶')
    expect(w.text()).toContain('+1.125 瓶')
    expect(w.text()).toContain('过账时间未记录')
    expect(w.text()).toContain('出库扣减不代表已发运')
    expect(w.text()).toContain('与当前商品单位不同')
    expect(w.findAll('.document-link')).toHaveLength(1)
    await w.find('.document-link').trigger('click')
    expect(uni.navigateTo).toHaveBeenCalledWith({ url: '/pages/orders/detail?id=9' })
  })

  it('首次失败重试成功，刷新失败保留旧数据', async () => {
    mocks.detail.mockRejectedValueOnce(new Error('offline'))
    const w = render(); await flushPromises()
    expect(w.text()).toContain('库存明细暂不可用，请重试')
    await w.find('.retry').trigger('click'); await flushPromises()
    expect(w.findAll('.stock-row')).toHaveLength(1)
    mocks.detail.mockRejectedValueOnce(new Error('offline'))
    await w.find('.refresh').trigger('click'); await flushPromises()
    expect(w.text()).toContain('更新失败，当前为上次数据')
    expect(w.findAll('.stock-row')).toHaveLength(1)
  })

  it('两个标签独立失败和重试', async () => {
    mocks.transactions.mockRejectedValueOnce(new Error('offline'))
    const w = render(); await flushPromises(); await transactions(w)
    expect(w.text()).toContain('请重试')
    await w.findAll('.tabs button')[0].trigger('click')
    expect(w.findAll('.stock-row')).toHaveLength(1)
    expect(w.find('.error-state').exists()).toBe(false)
  })

  it('游标加载更多不重叠，失败后沿用游标重试', async () => {
    mocks.transactions.mockResolvedValueOnce({ ...ledger(), next_cursor: 'token' }).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ...ledger(), results: [tx(2)] })
    const w = render(); await flushPromises(); await transactions(w)
    await w.find('.load-more').trigger('click'); await flushPromises()
    expect(w.findAll('.transaction-row')).toHaveLength(1)
    await w.find('.retry').trigger('click'); await flushPromises()
    expect(mocks.transactions).toHaveBeenLastCalledWith(7, { page_size: 50, cursor: 'token' })
    expect(w.findAll('.transaction-row')).toHaveLength(2)
  })

  it('过期响应不能覆盖新筛选结果', async () => {
    const pending = deferred()
    mocks.detail.mockReturnValueOnce(pending.promise)
    const w = render()
    await w.find('input').setValue('NEW')
    await w.find('.search').trigger('click'); await flushPromises()
    pending.resolve({ ...detail(), results: [{ ...stock, batch_no: 'OLD' }] }); await flushPromises()
    expect(w.find('.stock-row').text()).not.toContain('OLD')
  })

  it('隐藏及卸载忽略响应，返回刷新，不定时轮询', async () => {
    vi.useFakeTimers()
    const pending = deferred()
    mocks.detail.mockReturnValueOnce(pending.promise)
    const w = render(); mocks.hooks.hide()
    pending.resolve(detail()); await flushPromises()
    expect(w.find('.stock-row').exists()).toBe(false)
    mocks.hooks.show(); await flushPromises()
    expect(w.find('.stock-row').exists()).toBe(true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(mocks.detail).toHaveBeenCalledTimes(2)
    mocks.hooks.unload(); mocks.hooks.show(); await flushPromises()
    // Page unmount invalidates responses even if an old request completes later.
    w.unmount()
  })

  it.each([401, 403, 404, 409])('权限或接口失效 %s 清空两个标签和商品', async (statusCode) => {
    const w = render(); await flushPromises(); await transactions(w)
    mocks.transactions.mockRejectedValueOnce({ statusCode })
    await w.find('.refresh').trigger('click'); await flushPromises()
    expect(w.find('.product').exists()).toBe(false)
    expect(w.findAll('.transaction-row')).toHaveLength(0)
    await w.findAll('.tabs button')[0].trigger('click')
    expect(w.findAll('.stock-row')).toHaveLength(0)
    expect(w.text()).toContain(statusCode === 404 ? '库存明细暂不可用' : '访问权限已失效')
  })

  it('切换订单 ID 后不接受旧响应', async () => {
    const pending = deferred(); mocks.detail.mockReturnValueOnce(pending.promise)
    const w = render()
    mocks.hooks.load({ id: '8' })
    mocks.detail.mockResolvedValue({ ...detail(), summary_id: 8, product: { name: '新商品' } })
    mocks.hooks.show(); await flushPromises()
    pending.resolve(detail()); await flushPromises()
    expect(w.text()).toContain('新商品')
    expect(w.text()).not.toContain('测试商品')
  })
})
