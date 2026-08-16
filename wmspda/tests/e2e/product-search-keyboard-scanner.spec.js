import { expect, test } from '@playwright/test'

function product(id, name, gtin) {
  return {
    id,
    sku: `SKU-${id}`,
    name,
    spec: '',
    product_image_url: null,
    gtin,
    base_unit_name: '件',
    packaging: [],
    unitOptions: [
      { key: 'BASE', kind: 'base', label: '件', multiplier: 1, package_id: null, barcode: null },
    ],
    selectedUnitIndex: 0,
  }
}

async function openProductSearch(page) {
  await page.goto('/')
  await page.evaluate(async () => {
    const { useCart } = await import('/store/cart.js')
    useCart().setOwner({ id: 7, name: '键盘扫描测试货主' })
    window.location.hash = '#/pages/products/search'
  })
  const input = page.getByTestId('product-search-input').locator('input')
  await expect(input).toBeVisible({ timeout: 20_000 })
  return input
}

async function keyboardScan(page, input, barcode) {
  await input.click()
  await input.fill('')
  await page.keyboard.type(barcode, { delay: 0 })
  await expect(input).toHaveValue(barcode)
  await page.keyboard.press('Enter')
}

test('fast keyboard scans submit the complete confirmed barcode', async ({ page }) => {
  test.setTimeout(180_000)
  const localQueries = []
  const lookupBodies = []
  const runtimeErrors = []
  page.on('pageerror', (error) => runtimeErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') runtimeErrors.push(message.text())
  })
  await page.route('**/api/catalog/receive_products?**', async (route) => {
    localQueries.push(new URL(route.request().url()).searchParams.get('search'))
    await route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } })
  })
  await page.route('**/api/inbound/gs1-products/lookup/', async (route) => {
    lookupBodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { source: 'gs1', candidate: { found: false } } })
  })

  const input = await openProductSearch(page)
  await expect.poll(() => localQueries).toContain('')

  for (const barcode of ['6970618571299', '6953787364626']) {
    await keyboardScan(page, input, barcode)
    await expect.poll(() => lookupBodies.some((body) => body.barcode === barcode)).toBe(true)
  }

  expect(localQueries).toEqual(expect.arrayContaining(['6970618571299', '6953787364626']))
  expect(lookupBodies).toEqual(expect.arrayContaining([
    { owner_id: 7, barcode: '6970618571299' },
    { owner_id: 7, barcode: '6953787364626' },
  ]))
  expect(runtimeErrors).toEqual([])
})

test('a scan during the initial empty search is not discarded or overwritten', async ({ page }) => {
  test.setTimeout(180_000)
  const barcode = '6953787364626'
  let releaseInitial
  let markInitialStarted
  const initialGate = new Promise((resolve) => { releaseInitial = resolve })
  const initialStarted = new Promise((resolve) => { markInitialStarted = resolve })

  await page.route('**/api/catalog/receive_products?**', async (route) => {
    const keyword = new URL(route.request().url()).searchParams.get('search')
    if (keyword === '') {
      markInitialStarted()
      await initialGate
      await route.fulfill({
        json: { count: 1, next: null, previous: null, results: [product(1, '初始旧商品', '')] },
      })
      return
    }
    await route.fulfill({
      json: { count: 1, next: null, previous: null, results: [product(2, '扫码命中商品', barcode)] },
    })
  })

  const input = await openProductSearch(page)
  await initialStarted
  await expect(input).toBeFocused()
  await page.keyboard.type(barcode, { delay: 0 })
  await expect(input).toHaveValue(barcode)
  await page.keyboard.press('Enter')

  await expect(page.getByText('扫码命中商品', { exact: true })).toBeVisible()
  await expect(input).toBeFocused()
  releaseInitial()
  await expect(page.getByTestId('product-search-loading')).toHaveCount(0)
  await expect(page.getByText('扫码命中商品', { exact: true })).toBeVisible()
  await expect(page.getByText('初始旧商品', { exact: true })).toHaveCount(0)
})

test('rapid consecutive scans keep only the newest response', async ({ page }) => {
  test.setTimeout(180_000)
  const firstBarcode = '6970618571299'
  const secondBarcode = '6953787364626'
  let releaseFirst
  let markFirstStarted
  const firstGate = new Promise((resolve) => { releaseFirst = resolve })
  const firstStarted = new Promise((resolve) => { markFirstStarted = resolve })

  await page.route('**/api/catalog/receive_products?**', async (route) => {
    const keyword = new URL(route.request().url()).searchParams.get('search')
    if (keyword === firstBarcode) {
      markFirstStarted()
      await firstGate
      await route.fulfill({
        json: { count: 1, next: null, previous: null, results: [product(11, '较早扫码商品', firstBarcode)] },
      })
      return
    }
    if (keyword === secondBarcode) {
      await route.fulfill({
        json: { count: 1, next: null, previous: null, results: [product(12, '最新扫码商品', secondBarcode)] },
      })
      return
    }
    await route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } })
  })

  const input = await openProductSearch(page)
  await keyboardScan(page, input, firstBarcode)
  await firstStarted
  await keyboardScan(page, input, secondBarcode)

  await expect(page.getByText('最新扫码商品', { exact: true })).toBeVisible()
  releaseFirst()
  await expect(page.getByTestId('product-search-loading')).toHaveCount(0)
  await expect(page.getByText('最新扫码商品', { exact: true })).toBeVisible()
  await expect(page.getByText('较早扫码商品', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: '/tmp/wmspda-keyboard-scanner-latest.png', fullPage: false })
})
