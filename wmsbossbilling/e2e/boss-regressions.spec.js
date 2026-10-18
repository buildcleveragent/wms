import { expect, test } from '@playwright/test'

const meta = { data_status: 'COMPLETE', generated_at: '2026-01-01T00:00:00Z' }
const historical = { warehouse: '11', owner: '', date_from: '2024-02-01', date_to: '2024-02-15' }
const contextPayload = {
  warehouse_options: [{ id: 11, name: '测试仓库A' }, { id: 22, name: '测试仓库B' }],
  owner_options: [{ id: 31, name: '测试货主' }],
}

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

async function mockBoss(page, { scope = historical, canExport = true, respond } = {}) {
  const requests = []
  const errors = []
  const consoleErrors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (['warning', 'error'].includes(message.type())) consoleErrors.push(message.text())
  })
  await page.addInitScript(({ scope, canExport }) => {
    localStorage.setItem('access', 'boss-browser-test')
    localStorage.setItem('user', JSON.stringify({ id: 991, username: '测试老板', capabilities: {
      can_export_operations: canExport, can_create_business_review_snapshot: true,
    } }))
    localStorage.setItem('boss_scope:991', JSON.stringify(scope))
  }, { scope, canExport })
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    requests.push(url)
    const custom = await respond?.(url, route)
    if (custom === 'handled') return
    let body = { meta, count: 0, results: [], next_page: null }
    const path = url.pathname
    if (path.endsWith('/context/')) body = contextPayload
    else if (path.endsWith('/home/')) body = {
      meta, summary: { sku_count: 12 }, rankings: { revenue_contribution_by_currency: [
        { currency: 'CNY', rows: [{ owner: 31, owner_name: '测试货主', total: '100.00' }] },
      ] },
    }
    else if (path.endsWith('/warehouse-overview/')) body = { meta, scope: {
      owner: Number(url.searchParams.get('owner')) || null,
      warehouse: Number(url.searchParams.get('warehouse')) || null,
    } }
    else if (path.endsWith('/revenue-assurance/')) body = { meta, sections: { unpriced_events: { count: 3 } } }
    else if (path.endsWith('/performance/')) body = { meta, selected_period_vs_prior: [
      { currency: 'CNY', subtotal: '987.65', prior_subtotal: '800.00', change_rate: 0.2 },
    ] }
    else if (/\/billing\/(bills|accruals)\/1\/$/.test(path)) body = { id: 1, owner: 31, warehouse: 11, currency: 'CNY', total: '123.45', lines: [] }
    else if (path.includes('/alerts/sections/') && path.endsWith('/1/')) body = { meta, detail: { order_no: 'QA-1', status: 'PENDING' } }
    await route.fulfill(custom || { json: body })
  })
  return { requests, errors, consoleErrors }
}

async function open(page, path) {
  await page.goto(`/#/pages/${path}`)
  await expect(page).toHaveURL(`/#/pages/${path}`)
  await expect(page).not.toHaveTitle('')
  await expect(page.locator('uni-page-body').last()).not.toBeEmpty()
}

// Controlled completions reproduce races without timing-dependent sleeps.
test('late success or failure cannot replace the selected cockpit tab', async ({ page }) => {
  const pending = deferred()
  const arrived = deferred()
  await mockBoss(page, { respond: async url => {
    if (url.pathname.endsWith('/receivables/')) {
      arrived.resolve()
      await pending.promise
      return { json: { meta, outstanding_by_currency: [{ currency: 'CNY', total: '123.45' }] } }
    }
  } })
  await open(page, 'cockpit/index')
  await expect(page.locator('.card .value')).toHaveText('3')
  await page.getByText('应收回款', { exact: true }).click()
  await arrived.promise
  await page.getByText('目标预测', { exact: true }).click()
  await expect(page.locator('.money')).toContainText('987.65')
  const finished = page.waitForResponse(response => response.url().includes('/receivables/'))
  pending.resolve()
  await finished
  await expect(page.locator('.money')).toContainText('987.65')
  await expect(page.getByText('正在加载...', { exact: true })).toHaveCount(0)
})

