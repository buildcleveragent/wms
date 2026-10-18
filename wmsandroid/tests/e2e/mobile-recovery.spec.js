import { expect, test } from '@playwright/test'
import { expectMobileHealthy, fullProfile, installLoggedInMocks, product } from './mobile-fixtures'

// Only the camera result is synthetic. All page handlers/rendering run unchanged.
async function camera(page) {
  await page.route('**/utils/cameraScanner.js*', async (route) => {
    const response = await route.fetch()
    const body = (await response.text()).replace(/\b(?:[\w$]+\.)?scanCode\(\s*\{/g, 'window.__mobileTestScan({')
    await route.fulfill({ response, body })
  })
}
async function scan(page, code) {
  await page.evaluate((value) => { window.__mobileTestScan = options => options.success({ result: value, scanType: 'CODE_128' }); window.uni.vibrateShort = () => {} }, code)
  await page.locator('.scan-button').click()
}
const rows = (products, extra = {}) => ({ count: products.length, next: null, results: products, ...extra })
async function addSearch(page) {
  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill('商品'); await input.press('Enter')
  await page.getByTestId('pos-product-row').first().getByText('添加', { exact: true }).click()
}
const sale = (id, amount = '30.00') => ({
  id, sale_no: `MOBILE-SALE-${id}`, status: 'COMPLETED', total_amount: amount,
  warehouse_name: '测试仓库', cashier_name: '测试操作员', created_at: '2026-10-17T10:00:00Z',
  payment: { method: 'CASH' }, receipt: { customer: { name: '测试客户' } },
  orders: [{ id, order_no: `OUT-${id}`, owner_name: '测试货主', customer_name: '测试客户', status: 'COMPLETED' }],
  lines: [{ id: id * 10, owner_id: 8, owner_name: '测试货主', product_name: `商品${id}`, product_code: `P${id}`, base_unit: '件', qty: '3.000', amount, returnable_qty: '3.000', returned_qty: '0', returned_amount: '0' }],
})
async function openReturn(page, id) {
  await page.getByText(`MOBILE-SALE-${id}`, { exact: true }).click()
  await page.getByText('退货', { exact: true }).click()
  await page.locator('.return-line input').fill('1')
  await page.locator('.return-sheet .sheet-input input').fill('一次实物退回')
}
async function confirmReturn(page) {
  await page.getByText('确认退货并记录退款', { exact: true }).click()
  await page.locator('.uni-modal__btn_primary').click()
}

test('all pages of owner candidates, disabled stock, cancellation and owner labels survive checkout', async ({ page }, info) => {
  const candidates = Array.from({ length: 101 }, (_, index) => ({ ...product, id: 1000 + index, owner_id: 2000 + index, owner_name: `货主${index}长名称测试食品有限公司`, available_qty: index === 100 ? '0' : '120' }))
  const requests = []
  const errors = await installLoggedInMocks(page, {
    '/api/pos/products/': async (route, url) => {
      requests.push(url.searchParams.get('page'))
      expect(url.searchParams.get('include_out_of_stock')).toBe('1')
      const second = url.searchParams.get('page') === '2'
      await route.fulfill({ json: rows(second ? candidates.slice(100) : candidates.slice(0, 100), { count: 101, next: second ? null : '/api/pos/products/?page=2' }) })
    },
  })
  await camera(page); await page.goto('/#/pages/pos/index')
  await scan(page, 'SHARED')
  await expect(page.locator('.choice-row')).toHaveCount(101)
  await expect(page.locator('.choice-row').last()).toHaveAttribute('disabled', /.*/)
  await expect(page.locator('.choice-list')).toContainText('缺货，不可出库')
  await expect(page.locator('.cart-card')).toHaveCount(0)
  await page.getByText('取消选择', { exact: true }).click()
  await expect(page.locator('.choice-row')).toHaveCount(0)
  await scan(page, 'SHARED'); await expect(page.locator('.choice-row')).toHaveCount(101)
  await page.locator('.overlay').click({ position: { x: 3, y: 3 } })
  await expect(page.locator('.choice-row')).toHaveCount(0)
  await scan(page, 'SHARED'); await page.locator('.choice-row').first().click()
  await scan(page, 'SHARED'); await page.locator('.choice-row').nth(1).click()
  await expect(page.locator('.cart-card')).toHaveCount(2)
  await expect(page.locator('.cart-card').first()).toContainText(candidates[0].owner_name)
  await page.getByTestId('pos-checkout').click()
  await expect(page.locator('.payment-cart')).toContainText(candidates[0].owner_name)
  await expect(page.locator('.payment-cart')).toContainText(candidates[1].owner_name)
  await expectMobileHealthy(page)
  expect(requests).toContain('2'); expect(errors).toEqual([])
  await page.screenshot({ path: info.outputPath('multi-owner-confirmation.png') })
})

test('effective historical box barcode counts 12 and base-unit addition is blocked at stock 12', async ({ page }) => {
  await installLoggedInMocks(page, { '/api/pos/products/': async (route, url) => {
    const isBox = url.searchParams.get('barcode') === 'OLD-BOX'
    await route.fulfill({ json: rows([{ ...product, available_qty: '12', matched_barcode: isBox ? { package_id: 22, label: '箱', multiplier: 12, code_type: 'PACKAGE' } : product.matched_barcode }]) })
  } })
  await camera(page); await page.goto('/#/pages/pos/index'); await scan(page, 'OLD-BOX')
  await expect(page.locator('.cart-card')).toContainText('本行 12 盒')
  await expect(page.locator('.unit-value')).toContainText('箱')
  await scan(page, 'BASE-CODE')
  await expect(page.locator('.scan-feedback')).toContainText('合计超过')
  await expect(page.locator('.cart-card')).toHaveCount(1)
  await expect(page.locator('.quantity-input input')).toHaveValue('1')
})

test('cancelled slow lookup cannot replace a newer scan result', async ({ page }) => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  await installLoggedInMocks(page, { '/api/pos/products/': async (route, url) => {
    if (url.searchParams.get('barcode') === 'SLOW') await gate
    await route.fulfill({ json: rows([{ ...product, name: url.searchParams.get('barcode') }]) })
  } })
  await camera(page); await page.goto('/#/pages/pos/index'); await scan(page, 'SLOW')
  await page.getByText('取消选择', { exact: true }).click()
  await scan(page, 'NEW')
  release()
  await expect(page.locator('.cart-card')).toHaveCount(1)
  await expect(page.locator('.cart-name')).toContainText('NEW')
  await expect(page.locator('.choice-row')).toHaveCount(0)
})

test('checkout keeps one request after a lost response and browser restart', async ({ page }, info) => {
  const sent = []
  await installLoggedInMocks(page, {
    '/api/pos/products/': route => route.fulfill({ json: rows([product]) }),
    '/api/pos/checkout/': async route => {
      sent.push(route.request().postDataJSON())
      if (sent.length === 1) return route.abort('connectionreset')
      await route.fulfill({ status: 201, json: { sale: { id: 1, sale_no: 'RECOVERED' } } })
    },
  })
  await page.goto('/#/pages/pos/index'); await addSearch(page)
  await page.getByTestId('pos-checkout').click()
  await page.getByText('确认已收款并记录销售', { exact: true }).click()
  await expect(page.getByText('销售结果待确认', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('上一笔销售结果待确认', { exact: true })).toBeVisible()
  await page.getByText('重试确认', { exact: true }).click()
  await expect(page.getByText('销售已记录', { exact: true })).toBeVisible()
  expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0])
  await page.screenshot({ path: info.outputPath('checkout-recovered.png') })
})

