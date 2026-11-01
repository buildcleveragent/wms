import { test, expect as baseExpect } from '@playwright/test'

// Uni-app compiles newly visited routes on demand in the local dev server.
test.setTimeout(150_000)
const expect = baseExpect.configure({ timeout: 15_000 })

async function setup(page) {
  const errors = [], requests = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', message => {
    if (message.type() === 'error' && !message.text().includes('503')) errors.push(message.text())
  })
  const options = {
    owners: [{ id: 1, name: '示例货主' }], warehouses: [{ id: 1, name: '一号仓' }],
    processing_modes: [{ value: 'STANDARD', label: '标准出库' }, { value: 'WAREHOUSE_ASSISTED', label: '仓库代办出库' }],
    outbound_types: [{ value: 'SALES', label: '销售出库' }],
    submit_statuses: [{ value: 'SUBMITTED', label: '已提交' }],
    approval_statuses: [{ value: 'WHS_APPROVED', label: '仓库已确认' }],
    closed_statuses: [{ value: 'false', label: '未关闭' }, { value: 'true', label: '已关闭' }],
    operators: [{ id: 1, name: '仓库操作员' }], statuses: [{ value: 'COMPLETED', label: '已完成出库' }],
  }
  const row = (id) => ({ id, order_no: `CK-HISTORY-00${id}`, biz_date: '2026-10-20', owner_name: '示例货主', customer_name: '示例客户', warehouse_name: '一号仓', processing_mode_label: '标准出库', outbound_type_label: '销售出库', submit_status_name: '已提交', approval_status_name: '仓库已确认', is_closed: false, line_count: 2, total_base_qty: '12.500' })
  let failMore = false, denied = false
  await page.context().route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname, params = Object.fromEntries(url.searchParams)
    if (!path.startsWith('/api/')) return route.continue()
    const json = data => route.fulfill({ json: data })
    requests.push({ path, params })
    if (path.endsWith('/token/')) return json({ access: 'test-access', refresh: 'test-refresh' })
    if (path.endsWith('/profile/')) return json({ user: { id: 1, username: 'history' }, perms: ['outbound.view_outboundorder'], capabilities: { can_process_warehouse_assisted_outbound: !denied } })
    if (path.endsWith('/home-todos/')) return json({ items: [] })
    if (path.endsWith('/options/') || path.endsWith('/history-options/')) return json(options)
    if (path.endsWith('/stats/')) return json({
      period: { start_date: params.start_date, end_date: params.end_date }, summary: { order_count: 3, line_count: 6, total_base_qty: '37.5', completed_count: 3 },
      submit_rows: [{ value: 'SUBMITTED', label: '已提交', order_count: 3 }], approval_rows: [{ value: 'WHS_APPROVED', label: '仓库已确认', order_count: 3 }], closed_rows: [{ value: false, label: '未关闭', order_count: 3 }], status_rows: [{ status: 'COMPLETED', label: '已完成出库', order_count: 3 }],
      daily_rows: [{ date: params.start_date, order_count: 3, line_count: 6, total_base_qty: '37.5' }],
      owner_rows: [{ owner_id: 1, owner_name: '示例货主', order_count: 3, line_count: 6, total_base_qty: '37.5' }],
      operator_rows: [{ operator_id: 1, operator_name: '仓库操作员', order_count: 3 }],
      product_rows: [{ product_id: 1, base_uom_id: 1, product_name: '大米', product_display_name: '大米 · 示例品牌·新型号', product_spec: '5kg/袋', product_sku: 'SKU-001', order_count: 3, total_base_qty: '37.5', base_uom_name: '袋' }],
    })
    if (path.endsWith('/order-history/1/')) return json({ ...row(1), contact: '示例收件人', ship_to: '示例地址', amount_status: 'UNPRICED', lines: [{ id: 1, product_name: '旧名称大米', brand_model: '示例品牌·旧型号', product_spec: '5kg/袋', product_sku: 'SKU-001', base_qty: '12.500', base_uom_name: '袋', is_priced: false }] })
    if (path.endsWith('/order-history/')) {
      if (params.page === '2' && failMore) { failMore = false; return route.fulfill({ status: 503, json: { detail: '分页暂时失败' } }) }
      return json({ results: params.search === '无匹配' ? [] : params.page === '2' ? [row(3)] : [row(1), row(2)], count: params.search === '无匹配' ? 0 : 3, next: params.page === '2' || params.search === '无匹配' ? null : '?page=2' })
    }
    if (path.endsWith('/history/')) return json({ results: [{ id: 8, order_no: 'ASSIST-HISTORY-008', assisted_at: '2026-10-20T10:00:00Z', owner: { name: '示例货主' }, customer: { name: '示例客户' }, business_status: 'COMPLETED', business_status_label: '已完成出库', line_count: 2, total_base_qty: '10', can_reprint: false }], count: 1, next: null })
    return json({ results: [], count: 0, next: null })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('history')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  return { requests, errors, failNextPage: () => { failMore = true }, revoke: () => { denied = true } }
}

