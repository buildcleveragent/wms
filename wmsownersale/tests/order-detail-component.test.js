import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  onLoad: null,
  detail: vi.fn(),
  auth: { user: { id: 1, owner_id: 2 }, ensureAuth: vi.fn() },
  cart: { beginEdit: vi.fn() },
}))

vi.mock('@dcloudio/uni-app', () => ({
  onLoad: (callback) => { mocks.onLoad = callback },
  onShow: vi.fn(),
  onUnload: vi.fn(),
}))
vi.mock('@/utils/request', () => ({ api: { orderDetail: mocks.detail, orderEditContext: vi.fn() } }))
vi.mock('@/store/auth', () => ({ useAuth: () => mocks.auth }))
vi.mock('@/store/cart', () => ({ useCart: () => mocks.cart }))

import OrderDetail from '@/pages/orders/detail.vue'

describe('订单详情核单信息', () => {
  beforeEach(() => {
    mocks.detail.mockReset()
    vi.stubGlobal('uni', {
      redirectTo: vi.fn(),
      showToast: vi.fn(),
    })
  })

  it('加载失败显示稳定错误态且可以重试', async () => {
    mocks.detail
      .mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({ id: 7, order_no: 'SO-7', lines: [] })
    const wrapper = mount(OrderDetail)
    mocks.onLoad({ id: '7' })
    await flushPromises()

    expect(wrapper.text()).toContain('网络中断')
    await wrapper.find('.retry-btn').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('SO-7')
    expect(mocks.detail).toHaveBeenCalledTimes(2)
  })

  it('展示核单字段且商品金额优先使用后端 amount', async () => {
    mocks.detail.mockResolvedValue({
      id: 8,
      order_no: 'SO-8',
      customer_name: '客户甲',
      warehouse_name: '一号仓',
      owner_name: '货主甲',
      src_bill_no: 'PLATFORM-1',
      biz_date: '2026-08-04',
      contact: '张三',
      contact_phone: '13800000000',
      ship_to: '测试地址',
      memo: '备注内容',
      lines: [{ id: 1, product_name: '商品', base_price: '99', base_qty: '2', amount: '12.34' }],
    })
    const wrapper = mount(OrderDetail)
    mocks.onLoad({ id: '8' })
    await flushPromises()

    expect(wrapper.text()).toContain('客户甲')
    expect(wrapper.text()).toContain('一号仓')
    expect(wrapper.text()).toContain('PLATFORM-1')
    expect(wrapper.text()).toContain('张三')
    expect(wrapper.text()).toContain('测试地址')
    expect(wrapper.text()).toContain('¥ 12.34')
    expect(wrapper.text()).not.toContain('¥ 198.00')
  })
})
