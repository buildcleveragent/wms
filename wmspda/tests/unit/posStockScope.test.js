import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { checkoutStockScope, DUAL_ZONE, PICK_ONLY, posDraftKey, queryStockScope } from '@/utils/posStockScope'

const mocks = vi.hoisted(() => ({
  api: new Proxy({}, { get(target, key) { return target[key] ||= vi.fn().mockResolvedValue({}) } }),
}))
const cashier = { id: 7, username: 'fixture', warehouse_id: 3 }
const auth = reactive({ user: cashier, access: 'fixture', ensureProfile: vi.fn().mockResolvedValue({}) })
vi.mock('@/utils/request', async (importOriginal) => ({ ...(await importOriginal()), api: mocks.api }))
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))
vi.mock('@dcloudio/uni-app', () => ({ onHide: vi.fn(), onShow: vi.fn() }))
vi.mock('@/utils/useBarcodeScanner', () => ({ useBarcodeScanner: () => ({ lastScan: '', canScan: false, quickScan: vi.fn() }) }))
import PosPage from '@/pages/pos/index.vue'

const legacyKey = 'pos_sale_draft_v1'
const cashierKey = posDraftKey(cashier)
let store
let wrapper
const product = { product_id: 31, code: 'T31', name: '测试件', qty: '2', price: '3.0000', amount: '6.00',
  available_qty: 2, unit_options: [{ kind: 'base', label: '件', multiplier: 1 }], unit_labels: ['件'], unit_index: 0 }
const draft = () => ({ saved_at: Date.now(), cart_items: [{ ...product }], idempotency_key: 'original-key',
  src_bill_no: 'original-receipt', payment_method: 'CASH', amount_received: '6.00' })
async function show() {
  wrapper = mount(PosPage, { global: { config: { warnHandler: () => {} } } })
  await flushPromises()
}

