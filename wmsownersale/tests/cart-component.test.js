import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  onLoad: null,
  auth: { user: { id: 1, owner_id: 2 }, ensureAuth: vi.fn() },
}))

vi.mock('@dcloudio/uni-app', () => ({
  onLoad: callback => { mocks.onLoad = callback },
}))
vi.mock('@/store/auth', () => ({ useAuth: () => mocks.auth }))
vi.mock('@/utils/request', () => ({ api: {} }))

import CartPage from '@/pages/orders/cart.vue'
import { useCart } from '@/store/cart'

function render() {
  return mount(CartPage, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: tag => ['view', 'text', 'scroll-view', 'image'].includes(tag),
        },
      },
    },
  })
}

describe('购物车页面数量与纠错', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.stubGlobal('uni', {
      showToast: vi.fn(),
      showModal: vi.fn(),
      redirectTo: vi.fn(),
      navigateTo: vi.fn(),
      switchTab: vi.fn(),
    })
  })

  function preparedCart() {
    const cart = useCart()
    cart.beginOrder({ user_id: 1, owner_id: 2, warehouse: { id: 3, name: '仓库' } })
    cart.setCustomer({ id: 4, code: 'NORMAL', name: '客户' })
    cart.addItem({
      id: 5,
      name: '商品',
      qty: 1,
      price: 2,
      available: 2,
      base_unit_name: '件',
    })
    return cart
  }

  it('零数量显示行内错误并禁用提交', async () => {
    preparedCart()
    const wrapper = render()
    mocks.onLoad()
    const quantityInput = wrapper.findAll('.cart-item .field-input')[1]
    await quantityInput.trigger('input', { detail: { value: '0' } })

    expect(wrapper.text()).toContain('基本数量不能小于 0.001')
    expect(wrapper.find('.primary-button').attributes('disabled')).toBeDefined()
  })

  it('超过库存时失焦回落到可用库存', async () => {
    const cart = preparedCart()
    const wrapper = render()
    mocks.onLoad()
    const quantityInput = wrapper.findAll('.cart-item .field-input')[1]
    await quantityInput.trigger('input', { detail: { value: '3' } })
    await quantityInput.trigger('blur')

    expect(cart.items[0].qty).toBe(2)
    expect(uni.showToast).toHaveBeenCalledWith(expect.objectContaining({
      title: '已调整为可用库存 2',
    }))
  })

  it('删除操作移除商品，继续选品使用 redirectTo', async () => {
    const cart = preparedCart()
    const wrapper = render()
    mocks.onLoad()
    await wrapper.find('.remove-button').trigger('click')
    expect(cart.items).toEqual([])

    await wrapper.find('.secondary-button').trigger('click')
    expect(uni.redirectTo).toHaveBeenCalledWith({ url: '/pages/products/search' })
  })
})
