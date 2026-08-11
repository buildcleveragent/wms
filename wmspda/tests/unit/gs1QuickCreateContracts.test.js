import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('GS1 receiving quick-create contracts', () => {
  it('keeps every ApiZero call on the authenticated backend', () => {
    const request = read('utils/request.js')

    expect(request).toContain('/api/inbound/gs1-products/lookup/')
    expect(request).toContain('/api/inbound/gs1-products/options/')
    expect(request).toContain('/api/inbound/gs1-products/quick-create/')
    expect(request).not.toContain('v1.apizero.cn')
  })

  it('opens one in-page form and adds the server-normalized item to the cart', () => {
    const page = read('pages/products/search.vue')

    expect(page).toContain('<Gs1QuickCreateModal')
    expect(page).toContain('api.gs1ProductLookup')
    expect(page).toContain('api.gs1ProductQuickCreate')
    expect(page).toContain('cart.addItem({')
    expect(page).toContain('base_quantity: quantity')
    expect(page).toContain('product-search-error')
    expect(page).toContain('错误编号：{{ searchError.requestId }}')
    expect(page).toContain('showSearchError')
    expect(page).toContain('await lookupGs1(keyword)')
  })

  it('collects mandatory catalog and tracking fields before submission', () => {
    const modal = read('components/Gs1QuickCreateModal.vue')

    expect(modal).toContain('商品分类 *')
    expect(modal).toContain('基本单位 *')
    expect(modal).toContain('批次管理')
    expect(modal).toContain('效期管理')
    expect(modal).toContain('建档并加入购物车')
  })
})
