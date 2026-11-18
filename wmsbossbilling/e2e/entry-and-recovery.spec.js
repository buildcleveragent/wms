import { expectHttpFailure } from '../../frontend/browser-diagnostics.mjs'
import { expect, test } from './browser-test.js'

async function authenticated(page, { automatic = false, minutes = 5, failDetail = false } = {}) {
  let failed = failDetail ? 503 : 0
  const requests = []
  await page.addInitScript(({ automatic, minutes }) => {
    localStorage.setItem('access', 'entry-test')
    localStorage.setItem('user', JSON.stringify({ id: 991, username: '测试老板' }))
    localStorage.setItem('boss_refresh:991', JSON.stringify({ automatic, minutes }))
  }, { automatic, minutes })
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname
    if (/\/(bills|accruals)\/1\/$/.test(path)) {
      requests.push(new URL(route.request().url()))
      if (failed) { expectHttpFailure(page, path, failed); return route.fulfill({ status: failed, json: { detail: '暂时无法读取账单' } }) }
      return route.fulfill({ json: { id: 1, invoice_no: '恢复后的账单', currency: 'CNY', total: '110.00', lines: [] } })
    }
    if (path.endsWith('/context/')) return route.fulfill({ json: {
      business_today: '2026-11-11', warehouse_options: [{ id: 11, name: '测试仓库' }], owner_options: [],
    } })
    if (path.endsWith('/automation-settings/')) return route.fulfill({ json: { enabled: false, interval_minutes: 5, can_manage: false } })
    return route.fulfill({ json: { count: 0, results: [], financial: [], meta: { data_status: 'COMPLETE' } } })
  })
  return { requests, recover: () => { failed = 0 }, fail: status => { failed = status } }
}

test('an authenticated cold start opens the dashboard', async ({ page }) => {
  await authenticated(page)
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('.home-today')).toBeVisible()
  await expect(page.locator('.section-heading').first()).toContainText('今日')
})

test('an unauthenticated cold start opens login', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page).toHaveURL(/pages\/login/)
  await expect(page.locator('.submit-btn')).toBeVisible()
})

test('an authenticated settings deep link stays on settings', async ({ page }) => {
  await authenticated(page)
  await page.goto('/#/pages/settings/refresh', { waitUntil: 'domcontentloaded' })
  await expect(page.getByText('页面自动刷新', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/pages\/settings\/refresh/)
})

for (const pageName of ['bill_detail', 'accrual_detail']) {
  test(`${pageName} loads its initial scope once`, async ({ page }) => {
    const state = await authenticated(page)
    await page.goto(`/#/pages/billing/${pageName}?id=1&owner=31&warehouse=11`, { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.headline-grid')).toBeVisible()
    expect(state.requests).toHaveLength(1)
    expect(state.requests[0].searchParams.get('owner')).toBe('31')
    expect(state.requests[0].searchParams.get('warehouse')).toBe('11')
  })

  test(`${pageName} offers a working manual retry while automatic refresh is off`, async ({ page }) => {
    const state = await authenticated(page, { failDetail: true })
    await page.goto(`/#/pages/billing/${pageName}?id=1`, { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('自动刷新已关闭', { exact: false })).toBeVisible()
    await expect(page.getByText('暂时无法读取账单', { exact: false })).toBeVisible()
    state.recover()
    await page.getByText('重新加载', { exact: true }).click()
    await expect(page.getByText('暂时无法读取账单', { exact: false })).toHaveCount(0)
    await expect(page.locator('.headline-grid')).toBeVisible()
  })
}

test('detail refresh hints use the saved interval', async ({ page }) => {
  await authenticated(page, { automatic: true, minutes: 17 })
  await page.goto('/#/pages/billing/bill_detail?id=1', { waitUntil: 'domcontentloaded' })
  await expect(page.getByText('每 17 分钟检查更新', { exact: false })).toBeVisible()
  await expect(page.getByText('约 30 秒检查更新', { exact: false })).toHaveCount(0)
})

test('transient detail failure keeps the last result but revoked access clears it', async ({ page }) => {
  const state = await authenticated(page)
  await page.goto('/#/pages/billing/bill_detail?id=1', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('.headline-grid')).toContainText('¥110.00')
  state.fail(503)
  await page.getByText('刷新', { exact: true }).click()
  await expect(page.getByText('重新加载', { exact: true })).toBeVisible()
  await expect(page.locator('.headline-grid')).toContainText('¥110.00')
  state.fail(403)
  await page.getByText('重新加载', { exact: true }).click()
  await expect(page.locator('.headline-grid')).toHaveCount(0)
})
