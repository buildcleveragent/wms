import { expect, test } from '@playwright/test'

const candidate = {
  lookup_id: 'efdd7979-b070-46a8-88b3-b3a4e60cc548',
  barcode: '6921168509256',
  gtin14: '06921168509256',
  found: true,
  registered: false,
  name: '测试饮用水',
  brand: '测试品牌',
  specification: '550ml',
  manufacturer: '测试饮品制造有限公司',
  images: ['https://www.gds.org.cn/product/test.jpg'],
}

test('unknown GTIN can be created immediately and added to the receiving cart', async ({ page }) => {
  test.setTimeout(180_000)
  const quickCreatePayloads = []
  const runtimeErrors = []
  page.on('pageerror', (error) => runtimeErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') runtimeErrors.push(message.text())
  })
  await page.route('https://www.gds.org.cn/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9YE3gC8AAAAASUVORK5CYII=',
        'base64',
      ),
    }),
  )
  await page.route('**/api/catalog/receive_products?**', (route) =>
    route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
  )
  await page.route('**/api/inbound/gs1-products/lookup/', (route) =>
    route.fulfill({ json: { source: 'gs1', cache_hit: false, candidate } }),
  )
  await page.route('**/api/inbound/gs1-products/options/?**', (route) =>
    route.fulfill({
      json: {
        categories: [
          { id: 21, code: 'DRINK', name: '饮品', label: '食品 / 饮品' },
          { id: 22, code: 'OIL', name: '粮油', label: '食品 / 粮油' },
          { id: 23, code: 'OFFICE', name: '办公用品', label: '办公 / 文具' },
        ],
        uoms: [{ id: 8, code: 'BTL', name: '瓶', label: '瓶 (BTL)' }],
      },
    }),
  )
  await page.route('**/api/inbound/gs1-products/quick-create/', async (route) => {
    quickCreatePayloads.push(route.request().postDataJSON())
    await route.fulfill({
      status: 201,
      json: {
        created: true,
        product: {
          id: 101,
          sku: 'JQRT123',
          name: candidate.name,
          spec: candidate.specification,
          base_unit: 'BTL',
          base_unit_name: '瓶',
          product_image_url: null,
          gtin: candidate.barcode,
          packaging: [],
          unitOptions: [
            { key: 'BASE', kind: 'base', label: '瓶', multiplier: 1, package_id: null, barcode: null },
          ],
          selectedUnitIndex: 0,
        },
        cart_item: { product_id: 101, quantity: '1.0000', lot_no: '', mfg_date: null, exp_date: null },
      },
    })
  })

  await page.goto('/')
  await page.evaluate(async () => {
    const { useCart } = await import('/store/cart.js')
    useCart().setOwner({ id: 7, name: '测试货主' })
    window.location.hash = '#/pages/products/search'
  })

  const searchInput = page.getByTestId('product-search-input').locator('input')
  // The uni-app dev server compiles a page on first navigation.
  await expect(searchInput).toBeVisible({ timeout: 20_000 })
  await searchInput.fill(candidate.barcode)
  await page.getByTestId('product-search-submit').click()

  await expect(page.getByTestId('gs1-quick-create-modal')).toBeVisible()
  await expect(page.getByText(candidate.name, { exact: true })).toBeVisible()
  await expect(page.getByText(candidate.specification, { exact: true })).toBeVisible()
  await expect(page.getByText(/测试品牌.*测试饮品制造有限公司/)).toBeVisible()
  await expect(page.getByTestId('gs1-candidate-image')).toBeVisible()
  await expect(page.getByTestId('gs1-category-grid')).toBeVisible()
  await expect(page.getByTestId('gs1-category-option')).toHaveCount(3)
  await expect(page.getByTestId('gs1-batch-switch')).toHaveCount(0)
  await expect(page.getByTestId('gs1-expiry-switch')).toHaveCount(0)
  await expect(page.getByTestId('gs1-lot-no')).toBeVisible()
  await expect(page.getByTestId('gs1-expiry-fields')).toBeVisible()

  await page.getByTestId('gs1-category-option').filter({ hasText: '食品 / 饮品' }).click()
  await page.getByTestId('gs1-uom-option').click()
  const quantityInput = page.getByTestId('gs1-quantity').locator('input')
  await expect(quantityInput).toHaveValue('1')
  await page.screenshot({ path: '/tmp/wmspda-gs1-quick-create-form.png', fullPage: true })
  await page.getByTestId('gs1-expiry-fields').scrollIntoViewIfNeeded()
  await page.screenshot({ path: '/tmp/wmspda-gs1-quick-create-tracking.png', fullPage: false })
  await page.getByTestId('gs1-submit').click()

  await expect(page.getByTestId('gs1-quick-create-modal')).toHaveCount(0)
  expect(quickCreatePayloads[0]).toMatchObject({
    owner_id: 7,
    lookup_id: candidate.lookup_id,
    category_id: 21,
    base_uom_id: 8,
    quantity: '1',
    batch_control: false,
    expiry_control: false,
  })
  await expect(page.getByText('测试饮用水', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('查看、提交入库单：数量:1')).toBeVisible()

  await page.getByTestId('product-search-submit').click()
  await expect(page.getByTestId('gs1-quick-create-modal')).toBeVisible()
  await page.getByTestId('gs1-category-option').filter({ hasText: '食品 / 饮品' }).click()
  await page.getByTestId('gs1-uom-option').click()
  await page.getByTestId('gs1-lot-no').locator('input').fill('LOT-2026')
  await page.getByText('入库日期', { exact: true }).click()
  await page.getByTestId('gs1-inbound-valid-days').locator('input').fill('30')
  await page.getByTestId('gs1-submit').click()
  await expect(page.getByTestId('gs1-quick-create-modal')).toHaveCount(0)
  expect(quickCreatePayloads[1]).toMatchObject({
    batch_control: true,
    lot_no: 'LOT-2026',
    expiry_control: true,
    expiry_basis: 'INBOUND',
    inbound_valid_days: 30,
  })
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(runtimeErrors).toEqual([])
  await page.screenshot({ path: '/tmp/wmspda-gs1-quick-create.png', fullPage: true })
})