beforeEach(() => {
  store = new Map([['pos_auto_print_sale_v1', false]])
  auth.user = cashier
  auth.access = 'fixture'
  vi.spyOn(console, 'error').mockImplementation(() => {})
  mocks.api.posShiftCurrent.mockResolvedValue({ shift: null })
  mocks.api.posProducts.mockResolvedValue({ count: 0, results: [] })
  mocks.api.posCheckout.mockReset()
  vi.stubGlobal('uni', {
    getStorageInfoSync: () => ({ keys: [...store.keys()] }),
    getStorageSync: key => structuredClone(store.get(key)),
    setStorageSync: (key, value) => store.set(key, JSON.parse(JSON.stringify(value))),
    removeStorageSync: key => store.delete(key), showModal: vi.fn(), showToast: vi.fn(),
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.unstubAllGlobals() })

describe('POS sale stock scope and retry', () => {
  it('sends the default for a new sale and preserves the pick-only wire format for legacy sales', () => {
    expect(queryStockScope(DUAL_ZONE)).toEqual({})
    expect(checkoutStockScope(DUAL_ZONE)).toEqual({})
    expect(queryStockScope(PICK_ONLY)).toEqual({ zone_type: 1 })
    expect(checkoutStockScope(PICK_ONLY)).toEqual({ stock_zone_type: 1 })
    expect(posDraftKey({ id: 7 })).toBe('')
    expect(posDraftKey({ ...cashier, warehouse_id: 4 })).not.toBe(cashierKey)
  })

  it.each([['3.0000', '6.00'], ['0.0000', '0.00']])('replays a pre-upgrade receipt with original price %s after closing the shift', async (price, amount) => {
    const old = draft()
    old.cart_items[0] = { ...old.cart_items[0], price, amount }
    old.amount_received = amount
    store.set(legacyKey, old)
    await show()
    expect(wrapper.text()).not.toContain('测试件')
    await wrapper.findAll('button').find(button => button.text() === '恢复旧草稿').trigger('click')
    expect(wrapper.get('[data-testid=pos-cart-product]').text()).toContain('拣货区可售')
    expect(wrapper.find('.price-input').exists()).toBe(false)
    expect(store.has(legacyKey)).toBe(false)
    mocks.api.posCheckout.mockRejectedValueOnce({ code: 0, message: '结果未收到' })
    await wrapper.get('[data-testid=pos-checkout]').trigger('click')
    await flushPromises()
    const sent = mocks.api.posCheckout.mock.calls[0][0]
    expect(sent).toMatchObject({ stock_zone_type: 1, src_bill_no: 'original-receipt', idempotency_key: 'original-key',
      items: [{ product_id: 31, qty: '2', price, amount }] })
    expect(sent.items[0]).not.toHaveProperty('is_gift')
    expect(sent.items[0]).not.toHaveProperty('onsite_price')
    expect(store.get(cashierKey).pending_checkout).toEqual(sent)
    expect(wrapper.find('[data-testid=pos-product-input]').exists()).toBe(false)
    wrapper.unmount()
    const stored = store.get(cashierKey)
    stored.saved_at -= 48 * 60 * 60 * 1000
    await show()
    mocks.api.posCheckout.mockResolvedValueOnce({ sale: { id: 8, sale_no: 'original-receipt' } })
    await wrapper.get('[data-testid=pos-checkout-retry]').trigger('click')
    await flushPromises()
    expect(mocks.api.posCheckout).toHaveBeenCalledTimes(2)
    expect(mocks.api.posCheckout.mock.calls[1][0]).toEqual(sent)
    expect(mocks.api.posProducts).not.toHaveBeenCalled()
    expect(store.has(cashierKey)).toBe(false)
  })

  it('submits a new dual-zone draft only after saving its exact request, then replays it without revalidating stock', async () => {
    store.set(cashierKey, { ...draft(), stock_scope: DUAL_ZONE })
    mocks.api.posShiftCurrent.mockResolvedValue({ shift: { id: 1, status: 'OPEN', cashier_id: 7, warehouse_id: 3 } })
    mocks.api.posProducts.mockResolvedValue({ results: [{ id: 31, code: 'T31', name: '测试件', price: '3.00',
      available_qty: 10, picking_available_qty: 1, base_unit: { id: 1, name: '件' } }] })
    mocks.api.posCheckout.mockImplementationOnce(async body => {
      expect(store.get(cashierKey).pending_checkout).toEqual(body)
      throw { statusCode: 409, message: '库存正在被占用' }
    })
    await show()
    expect(wrapper.get('[data-testid=pos-cart-product]').text()).toContain('拣货区＋存储区可售 10 件')
    await wrapper.get('[data-testid=pos-checkout]').trigger('click')
    await flushPromises()
    const body = mocks.api.posCheckout.mock.calls[0][0]
    expect(body).not.toHaveProperty('stock_zone_type')
    expect(mocks.api.posProducts.mock.calls.every(([q]) => !('zone_type' in q) && !('picking_only' in q))).toBe(true)
    wrapper.unmount()
    mocks.api.posProducts.mockClear().mockResolvedValue({ results: [] })
    mocks.api.posShiftCurrent.mockResolvedValue({ shift: null })
    await show()
    mocks.api.posCheckout.mockResolvedValueOnce({ sale: { id: 1, sale_no: 'saved' } })
    await wrapper.get('[data-testid=pos-checkout-retry]').trigger('click')
    await flushPromises()
    expect(mocks.api.posCheckout.mock.calls[1][0]).toEqual(body)
    expect(mocks.api.posProducts).not.toHaveBeenCalled()
    expect(store.has(cashierKey)).toBe(false)
  })

  it('keeps the request local when its recovery snapshot cannot be saved', async () => {
    store.set(legacyKey, draft())
    await show()
    await wrapper.findAll('button').find(button => button.text() === '恢复旧草稿').trigger('click')
    const write = uni.setStorageSync
    uni.setStorageSync = (key, value) => {
      if (value?.pending_checkout) throw new Error('存储空间不足')
      write(key, value)
    }
    await wrapper.get('[data-testid=pos-checkout]').trigger('click')
    await flushPromises()
    expect(mocks.api.posCheckout).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid=pos-checkout-retry]').exists()).toBe(false)
    expect(store.get(cashierKey).idempotency_key).toBe('original-key')
  })

  it('asks the cashier to confirm the new total when an onsite price becomes catalogued', async () => {
    const sale = { ...draft(), stock_scope: DUAL_ZONE, payment_method: 'WECHAT' }
    sale.cart_items = [{ ...product, list_price: null, price: '1.23', onsite_price: true }]
    store.set(cashierKey, sale)
    const catalog = { id: 31, code: 'T31', name: '测试件', price: null, available_qty: 10,
      base_unit: { id: 1, name: '件' } }
    mocks.api.posShiftCurrent.mockResolvedValue({ shift: { id: 1, status: 'OPEN', cashier_id: 7, warehouse_id: 3 } })
    mocks.api.posProducts.mockResolvedValue({ results: [catalog] })
    await show()
    mocks.api.posProducts.mockResolvedValue({ results: [{ ...catalog, price: '5.00' }] })
    await wrapper.get('[data-testid=pos-checkout]').trigger('click')
    await flushPromises()
    expect(mocks.api.posCheckout).not.toHaveBeenCalled()
    expect(uni.showToast).toHaveBeenCalledWith(expect.objectContaining({ title: '商品价格已更新，请重新核对应收' }))
    mocks.api.posCheckout.mockResolvedValueOnce({ sale: { id: 1, sale_no: 'new-price' } })
    await wrapper.get('[data-testid=pos-checkout]').trigger('click')
    await flushPromises()
    expect(mocks.api.posCheckout.mock.calls[0][0].items).toEqual([{ product_id: 31, qty: '2', price: '5.0000' }])
    expect(mocks.api.posCheckout.mock.calls[0][0].payment.amount_received).toBe('10.00')
  })

  it('ignores a late current-shift response after the account changes before the new profile loads', async () => {
    store.set(cashierKey, { ...draft(), stock_scope: DUAL_ZONE,
      pending_checkout: { idempotency_key: 'original-key', src_bill_no: 'private-old-receipt' } })
    let resolveFirst
    mocks.api.posShiftCurrent.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    await show()
    expect(wrapper.text()).toContain('private-old-receipt')
    auth.user = null
    auth.access = 'second-session'
    await flushPromises()
    expect(wrapper.text()).not.toContain('private-old-receipt')
    resolveFirst({ shift: { id: 99, cashier_id: 7, warehouse_id: 3, shift_no: 'old-shift', status: 'OPEN' } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('old-shift')
    expect(wrapper.find('[data-testid=pos-checkout-retry]').exists()).toBe(false)
    expect(store.get(cashierKey).pending_checkout.src_bill_no).toBe('private-old-receipt')
  })

  it('makes an uncertain checkout visible only to the same authenticated warehouse and cashier', async () => {
    store.set(cashierKey, { ...draft(), stock_scope: DUAL_ZONE, pending_checkout: { idempotency_key: 'k', src_bill_no: 'old-pending' } })
    auth.user = { ...cashier, id: 11 }
    await show()
    expect(wrapper.text()).not.toContain('old-pending')
    auth.user = cashier
    await flushPromises()
    expect(wrapper.text()).toContain('old-pending')
    auth.user = { ...cashier, warehouse_id: 9 }
    await flushPromises()
    expect(wrapper.text()).not.toContain('old-pending')
    expect(store.get(cashierKey).pending_checkout.src_bill_no).toBe('old-pending')
  })
})
