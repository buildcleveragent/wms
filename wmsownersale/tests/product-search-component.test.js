import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  onLoad: null,
  products: vi.fn(),
  cart: {
    customer: { id: 1, name: '客户' },
    warehouse_id: 2,
    items: [],
    totalQty: 0,
    totalAmount: 0,
    hasContextForUser: () => true,
    resetOrder: vi.fn(),
    addItem: vi.fn(),
    setQty: vi.fn(),
  },
  auth: { user: { id: 1, owner_id: 3 }, ensureAuth: vi.fn() },
}))

vi.mock('@dcloudio/uni-app', () => ({ onLoad: (callback) => { mocks.onLoad = callback } }))
vi.mock('@/utils/request', () => ({ api: { products: mocks.products } }))
vi.mock('@/utils/pricing', () => ({
  enforceMinimumPrice: () => ({ valid: true }),
  initializePriceGuard: (item) => item,
}))
vi.mock('@/utils/scan', () => ({ scanOne: vi.fn() }))
vi.mock('@/store/auth', () => ({ useAuth: () => mocks.auth }))
vi.mock('@/store/cart', () => ({ useCart: () => mocks.cart }))

import ProductSearch from '@/pages/products/search.vue'

const product = (id, name) => ({
  id,
  name,
  sku: `SKU-${id}`,
  gtin: '',
  available: 100,
  price: 10,
  base_unit_name: '件',
  selectedUnitIndex: 0,
  unitOptions: [{ key: 'base', label: '件', multiplier: 1 }],
})

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function render() {
  return mount(ProductSearch, {
    global: {
      config: {
        compilerOptions: {
          isCustomElement: (tag) => [
            'view', 'text', 'scroll-view', 'radio-group', 'radio', 'image',
          ].includes(tag),
        },
      },
    },
  })
}

describe('商品搜索分页组件', () => {
  beforeEach(() => {
    mocks.products.mockReset()
    mocks.cart.items = []
    vi.stubGlobal('uni', {
      showToast: vi.fn(),
      navigateTo: vi.fn(),
      redirectTo: vi.fn(),
    })
  })

  it('第一页有 next 时触底只发出一次第二页请求', async () => {
    const second = deferred()
    mocks.products
      .mockResolvedValueOnce({ results: [product(1, '第一页')], next: '/page/2' })
      .mockReturnValueOnce(second.promise)
    const wrapper = render()
    await mocks.onLoad()
    await flushPromises()

    const scroller = wrapper.find('scroll-view')
    await scroller.trigger('scrolltolower')
    await scroller.trigger('scrolltolower')
    expect(mocks.products).toHaveBeenCalledTimes(2)
    second.resolve({ results: [product(2, '第二页')], next: null })
    await flushPromises()
    expect(wrapper.text()).toContain('第二页')
  })

  it('没有 next 时触底不发请求', async () => {
    mocks.products.mockResolvedValue({ results: [product(1, '唯一页')], next: null })
    const wrapper = render()
    await mocks.onLoad()
    await flushPromises()
    await wrapper.find('scroll-view').trigger('scrolltolower')
    expect(mocks.products).toHaveBeenCalledOnce()
  })

  it('新搜索完成后旧响应不得追加', async () => {
    const oldSearch = deferred()
    mocks.products
      .mockReturnValueOnce(oldSearch.promise)
      .mockResolvedValueOnce({ results: [product(2, '新结果')], next: null })
    const wrapper = render()
    mocks.onLoad()
    await wrapper.find('input').setValue('new')
    await wrapper.findAll('button')[0].trigger('click')
    await flushPromises()
    oldSearch.resolve({ results: [product(1, '旧结果')], next: null })
    await flushPromises()

    expect(wrapper.text()).toContain('新结果')
    expect(wrapper.text()).not.toContain('旧结果')
  })
})
