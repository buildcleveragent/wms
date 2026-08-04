import { describe, expect, it } from 'vitest'

import {
  comparePrice4,
  enforceMinimumPrice,
  initializePriceGuard,
  isPriceAllowed,
  normalizePrice4,
} from '@/utils/pricing'

describe('服务端最低价四位小数契约', () => {
  it('规范化四位小数且不使用二进制浮点比较', () => {
    expect(normalizePrice4('10.12344')).toBe('10.1234')
    expect(normalizePrice4('10.12345')).toBe('10.1235')
    expect(comparePrice4('10.1000', '10.0999')).toBe(1)
    expect(comparePrice4('0.3000', '0.3')).toBe(0)
  })

  it('直接采用接口 minimum_sale_price，不在客户端复算折扣公式', () => {
    const item = {
      price: 8.1234,
      minimum_sale_price: '8.1234',
      product_min_price: '99.99',
      max_discount: '0',
    }
    initializePriceGuard(item)
    expect(item.minimum_sale_price).toBe('8.1234')
    expect(isPriceAllowed(item)).toBe(true)
  })

  it('允许等于四位最低价，拒绝低一万分位及超过四位小数', () => {
    const allowed = { price: '1.2345', minimum_sale_price: '1.2345' }
    expect(enforceMinimumPrice(allowed)).toMatchObject({ valid: true, adjusted: false })

    const tooLow = { price: '1.2344', minimum_sale_price: '1.2345' }
    expect(enforceMinimumPrice(tooLow)).toMatchObject({ valid: false, adjusted: true })
    expect(tooLow.price).toBe(1.2345)

    const tooPrecise = { price: '1.23456', minimum_sale_price: '1.0000' }
    expect(enforceMinimumPrice(tooPrecise)).toMatchObject({ valid: false, adjusted: false })
  })
})
