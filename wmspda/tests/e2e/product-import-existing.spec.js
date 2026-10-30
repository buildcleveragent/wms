import { expect, test } from '@playwright/test'

test.use({ actionTimeout: 15_000 })

for (const width of [1280, 390]) {
  test(`archive then receive keeps original identifiers visible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    const errors = []
    let imports = 0
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => {
      if (['error', 'warning'].includes(message.type())) errors.push(message.text())
    })
    const product = { row: 2, product_id: 51, code: '000100001', sku: 'OWNER1', name: '先建档后收货', received_qty: '3' }
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (!path.startsWith('/api/')) return route.continue()
      if (path.endsWith('/token/')) return route.fulfill({ json: { access: 'import-test', refresh: 'import-refresh' } })
      if (path.endsWith('/profile/')) return route.fulfill({ json: {
        user: { id: 3, username: 'import-user' }, perms: [],
        capabilities: { can_import_products: true, can_receive_without_order: true },
      } })
      if (path.endsWith('/home-todos/')) return route.fulfill({ json: { scope: 'test', items: [] } })
      if (path.endsWith('/import-warehouses/')) return route.fulfill({ json: { results: [
        { id: 1, code: 'WH', name: '测试仓库', receipt_ready: true, default_receive_location: { id: 1, code: 'RECEIVE' } },
      ] } })
      if (path.endsWith('/import-excel/')) {
        imports += 1
        if (imports === 2) expect(route.request().postData()).toContain('name="warehouse_id"')
        return route.fulfill({ json: {
          total_rows: 1, created_count: imports === 1 ? 1 : 0,
          created: imports === 1 ? [{ ...product, received_qty: '0' }] : [],
          reused_count: imports === 1 ? 0 : 1, reused: imports === 1 ? [] : [product],
          skipped_count: 0, error_count: 0,
          receipts: imports === 1 ? [] : [{ task_id: 3, task_no: 'RK-003', owner_code: 'OWNER', warehouse_code: 'WH', item_count: 1 }],
        } })
      }
      errors.push(`Unexpected API: ${path}`)
      return route.fulfill({ status: 404, json: {} })
    })
    await page.goto('/')
    await page.locator('uni-input input').nth(0).fill('import-user')
    await page.locator('uni-input input').nth(1).fill('test-password')
    await page.locator('uni-button').filter({ hasText: '登录' }).click()
    await expect(page).toHaveURL(/pages\/index\/index/)
    await page.getByText('商品导入', { exact: true }).click()
    await expect(page).toHaveURL(/pages\/products\/import/)
    await expect(page).toHaveTitle('商品批量导入')
    await expect(page.getByText('1. 下载标准模板')).toBeVisible()

    for (let attempt = 0; attempt < 2; attempt += 1) {
      if (attempt === 1) {
        await page.locator('.picker-box').click()
        if (width < 768) {
          await page.locator('.uni-picker-content .uni-picker-item').filter({ hasText: '测试仓库' }).click()
          await page.locator('.uni-picker-action-confirm').click()
        } else {
          await page.locator('.uni-picker-select .uni-picker-item').filter({ hasText: '测试仓库' }).click()
        }
        await expect(page.locator('.picker-box')).toContainText('测试仓库')
      }
      const chooser = page.waitForEvent('filechooser')
      await page.getByText('选择 Excel', { exact: true }).click()
      await (await chooser).setFiles({ name: 'products.xlsx', mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer: Buffer.from('mock workbook; API behavior covered by MySQL tests') })
      await page.getByText('上传并导入商品', { exact: true }).click()
      await expect(page.getByTestId('product-import-result')).toBeVisible()
      if (attempt === 0) {
        await expect(page.getByText('新增成功', { exact: true })).toBeVisible()
        await expect(page.getByTestId('product-import-result')).toContainText(product.code)
      }
    }
    const reused = page.getByTestId('product-import-reused')
    await expect(reused).toBeVisible()
    await expect(reused).toContainText('编号保持不变')
    await expect(reused).toContainText('000100001 · SKU OWNER1')
    await expect(reused).toContainText('入库 3')
    await expect(page.getByText('新增成功', { exact: true })).toHaveCount(0)
    await expect(page.getByText('自动收货完成', { exact: true })).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `/tmp/wms-product-import-existing-${width}.png`, fullPage: true })
    expect(imports).toBe(2)
    expect(errors).toEqual([])
  })
}
