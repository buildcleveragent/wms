import { expect, test } from '@playwright/test'
import { installLoggedInMocks } from './mobile-fixtures'

test('a late sale response cannot change the sale being confirmed for void', async ({ page }) => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  const sent = []
  const sales = [1, 2].map(id => ({
    id, sale_no: `VOID-TARGET-${id}`, status: 'COMPLETED', total_amount: '10.00',
    lines: [{ id: id * 10, product_name: `商品${id}`, owner_name: '测试货主', owner_id: 8, qty: '1.000', amount: '10.00' }],
  }))
  const errors = await installLoggedInMocks(page, {
    '/api/pos/sales/': route => route.fulfill({ json: { count: 2, next: null, results: sales } }),
    '/api/pos/sales/1/': async route => { await gate; await route.fulfill({ json: sales[0] }) },
    '/api/pos/sales/2/': route => route.fulfill({ json: sales[1] }),
    '/api/pos/sales/2/void/': async route => {
      sent.push({ url: route.request().url(), body: route.request().postDataJSON() })
      await route.fulfill({ json: { ...sales[1], status: 'VOIDED' } })
    },
  })
  await page.goto('/#/pages/pos/history')
  await page.getByText(sales[0].sale_no, { exact: true }).click()
  await page.getByText(sales[1].sale_no, { exact: true }).click()
  await expect(page.locator('.detail-line')).toContainText('商品2')
  await page.getByText('作废', { exact: true }).click()
  await expect(page.locator('.uni-modal')).toContainText('VOID-TARGET-2')
  const delayed = page.waitForResponse(response => response.url().endsWith('/api/pos/sales/1/'))
  release()
  await delayed
  await page.locator('.uni-modal input, .uni-modal textarea').fill('测试作废，保持原单据')
  await page.locator('.uni-modal__btn_primary').click()
  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0].url).toMatch(/\/sales\/2\/void\/$/)
  expect(sent[0].body).toEqual({ reason: '测试作废，保持原单据' })
  expect(errors).toEqual([])
})
