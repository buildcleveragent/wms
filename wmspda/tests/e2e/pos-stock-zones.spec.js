import { expect, test } from './helpers/browser-test.js'

test.use({ viewport: { width: 390, height: 844 } })

const barcode = '6900000000711'
const product = {
  id: 71, owner_id: 8, name: '双区样品', code: 'POS-ZONES', sku: 'POS-ZONES',
  price: '2.00', base_unit: { id: 1, name: '件', code: 'EA' },
  unit_options: [{ kind: 'base', label: '件', multiplier: '1' }],
}
async function openRegister(page, { shiftOpen = true, registerProduct = product } = {}) {
  const profile = {
    user: { id: 7, username: 'cashier', warehouse_id: 3 },
    perms: ['pos.add_possale'], capabilities: {},
  }
  const shift = { id: 1, shift_no: 'ZONE-SHIFT', warehouse_id: 3, cashier_id: 7, status: 'OPEN', summary: {} }
  const queries = []
  const checkouts = []
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    let data = {}
    if (url.pathname.endsWith('/token/')) data = { access: 'zone-access', refresh: 'zone-refresh' }
    if (url.pathname.endsWith('/profile/')) data = profile
    if (url.pathname.endsWith('/home-todos/')) data = { scope: 'test-warehouse', items: [] }
    if (url.pathname === '/api/pos/shifts/current/') data = { shift: shiftOpen ? shift : null }
    if (url.pathname === '/api/pos/products/') {
      queries.push(url.searchParams)
      const qty = url.searchParams.has('zone_type') ? 4 : 10
      const found = url.searchParams.get('barcode') === barcode || url.searchParams.has('search')
      const results = found ? [{ ...registerProduct, available_qty: qty, picking_available_qty: 4 }] : []
      data = { count: results.length, results }
    }
    if (url.pathname === '/api/pos/checkout/') {
      checkouts.push(route.request().postDataJSON())
      data = { sale: { id: 1, sale_no: 'POS-ZONES-01' }, receipt: { sale_no: 'POS-ZONES-01' } }
    }
    return route.fulfill({ json: data })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('cashier')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await page.locator('[data-action="pos"]').click()
  await expect(page).toHaveTitle(/POS收银/)
  return { queries, checkouts }
}

test('a cashier can scan and sell from picking and storage in the same sale', async ({ page }, testInfo) => {
  const { queries, checkouts } = await openRegister(page)
  await expect(page.getByText('ZONE-SHIFT')).toBeVisible()
  await page.getByText('自动打印销售单', { exact: true }).click()

  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill(product.name)
  await input.press('Enter')
  await expect(page.getByTestId('pos-product-row')).toContainText(/拣货区＋存储区可售\s*10\s*件/)
  await input.fill(barcode)
  await input.press('Enter')
  const cart = page.getByTestId('pos-cart-product')
  await expect(cart).toContainText(/拣货区＋存储区可售\s*10\s*件/)
  const qty = page.locator('.cart-row .small-input input')
  await qty.fill('7')
  await qty.blur()
  await expect(cart).toContainText(/基本数量\s*7\s*件/)
  await cart.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('pda-pos-stock.png') })
  await page.getByTestId('pos-checkout').click()
  await expect(cart).toHaveCount(0)
  expect(checkouts).toHaveLength(1)
  expect(checkouts[0]).not.toHaveProperty('stock_zone_type')
  expect(Number(checkouts[0].items[0].qty)).toBe(7)
  expect(queries.some(q => q.get('barcode') === barcode)).toBe(true)
  expect(queries.some(q => q.get('search') === product.code)).toBe(true)
  expect(queries.every(q => !q.has('zone_type') && !q.has('picking_only'))).toBe(true)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
})

