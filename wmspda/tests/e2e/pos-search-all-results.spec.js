import { expect, test } from '@playwright/test'

const products = Array.from({ length: 105 }, (_, index) => {
  const number = index + 1
  return {
    id: number,
    code: `POS-BULK-${String(number).padStart(3, '0')}`,
    sku: `SKU-BULK-${String(number).padStart(3, '0')}`,
    name: `批量商品${number}`,
    spec: `${number}号规格`,
    price: '10.00',
    min_price: '8.00',
    max_discount: '20.00',
    available_qty: '10.0000',
    base_unit: { id: 1, code: 'EA', name: '件' },
    unit_options: [],
  }
})

test('POS loads, scrolls, and selects all paginated matches and accepts a zero gift amount', async ({
  page,
}) => {
  test.setTimeout(90_000)
  const errors = []
  const searchRequests = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))

  await page.route('http://192.168.1.6:8001/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/pos/products/') {
      if (url.searchParams.has('barcode')) {
        await route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } })
        return
      }

      const requestedPage = Number(url.searchParams.get('page') || '1')
      searchRequests.push({
        page: requestedPage,
        pageSize: Number(url.searchParams.get('page_size')),
      })
      const rows = requestedPage === 1 ? products.slice(0, 100) : [products[99], ...products.slice(100)]
      await route.fulfill({
        json: {
          count: 106,
          next: requestedPage === 1 ? '/api/pos/products/?search=批量&page=2' : null,
          previous: requestedPage === 1 ? null : '/api/pos/products/?search=批量&page=1',
          results: rows,
        },
      })
      return
    }
    await route.fulfill({ json: {} })
  })

  await page.goto('/#/pages/pos/index')
  await expect(page).toHaveURL(/pages\/pos\/index/)
  await expect(page).toHaveTitle(/.+/)
  await expect(page.locator('body')).not.toBeEmpty()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)

  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill('批量')
  await input.press('Enter')

  const resultRows = page.getByTestId('pos-product-row')
  await expect(resultRows).toHaveCount(105)
  await expect(page.locator('.scan-message')).toContainText('找到 105 个匹配商品')
  expect(searchRequests).toEqual([
    { page: 1, pageSize: 100 },
    { page: 2, pageSize: 100 },
  ])

  const productList = page.getByTestId('pos-product-list')
  const scrollState = await productList.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
  }))
  expect(scrollState.scrollHeight).toBeGreaterThan(scrollState.clientHeight)
  expect(scrollState.overflowY).toMatch(/auto|scroll/)
  await page.screenshot({ path: '/tmp/wmspda-pos-all-search-results.png', fullPage: false })

  const lastResult = resultRows.filter({ hasText: '批量商品105' })
  await lastResult.scrollIntoViewIfNeeded()
  await lastResult.locator('.add-btn').click()

  const cartRow = page.locator('.cart-row').first()
  await expect(cartRow).toContainText('批量商品105')
  const amountInput = cartRow.locator('.amount-input input')
  await amountInput.fill('0')
  await amountInput.blur()
  await expect(cartRow.locator('.line-price-error')).toHaveCount(0)
  await expect(cartRow.locator('.price-input input')).toHaveValue(/^0(?:\.0+)?$/)
  expect(errors).toEqual([])
  await page.screenshot({ path: '/tmp/wmspda-pos-zero-gift-line.png', fullPage: false })
})
