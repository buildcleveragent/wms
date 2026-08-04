import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useCart } from '@/store/cart'
import { previewBaseQuantity, validateDesiredQuantity } from '@/utils/quantity'

describe('商品数量校验', () => {
  it.each([undefined, '', '  ', 0, '0', -1, '-2', 'abc', Number.NaN])(
    '拒绝非法输入 %s',
    (value) => expect(validateDesiredQuantity(value, 1).valid).toBe(false),
  )

  it('拒绝换算后不足最小基本数量', () => {
    expect(validateDesiredQuantity('0.0009', 1).valid).toBe(false)
    expect(previewBaseQuantity('0.0009', 1)).toBe(0)
  })

  it('正确处理合法小数和包装换算', () => {
    expect(validateDesiredQuantity('1.25', 12)).toMatchObject({
      valid: true,
      saleQty: 1.25,
      baseQty: 15,
    })
    expect(validateDesiredQuantity('0.001', 1).baseQty).toBe(0.001)
  })
})

describe('购物车数量防线', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it.each([undefined, '', 0, -1, 'bad'])(
    'addItem 拒绝数量 %s 且不改变购物车',
    (qty) => {
      const cart = useCart()
      expect(cart.addItem({ id: 1, qty, name: '商品' })).toBe(false)
      expect(cart.items).toEqual([])
    },
  )

  it('合法基本数量可以加入并累计', () => {
    const cart = useCart()
    expect(cart.addItem({ id: 1, qty: 1.25, price: 2 })).toBe(true)
    expect(cart.addItem({ id: 1, qty: 0.75, price: 2 })).toBe(true)
    expect(cart.items).toHaveLength(1)
    expect(cart.items[0].qty).toBe(2)
  })
})
