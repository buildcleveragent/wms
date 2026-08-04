import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  onLoad: null,
  orders: vi.fn(),
}))

vi.mock('@dcloudio/uni-app', () => ({
  onLoad: (callback) => { mocks.onLoad = callback },
  onPullDownRefresh: vi.fn(),
  onReachBottom: vi.fn(),
  onShow: vi.fn(),
  onUnload: vi.fn(),
}))
vi.mock('@/utils/request', () => ({ api: { orders: mocks.orders } }))
vi.mock('@/utils/useOrderReviewActions.js', () => ({
  useOrderReviewActions: () => ({
    submitting: false,
    approveOrder: vi.fn(),
    rejectOrder: vi.fn(),
    cancelOrder: vi.fn(),
  }),
}))

import ApprovalList from '@/pages/approval/index.vue'

const row = (id, overrides = {}) => ({
  id,
  order_no: `SO-${id}`,
  customer_name: `客户-${id}`,
  submit_status: 'SUBMITTED',
  approval_status: 'OWNER_PENDING',
  can_owner_review: true,
  ...overrides,
})

describe('审批列表异步与行权限', () => {
  beforeEach(() => {
    mocks.orders.mockReset()
    vi.stubGlobal('uni', {
      navigateTo: vi.fn(),
      stopPullDownRefresh: vi.fn(),
    })
  })

  it('切换标签立即清空旧行，失败后只展示当前标签错误', async () => {
    mocks.orders
      .mockResolvedValueOnce({ results: [row(1)], next: null })
      .mockRejectedValueOnce(new Error('已通过订单加载失败'))
    const wrapper = mount(ApprovalList)
    mocks.onLoad()
    await flushPromises()
    expect(wrapper.text()).toContain('SO-1')

    await wrapper.findAll('.tab')[1].trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('SO-1')
    expect(wrapper.text()).toContain('已通过订单加载失败')
  })

  it('加载更多失败后重试同一页，不跳页', async () => {
    mocks.orders
      .mockResolvedValueOnce({ results: [row(1)], next: '/page/2' })
      .mockRejectedValueOnce(new Error('第二页失败'))
      .mockResolvedValueOnce({ results: [row(2)], next: null })
    const wrapper = mount(ApprovalList)
    mocks.onLoad()
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text() === '加载更多').trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '重试加载').trigger('click')
    await flushPromises()

    expect(mocks.orders.mock.calls.map(([params]) => params.page)).toEqual([1, 2, 2])
    expect(wrapper.text()).toContain('SO-2')
  })

  it('审核按钮依赖每行 can_owner_review 和真实状态', async () => {
    mocks.orders.mockResolvedValue({
      results: [row(1, { can_owner_review: false }), row(2, { approval_status: 'OWNER_APPROVED' })],
      next: null,
    })
    const wrapper = mount(ApprovalList)
    mocks.onLoad()
    await flushPromises()

    expect(wrapper.text()).not.toContain('审核通过')
    expect(wrapper.text()).not.toContain('取消订单')
  })
})
