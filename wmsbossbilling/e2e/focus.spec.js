import { expect, test } from '@playwright/test'

async function setup(page) {
  const calls = [], errors = []
  let confirmed = false
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', e => { if (e.type() === 'error') errors.push(e.text()) })
  await page.addInitScript(() => {
    localStorage.setItem('boss_refresh:991', JSON.stringify({ automatic: true, minutes: 1 }))
    localStorage.setItem('access', 'focus-test')
    localStorage.setItem('user', JSON.stringify({ id: 991, username: '测试老板' }))
    localStorage.setItem('boss_scope:991', JSON.stringify({ warehouse: '11', owner: '', date_from: '2020-01-01', date_to: '2026-11-02', granularity: 'year', preset: 'custom' }))
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url()), path = url.pathname
    calls.push({ url, method: route.request().method() })
    let body = { count: 0, results: [], meta: { data_status: 'COMPLETE' } }
    if (path.endsWith('/context/')) body = { business_today: '2026-11-02', limits: { max_range_days: null }, warehouse_options: [{ id: 11, name: '一号仓' }], owner_options: [] }
    else if (path.endsWith('/home/')) body = { summary: {}, meta: { data_status: 'COMPLETE' } }
    else if (path.endsWith('/attention/')) body = { count: 1, checks: [{ rule: 'DEADLINE_TASK', checked_at: '2026-11-02T09:00:00', stale: false }], results: [{ id: 1, title: '任务超过计划完成时间', severity: 'HIGH', status: 'OPEN', record_no: 'PICK-001', assignee_name: '王主管', warehouse_name: '一号仓', business_due_at: '2026-11-02T08:00:00', overdue_seconds: 3600, latest_note: '正在安排人员处理' }] }
    else if (path.endsWith('/digest/')) {
      if (route.request().method() === 'POST') { confirmed = true; body = { confirmed_at: '2026-11-02T09:00:00' } }
      else body = { from: '2026-11-02T00:00:00', to: '2026-11-02T09:00:00', token: 'signed-window', first_visit: !confirmed, results: [{ key: 'CHECKOUT', category: 'pos', action: 'CHECKOUT', label: 'POS销售完成', count: confirmed ? 0 : 3 }] }
    } else if (path.endsWith('/digest/details/')) body = { results: [{ id: 'pos:1', title: 'POS 销售完成', record_no: 'POS-DIGEST-001', occurred_at: '2026-11-02T08:50:00', warehouse: { name: '一号仓' }, actor: { name: '收银员' }, summary: '商品库存 -2 件' }] }
    else if (path.endsWith('/periods/summary/')) body = { results: [{ key: 'orders', label: '出库正式提交', count: 12 }], comparisons: { previous: { date_from: '2019-01-01', date_to: '2019-12-31', metrics: [{ key: 'orders', label: '出库正式提交', count: 8 }] } } }
    else if (path.endsWith('/periods/')) body = { count: 7, granularity: url.searchParams.get('granularity'), results: [{ date: '2020-01-01', date_from: '2020-01-01', date_to: '2020-12-31', metrics: { orders: { count: 2 } } }] }
    else if (path.endsWith('/periods/details/')) body = { count: 1, results: [{ id: 'order:1', title: 'ORDER-2020-001', fields: [{ label: '时间', value: '2020-06-01' }] }] }
    await route.fulfill({ json: body })
  })
  return { calls, errors }
}
for (const width of [390, 1440]) {
  test(`focus summary remains pinned after explicit read ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    const state = await setup(page)
    await page.goto('/#/pages/home/index')
    await expect(page).not.toHaveTitle('')
    await expect(page.getByText('需要立即关注', { exact: true })).toBeVisible()
    await expect(page.getByText('责任人：王主管', { exact: false })).toHaveCount(0)
    await page.locator('.digest-toggle').click()
    expect(state.calls.filter(r => r.method === 'POST')).toHaveLength(0)
    await page.getByText('已查看本次摘要', { exact: true }).click()
    await expect(page.locator('.digest-card .count')).toHaveText('0 ›')
    await expect(page.getByText('PICK-001', { exact: false })).toBeVisible()
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: `/tmp/boss-focus-${width}.png`, fullPage: false })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(state.errors).toEqual([])
  })
}
test('multi-year bucket drills into exactly its date range', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/#/pages/cockpit/index?tab=periods')
  await page.locator('.bucket-cell').filter({ hasText: '出库正式提交 2' }).click()
  await expect(page.getByText('ORDER-2020-001', { exact: true })).toBeVisible()
  const request = state.calls.find(r => r.url.pathname.endsWith('/periods/details/')).url
  expect(request.searchParams.get('date_from')).toBe('2020-01-01')
  expect(request.searchParams.get('date_to')).toBe('2020-12-31')
  expect(request.searchParams.get('metric')).toBe('orders')
  expect(state.errors).toEqual([])
})
test('quick dates use server day and digest opens a fixed signed window', async ({ page }) => {
  const state = await setup(page)
  await page.goto('/#/pages/cockpit/index?tab=periods')
  await page.getByText('本年', { exact: true }).click()
  await expect.poll(() => state.calls.some(r => r.url.pathname.endsWith('/periods/summary/') && r.url.searchParams.get('date_from') === '2026-01-01' && r.url.searchParams.get('date_to') === '2026-11-02')).toBe(true)
  await page.goto('/#/pages/home/index')
  await page.locator('.digest-toggle').click()
  await page.locator('.digest-card').click()
  await expect(page.getByText('POS-DIGEST-001', { exact: true })).toBeVisible()
  const request = state.calls.find(r => r.url.pathname.endsWith('/digest/details/')).url
  expect(request.searchParams.get('token')).toBe('signed-window')
  expect(request.searchParams.get('action')).toBe('CHECKOUT')
  expect(state.errors).toEqual([])
})

test('paging a long daily trend does not recalculate the full summary', async ({ page }) => {
  const state = await setup(page)
  await page.route(url => url.pathname === '/api/reports/boss/periods/', async route => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get('page'))
    await route.fulfill({ json: { count: 201, granularity: 'day', results: [{ date: `2020-01-0${pageNumber}`, date_from: `2020-01-0${pageNumber}`, date_to: `2020-01-0${pageNumber}`, metrics: { orders: { count: pageNumber } } }] } })
  })
  await page.goto('/#/pages/cockpit/index?tab=periods')
  await expect(page.locator('.bucket-cell')).toContainText('出库正式提交 1')
  const before = state.calls.filter(r => r.url.pathname.endsWith('/periods/summary/')).length
  await page.locator('.period-panel .pager').getByText('下一页', { exact: true }).click()
  await expect(page.locator('.bucket-cell')).toContainText('出库正式提交 2')
  expect(state.calls.filter(r => r.url.pathname.endsWith('/periods/summary/'))).toHaveLength(before)
  expect(state.errors).toEqual([])
})

test('an attention case opens its original task without write actions', async ({ page }) => {
  const state = await setup(page)
  await page.route(url => url.pathname === '/api/reports/boss/alert-cases/1/', route => route.fulfill({ json: {
    case: { title: '超时任务', status: 'OPEN', assignee_name: '王主管', source_target: { kind: 'task_detail', id: 7 }, history: [{ id: 1, action: 'ASSIGN', note: '安排处理' }] }, history_count: 1,
  } }))
  await page.route(url => url.pathname === '/api/reports/boss/activities/task_detail/7/', route => route.fulfill({ json: {
    header: { title: 'PICK-ORIGINAL-007', fields: [{ label: '计划完成', value: '未设期限' }] }, sections: {}, notices: [],
  } }))
  await page.goto('/#/pages/home/index')
  await page.getByText('PICK-001', { exact: false }).click()
  await page.getByText('查看来源单据与作业进度', { exact: true }).click()
  await expect(page.getByText('PICK-ORIGINAL-007', { exact: true })).toBeVisible()
  expect(state.calls.filter(r => r.method === 'POST')).toHaveLength(0)
  expect(state.errors).toEqual([])
})

test('stalled deadline checks warn immediately while the user is reading', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 500 })
  const state = await setup(page)
  let stale = false
  await page.clock.install()
  await page.route(url => url.pathname === '/api/reports/boss/attention/', route => route.fulfill({ json: {
    count: 0, results: [], checks: [{ rule: 'DEADLINE_TASK', stale, checked_at: '2026-11-02T09:00:00' }],
  } }))
  await page.goto('/#/pages/home/index')
  await expect(page.getByText('当前没有需要置顶的事项。', { exact: true })).toBeVisible()
  await page.locator('.activity-panel').scrollIntoViewIfNeeded()
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(120)
  await page.clock.runFor(200)
  stale = true
  await page.clock.fastForward(61000)
  await expect(page.getByText('重点事项有更新，点击查看', { exact: true })).toBeVisible()
  await expect(page.getByText('超时检查尚未完成或已延迟，当前列表可能不完整。', { exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})
