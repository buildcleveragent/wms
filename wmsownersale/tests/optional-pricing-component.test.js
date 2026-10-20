import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ProductCard from '@/components/ProductCard.vue'
import CartItemEditor from '@/components/CartItemEditor.vue'
import { amountSummaryLabel, orderAmount } from '@/utils/orderAmount'

describe('未计价商品展示', () => {
  const product = { name: '包材', available: '96.0000', base_unit_name: '个', is_priced: false }

  it('未计价商品保留数量输入和加入操作，隐藏价格金额', async () => {
    const wrapper = mount(ProductCard, { props: { product, baseQuantity: 10 } })
    expect(wrapper.text()).toContain('未计价')
    expect(wrapper.text()).not.toContain('基本单价')
    expect(wrapper.text()).not.toContain('¥')
    await wrapper.find('input').trigger('input', { detail: { value: '1.25' } })
    expect(wrapper.emitted('quantity-input')[0]).toEqual(['1.25'])
    await wrapper.find('.add-button').trigger('click')
    expect(wrapper.emitted('add')).toHaveLength(1)
  })

  it('未计价购物车行仍可编辑数量', () => {
    const wrapper = mount(CartItemEditor, { props: { item: product, quantityDraft: '10' } })
    expect(wrapper.findAll('input')).toHaveLength(1)
    expect(wrapper.find('input').element.value).toBe('10')
    expect(wrapper.text()).toContain('未计价')
    expect(wrapper.text()).not.toContain('基本单价')
  })

  it('全部和部分未计价不会显示成免费或完整订单金额', () => {
    const unpriced = { is_priced: false, qty: 100, price: 999 }
    const priced = { is_priced: true, qty: '1.25', price: '10' }
    expect(orderAmount([unpriced])).toBe('0.00')
    expect(amountSummaryLabel({ items: [unpriced], total_amount: '0.00' })).toBe('未计价')
    expect(orderAmount([unpriced, priced])).toBe('12.50')
    expect(amountSummaryLabel({
      items: [unpriced, priced], total_amount: orderAmount([unpriced, priced]),
    })).toBe('已计价小计 ¥12.50，另有 1 行未计价')
    expect(amountSummaryLabel({ items: [priced], total_amount: '12.50' })).toBe('¥ 12.50')
    expect(amountSummaryLabel({
      amount_status: 'PARTIAL', priced_amount: '12.50', unpriced_line_count: 1,
    })).toBe('已计价小计 ¥12.50，另有 1 行未计价')
  })
})
