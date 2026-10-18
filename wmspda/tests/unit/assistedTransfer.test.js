import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAssistedOutbound } from '@/store/assistedOutbound'

describe('assisted ownership transfer draft', () => {
  beforeEach(() => {
    globalThis.uni = { getStorageSync: vi.fn(), setStorageSync: vi.fn() }
    setActivePinia(createPinia())
  })

  it('defaults to direct sale and keeps the source catalog owner in transfer mode', () => {
    const draft = useAssistedOutbound()
    draft.setOwner({ id: 1, name: '原货主' })
    draft.setCustomer({ id: 5, name: '共享客户' })
    expect(draft.salesOwner.id).toBe(1)
    expect(draft.routeLabel).toBe('原货主 → 共享客户')
    draft.setNewOwner({ id: 2, name: '新货主' })
    expect(draft.owner.id).toBe(1)
    expect(draft.salesOwner.id).toBe(2)
    expect(draft.customer.id).toBe(5)
    expect(draft.routeLabel).toBe('原货主 → 新货主 → 共享客户')
    draft.setNewOwner(null)
    expect(draft.salesOwner.id).toBe(1)
    expect(draft.setNewOwner({ id: 1 })).toBe(false)
  })

  it('clears quantities and request identity when either owner changes', () => {
    const draft = useAssistedOutbound()
    draft.setOwner({ id: 1 })
    draft.setCustomer({ id: 5 })
    draft.items = [{ product_id: 7, qty: 10 }]
    draft.lastRequestId = 'pending'
    draft.setNewOwner({ id: 2 })
    expect(draft.items).toEqual([])
    expect(draft.lastRequestId).toBe('')
    draft.setOwner({ id: 3 })
    expect(draft.customer).toBeNull()
    expect(draft.newOwner).toBeNull()
  })

  it('keeps transfer and sales prices independent, and resets the identity after edits', () => {
    const draft = useAssistedOutbound()
    const added = draft.selectItem({ id: 7, name: '商品', available_qty: 100 }, '12', null, 10)
    expect(added.ok).toBe(true)
    expect(draft.items[0].transfer_price).toBe('')
    draft.setTransferPrice(0, '8')
    expect(draft.items[0].price).toBe('12')
    draft.lastRequestId = 'pending'
    draft.setPrice(0, '15')
    expect(draft.items[0].transfer_price).toBe('8')
    expect(draft.lastRequestId).toBe('')
    draft.setTransferPrice(0, '')
    expect(draft.items[0].price).toBe('15')
  })
})
