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
  let quickCreatePayload = null
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
        categories: [{ id: 21, code: 'DRINK', name: '饮品', label: '食品 / 饮品' }],
        uoms: [{ id: 8, code: 'BTL', name: '瓶', label: '瓶 (BTL)' }],
      },
    }),
  )
  await page.route('**/api/inbound/gs1-products/quick-create/', async (route) => {
    quickCreatePayload = route.request().postDataJSON()
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

  await page.getByTestId('gs1-category-option').click()
  await page.getByTestId('gs1-uom-option').click()
  const quantityInput = page.getByTestId('gs1-quantity').locator('input')
  await expect(quantityInput).toHaveValue('1')
  await page.getByTestId('gs1-batch-switch').click()
  await page.getByTestId('gs1-expiry-switch').click()
  await page.getByTestId('gs1-submit').click()

  await expect(page.getByTestId('gs1-quick-create-modal')).toHaveCount(0)
  expect(quickCreatePayload).toMatchObject({
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
  await page.screenshot({ path: '/tmp/wmspda-gs1-quick-create.png', fullPage: true })
})
