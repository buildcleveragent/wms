import { expect, test } from '@playwright/test'
import { expectMobileHealthy, installLoggedInMocks } from './mobile-fixtures'

test('short receiving screens can scroll the final date control clear of the fixed footer', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: page.viewportSize().width, height: 640 })
  await installLoggedInMocks(page, {
    '/api/catalog/owners': route => route.fulfill({ json: {
      count: 1, next: null, results: [{ id: 8, name: '测试货主', code: 'O8' }],
    } }),
    '/api/catalog/receive_products': route => route.fulfill({ json: {
      count: 1, next: null, results: [{
        id: 101, sku: 'QA-SKU', gtin: 'QA-UNIT', base_unit_name: '盒', spec: '250ml×12',
        name: '测试长名称商品——250 毫升十二盒一箱的常温纯牛奶（常温运输、独立包装、规格及名称完整显示）',
        packaging: [], unitOptions: [{ key: 'BASE', label: '盒', multiplier: 1, package_id: null }],
        selectedUnitIndex: 0,
      }],
    } }),
  })
  await page.goto('/#/pages/inbound/createwithoutorder/selectowner')
  await page.getByTestId('owner-row').click()
  await page.getByTestId('product-search-input').locator('input').fill('QA-SKU')
  await page.getByTestId('product-search-submit').click()
  await expect(page.locator('.picker-button')).toHaveCount(2)
  const scrollable = page.locator('.content')
  expect(await scrollable.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true)
  await scrollable.evaluate(el => { el.scrollTop = el.scrollHeight })
  const footer = await page.locator('.footer').boundingBox()
  const date = await page.locator('.picker-button').last().boundingBox()
  expect(date.y + date.height).toBeLessThanOrEqual(footer.y)
  expect(await page.evaluate(({ x, y }) => Boolean(document.elementFromPoint(x, y)?.closest('.picker-button')), {
    x: date.x + date.width / 2, y: date.y + date.height / 2,
  })).toBe(true)
  await expectMobileHealthy(page)
  await page.screenshot({ path: testInfo.outputPath('receiving-short-scroll.png') })
})
