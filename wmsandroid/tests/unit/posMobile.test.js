import { describe, expect, it } from 'vitest'
import { addProductToCart, buildCheckoutSnapshot, cartLineKey, createCartItem, draftStorageKey, finalizeCheckoutSnapshot, lineBaseQty, pendingStorageKey, unitIndexForBarcode, validateCart } from '@/utils/posMobile'

const product = {
  id: 7, owner_id: 3, owner_name: '货主甲', code: 'RICE', name: '测试香米', price: '2.00', available_qty: '200',
  matched_barcode: { package_id: 9, label: '箱', multiplier: '20', code_type: 'PACKAGE' },
  base_unit: { name: '袋' },
  unit_options: [
    { kind: 'base', package_id: null, label: '袋', multiplier: '1', barcode: 'BASE-7' },
    { kind: 'package', package_id: 9, label: '箱', multiplier: '20', barcode: 'CASE-7' },
  ],
}

describe('mobile POS cart and checkout', () => {
  it('keeps same-name products separate and displays their brand models', () => {
    const first = addProductToCart([], { ...product, name: '汽水', display_name: '汽水 · 大窑·橙诺橙味' })
    const second = addProductToCart(first.items, { ...product, id: 8, name: '汽水', display_name: '汽水 · 大窑·嘉宾原味' })
    expect(second.items).toHaveLength(2)
    expect(second.items.map(item => item.name)).toEqual(['汽水 · 大窑·橙诺橙味', '汽水 · 大窑·嘉宾原味'])
  })
  it('matches a package barcode and keeps product+unit as the line identity', () => {
    expect(unitIndexForBarcode(product, ' CASE-7 ')).toBe(1)
    const result = addProductToCart([], product, 'CASE-7')
    expect(result.error).toBeUndefined()
    expect(lineBaseQty(result.item)).toBe(20)
    expect(cartLineKey(result.item)).toBe('3:7:9:20')
    expect(result.item.owner_name).toBe('货主甲')
  })

  it('increments only the same unit and blocks stock overflow', () => {
    const first = addProductToCart([], { ...product, available_qty: 20 }, 'CASE-7').items
    expect(addProductToCart(first, { ...product, available_qty: 20 }, 'CASE-7').error).toMatch(/库存/)
    const base = addProductToCart(first, { ...product, available_qty: 20 })
    expect(base.error).toContain('各包装合计超过拣货区＋存储区可售库存')
    expect(base.items).toHaveLength(1)
  })

  it('explains the saleable stock scope when an item has no available quantity', () => {
    expect(addProductToCart([], { ...product, available_qty: '0' }).error).toContain('拣货区＋存储区可售库存不足')
  })

  it('leaves stock scope to the server for a new immediate-payment checkout', () => {
    const items = addProductToCart([], product, 'CASE-7').items
    const snapshot = finalizeCheckoutSnapshot(buildCheckoutSnapshot({ items, paymentMethod: 'WECHAT', amountReceived: '' }))
    expect(snapshot.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
    expect(snapshot.payload.idempotency_key).toBe(snapshot.idempotency_key)
    expect(snapshot.payload.items[0]).toEqual({ product_id: 7, qty: '20.000', price: '2.0000' })
    expect(snapshot.payload.payment).toMatchObject({ method: 'WECHAT', amount_received: '40.00' })
    expect(snapshot.payload).not.toHaveProperty('stock_zone_type')
    expect(snapshot.payload).not.toHaveProperty('zone_type')
    expect(snapshot.payload).not.toHaveProperty('customer_id')
    expect(snapshot.payload).not.toHaveProperty('payments')
    expect(validateCart(items)).toBe('')
  })

  it('isolates drafts and pending requests by user and warehouse', () => {
    const userA = { id: 1, warehouse_id: 2 }
    const userB = { id: 2, warehouse_id: 2 }
    expect(draftStorageKey(userA)).not.toBe(draftStorageKey(userB))
    expect(pendingStorageKey(userA)).toBe(`${draftStorageKey(userA)}:pending-checkout`)
  })

  it('requires a positive onsite price and rounds half a unit to cents once', () => {
    const item = { ...createCartItem({ ...product, price: null }), qty: 0.5 }
    expect(validateCart([item])).toContain('输入正单价')
    expect(validateCart([{ ...item, is_gift: true }])).toContain('不可赠送')
    item.price = '1.2300'
    expect(validateCart([item])).toContain('输入正单价')
    item.onsite_price = true
    expect(validateCart([item])).toBe('')
    const payload = buildCheckoutSnapshot({ items: [item], paymentMethod: 'WECHAT' }).payload
    expect(payload.items).toEqual([{ product_id: 7, qty: '0.500', price: '1.2300', onsite_price: true }])
    expect(payload.payment.amount_received).toBe('0.62')
    expect(validateCart([{ ...item, price: '0.0001' }])).toContain('0.01')
    expect(validateCart([{ ...item, min_price: 1.24 }])).toContain('最低')
  })

  it('records a gift explicitly and scans subsequent units as paid lines', () => {
    const initial = createCartItem({ ...product, price: 0 })
    expect(validateCart([initial])).toContain('正单价')
    const gift = { ...initial, is_gift: true }
    expect(validateCart([gift])).toBe('')
    const items = addProductToCart([gift], product).items
    const payload = buildCheckoutSnapshot({ items, paymentMethod: 'WECHAT' }).payload
    expect(payload.items).toEqual([
      { product_id: 7, qty: '1.000', price: '0.0000', is_gift: true },
      { product_id: 7, qty: '1.000', price: '2.0000' },
    ])
    expect(payload.payment.amount_received).toBe('2.00')
  })

  it('uses the effective alias snapshot even when the current package has a different multiplier', () => {
    const alias = { ...product, matched_barcode: { package_id: 9, label: '箱', multiplier: '12', code_type: 'PACKAGE' } }
    const first = addProductToCart([], alias, 'OLD-ALIAS')
    expect(lineBaseQty(first.item)).toBe(12)
    const next = addProductToCart(first.items, product, 'CASE-7')
    expect(next.items).toHaveLength(2)
    expect(next.items.map(lineBaseQty)).toEqual([12, 20])
  })

  it('fails closed for a scan without authoritative package semantics', () => {
    expect(addProductToCart([], { ...product, matched_barcode: null }, 'CASE-7').error).toMatch(/包装/)
    expect(addProductToCart([], { ...product, matched_barcode: null, barcode_error: '编码冲突' }, 'CASE-7').error).toBe('编码冲突')
    expect(addProductToCart([], { ...product, matched_barcode: null }).error).toBeUndefined()
  })

  it('checks aggregate stock without mutating the accepted cart on failure', () => {
    const first = addProductToCart([], { ...product, available_qty: 21 }, 'CASE-7').items
    const second = addProductToCart(first, { ...product, available_qty: 21 }).items
    expect(addProductToCart(second, { ...product, available_qty: 21 }).error).toMatch(/合计/)
    expect(second.map(lineBaseQty)).toEqual([20, 1])
    second[1].qty = 2
    expect(validateCart(second)).toMatch(/合计/)
  })

  it('keeps different owners separate and permits positive fractional quantities', () => {
    const first = createCartItem(product)
    first.qty = '0.125'
    const second = createCartItem({ ...product, id: 8, owner_id: 4, available_qty: 1 })
    expect(validateCart([first, second])).toBe('')
    expect(lineBaseQty(first)).toBe(0.125)
  })

  it.each(['', '0', '-1', 'NaN', 'Infinity', '0.0001'])('rejects invalid basic quantity %s', (qty) => {
    expect(validateCart([{ ...createCartItem(product), qty }])).toMatch(/数量/)
  })

  it('rounds base quantities once to the same precision sent to the backend', () => {
    const item = createCartItem(product, 'CASE-7')
    item.qty = 0.061725
    expect(lineBaseQty(item)).toBe(1.235)
    expect(buildCheckoutSnapshot({ items: [item], paymentMethod: 'CASH', amountReceived: 3 }).payload.items[0].qty).toBe('1.235')
  })
})
