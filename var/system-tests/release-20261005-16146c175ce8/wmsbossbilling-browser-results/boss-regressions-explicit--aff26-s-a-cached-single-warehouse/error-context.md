# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: boss-regressions.spec.js >> explicit ALL warehouse in a URL overrides a cached single warehouse
- Location: e2e/boss-regressions.spec.js:135:5

# Error details

```
Error: expect(locator).not.toBeEmpty() failed

Locator: locator('uni-page-body').last()
Expected: not empty
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "not toBeEmpty" with timeout 5000ms
  - waiting for locator('uni-page-body').last()

```

# Test source

```ts
  1   | import { expect, test } from '../.release-network-fixture.js'
  2   | 
  3   | const meta = { data_status: 'COMPLETE', generated_at: '2026-01-01T00:00:00Z' }
  4   | const historical = { warehouse: '11', owner: '', date_from: '2024-02-01', date_to: '2024-02-15' }
  5   | const contextPayload = {
  6   |   warehouse_options: [{ id: 11, name: '测试仓库A' }, { id: 22, name: '测试仓库B' }],
  7   |   owner_options: [{ id: 31, name: '测试货主' }],
  8   | }
  9   | 
  10  | function deferred() {
  11  |   let resolve
  12  |   const promise = new Promise(done => { resolve = done })
  13  |   return { promise, resolve }
  14  | }
  15  | 
  16  | async function mockBoss(page, { scope = historical, canExport = true, respond } = {}) {
  17  |   const requests = []
  18  |   const errors = []
  19  |   const consoleErrors = []
  20  |   page.on('pageerror', error => errors.push(error.message))
  21  |   page.on('console', message => {
  22  |     if (['warning', 'error'].includes(message.type())) consoleErrors.push(message.text())
  23  |   })
  24  |   await page.addInitScript(({ scope, canExport }) => {
  25  |     localStorage.setItem('access', 'boss-browser-test')
  26  |     localStorage.setItem('user', JSON.stringify({ id: 991, username: '测试老板', capabilities: {
  27  |       can_export_operations: canExport, can_create_business_review_snapshot: true,
  28  |     } }))
  29  |     localStorage.setItem('boss_scope:991', JSON.stringify(scope))
  30  |   }, { scope, canExport })
  31  |   await page.route('**/api/**', async route => {
  32  |     const url = new URL(route.request().url())
  33  |     requests.push(url)
  34  |     const custom = await respond?.(url, route)
  35  |     if (custom === 'handled') return
  36  |     let body = { meta, count: 0, results: [], next_page: null }
  37  |     const path = url.pathname
  38  |     if (path.endsWith('/context/')) body = contextPayload
  39  |     else if (path.endsWith('/home/')) body = {
  40  |       meta, summary: { sku_count: 12 }, rankings: { revenue_contribution_by_currency: [
  41  |         { currency: 'CNY', rows: [{ owner: 31, owner_name: '测试货主', total: '100.00' }] },
  42  |       ] },
  43  |     }
  44  |     else if (path.endsWith('/warehouse-overview/')) body = { meta, scope: {
  45  |       owner: Number(url.searchParams.get('owner')) || null,
  46  |       warehouse: Number(url.searchParams.get('warehouse')) || null,
  47  |     } }
  48  |     else if (path.endsWith('/revenue-assurance/')) body = { meta, sections: { unpriced_events: { count: 3 } } }
  49  |     else if (path.endsWith('/performance/')) body = { meta, selected_period_vs_prior: [
  50  |       { currency: 'CNY', subtotal: '987.65', prior_subtotal: '800.00', change_rate: 0.2 },
  51  |     ] }
  52  |     else if (/\/billing\/(bills|accruals)\/1\/$/.test(path)) body = { id: 1, owner: 31, warehouse: 11, currency: 'CNY', total: '123.45', lines: [] }
  53  |     else if (path.includes('/alerts/sections/') && path.endsWith('/1/')) body = { meta, detail: { order_no: 'QA-1', status: 'PENDING' } }
  54  |     await route.fulfill(custom || { json: body })
  55  |   })
  56  |   return { requests, errors, consoleErrors }
  57  | }
  58  | 
  59  | async function open(page, path) {
  60  |   await page.goto(`/#/pages/${path}`)
  61  |   await expect(page).toHaveURL(`/#/pages/${path}`)
  62  |   await expect(page).not.toHaveTitle('')
