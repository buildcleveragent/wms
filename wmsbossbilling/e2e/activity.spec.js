import { expect, test } from '@playwright/test'

const context = { business_today: '2026-11-02', warehouse_options: [{ id: 11, name: '一号仓' }], owner_options: [{ id: 31, name: '货主甲' }] }
const meta = { data_status: 'COMPLETE', generated_at: '2026-11-02T09:00:00+08:00' }
const event = { id: 'pos:1', title: 'POS 销售完成', record_no: 'POS-20261102-001',
  occurred_at: '2026-11-02T09:00:00+08:00', summary: '苏打水 -2 件',
  warehouse: { id: 11, name: '一号仓' }, owner: null, actor: { id: 6, name: '收银员甲' }, target: { kind: 'pos', id: 1 } }

async function setup(page) {
  const errors = []
  const calls = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  await page.addInitScript(() => {
    localStorage.setItem('boss_refresh:991', JSON.stringify({ automatic: true, minutes: 1 }))
    localStorage.setItem('access', 'activity-test')
    localStorage.setItem('user', JSON.stringify({ id: 991, username: '测试老板' }))
    localStorage.setItem('boss_scope:991', JSON.stringify({ warehouse: '11', owner: '', date_from: '2026-10-01', date_to: '2026-10-31' }))
  })
  let revision = 1
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    calls.push(url)
    let body = { meta, count: 0, results: [] }
    if (url.pathname.endsWith('/context/')) body = context
    else if (url.pathname.endsWith('/home/')) body = { meta, summary: { sku_count: 6 } }
    else if (url.pathname.endsWith('/activities/')) body = { meta, results: [{ ...event, id: `pos:${revision}`, record_no: `POS-20261102-00${revision}` }], next_cursor: null }
    else if (url.pathname.includes('/activities/pos/')) body = { meta, header: { ...event, fields: [{ label: '当前筛选商品金额', value: '20.00' }] },
      notices: ['本次增减与当前库存分别展示'], sections: { inventory: { label: '本次活动实际库存流水', count: 1, page: 1, page_size: 20,
        results: [{ id: 1, title: '苏打水', fields: [{ label: '本次增减', value: '-2.0000' }, { label: '当前该库位批次库存', value: '8.0000' }] }] } } }
    else if (url.pathname.endsWith('/revenue-assurance/')) body = { meta, sections: { unpriced_events: { count: 23 } } }
    else if (url.pathname.includes('/revenue-assurance/sections/')) body = { meta, count: 23, page: Number(url.searchParams.get('page')), page_size: 20,
      results: [{ id: url.searchParams.get('page'), title: `计费记录第 ${url.searchParams.get('page')} 页`, fields: [{ label: '原因', value: '尚未匹配规则' }] }] }
    await route.fulfill({ json: body })
  })
  return { errors, calls, addEvent: () => revision++ }
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`homepage important activity drills into real-stock fields ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    const state = await setup(page)
    await page.goto('/#/pages/home/index')
    await expect(page).not.toHaveTitle('')
    await expect(page.getByText('最新重要动态', { exact: true })).toBeVisible()
    await expect(page.getByText('POS-20261102-001', { exact: true })).toBeVisible()
    const query = state.calls.find(url => url.pathname.endsWith('/activities/'))
    expect(query.searchParams.get('priority')).toBe('important')
    expect(query.searchParams.get('limit')).toBe('5')
    expect(query.searchParams.has('date_from')).toBe(false)
    await page.screenshot({ path: `/tmp/boss-activities-${viewport.width}.png`, fullPage: false })
    await page.getByText('POS-20261102-001', { exact: true }).click()
    await expect(page).toHaveURL(/pages\/activity\/detail/)
    await expect(page.getByText('本次活动实际库存流水 · 1 条')).toBeVisible()
    await expect(page.getByText('-2.0000', { exact: true })).toBeVisible()
    await expect(page.getByText('8.0000', { exact: true })).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(state.errors).toEqual([])
  })
}

test('foreground polls at the selected one minute interval and displays new activity without recomputing home', async ({ page }) => {
  const state = await setup(page)
  await page.clock.install()
  await page.goto('/#/pages/home/index')
  await expect(page.getByText('POS-20261102-001', { exact: true })).toBeVisible()
  const homeCalls = state.calls.filter(url => url.pathname.endsWith('/home/')).length
  state.addEvent()
  await page.clock.fastForward(61000)
  await expect(page.getByText('POS-20261102-002', { exact: true })).toBeVisible()
  expect(state.calls.filter(url => url.pathname.endsWith('/home/'))).toHaveLength(homeCalls)
  expect(state.errors).toEqual([])
})

test('assurance card opens a paginated full list', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/#/pages/cockpit/index')
  await page.locator('.card').filter({ hasText: '23' }).click()
  await expect(page).toHaveURL(/pages\/cockpit\/details/)
  await expect(page.getByText('计费记录第 1 页', { exact: true })).toBeVisible()
  await page.getByText('下一页', { exact: true }).click()
  await expect(page.getByText('计费记录第 2 页', { exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})

test('leaving the home page pauses its feed and a hidden tab pauses detail polling', async ({ page }) => {
  const state = await setup(page)
  await page.clock.install()
  await page.goto('/#/pages/home/index')
  await expect(page.getByText('POS-20261102-001', { exact: true })).toBeVisible()
  await page.getByText('POS-20261102-001', { exact: true }).click()
  await expect(page.getByText('本次活动实际库存流水 · 1 条')).toBeVisible()
  const homeCalls = state.calls.filter(url => url.pathname.endsWith('/activities/')).length
  await page.clock.fastForward(61000)
  expect(state.calls.filter(url => url.pathname.endsWith('/activities/'))).toHaveLength(homeCalls)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const detailCalls = state.calls.filter(url => url.pathname.includes('/activities/pos/')).length
  await page.clock.fastForward(120000)
  expect(state.calls.filter(url => url.pathname.includes('/activities/pos/'))).toHaveLength(detailCalls)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect.poll(() => state.calls.filter(url => url.pathname.includes('/activities/pos/')).length).toBe(detailCalls + 1)
  expect(state.errors).toEqual([])
})

test('bill detail retains the reading state until the user accepts a polled update', async ({ page }) => {
  const state = await setup(page)
  let revision = 1
  await page.route(url => url.pathname === '/api/billing/bills/7/', route => route.fulfill({ json: {
    id: 7, invoice_no: `ACT-BILL-${revision}`, currency: 'CNY', total: '20.00', subtotal: '20.00',
    tax_total: '0.00', status: 'ISSUED', owner: 31, warehouse: 11, lines: [],
  } }))
  await page.clock.install()
  await page.goto('/#/pages/billing/bill_detail?id=7')
  await expect(page.getByText('ACT-BILL-1', { exact: true })).toBeVisible()
  revision++
  await page.clock.fastForward(61000)
  await expect(page.getByText('账单有更新，点击查看最新内容', { exact: true })).toBeVisible()
  await expect(page.getByText('ACT-BILL-1', { exact: true })).toBeVisible()
  await page.getByText('账单有更新，点击查看最新内容', { exact: true }).click()
  await expect(page.getByText('ACT-BILL-2', { exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})

test('view all starts on the business day without changing the financial reporting dates', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/#/pages/home/index')
  await expect(page.getByText('POS-20261102-001', { exact: true })).toBeVisible()
  await page.getByText('查看全部', { exact: true }).click()
  await expect(page).toHaveURL(/pages\/activity\/index/)
  // uni-app renders the placeholder as a sibling element, not an input attribute.
  await expect(page.getByText('单号或商品', { exact: true })).toBeVisible()
  await expect.poll(() => state.calls.filter(url => url.searchParams.get('priority') === 'all').length).toBeGreaterThan(0)
  const query = state.calls.find(url => url.searchParams.get('priority') === 'all')
  expect(query.searchParams.get('date_from')).toBe('2026-11-02')
  expect(query.searchParams.get('limit')).toBe('20')
  await page.locator('.activity-filters input').first().fill('POS-20261102')
  await page.getByText('查询', { exact: true }).click()
  await expect.poll(() => state.calls.some(url => url.searchParams.get('search') === 'POS-20261102')).toBe(true)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('boss_scope:991')).date_from)).toBe('2026-10-01')
  expect(state.errors).toEqual([])
})
