import { expect, test } from '@playwright/test'

const scope = { warehouse: '11', owner: '31', date_from: '2024-02-01', date_to: '2024-02-15' }
const complete = { data_status: 'COMPLETE', warnings: [] }
async function prepare(page) {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  await page.addInitScript(scope => {
    localStorage.setItem('access', 'boss-identity-test')
    localStorage.setItem('user', JSON.stringify({ id: 991, username: '测试老板', capabilities: {} }))
    localStorage.setItem('boss_scope:991', JSON.stringify(scope))
  }, scope)
  return errors
}
for (const width of [390, 1280]) {
  test(`inventory identity, precision and search at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const errors = await prepare(page)
    let searches = []
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const url = new URL(route.request().url())
      let data = { meta: complete }
      if (url.pathname.endsWith('/context/')) data = {
        warehouse_options: [{ id: 11, name: '测试仓库' }], owner_options: [{ id: 31, name: '测试货主' }],
      }
      if (url.pathname.endsWith('/inventory/details/')) {
        const search = url.searchParams.get('search') || ''
        searches.push(search)
        const rows = ['橙味', '原味'].filter(name => !search || name === search).map((name, i) => ({
          id: i + 1, product_id: i + 1, warehouse_id: 11, owner_id: 31,
          product_name: '汽水', brand_model: `品牌甲·${name}`, product_display_name: `汽水 · 品牌甲·${name}`,
          product_code: `P-${i}`, product_spec: '480ml×12瓶', base_unit: 'EA', base_unit_name: '瓶', base_unit_source: 'VERIFIED',
          owner_name: '测试货主', warehouse_name: '测试仓库', location_code: 'A01',
          onhand_qty: '0.1234', available_qty: '0', allocated_qty: null, locked_qty: '0', damaged_qty: '0',
          // Formatted package text must not override the base-unit number.
          onhand_qty_display: '9箱', snapshot_date: '2024-02-15',
        }))
        data = { meta: complete, count: rows.length, results: rows, next_page: null }
      }
      await route.fulfill({ json: data })
    })
    await page.goto('/#/pages/inventory/detail')
    await expect(page).toHaveURL(/inventory\/detail/)
    await expect(page).not.toHaveTitle('')
    const active = page.locator('uni-page-body').last()
    await expect(active.getByText('汽水 · 品牌甲·橙味', { exact: true })).toBeVisible()
    await expect(active.getByText('汽水 · 品牌甲·原味', { exact: true })).toBeVisible()
    await expect(active.getByText('0.1234 瓶', { exact: true })).toHaveCount(2)
    await expect(active.getByText('9箱', { exact: true })).toHaveCount(0)
    await active.locator('input').fill('橙味')
    await active.getByText('查询', { exact: true }).click()
    await expect.poll(() => searches.at(-1)).toBe('橙味')
    await expect(active.getByText('汽水 · 品牌甲·原味', { exact: true })).toHaveCount(0)
    await expect(active.getByText('汽水 · 品牌甲·橙味', { exact: true })).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(errors).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`inventory-${width}.png`) })
    if (width === 390) {
      await active.locator('.table-scroll').evaluate(element => {
        const scroller = [...element.querySelectorAll('.uni-scroll-view')]
          .find(node => getComputedStyle(node).overflowX === 'auto')
        scroller.scrollLeft = scroller.scrollWidth
      })
      await expect(active.locator('.table-head').getByText('残次', { exact: true })).toBeInViewport()
      await page.screenshot({ path: testInfo.outputPath('inventory-390-scrolled.png') })
    }
  })
}

test('unavailable inventory does not render zero KPI cards', async ({ page }, testInfo) => {
  const errors = await prepare(page)
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const data = url.pathname.endsWith('/context/') ? {
      warehouse_options: [{ id: 11, name: '测试仓库' }], owner_options: [{ id: 31, name: '测试货主' }],
    } : {
      meta: { data_status: 'UNAVAILABLE', warnings: [{ code: 'HISTORICAL_INVENTORY_MISSING', count: 1 }] },
      summary: { sku_count: 0, quantity_by_uom: [], stale_sku_count_30d: 0 },
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/#/pages/inventory/index')
  const active = page.locator('uni-page-body').last()
  await expect(active.getByText('所选范围数据不可用', { exact: true })).toBeVisible()
  await expect(active.getByText('所选日期缺少库存快照（1）', { exact: true })).toBeVisible()
  await expect(active.locator('.kpi-card')).toHaveCount(0)
  expect(errors).toEqual([])
  await page.screenshot({ path: testInfo.outputPath('inventory-unavailable.png') })
})

test('cockpit shows scoped order counts and a genuine zero trend without mixing units', async ({ page }) => {
  const errors = await prepare(page)
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const data = url.pathname.endsWith('/context/') ? {
      warehouse_options: [{ id: 11, name: '测试仓库' }], owner_options: [{ id: 31, name: '测试货主' }],
    } : url.pathname.endsWith('/periods/summary/') ? { results: [
      { key: 'inbound', label: '实际收货单数', count: 2 },
      { key: 'outbound', label: '实际发运任务数', count: 1 },
    ] } : url.pathname.endsWith('/periods/') ? { count: 1, granularity: 'day', results: [
      { date: '2024-02-15', date_from: '2024-02-15', date_to: '2024-02-15', metrics: { inbound: { count: 0 }, outbound: { count: 1 } } },
    ] } : {
      meta: complete,
      summary: { today_inbound_orders: 2, today_outbound_orders: 1, today_inbound_qty: '999999', today_outbound_qty: '888888' },
      trend_7d: [{ date: '2024-02-15', inbound_orders: 0, outbound_orders: 1, inbound_qty: '999999', outbound_qty: '888888', accruals_by_currency: [] }],
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/#/pages/cockpit/index?tab=periods')
  const active = page.locator('uni-page-body').last()
  await expect(active.locator('.metric').filter({ hasText: '实际收货单数' })).toContainText('2')
  await expect(active.locator('.bucket-cell').filter({ hasText: '实际收货单数' })).toContainText('实际收货单数 0')
  await expect(active.locator('.bucket-cell').filter({ hasText: '实际发运任务数' })).toContainText('实际发运任务数 1')
  await expect(active).not.toContainText('999999')
  expect(errors).toEqual([])
})

test('overdue bill drilldown distinguishes unpaid balance from the original total', async ({ page }) => {
  const errors = await prepare(page)
  const bill = { id: 1, item_type: 'bill', invoice_no: 'PARTIAL-110', currency: 'CNY',
    total: '110', paid_amount: '40', outstanding_amount: '70', status: 'ISSUED' }
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname
    const data = path.endsWith('/context/') ? {
      warehouse_options: [{ id: 11, name: '测试仓库' }], owner_options: [{ id: 31, name: '测试货主' }],
    } : path.includes('/sections/') ? { meta: complete, detail: bill } : {
      meta: complete,
      summary: { section_count: 1, total_items: 1, high_risk_items: 1 },
      sections: { overdue_bills: { count: 1, shown_count: 1, items: [bill] } },
    }
    await route.fulfill({ json: data })
  })
  await page.goto('/#/pages/alerts/index')
  const active = () => page.locator('uni-page-body').last()
  await expect(active().locator('.row-side')).toHaveText('未收 ¥70.00')
  await active().getByText('PARTIAL-110', { exact: true }).click()
  await expect(page).toHaveURL(/alerts\/detail/)
  await expect(active().locator('.field').filter({ hasText: '账单总额' })).toContainText('¥110.00')
  await expect(active().locator('.field').filter({ hasText: '截至所选日期净收款' })).toContainText('¥40.00')
  await expect(active().locator('.field').filter({ hasText: '截至所选日期未收余额' })).toContainText('¥70.00')
  expect(errors).toEqual([])
})