test('home ignores an old failed refresh after the latest one succeeds', async ({ page }) => {
  let count = 0
  const old = deferred(), arrived = deferred()
  await mockBoss(page, { respond: async url => {
    if (url.pathname.endsWith('/home/') && ++count === 2) {
      arrived.resolve()
      await old.promise
      return { status: 503, json: { detail: '旧请求失败' } }
    }
    if (url.pathname.endsWith('/home/') && count > 2) return { json: { meta, summary: { sku_count: 222 } } }
  } })
  await open(page, 'home/index')
  await expect(page.locator('.kpi-card').nth(1)).toContainText('12')
  await page.getByText('刷新', { exact: true }).click()
  await arrived.promise
  await page.getByText('刷新', { exact: true }).click()
  await expect(page.locator('.kpi-card').nth(1)).toContainText('222')
  const finished = page.waitForResponse(response => response.status() === 503)
  old.resolve()
  await finished
  await expect(page.locator('.kpi-card').nth(1)).toContainText('222')
  await expect(page.getByText('旧请求失败', { exact: true })).toHaveCount(0)
})

for (const warehouse of ['11', '']) {
  test(`home owner drilldown and return preserve warehouse ${warehouse || 'ALL'} and dates`, async ({ page }) => {
    const { requests } = await mockBoss(page, { scope: { ...historical, warehouse } })
    await open(page, 'home/index')
    await page.locator('.row-title').filter({ hasText: '测试货主' }).click()
    await expect(page).toHaveURL(/owner_detail/)
    await expect.poll(() => requests.filter(url => url.pathname.endsWith('/accruals/')).length).toBe(1)
    const detail = requests.find(url => url.pathname.endsWith('/warehouse-overview/'))
    expect(detail.searchParams.get('warehouse') || '').toBe(warehouse)
    expect(detail.searchParams.get('owner')).toBe('31')
    expect(detail.searchParams.get('date_from')).toBe(historical.date_from)
    expect(detail.searchParams.get('date_to')).toBe(historical.date_to)
    await page.getByText('返回总览', { exact: true }).click()
    await expect.poll(() => requests.filter(url => url.pathname.endsWith('/warehouse-overview/')).length).toBe(2)
    const returned = requests.filter(url => url.pathname.endsWith('/warehouse-overview/')).at(-1)
    expect(returned.searchParams.get('warehouse') || '').toBe(warehouse)
    expect(returned.searchParams.get('date_from')).toBe(historical.date_from)
    expect(returned.searchParams.get('date_to')).toBe(historical.date_to)
  })
}

test('explicit ALL warehouse in a URL overrides a cached single warehouse', async ({ page }) => {
  const { requests } = await mockBoss(page)
  await open(page, 'billing/overview?warehouse=&owner=31&date_from=2024-02-01&date_to=2024-02-15')
  await expect.poll(() => requests.some(url => url.pathname.endsWith('/warehouse-overview/'))).toBe(true)
  const query = requests.find(url => url.pathname.endsWith('/warehouse-overview/')).searchParams
  expect(query.has('warehouse')).toBe(false)
  await expect(page.locator('.scope-filter')).toContainText('全部授权仓库')
})

for (const count of [0, 20, 21, 25]) {
  test(`closed-loop alerts paginate ${count} records and reset after changing tabs`, async ({ page }) => {
    await mockBoss(page, { respond: url => {
      if (url.pathname.endsWith('/alert-cases/')) {
        const pageNumber = Number(url.searchParams.get('page'))
        expect(url.searchParams.get('page_size')).toBe('20')
        const start = (pageNumber - 1) * 20
        return { json: { meta, count, page: pageNumber, page_size: 20,
          results: Array.from({ length: Math.max(0, Math.min(20, count - start)) }, (_, i) => ({
            id: start + i + 1, title: `预警-${start + i + 1}`, severity: 'HIGH', status: 'OPEN',
          })),
        } }
      }
    } })
    await open(page, 'cockpit/index')
    await expect(page.locator('.card .value')).toHaveText('3')
    await page.getByText('预警闭环', { exact: true }).click()
    await expect(page.locator('.wide-card')).toHaveCount(Math.min(20, count))
    await expect(page.locator('.pager')).toContainText(`共 ${count} 条`)
    await expect(page.getByText('上一页', { exact: true })).toHaveAttribute('disabled', 'true')
    if (count > 20) {
      await page.getByText('下一页', { exact: true }).click()
      await expect(page.locator('.wide-card')).toHaveCount(count - 20)
      await expect(page.locator('.wide-card').first()).toContainText('预警-21')
      await expect(page.getByText('下一页', { exact: true })).toHaveAttribute('disabled', 'true')
      await page.getByText('收入保障', { exact: true }).click()
      await page.getByText('预警闭环', { exact: true }).click()
      await expect(page.locator('.pager')).toContainText('第 1 / 2 页')
    } else await expect(page.getByText('下一页', { exact: true })).toHaveAttribute('disabled', 'true')
    if (!count) await expect(page.getByText('当前范围暂无数据', { exact: true })).toBeVisible()
  })
}

