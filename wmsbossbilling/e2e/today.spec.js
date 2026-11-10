import { expect, test } from '@playwright/test'

async function setup(page) {
  const calls = [], errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  await page.addInitScript(() => {
    localStorage.setItem('boss_refresh:993', JSON.stringify({ automatic: true, minutes: 1 }))
    localStorage.setItem('access', 'today-test')
    localStorage.setItem('user', JSON.stringify({ id: 993, username: '老板' }))
    localStorage.setItem('boss_scope:993', JSON.stringify({ warehouse: '11', owner: '31', date_from: '2020-01-01', date_to: '2024-12-31' }))
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url()); calls.push(url)
    let data = { count: 0, results: [], meta: { data_status: 'COMPLETE' } }
    if (url.pathname.endsWith('/context/')) data = { business_today: '2026-11-02', warehouse_options: [{ id: 11, name: '一号仓' }], owner_options: [{ id: 31, name: '测试货主' }] }
    if (url.pathname.endsWith('/today/')) data = {
      business_today: '2026-11-02', scope: { date_to: '2026-11-02' }, meta: { generated_at: '2026-11-02T09:30:00' },
      orders: { inbound: { count: 12, submitted: 8, execution_records: 15, money: [] }, outbound: { count: 28, submitted: 19, execution_records: 35, money: [] } },
      financial: [{ key: 'pos_sales', count: 16, money: [], unknown_currency: true }, { key: 'pos_returns', count: 1, money: [], unknown_currency: true }, { key: 'accruals', money: [{ currency: 'CNY', amount: '3260.00' }] }, { key: 'receipts', money: [{ currency: 'CNY', amount: '8000.00' }] }],
      backlog: { inbound: 6, outbound: 9, overdue: 3 },
    }
    if (url.pathname.endsWith('/attention/')) data = { count: 8, checks: [{ rule: 'DEADLINE_TASK', checked_at: '2026-11-02T09:30:00', stale: false }], results: [1, 2, 3].map(id => ({ id, record_no: `PICK-00${id}`, title: '拣货任务超过计划完成时间', severity: id === 1 ? 'CRITICAL' : 'HIGH', overdue_seconds: 3600, assignee_name: '责任人详情不应在首页展开' })) }
    if (url.pathname.endsWith('/digest/')) data = { token: 'signed', first_visit: false, results: [{ key: 'CHECKOUT', label: 'POS 销售', count: 16, category: 'pos', action: 'CHECKOUT' }] }
    if (url.pathname.endsWith('/activities/')) data = { scope: { date_to: '2026-11-02' }, results: [1, 2, 3, 4, 5].map(id => ({ id, title: 'POS 销售完成', record_no: `POS-00${id}`, occurred_at: '2026-11-02T09:20:00', summary: '商品 A 库存 -2 件', target: { kind: 'pos', id } })) }
    if (url.pathname.endsWith('/today/details/')) data = { count: 1, results: [{ id: 1, title: 'OUT-TODAY-001', fields: [{ label: '本日实发', value: '5 件' }], drill: { type: 'today', metric: 'outbound', document_id: 1 } }] }
    await route.fulfill({ json: data })
  })
  return { calls, errors }
}
for (const width of [390, 1440]) {
  test(`today first viewport and exact drilldown ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    const state = await setup(page)
    await page.goto('/#/pages/home/index')
    await expect(page).not.toHaveTitle('')
    await expect(page.locator('.order-card.outbound .order-number')).toHaveText('28 单', { timeout: 15000 })
    await expect(page.locator('.scope-filter uni-picker').nth(1)).toContainText('全部货主')
    await expect(page.getByText('需要立即关注', { exact: true })).toBeInViewport()
    await expect(page.locator('.backlog')).toBeInViewport()
    await expect(page.locator('.nav-brand, .hero')).toHaveCount(0)
    await expect(page.locator('.focus-panel .item')).toHaveCount(3)
    await expect(page.locator('.digest-card')).toHaveCount(0)
    await expect(page.getByText('责任人详情不应在首页展开')).toHaveCount(0)
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/boss-today-${width}.png`, fullPage: false })
    expect(state.calls.some(url => /\/(home|periods)\//.test(url.pathname))).toBe(false)
    await page.locator('.order-card.outbound .order-number').click()
    await expect(page.getByText('OUT-TODAY-001')).toBeVisible()
    const query = state.calls.find(url => url.pathname.endsWith('/today/details/')).searchParams
    expect(Object.fromEntries(query)).toMatchObject({ date_from: '2026-11-02', date_to: '2026-11-02', warehouse: '11', metric: 'outbound' })
    expect(query.get('owner') || '').toBe('')
    const summaries = state.calls.filter(url => /\/(today|attention|digest|activities)\/$/.test(url.pathname))
    expect(summaries.length).toBeGreaterThanOrEqual(4)
    expect(summaries.every(url => !url.searchParams.get('owner'))).toBe(true)
    expect(state.errors).toEqual([])
  })
}
test('boss voluntarily filters an owner and details keep the same scope', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/#/pages/home/index')
  const picker = page.locator('.scope-filter uni-picker').nth(1)
  await expect(picker).toContainText('全部货主')
  await expect(page.locator('.order-card.outbound .order-number')).toHaveText('28 单', { timeout: 15000 })
  await picker.locator('.scope-field').click()
  await page.locator('.uni-picker-select .uni-picker-item').filter({ hasText: '测试货主' }).click()
  await expect(picker).toContainText('测试货主')
  for (const endpoint of ['today', 'attention', 'digest', 'activities']) {
    await expect.poll(() => state.calls.filter(url => url.pathname.endsWith(`/${endpoint}/`)).at(-1)?.searchParams.get('owner')).toBe('31')
  }
  await page.locator('.order-card.outbound .order-number').click()
  await expect(page.getByText('OUT-TODAY-001')).toBeVisible()
  expect(state.calls.find(url => url.pathname.endsWith('/today/details/')).searchParams.get('owner')).toBe('31')
  expect(state.errors).toEqual([])
})
test('today polling is light and stops while hidden', async ({ page }) => {
  const state = await setup(page)
  await page.clock.install()
  await page.goto('/#/pages/home/index')
  await expect(page.locator('.order-card.outbound .order-number')).toHaveText('28 单', { timeout: 15000 })
  const count = () => state.calls.filter(url => url.pathname.endsWith('/today/')).length
  const before = count()
  await page.clock.fastForward(61000)
  await expect.poll(count).toBe(before + 1)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')) })
  await page.clock.fastForward(120000)
  expect(count()).toBe(before + 1)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')) })
  await expect.poll(count).toBe(before + 2)
  expect(state.calls.some(url => /\/(home|periods)\//.test(url.pathname))).toBe(false)
  expect(state.errors).toEqual([])
})