test('late sale detail cannot change the return target, and all receipt lines are visible', async ({ page }) => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  const a = sale(1); const b = sale(2)
  b.lines = Array.from({ length: 8 }, (_, i) => ({ ...b.lines[0], id: 20 + i, product_name: `第${i + 1}项` }))
  const sent = []
  const errors = await installLoggedInMocks(page, {
    '/api/pos/sales/': route => route.fulfill({ json: rows([a, b]) }),
    '/api/pos/sales/1/': async route => { await gate; await route.fulfill({ json: a }) },
    '/api/pos/sales/2/': route => route.fulfill({ json: b }),
    '/api/pos/returns/': async route => { sent.push(route.request().postDataJSON()); await route.fulfill({ status: 201, json: { return: { id: 88 } } }) },
  })
  await page.goto('/#/pages/pos/history')
  await page.getByText(a.sale_no, { exact: true }).click(); await page.getByText(b.sale_no, { exact: true }).click()
  await expect(page.locator('.detail-line')).toHaveCount(8)
  release(); await page.waitForResponse(response => response.url().endsWith('/api/pos/sales/1/'))
  await expect(page.locator('.detail-line').last()).toContainText('第8项')
  await page.getByText('查看详情', { exact: true }).click()
  await expect(page.locator('.receipt-line')).toHaveCount(8)
  await expect(page.locator('.receipt-sheet')).toContainText('测试仓库')
  await expect(page.locator('.receipt-sheet')).toContainText('OUT-2')
  await page.getByText('关闭详情', { exact: true }).click()
  await page.getByText('退货', { exact: true }).click()
  await page.locator('.return-line input').first().fill('1')
  await page.locator('.return-sheet .sheet-input input').fill('目标单校验')
  await confirmReturn(page)
  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0].sale_id).toBe(2); expect(sent[0].lines[0].sale_line_id).toBe(20)
  expect(errors).toEqual([])
})