test('failed second alert page retries page two without skipping', async ({ page }) => {
  let attempts = 0
  await mockBoss(page, { respond: url => {
    if (!url.pathname.endsWith('/alert-cases/')) return
    const number = Number(url.searchParams.get('page'))
    if (number === 2 && ++attempts === 1) return { status: 503, json: { detail: '分页暂不可用' } }
    return { json: { meta, count: 25, results: [{ id: number, title: `第${number}页预警` }] } }
  } })
  await open(page, 'cockpit/index')
  await expect(page.locator('.card .value')).toHaveText('3')
  await page.getByText('预警闭环', { exact: true }).click()
  await page.getByText('下一页', { exact: true }).click()
  await expect(page.getByText('分页暂不可用', { exact: true })).toBeVisible()
  await page.getByText('重试', { exact: true }).click()
  await expect(page.locator('.pager')).toContainText('第 2 / 2 页')
  await expect(page.getByText('第2页预警', { exact: true })).toBeVisible()
  expect(attempts).toBe(2)
})

test('warehouse context failure restores selection; success resets alert pagination', async ({ page }) => {
  let failed = false
  const pending = deferred(), arrived = deferred()
  const { requests, errors } = await mockBoss(page, { respond: async url => {
    if (url.pathname.endsWith('/context/') && url.searchParams.get('warehouse') === '22' && !failed) {
      failed = true
      arrived.resolve()
      await pending.promise
      return { status: 503, json: { detail: '切仓暂不可用' } }
    }
    if (url.pathname.endsWith('/alert-cases/')) return { json: { meta, count: 25,
      results: [{ id: 1, title: `仓库${url.searchParams.get('warehouse')}第${url.searchParams.get('page')}页` }],
    } }
  } })
  await open(page, 'cockpit/index')
  await expect(page.locator('.card .value')).toHaveText('3')
  await page.getByText('预警闭环', { exact: true }).click()
  await page.getByText('下一页', { exact: true }).click()
  await expect(page.locator('.pager')).toContainText('第 2 / 2 页')
  const warehousePicker = page.locator('.scope-filter uni-picker').first()
  async function selectB() {
    await warehousePicker.locator('.scope-field').click()
    await page.locator('.uni-picker-select .uni-picker-item').filter({ hasText: '测试仓库B' }).click()
  }
  await selectB()
  await arrived.promise
  await expect(warehousePicker.locator('.scope-field')).toContainText('测试仓库A')
  pending.resolve()
  await expect(page.getByText('切仓暂不可用', { exact: true })).toBeVisible()
  await expect(warehousePicker.locator('.scope-field')).toContainText('测试仓库A')
  await selectB()
  await expect(warehousePicker.locator('.scope-field')).toContainText('测试仓库B')
  await expect(page.locator('.pager')).toContainText('第 1 / 2 页')
  await expect(page.locator('.wide-card')).toContainText('仓库22第1页')
  expect(requests.filter(url => url.pathname.endsWith('/alert-cases/')).at(-1).searchParams.get('page')).toBe('1')
  expect(errors).toEqual([])
})

for (const failure of [403, 503, 'network']) {
  test(`context ${failure} shows an error and recovers after retry`, async ({ page }) => {
    let attempts = 0
    const { requests, errors } = await mockBoss(page, { respond: async (url, route) => {
      if (url.pathname.endsWith('/context/') && ++attempts === 1) {
        if (failure === 'network') { await route.abort('failed'); return 'handled' }
        return { status: failure, json: { detail: '范围加载失败' } }
      }
    } })
    await open(page, 'cockpit/index')
    await expect(page.getByText('重试', { exact: true })).toBeVisible()
    expect(requests.filter(url => url.pathname.endsWith('/revenue-assurance/'))).toHaveLength(0)
    await expect(page.getByText('当前范围暂无数据', { exact: true })).toHaveCount(0)
    await expect(page.getByText('导出当前报表', { exact: true })).toHaveAttribute('disabled', 'true')
    await expect(page.getByText('创建例会快照', { exact: true })).toHaveAttribute('disabled', 'true')
    await page.getByText('重试', { exact: true }).click()
    await expect(page.locator('.card .value')).toHaveText('3')
    await expect(page.getByText('重试', { exact: true })).toHaveCount(0)
    expect(attempts).toBe(2)
    expect(errors).toEqual([])
  })
}

