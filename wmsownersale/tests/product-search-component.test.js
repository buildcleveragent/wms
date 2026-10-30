import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mocks = vi.hoisted(() => ({
  onLoad: null,
  products: vi.fn(),
  frequentProducts: vi.fn(),
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

vi.mock('@dcloudio/uni-app', () => ({
  onLoad: (callback) => { mocks.onLoad = callback }, onShow: vi.fn(), onHide: vi.fn(),
}))
vi.mock('@/utils/request', () => ({ api: { products: mocks.products, frequentProducts: mocks.frequentProducts } }))
vi.mock('@/utils/pricing', () => ({
  enforceMinimumPrice: () => ({ valid: true }),
  initializePriceGuard: (item) => item,
}))
vi.mock('@/utils/scan', () => ({ scanOne: vi.fn() }))
vi.mock('@/store/auth', () => ({ useAuth: () => mocks.auth }))
vi.mock('@/store/cart', () => ({ useCart: () => mocks.cart }))

import ProductSearch from '@/pages/products/search.vue'
import ProductCard from '@/components/ProductCard.vue'

const { useCart: useRealCart } = await vi.importActual('@/store/cart')

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
    mocks.frequentProducts.mockReset()
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
    expect(mocks.products).toHaveBeenCalledTimes(1)
    oldSearch.resolve({ results: [product(1, '旧结果')], next: null })
    await flushPromises()

    expect(wrapper.text()).toContain('新结果')
    expect(wrapper.text()).not.toContain('旧结果')
  })

  it('常购使用当前客户和仓库，零库存商品显示且不能加入', async () => {
    mocks.products.mockResolvedValue({ results: [], next: null })
    mocks.frequentProducts.mockResolvedValue({ results: [{
      ...product(5, '客户常买商品'), purchase_count: 4, last_order_date: '2026-10-19',
      available: '0.0000', can_add: false, issues: [],
    }], next: null })
    const wrapper = render()
    await mocks.onLoad(); await flushPromises()
    await wrapper.findAll('button').find(node => node.text() === '客户常购').trigger('click')
    await flushPromises()
    expect(mocks.frequentProducts).toHaveBeenCalledWith(1, {warehouse_id:2,page:1,search:''})
    expect(wrapper.text()).toContain('订购 4 次')
    expect(wrapper.text()).toContain('当前仓库没有可用库存')
    expect(wrapper.find('.add-button').attributes('disabled')).toBeDefined()
  })

  it('首次加载失败显示错误并可重试', async () => {
    mocks.products
      .mockRejectedValueOnce(new Error('网络不可用'))
      .mockResolvedValueOnce({ results: [product(2, '重试结果')], next: null })
    const wrapper = render()
    await mocks.onLoad()
    await flushPromises()

    expect(wrapper.text()).toContain('网络不可用')
    await wrapper.find('.retry-button').trigger('click')
    await flushPromises()
    expect(mocks.products).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('重试结果')
  })

  it('空结果显示明确提示', async () => {
    mocks.products.mockResolvedValue({ results: [], next: null })
    const wrapper = render()
    await mocks.onLoad()
    await flushPromises()
    expect(wrapper.text()).toContain('没有符合条件的商品')
  })

  it('订单按钮实时累计加入的基本数量，重复加入、小数和未计价商品均计入', async () => {
    const previousCart = mocks.cart
    setActivePinia(createPinia())
    const cart = useRealCart()
    cart.beginOrder({ user_id: 1, owner_id: 3, warehouse: { id: 2 } })
    cart.customer = { id: 1, name: '客户' }
    mocks.cart = cart
    mocks.products.mockResolvedValue({ results: [
      product(1, '计价商品'),
      { ...product(2, '未计价商品'), is_priced: false },
    ], next: null })
    const wrapper = render()
    try {
      await mocks.onLoad()
      await flushPromises()
      const button = () => wrapper.find('.cart-button').text()
      expect(button()).toContain('已加入 0（基本数量）')
      const cards = wrapper.findAllComponents(ProductCard)
      cards[0].vm.$emit('quantity-input', '0.1')
      await cards[0].find('.add-button').trigger('click')
      cards[0].vm.$emit('quantity-input', '0.2')
      await cards[0].find('.add-button').trigger('click')
      expect(button()).toContain('1 行 · 已加入 0.3（基本数量）')
      cards[1].vm.$emit('quantity-input', '43')
      await cards[1].find('.add-button').trigger('click')
      expect(button()).toContain('2 行 · 已加入 43.3（基本数量）')
      expect(button()).toContain('未计价')
      cart.remove(0)
      await flushPromises()
      expect(button()).toContain('1 行 · 已加入 43（基本数量）')
      await wrapper.find('.cart-button').trigger('click')
      expect(uni.redirectTo).toHaveBeenCalledWith({ url: '/pages/orders/cart' })
    } finally {
      wrapper.unmount()
      mocks.cart = previousCart
    }
  })
})
