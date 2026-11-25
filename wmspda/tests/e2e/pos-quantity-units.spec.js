import { expect, test } from '@playwright/test'

test.use({ baseURL: 'http://localhost:5173' })
test.beforeEach(async ({ page }) => {
  page.setDefaultTimeout(15000)
  page.setDefaultNavigationTimeout(45000)
  await page.route('https://cn-shanghai-aliyun-cloudauth.oss-cn-shanghai.aliyuncs.com/**', route =>
    route.fulfill({ contentType: 'application/javascript', body: '' }))
})
const units = [
  { base_unit_id: 1, base_unit: '袋', completed_qty: '20.000', net_qty: '18.000' },
  { base_unit_id: 2, base_unit: '千克', completed_qty: '12.500', net_qty: '-0.500' },
  { base_unit_id: 3, base_unit: '件', completed_qty: '0.000', net_qty: '0.000' },
]
const report = {
  summary: { net_amount: '123.45', completed_qty: '32.500', net_qty: '17.500', quantities_by_unit: units },
  products: units.map((unit, i) => ({ ...unit, product_id: i + 1, owner_id: 1, product_name: `验收商品${i}`, net_amount: '12.00' })),
  owners: [{ owner_id: 1, owner_name: '验收货主', net_amount: '123.45', net_qty: '17.500', quantities_by_unit: units }],
  payments: [], cashiers: [],
}
const sale = {
  id: 1, sale_no: 'POS-UNIT-001', status: 'COMPLETED', total_amount: '15.00', created_at: '2026-11-12T10:00:00',
  lines: [
    { id: 1, line_no: 1, product_id: 1, product_name: '历史袋装商品', base_unit_id: 1, base_unit: '袋', qty: '2.000', price: '5', amount: '10', returnable_qty: '2.000' },
    { id: 2, line_no: 2, product_id: 2, product_name: '历史称重商品', base_unit_id: 2, base_unit: '千克', qty: '1.250', price: '4', amount: '5', returnable_qty: '1.250' },
  ],
}
sale.receipt = { sale_no: sale.sale_no, total_amount: sale.total_amount, lines: sale.lines, payment: { amount_received: '15.00' } }
function errorsOf(page) {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (['error', 'warning'].includes(message.type())) errors.push(message.text()) })
  return errors
}