test('return recovery preserves payload and key across reload', async ({ page }, info) => {
  const record = sale(1); const sent = []
  await installLoggedInMocks(page, {
    '/api/pos/sales/': route => route.fulfill({ json: rows([record]) }),
    '/api/pos/sales/1/': route => route.fulfill({ json: record }),
    '/api/pos/returns/': async route => {
      sent.push(route.request().postDataJSON())
      if (sent.length === 1) return route.abort('connectionreset')
      await route.fulfill({ status: 201, json: { return: { id: 11 } } })
    },
  })
  await page.goto('/#/pages/pos/history'); await openReturn(page, 1); await confirmReturn(page)
  await expect(page.locator('.pending-return')).toBeVisible()
  await page.reload(); await expect(page.locator('.pending-return')).toContainText(record.sale_no)
  await page.getByText('继续确认结果', { exact: true }).click()
  await expect(page.locator('.pending-return')).toHaveCount(0)
  expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0])
  await page.screenshot({ path: info.outputPath('return-recovered.png') })
})

test('zero-priced goods can be returned without a refund row', async ({ page }) => {
  const record = sale(1, '0.00'); let payload
  await installLoggedInMocks(page, {
    '/api/pos/sales/': route => route.fulfill({ json: rows([record]) }),
    '/api/pos/sales/1/': route => route.fulfill({ json: record }),
    '/api/pos/returns/': async route => { payload = route.request().postDataJSON(); await route.fulfill({ status: 201, json: { return: { id: 11 } } }) },
  })
  await page.goto('/#/pages/pos/history'); await openReturn(page, 1); await confirmReturn(page)
  await expect.poll(() => payload).toBeTruthy()
  expect(payload.refunds).toEqual([]); expect(payload.lines).toEqual([{ sale_line_id: 10, qty: '1.000' }])
  await expect(page.locator('.return-sheet')).toHaveCount(0)
})

test('picking response loss locks edits and replays the same scan after reload', async ({ page }, info) => {
  const sent = []; let done = 0
  await installLoggedInMocks(page, {
    '/api/pda/pick-tasks/5/': route => route.fulfill({ json: { id: 5, task_no: 'PICK-MOBILE-5', status: 'RELEASED', owner_name: '测试货主', warehouse_name: '测试仓库' } }),
    '/api/pda/pick-tasks/5/lines/': route => route.fulfill({ json: [{ id: 51, product_name: '拣货测试', qty_plan: '2', qty_done: String(done) }] }),
    '/api/pda/pick-tasks/5/scan/': async route => {
      sent.push(route.request().postDataJSON())
      if (sent.length === 1) { done = 1; return route.abort('connectionreset') }
      await route.fulfill({ json: { line_id: 51, qty_done: '1.000', idempotent: true, line: { id: 51, qty_done: '1.000' } } })
    },
  })
  await page.goto('/#/pages/picking/task_detail?task_id=5')
  await page.locator('.scan-bar .flex-input input').fill('PICK-ALIAS')
  await page.getByText('录入', { exact: true }).click()
  await expect(page.locator('.pending-scan')).toBeVisible()
  await expect(page.locator('.line-qty-input input')).toBeDisabled()
  await page.reload(); await expect(page.locator('.pending-scan')).toContainText('PICK-ALIAS')
  await page.getByText('继续确认结果', { exact: true }).click()
  await expect(page.locator('.pending-scan')).toHaveCount(0)
  expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0])
  await expect(page.locator('.line-qty-input input')).toHaveValue('1')
  await expectMobileHealthy(page)
  await page.screenshot({ path: info.outputPath('picking-recovered.png') })
})

