import { expect, test } from '@playwright/test'
import { expectMobileHealthy, installLoggedInMocks } from './mobile-fixtures'

test('receiving keeps touch controls usable and retries the same valid input after a network failure', async ({ page }, testInfo) => {
  const sent = []
  await installLoggedInMocks(page, {
    '/api/catalog/owners': route => route.fulfill({ json: {
      count: 1, next: null, results: [{ id: 8, name: '测试甲货主', code: 'O8' }],
    } }),
    '/api/catalog/receive_products': route => route.fulfill({ json: {
      count: 1, next: null, results: [{
        id: 101, name: '测试长名称商品——250 毫升十二盒一箱的常温纯牛奶',
        sku: 'QA-SKU', gtin: 'QA-UNIT', base_unit_name: '盒', spec: '250ml×12',
        packaging: [], unitOptions: [{ key: 'BASE', label: '盒', multiplier: 1, package_id: null }],
        selectedUnitIndex: 0,
      }],
    } }),
    '/api/inbound/receive_without_order/': async route => {
      sent.push(route.request().postDataJSON())
      if (sent.length === 1) return route.abort('connectionreset')
      await route.fulfill({ status: 201, json: { task_id: 42, task_no: 'QA-IN-42' } })
    },
  })
  await page.goto('/#/pages/inbound/createwithoutorder/selectowner')
  await page.getByTestId('owner-row').click()
  await page.getByTestId('product-search-input').locator('input').fill('QA-SKU')
  await page.getByTestId('product-search-submit').click()
  await expect(page.locator('.product-name')).toContainText('测试长名称商品')
  const controls = page.locator('.bar .btn-outline, .row-actions .btnnew, .picker-button, .pkg-radio-row, .footer .btn-outline')
  await expect(controls).toHaveCount(8)
  for (const control of await controls.all()) {
    const box = await control.boundingBox()
    expect(box.height).toBeGreaterThanOrEqual(48)
    expect(box.width).toBeGreaterThanOrEqual(48)
  }
  await expectMobileHealthy(page)
  await page.screenshot({ path: testInfo.outputPath('receiving-touch-controls.png') })
  await page.locator('.qty-input-sh input[type=number]').fill('2')
  await page.getByText('添加', { exact: true }).click()
  await page.locator('.footer .btn-outline').click()
  await expect(page.locator('.item-card')).toHaveCount(1)
  await page.getByText('确认收货', { exact: true }).click()
  await expect.poll(() => sent.length).toBe(1)
  await expect(page.getByText('确认收货', { exact: true })).toBeVisible()
  await page.getByText('确认收货', { exact: true }).click()
  await expect(page.getByText('收货已创建', { exact: true })).toBeVisible()
  expect(sent).toHaveLength(2)
  expect(sent[1]).toEqual(sent[0])
  expect(sent[0].items).toEqual([{ product_id: 101, qty: 2 }])
})
