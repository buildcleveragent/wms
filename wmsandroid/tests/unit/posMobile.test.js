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
    expect(base.error).toMatch(/合计.*库存/)
    expect(base.items).toHaveLength(1)
  })

  it('builds an immediate-payment full-warehouse payload with a UUID and no zone/customer fields', () => {
    const items = addProductToCart([], product, 'CASE-7').items
    const snapshot = finalizeCheckoutSnapshot(buildCheckoutSnapshot({ items, paymentMethod: 'WECHAT', amountReceived: '' }))
    expect(snapshot.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
    expect(snapshot.payload.idempotency_key).toBe(snapshot.idempotency_key)
    expect(snapshot.payload.items[0]).toMatchObject({ qty: '20.000', amount: '40.00' })
    expect(snapshot.payload.payment).toMatchObject({ method: 'WECHAT', amount_received: '40.00' })
    expect(snapshot.payload).not.toHaveProperty('stock_zone_type')
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