test('export capability controls both buttons; revoked permission shows the server reason', async ({ page }) => {
  await mockBoss(page, { respond: url => {
    if (url.pathname.endsWith('/exports/')) return { status: 403, json: { detail: '没有导出权限' } }
  } })
  await open(page, 'cockpit/index')
  await expect(page.getByText('导出当前报表', { exact: true })).not.toHaveAttribute('disabled', 'true')
  await page.getByText('导出当前报表', { exact: true }).click()
  await expect(page.getByText('没有导出权限', { exact: true })).toBeVisible()
  await page.evaluate(() => {
    const user = JSON.parse(localStorage.getItem('user'))
    user.capabilities.can_export_operations = false
    localStorage.setItem('user', JSON.stringify(user))
  })
  // Keep the modified storage; init scripts run on a full reload, so navigate inside the app.
  await page.getByText('首页总览', { exact: true }).click()
  await page.getByText('经营驾驶舱', { exact: true }).click()
  await expect(page.getByText('导出当前报表', { exact: true })).toHaveCount(0)
  await page.getByText('运营履约', { exact: true }).click()
  await expect(page.getByText('导出运营明细', { exact: true })).toHaveCount(0)
})

test('inventory search replaces a pending page and keeps the new result', async ({ page }) => {
  const old = deferred(), arrived = deferred()
  await mockBoss(page, { respond: async url => {
    if (!url.pathname.endsWith('/inventory/details/')) return
    const search = url.searchParams.get('search')
    if (!search) { arrived.resolve(); await old.promise }
    return { json: { meta, count: 1, next_page: null, results: [{
      id: search ? 2 : 1, product_id: search ? 2 : 1,
      product_name: search ? '新商品' : '旧商品', base_unit: 'PCS', onhand_qty: '10',
    }] } }
  } })
  await open(page, 'inventory/detail')
  await arrived.promise
  await page.getByRole('searchbox').fill('新商品')
  await page.getByText('查询', { exact: true }).click()
  await expect(page.locator('.product-name')).toHaveText('新商品')
  const finished = page.waitForResponse(response => response.url().includes('/inventory/details/') && !response.url().includes('search='))
  old.resolve()
  await finished
  await expect(page.locator('.product-name')).toHaveText('新商品')
  await expect(page.getByText('正在加载...', { exact: true })).toHaveCount(0)
})

test('owner billing commits all responses from the latest refresh together', async ({ page }) => {
  const old = deferred(), arrived = deferred()
  let overviewCalls = 0, billCalls = 0
  await mockBoss(page, { respond: async url => {
    if (url.pathname.endsWith('/warehouse-overview/')) return { json: { meta,
      scope: { owner: 31 }, summary: { accrual_count: ++overviewCalls === 1 ? 111 : 222 },
    } }
    if (url.pathname.endsWith('/bills/') && ++billCalls === 1) {
      arrived.resolve(); await old.promise
      return { json: [{ id: 1, invoice_no: '旧账单', currency: 'CNY', total: '100' }] }
    }
  } })
  await open(page, 'billing/owner_detail?owner=31')
  await arrived.promise
  await page.getByText('刷新', { exact: true }).click()
  await expect(page.locator('.kpi-card').first()).toContainText('222')
  const finished = page.waitForResponse(response => response.url().includes('/bills/'))
  old.resolve()
  await finished
  await expect(page.locator('.kpi-card').first()).toContainText('222')
  await expect(page.getByText('旧账单', { exact: true })).toHaveCount(0)
})

for (const width of [390, 1440]) {
  test(`all 12 page routes render at width ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    const { errors, consoleErrors } = await mockBoss(page)
    const routes = ['login', 'home/index', 'inventory/index', 'alerts/index', 'cockpit/index',
      'billing/overview', 'billing/owner_detail?owner=31', 'inventory/detail',
      'alerts/section?section=review_differences',
      'alerts/detail?section=review_differences&item_type=review_difference&id=1',
      'billing/bill_detail?id=1', 'billing/accrual_detail?id=1']
    for (const route of routes) {
      await open(page, route)
      await expect(page.locator('uni-page-body').last()).toContainText(/仓储|计费|预警|收费|账单|Owner|Accrual|Bill/)
      await expect(page.locator('vite-error-overlay')).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      if (route === 'cockpit/index') {
        await expect(page.locator('.card .value')).toHaveText('3')
        await page.screenshot({ path: testInfo.outputPath(`cockpit-${width}.png`) })
      }
    }
    expect(errors).toEqual([])
    expect(consoleErrors).toEqual([])
  })
}