test('history entry, pagination retry, snapshot detail and empty search on PDA', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await setup(page)
  await page.getByText('出库历史', { exact: true }).click()
  await expect(page).toHaveURL(/pages\/orders\/index/)
  await expect(page).toHaveTitle('全部出库订单历史')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await expect(page.getByText('CK-HISTORY-001', { exact: true })).toBeVisible()
  state.failNextPage()
  await page.getByText('加载更多', { exact: true }).click()
  await expect(page.locator('.history-error')).toContainText('分页暂时失败')
  await expect(page.getByText('CK-HISTORY-001', { exact: true })).toBeVisible()
  await page.getByText('重试', { exact: true }).click()
  await expect(page.getByText('CK-HISTORY-003', { exact: true })).toBeVisible()
  await page.getByText('CK-HISTORY-001', { exact: true }).click()
  await expect(page.getByText('旧名称大米', { exact: true })).toBeVisible()
  await expect(page.getByText('品牌型号：示例品牌·旧型号', { exact: false })).toBeVisible()
  await expect(page.getByText('未定价', { exact: true })).toHaveCount(2)
  await page.screenshot({ path: '/tmp/wms-history-detail-mobile.png', fullPage: true })
  await page.goBack()
  await expect(page.getByText('CK-HISTORY-001', { exact: true })).toBeVisible()
  await page.locator('.history-input input').fill('无匹配')
  await page.getByText('查询', { exact: true }).click()
  await expect(page.getByText('暂无符合条件的订单')).toBeVisible()
  await page.getByText('重置', { exact: true }).click()
  await expect(page.getByText('CK-HISTORY-001', { exact: true })).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '/tmp/wms-history-list-mobile.png', fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(state.errors).toEqual([])
})

test('statistics drilldown retains successful query rather than unsent filters', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/#/pages/orders/stats?start_date=2026-10-01&end_date=2026-10-31&search=old&owner_id=1')
  await expect(page.getByText('大米 · 示例品牌·新型号', { exact: true })).toBeVisible()
  await page.locator('.history-input input').fill('unsent')
  await expect(page.getByText('筛选条件已修改，点击统计后生效；当前结果及历史跳转仍按上次统计条件。')).toBeVisible()
  await page.screenshot({ path: '/tmp/wms-history-stats-desktop.png', fullPage: true })
  await page.locator('.history-card').filter({ has: page.getByText('关闭状态分布', { exact: true }) }).getByText('未关闭', { exact: true }).click()
  await expect(page).toHaveURL(/pages\/orders\/index/)
  await expect(page).toHaveTitle('全部出库订单历史')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await expect(page.getByText('CK-HISTORY-001', { exact: true })).toBeVisible()
  expect(page.url()).toContain('search=old'); expect(page.url()).toContain('is_closed=false'); expect(page.url()).not.toContain('unsent')
  expect(state.requests.filter(x => x.path.endsWith('/order-history/')).at(-1).params).toMatchObject({ search: 'old', is_closed: 'false', owner_id: '1' })
  expect(state.errors).toEqual([])
})

test('assisted statistics links to filtered history and rechecks revoked permission', async ({ page }) => {
  const state = await setup(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/pages/outbound/assisted_stats?start_date=2026-10-01&end_date=2026-10-31&operator_id=1')
  await expect(page.getByText('代货主出库统计', { exact: true }).last()).toBeVisible()
  await expect(page.getByText('大米 · 示例品牌·新型号', { exact: true })).toBeVisible()
  await page.screenshot({ path: '/tmp/wms-assisted-stats-mobile.png', fullPage: true })
  await page.locator('.history-metric').filter({ hasText: '已完成' }).click()
  await expect(page.getByText('ASSIST-HISTORY-008', { exact: true })).toBeVisible()
  expect(state.requests.filter(x => x.path.endsWith('/history/')).at(-1).params).toMatchObject({ status: 'COMPLETED', operator_id: '1', start_date: '2026-10-01' })
  state.revoke(); await page.goBack()
  await expect(page.getByText('当前账号无代货主出库权限', { exact: false })).toBeVisible()
  await expect(page.getByText('大米 · 示例品牌·新型号', { exact: true })).toHaveCount(0)
  expect(state.errors).toEqual([])
})
