import { test, expect } from '@playwright/test'

async function setup(page, { manager = true, failSave = false } = {}) {
  const calls = [], errors = [], writes = []
  let config = { enabled: false, interval_minutes: 5, state: 'disabled', can_manage: manager, last_finished_at: null, next_check_at: null }
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', e => { if (e.type() === 'error') errors.push(e.text()) })
  await page.addInitScript(() => {
    localStorage.setItem('access', 'refresh-test')
    localStorage.setItem('user', JSON.stringify({ id: 44, username: 'admin' }))
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url()); calls.push(url)
    let data = { count: 0, results: [], meta: {} }
    if (url.pathname.endsWith('/automation-settings/')) {
      if (route.request().method() === 'PATCH') {
        writes.push(route.request().postDataJSON())
        if (failSave) return route.fulfill({ status: 503, json: { detail: '检查设置保存失败' } })
        config = { ...config, ...writes.at(-1), state: writes.at(-1).enabled ? 'waiting' : 'disabled' }
      }
      data = config
    } else if (url.pathname.endsWith('/context/')) data = { business_today: '2026-11-03', warehouse_options: [], owner_options: [] }
    else if (url.pathname.endsWith('/today/')) data = { business_today: '2026-11-03', orders: { inbound: { count: 12 }, outbound: { count: 28 } }, financial: [], backlog: {} }
    else if (url.pathname.endsWith('/attention/')) data = { count: 0, results: [], automation: config, checks: [{ rule: 'DEADLINE_TASK', checked_at: null, stale: false }] }
    else if (url.pathname.endsWith('/digest/')) data = { first_visit: true, results: [] }
    await route.fulfill({ json: data })
  })
  return { calls, errors, writes }
}

test('defaults off, loads once and manual refresh works without background requests', async ({ page }) => {
  const state = await setup(page)
  await page.clock.install()
  await page.goto('/#/pages/home/index')
  await expect(page.locator('.order-card.outbound .order-number')).toHaveText('28 单', { timeout: 15000 })
  await expect(page.getByText('暂无已发现的重点事项，检查结果待更新。')).toBeVisible()
  await expect(page.getByText('自动检查已关闭', { exact: false })).toBeVisible()
  const count = () => state.calls.filter(url => url.pathname.endsWith('/today/')).length
  const initial = count()
  await page.clock.fastForward(3600000)
  expect(count()).toBe(initial)
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')) })
  await page.clock.fastForward(60000)
  expect(count()).toBe(initial)
  await page.getByText('立即刷新', { exact: true }).click()
  await expect.poll(count).toBe(initial + 1)
  await page.getByText('admin ▾', { exact: true }).click()
  await page.getByText('刷新与检查设置', { exact: true }).click()
  await expect(page.locator('.save-local')).toBeVisible()
  expect(state.errors).toEqual([])
})

for (const width of [390, 1440]) {
  test(`minute preferences and global setting round trip ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    const state = await setup(page)
    await page.goto('/#/pages/settings/refresh')
    await expect(page.locator('.save-global')).toBeVisible({ timeout: 15000 })
    await expect(page.locator('.interval')).toHaveCount(0)
    await page.locator('uni-switch').first().click()
    await page.getByText('3 分钟', { exact: true }).click()
    await page.locator('.save-local').click()
    await expect(page.getByText('已保存：每 3 分钟刷新', { exact: false })).toBeVisible()
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('boss_refresh:44')))).toEqual({ automatic: true, minutes: 3 })
    await page.locator('uni-switch').nth(1).click()
    await page.locator('.settings-card').nth(1).getByText('10 分钟', { exact: true }).click()
    await page.locator('.save-global').click()
    await expect(page.getByText('已保存：每 10 分钟检查')).toBeVisible()
    expect(state.writes).toEqual([{ enabled: true, interval_minutes: 10 }])
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/boss-refresh-settings-${width}.png`, fullPage: true })
    await page.reload()
    await expect(page.getByText('已保存：每 3 分钟刷新', { exact: false })).toBeVisible()
    await expect(page.getByText('已保存：每 10 分钟检查')).toBeVisible()
    expect(state.errors).toEqual([])
  })
}

test('ordinary boss cannot edit global settings and save failure does not look successful', async ({ page }) => {
  const state = await setup(page, { manager: false })
  await page.goto('/#/pages/settings/refresh')
  await expect(page.getByText('已保存：自动检查关闭')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('.save-global')).toHaveCount(0)
  await page.locator('uni-switch').nth(1).click()
  await expect(page.locator('.settings-card').nth(1).locator('.interval')).toHaveCount(0)
  expect(state.writes).toEqual([])
  expect(state.errors).toEqual([])
})

test('invalid minutes and rejected global save retain last confirmed settings', async ({ page }) => {
  const state = await setup(page, { failSave: true })
  await page.goto('/#/pages/settings/refresh')
  await expect(page.locator('.save-global')).toBeVisible({ timeout: 15000 })
  await page.locator('uni-switch').nth(1).click()
  const field = page.locator('.settings-card').nth(1).locator('input[type="number"]')
  await field.fill('0')
  await page.locator('.save-global').click()
  await expect(page.getByText('请输入 1～1440 的整数分钟', { exact: true })).toBeVisible()
  expect(state.writes).toEqual([])
  await field.fill('5')
  await page.locator('.save-global').click()
  await expect(page.getByText('保存结果未确认', { exact: false })).toBeVisible()
  await expect(page.locator('.save-global')).toHaveCount(0)
  await page.getByText('重新加载 ›', { exact: true }).click()
  await expect(page.getByText('已保存：自动检查关闭')).toBeVisible()
})
