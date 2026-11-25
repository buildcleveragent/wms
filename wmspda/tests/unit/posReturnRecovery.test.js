import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BASE_URL } from '@/utils/request'
import { clearPendingReturn, returnStorageScope, storePendingReturn } from '@/utils/posReturnRecovery'
import PosPage from '@/pages/pos/index.vue'

const mocks = vi.hoisted(() => ({
  api: new Proxy({}, { get(target, key) { return target[key] ||= vi.fn().mockResolvedValue({}) } }),
}))
vi.mock('@/utils/request', async importOriginal => ({ ...(await importOriginal()), api: mocks.api }))
vi.mock('@/store/auth', () => ({ useAuth: () => auth }))
vi.mock('@dcloudio/uni-app', () => ({ onHide: vi.fn(), onShow: vi.fn() }))
vi.mock('@/utils/useBarcodeScanner', () => ({ useBarcodeScanner: () => ({ lastScan: '', quickScan: vi.fn() }) }))

const cashier = { id: 7, warehouse_id: 3 }
const auth = reactive({ user: cashier, access: 'test', ensureProfile: vi.fn() })
const scope = returnStorageScope(BASE_URL, cashier)
const sales = [41, 42].map(id => ({
  id, sale_no: 'S-' + id, total_amount: '18.00', status: 'COMPLETED', payment: { method: 'CASH' },
  lines: [{ id: id * 2, product_name: '测试件', base_unit: '件', qty: '2.000', price: '9.0000', amount: '18.00', returnable_qty: '2.000' }],
}))
const panel = '[data-testid=pos-return-pending]'
const form = '[data-testid=pos-return-form]'
const confirm = '[data-testid=pos-return-confirm]'
const retry = '[data-testid=pos-return-retry]'
let storage, page, rows

async function show() {
  page = mount(PosPage, { global: { config: { warnHandler: () => {} } } })
  await flushPromises()
}

function saleButton(saleId, text) {
  const row = page.findAll('.history-row').find(el => el.text().includes('S-' + saleId))
  return row.findAll('button').find(button => button.text() === text)
}

async function enterReturn(id = 41) {
  await saleButton(id, '退货').trigger('click')
  await flushPromises()
  await page.get(form + ' .return-qty-input').setValue('1')
  await page.get(form + ' .void-input').setValue('退一件')
  await page.get(form + ' .bill-input').setValue('原参考号')
}

async function submit() {
  await page.get(confirm).trigger('click')
  await flushPromises()
}

beforeEach(() => {
  auth.user = cashier
  auth.access = 'test'
  rows = [...sales]
  storage = new Map()
  vi.stubGlobal('uni', {
    getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
    getStorageSync: key => structuredClone(storage.get(key)),
    setStorageSync: (key, value) => storage.set(key, JSON.parse(JSON.stringify(value))),
    removeStorageSync: key => storage.delete(key),
    showToast: vi.fn(),
    showModal: vi.fn(options => options.success?.({ confirm: true })),
  })
  mocks.api.posSales.mockImplementation(async () => ({ results: rows }))
  mocks.api.posSaleDetail.mockImplementation(async id => sales.find(s => s.id === id))
  mocks.api.posReturnCreate.mockReset()
})
afterEach(() => { page?.unmount(); page = null; vi.unstubAllGlobals() })