test('a cashier can recover a pre-upgrade pick-only receipt after the shift closes', async ({ page }) => {
  await page.addInitScript(() => {
    const oldDraft = {
      saved_at: Date.now() - 48 * 60 * 60 * 1000,
      cart_items: [{
        product_id: 71, owner_id: 8, code: 'POS-ZONES', name: '旧单商品',
        qty: '2', price: '2.0000', amount: '4.00', available_qty: 4, base_unit_name: '件',
      }],
      idempotency_key: 'pre-upgrade-key', src_bill_no: 'POS-LEGACY-01',
      payment_method: 'CASH', amount_received: '4.00', payment_reference_no: 'orig-ref',
    }
    localStorage.setItem('pos_sale_draft_v1', JSON.stringify({ type: 'object', data: oldDraft }))
    localStorage.setItem('pos_auto_print_sale_v1', JSON.stringify({ type: 'boolean', data: false }))
  })
  const { queries, checkouts } = await openRegister(page, { shiftOpen: false })
  await expect(page.getByTestId('pos-cart-product')).toHaveCount(0)
  await page.getByText('恢复旧草稿', { exact: true }).click()
  await expect(page.getByTestId('pos-cart-product')).toContainText(/拣货区可售\s*4\s*件/)
  await expect(page.getByTestId('pos-product-input')).toHaveCount(0)
  await page.getByTestId('pos-checkout').click()
  await expect(page.getByTestId('pos-cart-product')).toHaveCount(0)
  expect(checkouts).toHaveLength(1)
  expect(checkouts[0]).toMatchObject({
    stock_zone_type: 1, idempotency_key: 'pre-upgrade-key', src_bill_no: 'POS-LEGACY-01',
    items: [{ product_id: 71, qty: '2', price: '2.0000', amount: '4.00' }],
    payment: { method: 'CASH', amount_received: '4.00', reference_no: 'orig-ref' },
  })
  expect(queries).toHaveLength(0)
  await expect(page.getByText('拣货区＋存储区可售', { exact: true })).toBeVisible()
})

for (const mode of ['unit', 'onsite', 'amount', 'gift']) {
  test(`cashier confirms ${mode} pricing on a half-kilogram sale`, async ({ page }) => {
    const registerProduct = {
      ...product, price: mode === 'onsite' ? null : '1.23',
      base_unit: { id: 4, code: 'KG', name: '千克' },
      unit_options: [{ kind: 'base', label: '千克', multiplier: '1' }],
    }
    const { checkouts } = await openRegister(page, { registerProduct })
    await page.getByText('自动打印销售单', { exact: true }).click()
    const scan = page.getByTestId('pos-product-input').locator('input')
    await scan.fill(barcode)
    await scan.press('Enter')
    const line = page.locator('.cart-row.cart-line-row')
    const qty = line.locator('.small-input input')
    await qty.fill('0.5')
    await qty.blur()
    await expect(line.getByTestId('pos-cart-product')).toContainText(/基本数量\s*0.5(?:00)?\s*千克/)
    const price = line.locator('.price-input input')
    const amount = line.locator('.amount-input input')
    if (mode === 'onsite') {
      await expect(price).toHaveValue('')
      await expect(line.getByText('本行赠送')).toHaveCount(0)
      await expect(amount).toBeDisabled()
      await expect(page.getByTestId('pos-checkout')).toHaveAttribute('disabled', /.*/)
      await price.fill('1.23')
      await price.blur()
    } else if (mode === 'amount') {
      await amount.fill('0.65')
      await amount.blur()
    } else if (mode === 'gift') {
      await amount.fill('0')
      await amount.blur()
      await expect(line.locator('.line-price-error')).toContainText('普通销售')
      await line.getByText('本行赠送').click()
    } else {
      await amount.focus()
      await amount.blur()
    }
    const due = mode === 'amount' ? '0.65' : mode === 'gift' ? '0.00' : '0.62'
    await expect(amount).toHaveValue(due)
    await page.getByTestId('pos-checkout').click()
    await expect.poll(() => checkouts.length).toBe(1)
    expect(checkouts[0].items).toEqual([{
      product_id: product.id, qty: '0.500',
      price: mode === 'gift' ? '0.0000' : mode === 'amount' ? '1.3000' : '1.2300',
      ...(mode === 'onsite' ? { onsite_price: true } : {}),
      ...(mode === 'amount' ? { amount: due } : {}),
      ...(mode === 'gift' ? { is_gift: true } : {}),
    }])
    expect(checkouts[0].payment.amount_received).toBe(due)
  })
}
