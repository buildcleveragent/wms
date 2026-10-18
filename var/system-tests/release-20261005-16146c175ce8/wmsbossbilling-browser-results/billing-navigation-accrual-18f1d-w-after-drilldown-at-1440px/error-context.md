# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: billing-navigation.spec.js >> accrual returns to scoped overview after drilldown at 1440px
- Location: e2e/billing-navigation.spec.js:14:9

# Error details

```
Test timeout of 60000ms exceeded.
```

```
Error: locator.click: Test timeout of 60000ms exceeded.
Call log:
  - waiting for locator('uni-page-body').last().getByText('测试货主', { exact: true }).last()

```

# Test source

```ts
  1  | import { expect, test } from '../.release-network-fixture.js'
  2  | 
  3  | const scope = { owner: '31', warehouse: '11', date_from: '2024-02-01', date_to: '2024-02-29' }
  4  | const query = new URLSearchParams(scope).toString()
  5  | const bill = {
  6  |   id: 1, owner: 31, warehouse: 11, invoice_no: 'REG-INVOICE', currency: 'CNY',
  7  |   status: 'ISSUED', subtotal: '10.00', tax_total: '0.60', total: '10.60',
  8  |   lines: [{ id: 1, accrual: 1, service_date: '2024-02-15', charge_type: 'STORAGE',
  9  |     amount: '10.00', tax_amount: '0.60', quantity: '100', unit_price: '0.10' }],
  10 | }
  11 | 
  12 | for (const width of [390, 1440]) {
  13 |   for (const entry of ['drilldown', 'direct']) {
  14 |     test(`accrual returns to scoped overview after ${entry} at ${width}px`, async ({ page }, testInfo) => {
  15 |       await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
  16 |       const errors = []
  17 |       const overviewRequests = []
  18 |       page.on('pageerror', error => errors.push(error.message))
  19 |       await page.addInitScript(scope => {
  20 |         localStorage.setItem('access', 'navigation-regression')
  21 |         localStorage.setItem('user', JSON.stringify({ id: 992, username: '导航测试' }))
  22 |         localStorage.setItem('boss_scope:992', JSON.stringify(scope))
  23 |       }, scope)
  24 |       await page.route('**/api/**', async route => {
  25 |         const url = new URL(route.request().url())
  26 |         const path = url.pathname
  27 |         let body = { count: 0, results: [] }
  28 |         if (path.endsWith('/context/')) body = {
  29 |           warehouse_options: [{ id: 11, name: '测试仓库' }],
  30 |           owner_options: [{ id: 31, name: '测试货主' }],
  31 |         }
  32 |         else if (path.endsWith('/warehouse-overview/')) {
  33 |           overviewRequests.push(url)
  34 |           body = {
  35 |             meta: { data_status: 'COMPLETE' }, scope: { owner: 31, warehouse: 11 },
  36 |             by_owner: [{ owner: 31, owner_name: '测试货主', currency: 'CNY', subtotal: '10.00', accrual_count: 1 }],
  37 |           }
  38 |         }
  39 |         else if (path.endsWith('/bills/1/')) body = bill
  40 |         else if (path.endsWith('/bills/')) body = [bill]
  41 |         else if (path.endsWith('/accruals/1/')) body = {
  42 |           id: 1, owner: 31, warehouse: 11, charge_type: 'STORAGE', status: 'BILLED',
  43 |           currency: 'CNY', amount: '10.00', tax_amount: '0.60', service_date: '2024-02-15',
  44 |         }
  45 |         await route.fulfill({ json: body })
  46 |       })
  47 |       const active = () => page.locator('uni-page-body').last()
  48 |       if (entry === 'drilldown') {
  49 |         await page.goto(`/#/pages/billing/overview?${query}`)
> 50 |         await active().getByText('测试货主', { exact: true }).last().click()
     |                                                                  ^ Error: locator.click: Test timeout of 60000ms exceeded.
  51 |         await expect(page).toHaveURL(/billing\/owner_detail/)
  52 |         await active().getByText('REG-INVOICE', { exact: true }).click()
  53 |         await expect(page).toHaveURL(/billing\/bill_detail/)
  54 |         await active().locator('.line-card').click()
  55 |       } else {
  56 |         await page.goto(`/#/pages/billing/accrual_detail?id=1&${query}`)
  57 |       }
  58 |       await expect(page).toHaveURL(/billing\/accrual_detail/)
  59 |       await expect(active().getByText('基础信息', { exact: true })).toBeVisible()
  60 |       const detailScope = new URLSearchParams(page.url().split('?')[1])
  61 |       await active().getByText('返回总览', { exact: true }).click()
  62 |       await expect(page).toHaveURL(/billing\/overview\?/)
  63 |       await expect.poll(() => overviewRequests.length).toBe(entry === 'drilldown' ? 3 : 1)
  64 |       for (const [name, value] of Object.entries(scope)) {
  65 |         expect(overviewRequests.at(-1).searchParams.get(name)).toBe(value)
  66 |         // Bill drilldown may have no date filter; preserve the detail's actual query.
  67 |         expect(new URLSearchParams(page.url().split('?')[1]).get(name)).toBe(detailScope.get(name))
  68 |       }
  69 |       await expect(active()).not.toBeEmpty()
  70 |       await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  71 |       expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  72 |       expect(errors).toEqual([])
  73 |       await page.screenshot({ path: testInfo.outputPath(`overview-${entry}-${width}.png`) })
  74 |     })
  75 |   }
  76 | }
  77 | 
```