import { expect, test } from '@playwright/test'
import { fullProfile, installLoggedInMocks } from './mobile-fixtures'

test('expired login preserves a pending return until the original account logs in again', async ({ page }) => {
  const sent = []
  const sale = {
    id: 81, sale_no: 'SESSION-RETURN-81', status: 'COMPLETED', total_amount: '20.00',
    payment: { method: 'CASH' },
    lines: [{ id: 811, owner_id: 8, owner_name: '测试货主', product_name: '登录恢复商品', base_unit: '件', qty: '2.000', amount: '20.00', returnable_qty: '2.000', returned_qty: '0', returned_amount: '0' }],
  }
  await installLoggedInMocks(page, {
    '/api/pda/token/': route => route.fulfill({ json: { access: 'new-session-access', refresh: 'new-session-refresh' } }),
    '/api/pos/sales/': route => route.fulfill({ json: { count: 1, next: null, results: [sale] } }),
    '/api/pos/sales/81/': route => route.fulfill({ json: sale }),
    '/api/pos/returns/': async route => {
      sent.push(route.request().postDataJSON())
      if (sent.length === 1) return route.fulfill({ status: 401, json: { detail: '登录已过期' } })
      await route.fulfill({ status: 201, json: { return: { id: 91 } } })
    },
  })
  await page.goto('/#/pages/pos/history')
  await page.getByText(sale.sale_no, { exact: true }).click()
  await page.getByText('退货', { exact: true }).click()
  await page.locator('.return-line input').fill('1')
  await page.locator('.return-sheet .sheet-input input').fill('过期登录恢复测试')
  await page.getByText('确认退货并记录退款', { exact: true }).click()
  await page.locator('.uni-modal__btn_primary').click()
  // uni-app aliases its first page to / on H5; assert the rendered login page.
  await expect(page.locator('.login-page')).toBeVisible()
  await page.locator('input[type=text]').first().fill(fullProfile.user.username)
  await page.locator('input[type=password]').fill('synthetic-test-password')
  await page.getByText('登录', { exact: true }).last().click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.goto('/#/pages/pos/history')
  await expect(page.locator('.pending-return')).toContainText(sale.sale_no)
  expect(sent).toHaveLength(1)
  await page.getByText('继续确认结果', { exact: true }).click()
  await expect(page.locator('.pending-return')).toHaveCount(0)
  expect(sent).toHaveLength(2)
  expect(sent[1]).toEqual(sent[0])
})