test('360px short screen keeps payment and enlarged touch controls reachable', async ({ page }, info) => {
  await page.setViewportSize({ width: 360, height: 640 })
  await installLoggedInMocks(page, { '/api/pos/products/': route => route.fulfill({ json: rows([{ ...product, owner_name: '长名称货主有限公司'.repeat(3), name: '长商品名称'.repeat(6) }]) }) })
  await page.goto('/#/pages/pos/index'); await addSearch(page)
  for (const selector of ['.quantity-button', '.quantity-input', '.unit-value', '.remove-link']) {
    const bounds = await page.locator(selector).first().boundingBox(); expect(bounds.height).toBeGreaterThanOrEqual(48)
  }
  await page.getByTestId('pos-checkout').click()
  await expectMobileHealthy(page)
  await page.getByText('现金', { exact: true }).click()
  await page.locator('.cash-row input').fill('10')
  await page.getByText('取消', { exact: true }).click()
  await expect(page.getByText('确认收款', { exact: true })).toHaveCount(0)
  await expect(page.locator('.cart-card')).toHaveCount(1)
  await page.screenshot({ path: info.outputPath('short-screen.png') })
})

test('pending return is isolated from another account and survives an expired session', async ({ page }) => {
  const record = sale(1); const sent = []; let profile = fullProfile
  await installLoggedInMocks(page, {
    '/api/auth/profile/': route => route.fulfill({ json: profile }),
    '/api/pos/sales/': route => route.fulfill({ json: rows([record]) }),
    '/api/pos/sales/1/': route => route.fulfill({ json: record }),
    '/api/pos/returns/': async route => {
      sent.push(route.request().postDataJSON())
      if (sent.length === 1) return route.fulfill({ status: 403, json: { detail: '权限已失效，请重新登录后确认' } })
      await route.fulfill({ status: 201, json: { return: { id: 11 } } })
    },
  })
  await page.goto('/#/pages/pos/history'); await openReturn(page, 1); await confirmReturn(page)
  await expect(page.locator('.pending-return')).toBeVisible()
  profile = { ...fullProfile, user: { ...fullProfile.user, id: 99 } }
  await page.reload(); await expect(page.getByText(record.sale_no, { exact: true })).toBeVisible()
  await expect(page.locator('.pending-return')).toHaveCount(0)
  expect(sent).toHaveLength(1)
  profile = fullProfile
  await page.reload(); await expect(page.locator('.pending-return')).toBeVisible()
  await page.getByText('继续确认结果', { exact: true }).click()
  await expect(page.locator('.pending-return')).toHaveCount(0)
  expect(sent[1]).toEqual(sent[0])
})

test('invalid manual quantities and unit changes cannot exceed combined stock', async ({ page }) => {
  const errors = await installLoggedInMocks(page, { '/api/pos/products/': route => route.fulfill({ json: rows([{ ...product, available_qty: '12' }]) }) })
  await page.goto('/#/pages/pos/index'); await addSearch(page)
  const quantity = () => page.locator('.quantity-input input')
  await quantity().fill('13'); await quantity().blur()
  await expect(quantity()).toHaveValue('1')
  await expect(page.locator('.scan-feedback')).toContainText('合计超过')
  await quantity().fill('0'); await quantity().blur()
  await expect(quantity()).toHaveValue('1')
  await quantity().fill('0.125'); await quantity().blur()
  await expect(page.locator('.cart-code').last()).toContainText('本行 0.125 盒')
  await page.locator('.unit-value').click()
  const pickerColumn = page.locator('uni-picker-view-column')
  // Wait for the opening animation before calculating real touch coordinates.
  await pickerColumn.click({ trial: true })
  const column = await pickerColumn.boundingBox()
  const touch = await page.context().newCDPSession(page)
  const point = { x: column.x + column.width / 2, y: column.y + column.height / 2 }
  await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] })
  for (let step = 1; step <= 8; step++) {
    await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: point.x, y: point.y - step * 5 }] })
    await page.waitForTimeout(50)
  }
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expect(page.locator('.uni-picker-select .uni-picker-item.selected')).toContainText('箱')
  await page.locator('.uni-picker-action-confirm').click()
  await expect(page.locator('.cart-code').last()).toContainText('本行 1.500 盒')
  await expect(page.locator('.uni-picker-custom')).toBeHidden()
  expect(errors).toEqual([])
})
