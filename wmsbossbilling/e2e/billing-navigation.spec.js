import { expect, test } from '@playwright/test'

const scope = { owner: '31', warehouse: '11', date_from: '2024-02-01', date_to: '2024-02-29' }
const query = new URLSearchParams(scope).toString()
const bill = {
  id: 1, owner: 31, warehouse: 11, invoice_no: 'REG-INVOICE', currency: 'CNY',
  status: 'ISSUED', subtotal: '10.00', tax_total: '0.60', total: '10.60',
  lines: [{ id: 1, accrual: 1, service_date: '2024-02-15', charge_type: 'STORAGE',
    amount: '10.00', tax_amount: '0.60', quantity: '100', unit_price: '0.10' }],
}

for (const width of [390, 1440]) {
  for (const entry of ['drilldown', 'direct']) {
    test(`accrual returns to scoped overview after ${entry} at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
      const errors = []
      const overviewRequests = []
      page.on('pageerror', error => errors.push(error.message))
      await page.addInitScript(scope => {
        localStorage.setItem('access', 'navigation-regression')
        localStorage.setItem('user', JSON.stringify({ id: 992, username: '导航测试' }))
        localStorage.setItem('boss_scope:992', JSON.stringify(scope))
      }, scope)
      await page.route('**/api/**', async route => {
        const url = new URL(route.request().url())
        const path = url.pathname
        let body = { count: 0, results: [] }
        if (path.endsWith('/context/')) body = {
          warehouse_options: [{ id: 11, name: '测试仓库' }],
          owner_options: [{ id: 31, name: '测试货主' }],
        }
        else if (path.endsWith('/warehouse-overview/')) {
          overviewRequests.push(url)
          body = {
            meta: { data_status: 'COMPLETE' }, scope: { owner: 31, warehouse: 11 },
            by_owner: [{ owner: 31, owner_name: '测试货主', currency: 'CNY', subtotal: '10.00', accrual_count: 1 }],
          }
        }
        else if (path.endsWith('/bills/1/')) body = bill
        else if (path.endsWith('/bills/')) body = [bill]
        else if (path.endsWith('/accruals/1/')) body = {
          id: 1, owner: 31, warehouse: 11, charge_type: 'STORAGE', status: 'BILLED',
          currency: 'CNY', amount: '10.00', tax_amount: '0.60', service_date: '2024-02-15',
        }
        await route.fulfill({ json: body })
      })
      const active = () => page.locator('uni-page-body').last()
      if (entry === 'drilldown') {
        await page.goto(`/#/pages/billing/overview?${query}`)
        await active().getByText('测试货主', { exact: true }).last().click()
        await expect(page).toHaveURL(/billing\/owner_detail/)
        await active().getByText('REG-INVOICE', { exact: true }).click()
        await expect(page).toHaveURL(/billing\/bill_detail/)
        await active().locator('.line-card').click()
      } else {
        await page.goto(`/#/pages/billing/accrual_detail?id=1&${query}`)
      }
      await expect(page).toHaveURL(/billing\/accrual_detail/)
      await expect(active().getByText('基础信息', { exact: true })).toBeVisible()
      const detailScope = new URLSearchParams(page.url().split('?')[1])
      await active().getByText('返回总览', { exact: true }).click()
      await expect(page).toHaveURL(/billing\/overview\?/)
      await expect.poll(() => overviewRequests.length).toBe(entry === 'drilldown' ? 3 : 1)
      for (const [name, value] of Object.entries(scope)) {
        expect(overviewRequests.at(-1).searchParams.get(name)).toBe(value)
        // Bill drilldown may have no date filter; preserve the detail's actual query.
        expect(new URLSearchParams(page.url().split('?')[1]).get(name)).toBe(detailScope.get(name))
      }
      await expect(active()).not.toBeEmpty()
      await expect(page.locator('vite-error-overlay')).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      expect(errors).toEqual([])
      await page.screenshot({ path: testInfo.outputPath(`overview-${entry}-${width}.png`) })
    })
  }
}
