import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('POS full product search and gift pricing contracts', () => {
  it('loads every product page with the maximum page size and de-duplicates by id', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain('const POS_PRODUCT_PAGE_SIZE = 100')
    expect(page).toContain('async function fetchAllPosProducts(params)')
    expect(page).toContain('const seenProductIds = new Set()')
    expect(page).toContain('page_size: POS_PRODUCT_PAGE_SIZE')
    expect(page).toContain('if (Array.isArray(res) || !res?.next) break')
    expect(page).toContain('return fetchAllPosProducts({ ...POS_STOCK_QUERY, barcode })')
    expect(page).toContain('const rows = await fetchAllPosProducts({')
  })

  it('keeps the product result list inside a vertically scrollable area', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain('data-testid="pos-product-list"')
    expect(page).toContain('.pos-toolbar .product-list {')
    expect(page).toContain('max-height: min(42vh, 560rpx);')
    expect(page).toContain('overflow-y: auto;')
  })

  it('accepts zero gift amounts while retaining negative and minimum-price checks', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain('amount >= 0 && price >= 0')
    expect(page).toContain('if (totalAmount.value < 0) return false')
    expect(page).toContain('return !splitPaymentEnabled.value && moneyEqual(receivedAmount.value, 0)')
    expect(page).toContain('} else if (amount < 0) {')
    expect(page).toContain('} else if (price < 0) {')
    expect(page).toContain('} else if (amount > 0) {')
  })
})
