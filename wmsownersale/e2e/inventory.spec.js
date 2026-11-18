import { test as base, expect } from '@playwright/test'
import { instrumentBrowserTests } from '../../frontend/browser-diagnostics.mjs'
const test = instrumentBrowserTests(base, expect)

for (const width of [390, 1280]) {
  test(`inventory scroll, exact quantities and pagination at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const capabilities = { can_view_owner_inventory: true, can_export_owner_inventory: false }
    await page.addInitScript(capabilities => {
      localStorage.setItem('access', 'owner-layout-test')
      localStorage.setItem('user', JSON.stringify({ type: 'object', data: { id: 1, owner_id: 2, roles: ['owner_manager'], capabilities } }))
    }, capabilities)
    const requests = []
    await page.route('**/api/**', route => {
      const url = new URL(route.request().url())
      if (url.pathname === '/api/inventory/summary/') {
        requests.push(Object.fromEntries(url.searchParams))
        const current = Number(url.searchParams.get('page') || 1)
        return route.fulfill({ json: {
          count: 52, next: current === 1 ? '?page=2' : null,
          results: Array.from({ length: current === 1 ? 50 : 2 }, (_, index) => ({
            id: (current - 1) * 50 + index + 1,
            product_name: `合成商品${(current - 1) * 50 + index + 1}`,
            product_code: `CODE-${index}`, product_sku: `SKU-${index}`, base_unit_name: '瓶',
            onhand_qty: '99999999999999.9999', available_qty: '12345678901234.5678',
            allocated_qty: '0.0001', locked_qty: '12.5000', damaged_qty: '0.0000',
          })),
        } })
      }
      if (url.pathname.endsWith('/profile/')) return route.fulfill({ json: {
        user: { id: 1, owner_id: 2, roles: ['owner_manager'] }, capabilities,
      } })
      return route.fulfill({ json: { count: 0, results: [] } })
    })
    await page.goto('/#/pages/inventory/index', { waitUntil: 'domcontentloaded' })
    await expect(page).toHaveURL(/pages\/inventory\/index/)
    await expect(page).not.toHaveTitle('')
    await expect(page.locator('.table-row')).toHaveCount(50)
    await expect(page.locator('.table-row').first()).toContainText('99999999999999.9999')
    await expect(page.locator('.table-row').first()).toContainText('12345678901234.5678')
    const dimensions = await page.locator('.table-scroll').evaluate(element => {
      const scroller = [...element.querySelectorAll('.uni-scroll-view')]
        .find(node => getComputedStyle(node).overflowX === 'auto')
      return { scrollWidth: scroller.scrollWidth, clientWidth: scroller.clientWidth }
    })
    // A desktop can fit the whole table; the narrow viewport must scroll.
    if (width === 390) expect(dimensions.scrollWidth).toBeGreaterThan(dimensions.clientWidth)
    else expect(dimensions.scrollWidth).toBeGreaterThanOrEqual(dimensions.clientWidth)
    const headerWidths = await page.locator('.table-header .cell').evaluateAll(cells => cells.map(cell => cell.getBoundingClientRect().width))
    const rowWidths = await page.locator('.table-row').first().locator('.cell').evaluateAll(cells => cells.map(cell => cell.getBoundingClientRect().width))
    expect(rowWidths).toEqual(headerWidths)
    await page.locator('.table-scroll').evaluate(element => {
      const scroller = [...element.querySelectorAll('.uni-scroll-view')]
        .find(node => getComputedStyle(node).overflowX === 'auto')
      scroller.scrollLeft = scroller.scrollWidth
    })
    await expect(page.locator('.table-header .cell').last()).toBeInViewport()
    await page.screenshot({ path: testInfo.outputPath(`inventory-${width}-scrolled.png`) })
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    await expect(page.locator('.table-row')).toHaveCount(52)
    await expect(page.locator('.loaded-count')).toHaveText('已加载 52 / 总计 52')
    expect(requests.map(request => request.page)).toEqual(['1', '2'])
    expect(requests.every(request => request.page_size === '50')).toBe(true)
    await page.evaluate(() => window.scrollTo(0, 0))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    await page.screenshot({ path: testInfo.outputPath(`inventory-${width}.png`) })
  })
}