describe('PDA partial return recovery', () => {
  it('returns only the selected gift from a paid sale without adding a zero cash refund', async () => {
    const original = sales[0]
    const gift = { ...original.lines[0], id: 90, product_name: '赠品', price: '0.0000', amount: '0.00' }
    const mixed = { ...original, lines: [gift, ...original.lines] }
    mocks.api.posSaleDetail.mockResolvedValueOnce(mixed)
    mocks.api.posReturnCreate.mockRejectedValueOnce({ statusCode: 0 })
    await show()
    await enterReturn()
    // The register tolerates sub-cent input. The refundable line total is exactly zero.
    await page.get(form + ' .payment-input').setValue('0.001')
    await submit()
    expect(mocks.api.posReturnCreate.mock.calls[0][0]).toMatchObject({
      lines: [{ sale_line_id: 90, qty: '1.000' }], refunds: [],
    })
    expect(uni.showModal).toHaveBeenCalledWith(expect.objectContaining({ content: '仅退回商品，无需退款。' }))
    expect(page.get(panel).text()).toContain('无需退款')
  })

  it('saves before sending, restores with no sale history or open shift, then allows a distinct return', async () => {
    mocks.api.posReturnCreate.mockImplementationOnce(async body => {
      expect(storage.get(scope + '41').payload).toEqual(body)
      throw { statusCode: 0 }
    })
    await show()
    await enterReturn()
    await submit()
    const first = mocks.api.posReturnCreate.mock.calls[0][0]
    expect(first).toMatchObject({ sale_id: 41, reason: '退一件', idempotency_key: expect.stringMatching(/^[\da-f-]{36}$/),
      lines: [{ sale_line_id: 82, qty: '1.000' }], refunds: [{ method: 'CASH', amount: '9.00', reference_no: '原参考号' }] })
    expect(page.get(panel).text()).toContain('9.00')
    expect(page.get(panel).text()).toMatch(/测试件\s*×\s*1 件/)
    expect(page.find(form).exists()).toBe(false)
    expect(saleButton(41, '作废').element.disabled).toBe(true)
    expect(saleButton(42, '作废').element.disabled).toBe(false)
    page.unmount()
    rows = []
    await show()
    expect(mocks.api.posReturnCreate).toHaveBeenCalledTimes(1)
    mocks.api.posReturnCreate.mockImplementationOnce(async () => { rows = sales; return { return: { id: 50, sale_id: 41 } } })
    await page.get(retry).trigger('click')
    await flushPromises()
    expect(mocks.api.posReturnCreate.mock.calls[1][0]).toEqual(first)
    expect(page.find(panel).exists()).toBe(false)
    expect(storage.has(scope + '41')).toBe(false)
    await enterReturn()
    mocks.api.posReturnCreate.mockResolvedValueOnce({ return: { id: 51, sale_id: 41 } })
    await submit()
    expect(mocks.api.posReturnCreate.mock.calls[2][0].idempotency_key).not.toBe(first.idempotency_key)
  })

  it('locks before confirmation and sends no request if persisting the snapshot fails', async () => {
    await show()
    await enterReturn()
    let answer
    uni.showModal.mockImplementationOnce(options => { answer = options.success })
    const save = uni.setStorageSync
    uni.setStorageSync = (key, value) => {
      if (key.startsWith(scope)) throw new Error('存储空间不足')
      save(key, value)
    }
    await page.get(confirm).trigger('click')
    await page.get(confirm).trigger('click')
    expect(uni.showModal).toHaveBeenCalledTimes(1)
    answer({ confirm: true })
    await flushPromises()
    expect(mocks.api.posReturnCreate).not.toHaveBeenCalled()
    expect(storage.has(scope + '41')).toBe(false)
    expect(page.find(form).exists()).toBe(true)
  })

  it('retains incomplete receipts, permission loss and idempotency conflicts; lets another sale proceed', async () => {
    await show()
    await enterReturn()
    mocks.api.posReturnCreate.mockResolvedValueOnce({ return: { id: 99, sale_id: 42 } })
    await submit()
    for (const error of [
      { statusCode: 403 }, { statusCode: 409, sessionChanged: true }, { statusCode: 500 },
      { statusCode: 400, data: {} }, { statusCode: 400, data: { detail: '请求处理状态未知' } },
      { statusCode: 400, data: { idempotency_key: ['与原退货内容不一致'] } },
    ]) {
      mocks.api.posReturnCreate.mockRejectedValueOnce(error)
      await page.get(retry).trigger('click')
      await flushPromises()
      expect(saleButton(41, '退货').element.disabled).toBe(true)
    }
    await enterReturn(42)
    mocks.api.posReturnCreate.mockRejectedValueOnce({ statusCode: 0 })
    await submit()
    expect(page.findAll(panel)).toHaveLength(2)
    mocks.api.posReturnCreate.mockResolvedValueOnce({ return: { id: 51, sale_id: 42 } })
    await page.get('[data-sale-id="42"] ' + retry).trigger('click')
    await flushPromises()
    expect(page.findAll(panel)).toHaveLength(1)
    expect(storage.has(scope + '41')).toBe(true)
    expect(storage.has(scope + '42')).toBe(false)
  })

  it('restores editing for a clearly rejected input and keeps the entered refund on screen', async () => {
    await show()
    await enterReturn()
    mocks.api.posReturnCreate.mockRejectedValueOnce({ statusCode: 400, data: { refunds: ['退款金额不匹配'] } })
    await submit()
    expect(page.find(panel).exists()).toBe(false)
    expect(page.find(form).exists()).toBe(true)
    expect(page.get(form + ' .void-input').element.value).toBe('退一件')
    expect(storage.has(scope + '41')).toBe(false)
  })

  it('preserves a pending request when a late success arrives after switching warehouse or cashier', async () => {
    await show()
    await enterReturn()
    let resolve
    mocks.api.posReturnCreate.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    await submit()
    auth.user = { ...cashier, warehouse_id: 8 }
    await flushPromises()
    expect(page.find(panel).exists()).toBe(false)
    resolve({ return: { id: 51, sale_id: 41 } })
    await flushPromises()
    expect(storage.has(scope + '41')).toBe(true)
    auth.user = { ...cashier, id: 8 }
    await flushPromises()
    expect(page.find(panel).exists()).toBe(false)
    auth.user = cashier
    await flushPromises()
    expect(page.find(panel).exists()).toBe(true)
  })

  it('keeps corrupt records and prevents a stale receipt from removing a different request', async () => {
    const body = { sale_id: 41, reason: '退一件', idempotency_key: 'original', lines: [{ sale_line_id: 82, qty: '1.000' }],
      refunds: [{ method: 'CASH', amount: '9.00', reference_no: '' }] }
    storePendingReturn(uni, scope, body, 'S-41')
    clearPendingReturn(uni, scope, { ...body, idempotency_key: 'stale' })
    expect(storage.get(scope + '41').payload).toEqual(body)
    storage.set(scope + '41', '')
    await show()
    expect(page.get(panel).text()).toContain('不完整')
    expect(page.find(retry).exists()).toBe(false)
    expect(saleButton(41, '退货').element.disabled).toBe(true)
    expect(saleButton(42, '退货').element.disabled).toBe(false)
  })
})
