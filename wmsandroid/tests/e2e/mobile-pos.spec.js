import { expect, test } from '@playwright/test'
import { expectMobileHealthy, installLoggedInMocks, product } from './mobile-fixtures'

test('POS search, cart, payment and checkout use one mobile flow and a whole-warehouse payload', async ({ page }, testInfo) => {
  let checkoutPayload = null
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/pos/products/': async (route) => route.fulfill({ json: { count: 1, next: null, results: [product] } }),
    '/api/pos/checkout/': async (route) => {
      checkoutPayload = route.request().postDataJSON()
      await route.fulfill({ status: 201, json: { sale: { id: 71, sale_no: 'XS-20260831-0071' } } })
    },
  })
  await page.goto('/#/pages/pos/index')

  await expect(page).toHaveURL(/pages\/pos\/index/)
  await expect(page.getByText('手机收银', { exact: true })).toBeVisible()
  await expect(page.getByText('库存范围：全仓', { exact: true })).toBeVisible()
  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill('纯牛奶')
  await input.press('Enter')
  await expect(page.getByTestId('pos-product-row')).toHaveCount(1)
  await page.getByTestId('pos-product-row').getByText('添加', { exact: true }).click()
  await expect(page.getByTestId('pos-cart-product').locator('.cart-name')).toHaveText('纯牛奶　250ml×12盒')
  await expect(page.locator('.price-input')).toHaveCount(0)

  await page.getByTestId('pos-checkout').click()
  await expect(page.getByText('确认收款', { exact: true })).toBeVisible()
  await expect(page.getByText('顾客已完成付款后再确认', { exact: true })).toBeVisible()
  if (testInfo.project.name === 'android-390') await page.screenshot({ path: '/tmp/wmsandroid-payment-390.png', fullPage: false })
  await page.getByText('确认已收款并记录销售', { exact: true }).click()
  await expect.poll(() => checkoutPayload).not.toBeNull()
  expect(checkoutPayload.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
  expect(checkoutPayload).not.toHaveProperty('stock_zone_type')
  expect(checkoutPayload).not.toHaveProperty('customer_id')
  expect(checkoutPayload).not.toHaveProperty('payments')
  expect(checkoutPayload.payment.method).toBe('WECHAT')
  await expect(page.getByTestId('pos-cart-product')).toHaveCount(0)
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
})

test('sales history exposes detail, return and void only with their permissions', async ({ page }, testInfo) => {
  const sale = {
    id: 71,
    sale_no: 'XS-20260831-0071',
    status: 'COMPLETED',
    created_at: '2026-08-31T08:30:00+08:00',
    total_amount: '69.60',
    item_count: 12,
    payment: { method: 'WECHAT' },
  }
  const detail = {
    ...sale,
    lines: [{ id: 701, product_name: '纯牛奶', product_code: 'MILK-250', qty: '12.000', amount: '69.60', returnable_qty: '12.000', returned_qty: '0', returned_amount: '0' }],
  }
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/pos/sales/': async (route) => route.fulfill({ json: { count: 1, next: null, results: [sale] } }),
    '/api/pos/sales/71/': async (route) => route.fulfill({ json: detail }),
  })
  await page.goto('/#/pages/pos/history')

  await expect(page.getByText('XS-20260831-0071', { exact: true })).toBeVisible()
  await page.getByText('XS-20260831-0071', { exact: true }).click()
  await expect(page.getByText('纯牛奶', { exact: true })).toBeVisible()
  await expect(page.getByText('退货', { exact: true })).toBeVisible()
  await expect(page.getByText('作废', { exact: true })).toBeVisible()
  await page.getByText('退货', { exact: true }).click()
  await expect(page.getByText('销售退货', { exact: true })).toBeVisible()
  await expect(page.getByText('确认退货并记录退款', { exact: true })).toBeVisible()
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
  if (testInfo.project.name === 'android-390') await page.screenshot({ path: '/tmp/wmsandroid-history-390.png', fullPage: false })
})
