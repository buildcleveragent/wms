import { expect, test } from '@playwright/test'

const productResults = [
  {
    id: 1,
    code: 'POS-RICE-01',
    sku: 'SKU-RICE-01',
    name: '测试香米',
    spec: '25KG/袋',
    price: '98.00',
    available_qty: '200.0000',
    base_unit: { id: 1, code: 'BAG', name: '袋' },
    unit_options: [],
  },
  {
    id: 2,
    code: 'POS-TAPE-02',
    sku: 'SKU-TAPE-02',
    name: '测试双面胶',
    spec: '2cm×20米',
    price: '6.00',
    available_qty: '143.0000',
    base_unit: { id: 2, code: 'ROLL', name: '卷' },
    unit_options: [],
  },
  {
    id: 3,
    code: 'POS-NOSPEC-03',
    sku: 'SKU-NOSPEC-03',
    name: '无规格商品',
    spec: '',
    price: '10.00',
    available_qty: '12.0000',
    base_unit: { id: 3, code: 'EA', name: '件' },
    unit_options: [],
  },
]

test('POS search results and cart show each non-empty product specification after its name', async ({ page }) => {
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))

  await page.route('http://192.168.1.6:8001/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/pos/products/') {
      const rows = url.searchParams.get('barcode') ? [] : productResults
      await route.fulfill({
        json: { count: rows.length, next: null, previous: null, results: rows },
      })
      return
    }
    await route.fulfill({ json: {} })
  })

  await page.goto('/#/pages/pos/index')
  await expect(page).toHaveURL(/pages\/pos\/index/)
  await expect(page.locator('body')).not.toBeEmpty()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)

  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill('测试')
  await input.press('Enter')

  const names = page.getByTestId('pos-product-name')
  await expect(names).toHaveCount(3)
  await expect(names.nth(0)).toHaveText('测试香米　25KG/袋')
  await expect(names.nth(1)).toHaveText('测试双面胶　2cm×20米')
  await expect(names.nth(2)).toHaveText('无规格商品')
  await expect(names.nth(2)).not.toContainText(/undefined|null/)

  await page.getByTestId('pos-product-row').nth(0).locator('.add-btn').click()

  const cartProducts = page.getByTestId('pos-cart-product')
  await expect(cartProducts).toHaveCount(1)
  await expect(cartProducts.nth(0)).toContainText('测试香米　25KG/袋　POS-RICE-01')

  await input.fill('无规格')
  await input.press('Enter')
  await expect(page.getByTestId('pos-product-row')).toHaveCount(3)
  await page.getByTestId('pos-product-row').nth(2).locator('.add-btn').click()

  await expect(cartProducts).toHaveCount(2)
  await expect(cartProducts.nth(1)).toContainText('无规格商品　POS-NOSPEC-03')
  await expect(cartProducts.nth(1)).not.toContainText(/undefined|null/)

  const firstCartRow = page.locator('.cart-row').first()
  await expect(firstCartRow.locator('.unit-picker')).toBeVisible()
  await expect(firstCartRow.locator('.small-input')).toBeVisible()
  await expect(firstCartRow.locator('.price-input')).toBeVisible()
  await expect(firstCartRow.locator('.amount-input')).toBeVisible()
  await expect(firstCartRow.locator('.remove-btn')).toBeVisible()
  expect(errors).toEqual([])

  await page.screenshot({ path: '/tmp/wmspda-pos-cart-product-spec.png', fullPage: false })
})