test('report renders actual units, grouped totals and legacy/empty states after refresh', async ({ page }) => {
  const errors = errorsOf(page), calls = []
  let payload = report
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname === '/api/pos/stats/') {
      calls.push(url.searchParams)
      return route.fulfill({ json: payload })
    }
    return route.fulfill({ json: url.pathname === '/api/pos/sales/' ? { count: 1, results: [sale] } : {} })
  })
  await page.goto('/#/pages/pos/report', { waitUntil: 'domcontentloaded' })
  await expect(page).toHaveURL(/pages\/pos\/report/)
  await expect(page).toHaveTitle(/POS销售报表/)
  await expect(page.getByTestId('pos-product-ranking')).toContainText('18 袋')
  await expect(page.getByTestId('pos-product-ranking')).toContainText('-0.5 千克')
  await expect(page.getByTestId('pos-product-ranking')).toContainText('0 件')
  await expect(page.getByTestId('pos-owner-summary')).toContainText('18 袋、-0.5 千克、0 件')
  await expect(page.locator('.sale-lines')).toContainText('数量：1.25 千克')
  await expect(page.locator('.summary-grid')).toContainText('18 袋、-0.5 千克、0 件')
  await page.getByText('月度', { exact: true }).click()
  await expect.poll(() => calls.length).toBeGreaterThan(1)
  await expect(page.locator('.subtitle')).toContainText('月度')
  await page.getByText('时间段', { exact: true }).click()
  await expect(page.locator('.subtitle')).toContainText('至')
  await page.screenshot({ path: '/tmp/pos-units-report-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByTestId('pos-owner-summary')).toContainText('18 袋、-0.5 千克、0 件')
  const bounds = await page.getByTestId('pos-owner-summary').boundingBox()
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(391)
  await page.screenshot({ path: '/tmp/pos-units-report-mobile.png', fullPage: true })
  payload = { ...report, summary: {}, owners: [{ owner_id: 1, net_qty: '9' }], products: [{ product_id: 9, net_qty: '9' }] }
  await page.locator('.header-btn').click()
  await expect(page.getByTestId('pos-owner-summary')).toContainText('单位明细不可用')
  await expect(page.getByTestId('pos-product-ranking')).toContainText('9 单位未设置')
  payload = { summary: { quantities_by_unit: [] }, products: [], owners: [] }
  await page.locator('.header-btn').click()
  await expect(page.getByTestId('pos-product-ranking')).toContainText('暂无商品数据')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('cart, packaged checkout, return and reprint keep the correct basic units', async ({ page }) => {
  const errors = errorsOf(page)
  let checkoutPayload
  const product = { id: 3, code: 'CURRENT-BOTTLE', name: '当前瓶装商品', price: '1', available_qty: '100',
    base_unit: { id: 3, code: 'BOT', name: '瓶' }, unit_options: [
      { kind: 'base', label: '瓶', multiplier: '1' },
      { kind: 'package', label: '箱', multiplier: '12', package_id: 1 },
    ] }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path === '/api/pos/checkout/') {
      checkoutPayload = route.request().postDataJSON()
      return route.fulfill({ json: { sale: { id: 2, sale_no: 'POS-UNIT-002' }, receipt: {
        sale_no: 'POS-UNIT-002', total_amount: '12.00', payment: { amount_received: '12.00' },
        lines: [{ product_id: 3, name: '成交瓶装商品', base_unit_id: 3, unit: '瓶', qty: '12.000', price: '1', amount: '12' }],
      } } })
    }
    const matches = new URL(route.request().url()).searchParams.has('barcode') ? [] : [product]
    const data = path === '/api/pos/products/' ? { results: matches, count: matches.length }
      : path === '/api/pos/sales/' ? { results: [sale], count: 1 }
      : path === '/api/pos/sales/1/' ? sale
      : path === '/api/pos/shifts/current/' ? { shift: { id: 1, status: 'OPEN', shift_no: 'UNIT-SHIFT', cashier_id: 7, warehouse_id: 3, summary: {} } } : {}
    await route.fulfill({ json: data })
  })
  await page.goto('/#/pages/pos/index', { waitUntil: 'domcontentloaded' })
  await page.evaluate(() => {
    window.open = () => ({ closed: false, focus() {}, print() {}, document: {
      open() {}, close() {}, write(html) { window.__posPrintHtml = html },
    } })
  })
  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill('当前')
  await input.press('Enter')
  await expect(page.getByTestId('pos-product-row')).toContainText('100 瓶')
  await page.getByTestId('pos-product-row').locator('.add-btn').click()
  await expect(page.locator('.summary-sub')).toContainText('基本数量 1 瓶')
  await page.locator('.unit-picker').click()
  await page.getByText('箱', { exact: true }).last().click()
  await expect(page.locator('.summary-sub')).toContainText('基本数量 12 瓶')
  await page.getByText('重打', { exact: true }).first().click()
  await expect.poll(() => page.evaluate(() => window.__posPrintHtml || '')).toContain('2 袋、1.25 千克')
  const html = await page.evaluate(() => window.__posPrintHtml)
  expect(html).toContain('历史称重商品')
  expect(html).not.toContain('当前瓶装商品')
  await page.evaluate(html => {
    const frame = document.createElement('iframe')
    frame.id = 'pos-print-proof'
    frame.style.cssText = 'width:100%;height:800px;background:white'
    frame.srcdoc = html
    document.body.prepend(frame)
  }, html)
  await expect(page.frameLocator('#pos-print-proof').locator('body')).toContainText('2 袋、1.25 千克')
  await page.locator('#pos-print-proof').screenshot({ path: '/tmp/pos-units-print.png' })
  await page.getByText('退货', { exact: true }).first().click()
  await expect(page.locator('.return-qty-input').nth(1)).toContainText('退货数量（千克）')
  await page.getByText('取消', { exact: true }).click()
  await page.getByTestId('pos-checkout').click()
  await expect.poll(() => checkoutPayload?.items?.[0]?.qty).toBe('12')
  await expect.poll(() => page.evaluate(() => window.__posPrintHtml || '')).toContain('成交瓶装商品')
  const firstPrint = await page.evaluate(() => window.__posPrintHtml)
  expect(firstPrint).toContain('12 瓶')
  expect(firstPrint).not.toContain('历史称重商品')
  await expect(page.getByTestId('pos-cart-product')).toHaveCount(0)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
})
