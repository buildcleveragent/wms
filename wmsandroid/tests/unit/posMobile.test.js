import { describe, expect, it } from 'vitest'
import { addProductToCart, buildCheckoutSnapshot, cartLineKey, draftStorageKey, finalizeCheckoutSnapshot, lineBaseQty, pendingStorageKey, unitIndexForBarcode, validateCart } from '@/utils/posMobile'

const product = {
  id: 7, owner_id: 3, code: 'RICE', name: '测试香米', price: '2.00', available_qty: '200',
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
    expect(cartLineKey(result.item)).toBe('7:9')
  })

  it('increments only the same unit and blocks stock overflow', () => {
    const first = addProductToCart([], { ...product, available_qty: 20 }, 'CASE-7').items
    expect(addProductToCart(first, { ...product, available_qty: 20 }, 'CASE-7').error).toMatch(/库存/)
    const base = addProductToCart(first, { ...product, available_qty: 20 }, 'BASE-7')
    expect(base.items).toHaveLength(2)
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
})