> 63  |   await expect(page.locator('uni-page-body').last()).not.toBeEmpty()
      |                                                          ^ Error: expect(locator).not.toBeEmpty() failed
  64  | }
  65  | 
  66  | // Controlled completions reproduce races without timing-dependent sleeps.
  67  | test('late success or failure cannot replace the selected cockpit tab', async ({ page }) => {
  68  |   const pending = deferred()
  69  |   const arrived = deferred()
  70  |   await mockBoss(page, { respond: async url => {
  71  |     if (url.pathname.endsWith('/receivables/')) {
  72  |       arrived.resolve()
  73  |       await pending.promise
  74  |       return { json: { meta, outstanding_by_currency: [{ currency: 'CNY', total: '123.45' }] } }
  75  |     }
  76  |   } })
  77  |   await open(page, 'cockpit/index')
  78  |   await expect(page.locator('.card .value')).toHaveText('3')
  79  |   await page.getByText('应收回款', { exact: true }).click()
  80  |   await arrived.promise
  81  |   await page.getByText('目标预测', { exact: true }).click()
  82  |   await expect(page.locator('.money')).toContainText('987.65')
  83  |   const finished = page.waitForResponse(response => response.url().includes('/receivables/'))
  84  |   pending.resolve()
  85  |   await finished
  86  |   await expect(page.locator('.money')).toContainText('987.65')
  87  |   await expect(page.getByText('正在加载...', { exact: true })).toHaveCount(0)
  88  | })
  89  | 
  90  | test('home ignores an old failed refresh after the latest one succeeds', async ({ page }) => {
  91  |   let count = 0
  92  |   const old = deferred(), arrived = deferred()
  93  |   await mockBoss(page, { respond: async url => {
  94  |     if (url.pathname.endsWith('/home/') && ++count === 2) {
  95  |       arrived.resolve()
  96  |       await old.promise
  97  |       return { status: 503, json: { detail: '旧请求失败' } }
  98  |     }
  99  |     if (url.pathname.endsWith('/home/') && count > 2) return { json: { meta, summary: { sku_count: 222 } } }
  100 |   } })
  101 |   await open(page, 'home/index')
  102 |   await expect(page.locator('.kpi-card').nth(1)).toContainText('12')
  103 |   await page.getByText('刷新', { exact: true }).click()
  104 |   await arrived.promise
  105 |   await page.getByText('刷新', { exact: true }).click()
  106 |   await expect(page.locator('.kpi-card').nth(1)).toContainText('222')
  107 |   const finished = page.waitForResponse(response => response.status() === 503)
  108 |   old.resolve()
  109 |   await finished
  110 |   await expect(page.locator('.kpi-card').nth(1)).toContainText('222')
  111 |   await expect(page.getByText('旧请求失败', { exact: true })).toHaveCount(0)
  112 | })
  113 | 
  114 | for (const warehouse of ['11', '']) {
  115 |   test(`home owner drilldown and return preserve warehouse ${warehouse || 'ALL'} and dates`, async ({ page }) => {
  116 |     const { requests } = await mockBoss(page, { scope: { ...historical, warehouse } })
  117 |     await open(page, 'home/index')
  118 |     await page.locator('.row-title').filter({ hasText: '测试货主' }).click()
  119 |     await expect(page).toHaveURL(/owner_detail/)
  120 |     await expect.poll(() => requests.filter(url => url.pathname.endsWith('/accruals/')).length).toBe(1)
  121 |     const detail = requests.find(url => url.pathname.endsWith('/warehouse-overview/'))
  122 |     expect(detail.searchParams.get('warehouse') || '').toBe(warehouse)
  123 |     expect(detail.searchParams.get('owner')).toBe('31')
  124 |     expect(detail.searchParams.get('date_from')).toBe(historical.date_from)
  125 |     expect(detail.searchParams.get('date_to')).toBe(historical.date_to)
  126 |     await page.getByText('返回总览', { exact: true }).click()
  127 |     await expect.poll(() => requests.filter(url => url.pathname.endsWith('/warehouse-overview/')).length).toBe(2)
  128 |     const returned = requests.filter(url => url.pathname.endsWith('/warehouse-overview/')).at(-1)
  129 |     expect(returned.searchParams.get('warehouse') || '').toBe(warehouse)
  130 |     expect(returned.searchParams.get('date_from')).toBe(historical.date_from)
  131 |     expect(returned.searchParams.get('date_to')).toBe(historical.date_to)
  132 |   })
  133 | }
  134 | 
  135 | test('explicit ALL warehouse in a URL overrides a cached single warehouse', async ({ page }) => {
  136 |   const { requests } = await mockBoss(page)
  137 |   await open(page, 'billing/overview?warehouse=&owner=31&date_from=2024-02-01&date_to=2024-02-15')
  138 |   await expect.poll(() => requests.some(url => url.pathname.endsWith('/warehouse-overview/'))).toBe(true)
  139 |   const query = requests.find(url => url.pathname.endsWith('/warehouse-overview/')).searchParams
  140 |   expect(query.has('warehouse')).toBe(false)
  141 |   await expect(page.locator('.scope-filter')).toContainText('全部授权仓库')
  142 | })
  143 | 
  144 | for (const count of [0, 20, 21, 25]) {
  145 |   test(`closed-loop alerts paginate ${count} records and reset after changing tabs`, async ({ page }) => {
  146 |     await mockBoss(page, { respond: url => {
  147 |       if (url.pathname.endsWith('/alert-cases/')) {
  148 |         const pageNumber = Number(url.searchParams.get('page'))
  149 |         expect(url.searchParams.get('page_size')).toBe('20')
  150 |         const start = (pageNumber - 1) * 20
  151 |         return { json: { meta, count, page: pageNumber, page_size: 20,
  152 |           results: Array.from({ length: Math.max(0, Math.min(20, count - start)) }, (_, i) => ({
  153 |             id: start + i + 1, title: `预警-${start + i + 1}`, severity: 'HIGH', status: 'OPEN',
  154 |           })),
  155 |         } }
  156 |       }
  157 |     } })
  158 |     await open(page, 'cockpit/index')
  159 |     await expect(page.locator('.card .value')).toHaveText('3')
  160 |     await page.getByText('预警闭环', { exact: true }).click()
  161 |     await expect(page.locator('.wide-card')).toHaveCount(Math.min(20, count))
  162 |     await expect(page.locator('.pager')).toContainText(`共 ${count} 条`)
  163 |     await expect(page.getByText('上一页', { exact: true })).toHaveAttribute('disabled', 'true')
```