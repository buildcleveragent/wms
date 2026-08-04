import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useCart } from '@/store/cart'
import {
  previewBaseQuantity,
  validateCartQuantity,
  validateDesiredQuantity,
} from '@/utils/quantity'

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

  it('拒绝零、负数、超过三位小数和超过库存的修改', () => {
    expect(validateCartQuantity('0', 10).valid).toBe(false)
    expect(validateCartQuantity('-1', 10).valid).toBe(false)
    expect(validateCartQuantity('0.0009', 10).valid).toBe(false)
    expect(validateCartQuantity('1.2345', 10).valid).toBe(false)
    expect(validateCartQuantity('11', 10)).toMatchObject({
      valid: false,
      overAvailable: true,
      available: 10,
    })
    expect(validateCartQuantity('1.234', 10)).toMatchObject({ valid: true, value: 1.234 })
  })

  it('Store 拒绝非法程序化数量且保留原值', () => {
    const cart = useCart()
    expect(cart.addItem({ id: 1, qty: 1, available: 2, name: '商品' })).toBe(true)
    expect(cart.setQty(0, 0)).toBe(false)
    expect(cart.setQty(0, 3)).toBe(false)
    expect(cart.setQty(0, 1.2345)).toBe(false)
    expect(cart.items[0].qty).toBe(1)
  })

  it('更换客户保留商品并清空客户相关表头', () => {
    const cart = useCart()
    cart.beginOrder({ user_id: 1, owner_id: 2, warehouse: { id: 3, name: '仓库' } })
    cart.setCustomer({ id: 4, code: 'A', name: '客户 A' })
    cart.addItem({ id: 5, qty: 1, available: 2, name: '商品' })
    Object.assign(cart.order_header, {
      src_bill_no: 'PLATFORM-1', contact: '张三', contact_phone: '13800000000', ship_to: '地址',
    })
    const firstKey = cart.idempotency_key

    expect(cart.setCustomer({ id: 6, code: 'B', name: '客户 B' })).toBe(true)
    expect(cart.items).toHaveLength(1)
    expect(cart.order_header).toMatchObject({
      src_bill_no: '', contact: '', contact_phone: '', ship_to: '',
    })
    expect(cart.idempotency_key).not.toBe(firstKey)
  })

  it('更换仓库清空客户、商品和订单表头', () => {
    const cart = useCart()
    cart.beginOrder({ user_id: 1, owner_id: 2, warehouse: { id: 3, name: '仓库 A' } })
    cart.setCustomer({ id: 4, code: 'A', name: '客户 A' })
    cart.addItem({ id: 5, qty: 1, available: 2, name: '商品' })
    cart.order_header.src_bill_no = 'PLATFORM-1'

    expect(cart.changeWarehouse({ id: 7, name: '仓库 B' })).toBe(true)
    expect(cart.warehouse_id).toBe(7)
    expect(cart.customer).toBeNull()
    expect(cart.items).toEqual([])
    expect(cart.order_header.src_bill_no).toBe('')
  })
})
