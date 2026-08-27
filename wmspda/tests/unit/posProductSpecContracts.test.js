import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const root = path.resolve(import.meta.dirname, '../..')
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8')

describe('POS product specification contracts', () => {
  it('renders a non-empty specification immediately after the product name', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain('data-testid="pos-product-name"')
    expect(page).toContain('v-if="product.spec" class="product-spec"')
    expect(page).toContain('　{{ product.spec }}')
  })

  it('renders a non-empty specification between the cart product name and code', () => {
    const page = read('pages/pos/index.vue')

    expect(page).toContain('data-testid="pos-cart-product"')
    expect(page).toContain('v-if="item.spec" class="cart-product-spec"')
    expect(page).toContain('　{{ item.spec }}</text>　{{ item.code }}')
  })
})
