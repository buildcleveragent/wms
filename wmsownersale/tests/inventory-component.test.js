import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  inventorySummary: vi.fn(),
  onReachBottom: null,
  onPullDownRefresh: null,
  onUnload: null,
}))

vi.mock('@dcloudio/uni-app', () => ({
  onReachBottom: (callback) => { mocks.onReachBottom = callback },
  onPullDownRefresh: (callback) => { mocks.onPullDownRefresh = callback },
  onUnload: (callback) => { mocks.onUnload = callback },
}))
vi.mock('@/utils/request', () => ({
  api: { inventorySummary: mocks.inventorySummary },
}))

import InventoryPage from '@/pages/inventory/index.vue'

function inventory(id, name = `商品-${id}`) {
  return {
    id,
    product_name: name,
    product_code: `CODE-${id}`,
    product_sku: `SKU-${id}`,
    product_spec: '',
    base_unit: 'PCS',
    onhand_qty: '10.0000',
    available_qty: '10.0000',
    allocated_qty: '0.0000',
    locked_qty: '0.0000',
    damaged_qty: '0.0000',
  }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function render() {
  return mount(InventoryPage, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: (tag) => ['view', 'text', 'scroll-view'].includes(tag),
        },
      },
    },
  })
}

describe('实时库存分页组件', () => {
  beforeEach(() => {
    mocks.inventorySummary.mockReset()
    vi.stubGlobal('uni', { stopPullDownRefresh: vi.fn() })
  })

  it('第一页加载 50 条并通过触底完整合并第二页', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => inventory(index + 1))
    mocks.inventorySummary
      .mockResolvedValueOnce({ count: 52, next: '/page/2', results: firstPage })
      .mockResolvedValueOnce({ count: 52, next: null, results: [inventory(51), inventory(52)] })

    const wrapper = render()
    await flushPromises()
    expect(mocks.inventorySummary).toHaveBeenNthCalledWith(1, {
      search: '', page: 1, page_size: 50,
    })

    await mocks.onReachBottom()
    await flushPromises()

    expect(mocks.inventorySummary).toHaveBeenNthCalledWith(2, {
      search: '', page: 2, page_size: 50,
    })
    expect(wrapper.findAll('.table-row')).toHaveLength(52)
    expect(wrapper.text()).toContain('已加载 52 / 总计 52')
    expect(wrapper.text()).toContain('已加载全部库存')
  })

  it('连续触底只发出一个加载更多请求', async () => {
    const secondPage = deferred()
    mocks.inventorySummary
      .mockResolvedValueOnce({ count: 51, next: '/page/2', results: [inventory(1)] })
      .mockReturnValueOnce(secondPage.promise)

    render()
    await flushPromises()
    mocks.onReachBottom()
    mocks.onReachBottom()
    expect(mocks.inventorySummary).toHaveBeenCalledTimes(2)

    secondPage.resolve({ count: 51, next: null, results: [inventory(2)] })
    await flushPromises()
  })

  it('第二页失败时保留第一页并用同一页码重试', async () => {
    mocks.inventorySummary
      .mockResolvedValueOnce({ count: 2, next: '/page/2', results: [inventory(1)] })
      .mockRejectedValueOnce(new Error('第二页失败'))
      .mockResolvedValueOnce({ count: 2, next: null, results: [inventory(2)] })

    const wrapper = render()
    await flushPromises()
    await mocks.onReachBottom()
    await flushPromises()

    expect(wrapper.findAll('.table-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('重试加载')
    await wrapper.find('.load-more-button').trigger('click')
    await flushPromises()

    expect(mocks.inventorySummary).toHaveBeenNthCalledWith(3, {
      search: '', page: 2, page_size: 50,
    })
    expect(wrapper.findAll('.table-row')).toHaveLength(2)
  })

  it('跨页按库存记录 ID 去重', async () => {
    mocks.inventorySummary
      .mockResolvedValueOnce({ count: 2, next: '/page/2', results: [inventory(1)] })
      .mockResolvedValueOnce({ count: 2, next: null, results: [inventory(1), inventory(2)] })

    const wrapper = render()
    await flushPromises()
    await mocks.onReachBottom()
    await flushPromises()

    expect(wrapper.findAll('.table-row')).toHaveLength(2)
    expect(wrapper.text()).toContain('已加载 2 / 总计 2')
  })

  it('没有 next 时不再请求并显示全部完成', async () => {
    mocks.inventorySummary.mockResolvedValueOnce({ count: 1, next: null, results: [inventory(1)] })
    const wrapper = render()
    await flushPromises()

    await mocks.onReachBottom()
    expect(mocks.inventorySummary).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('已加载全部库存')
  })

  it('新搜索完成后丢弃旧搜索响应', async () => {
    const oldSearch = deferred()
    mocks.inventorySummary
      .mockReturnValueOnce(oldSearch.promise)
      .mockResolvedValueOnce({ count: 1, next: null, results: [inventory(2, '新结果')] })

    const wrapper = render()
    await wrapper.find('input').setValue('new')
    await wrapper.find('.search-btn').trigger('click')
    await flushPromises()
    oldSearch.resolve({ count: 1, next: null, results: [inventory(1, '旧结果')] })
    await flushPromises()

    expect(wrapper.text()).toContain('新结果')
    expect(wrapper.text()).not.toContain('旧结果')
  })

  it('下拉刷新回到第一页并停止刷新动画', async () => {
    mocks.inventorySummary
      .mockResolvedValueOnce({ count: 2, next: '/page/2', results: [inventory(1)] })
      .mockResolvedValueOnce({ count: 1, next: null, results: [inventory(3, '刷新结果')] })

    const wrapper = render()
    await flushPromises()
    await mocks.onPullDownRefresh()
    await flushPromises()

    expect(mocks.inventorySummary).toHaveBeenNthCalledWith(2, {
      search: '', page: 1, page_size: 50,
    })
    expect(wrapper.text()).toContain('刷新结果')
    expect(wrapper.text()).not.toContain('商品-1')
    expect(uni.stopPullDownRefresh).toHaveBeenCalledOnce()
  })
})