test('unknown GTIN shows a specific retryable backend error and request id', async ({ page }) => {
  test.setTimeout(180_000)
  const runtimeErrors = []
  let lookupCount = 0
  page.on('pageerror', (error) => runtimeErrors.push(error.message))
  await page.route('**/api/catalog/receive_products?**', (route) =>
    route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
  )
  await page.route('**/api/inbound/gs1-products/lookup/', (route) => {
    lookupCount += 1
    return route.fulfill({
      status: 503,
      json: {
        code: 'GS1_CONFIG_MISSING',
        detail: 'GS1 查询配置缺失：尚未配置 ApiZero API Key，请管理员在系统设置中配置。',
        request_id: 'gs1-config-test-0001',
        retry_after: null,
      },
    })
  })

  await page.goto('/')
  await page.evaluate(async () => {
    const { useCart } = await import('/store/cart.js')
    useCart().setOwner({ id: 7, name: '测试货主' })
    window.location.hash = '#/pages/products/search'
  })

  const searchInput = page.getByTestId('product-search-input').locator('input')
  await expect(searchInput).toBeVisible({ timeout: 20_000 })
  await searchInput.fill('6921168509256')
  await page.getByTestId('product-search-submit').click()

  const errorPanel = page.getByTestId('product-search-error')
  await expect(errorPanel).toBeVisible()
  await expect(errorPanel).toContainText('GS1 查询配置缺失')
  await expect(errorPanel).toContainText('GS1_CONFIG_MISSING')
  await expect(errorPanel).toContainText('gs1-config-test-0001')
  await page.getByTestId('product-search-retry').click()
  await expect.poll(() => lookupCount).toBe(2)
  expect(runtimeErrors).toEqual([])
  await page.screenshot({ path: '/tmp/wmspda-gs1-config-error.png', fullPage: true })
})
