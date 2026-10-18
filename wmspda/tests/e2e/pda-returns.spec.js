import { test, expect } from '@playwright/test'

const product = { product_id: 10, product_name: '测试饮料', product_code: 'DRINK', spec: '500ml', owner_id: 1, owner_name: '货主甲', available_qty: '10', damaged_qty: '5', base_unit: '瓶' }
const stock = { ...product, id: 100, location_name: 'A-01', batch_no: 'LOT-1', expiry_date: '2027-01-01', serial_no: '', allocated_qty: '3', locked_qty: '2', supplier_id: 3, supplier_name: '原供应商' }

async function setup(page, { noSupplier = false, loseConfirmResponse = false } = {}) {
  const errors = [], payloads = [], confirms = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (['error', 'warning'].includes(message.type()) && /SyntaxError|ReferenceError|TypeError|Failed to resolve|Unhandled/.test(message.text())) errors.push(message.text()) })
  let row, confirmCount = 0
  await page.route('**/api/pda/token/', route => route.fulfill({ json: { access: 'test', refresh: 'test-refresh' } }))
  await page.route('**/api/auth/profile/', route => route.fulfill({ json: { user: { id: 7, username: 'operator', warehouse_id: 1 }, perms: [], capabilities: { can_process_pda_return: true } } }))
  await page.route('**/api/pda/return-orders/**', async route => {
    const url = new URL(route.request().url()), method = route.request().method()
    const path = url.pathname.replace('/api/pda/return-orders/', '')
    if (path === 'products/') return route.fulfill({ json: { results: [product, { ...product, product_id: 20, owner_id: 2, owner_name: '货主乙' }], next: null } })
    if (path === 'stock-options/') return route.fulfill({ json: { results: [{ ...stock, ...(noSupplier ? { supplier_id: null, supplier_name: '' } : {}) }], next: null } })
    if (path === 'options/') return route.fulfill({ json: { owners: [{ owner_id: 1, owner__name: '货主甲' }], products: [], suppliers: [], operators: [] } })
    if (path === 'stats/') return route.fulfill({ json: { confirmed_count: 1, reversed_count: 1, units: [{ id: '瓶', label: '瓶', base_unit: '瓶', normal_qty: '2', damaged_qty: '4', new_damage_qty: '1', reverse_qty: '6', net_qty: '0', confirmed_count: 1, reversed_count: 1 }] } })
    if (path === '' && method === 'GET') return route.fulfill({ json: { results: row ? [row] : [], next: null } })
    if (path === '' && method === 'POST' || path === '1/' && method === 'PATCH') {
      const data = route.request().postDataJSON(); payloads.push(data)
      row = { ...data, id: 1, version: (row?.version || 0) + 1, return_no: 'RT-TEST', status: 'DRAFT', owner_name: '货主甲', recipient_name: data.recipient_type === 'SUPPLIER' ? '原供应商' : '货主甲', operator_id: 7, operator_name: 'operator', can_edit: true, can_reverse: false,
        lines: data.lines.map(line => ({ ...stock, ...line, dimensions: { batch_no: stock.batch_no, expiry_date: stock.expiry_date, serial_no: '' }, ...(noSupplier ? { supplier_id: null, supplier_name: '' } : {}) })) }
      return route.fulfill({ json: row })
    }
    if (path === '1/confirm/') {
      confirms.push(route.request().postDataJSON()); confirmCount++
      row = { ...row, status: 'COMPLETED', version: 2, can_edit: false, can_reverse: true }
      if (loseConfirmResponse && confirmCount === 1) return route.abort('failed')
      if (loseConfirmResponse && confirmCount === 2) return route.fulfill({ status: 403, json: { detail: '临时权限核验失败' } })
      return route.fulfill({ json: row })
    }
    if (path === '1/reverse/') {
      row = { ...row, status: 'REVERSED', version: 3, can_reverse: false, reversal_reason: route.request().postDataJSON().reason }
      return route.fulfill({ json: row })
    }
    if (path === '1/') return route.fulfill({ json: row })
    return route.fulfill({ status: 404, json: { detail: 'unexpected endpoint' } })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('operator')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.getByText('退货出库', { exact: true }).click()
  await expect(page).toHaveURL(/pages\/returns\/index/)
  await expect(page.locator('.title')).toHaveText('退货出库')
  return { errors, payloads, confirms }
}

async function fillReturn(page) {
  await page.locator('.search-row input').fill('DRINK')
  await page.getByText('搜索', { exact: true }).click()
  await expect(page.locator('.product-row')).toHaveCount(2)
  await expect(page.locator('.product-row').nth(1)).toContainText('货主乙')
  await page.locator('.product-row').filter({ hasText: '货主甲' }).click()
  await page.getByText('选择该库存', { exact: true }).click()
  const inputs = page.locator('.quantity-grid input')
  await inputs.nth(0).fill('2'); await inputs.nth(1).fill('3'); await inputs.nth(2).fill('1')
  await page.locator('textarea').first().fill('运输破损退货')
}

async function acceptModal(page) {
  await page.locator('.uni-modal').getByText('确定', { exact: true }).click()
}

test('mobile mixed-damage supplier return, reversal and statistics', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const ctx = await setup(page)
  await fillReturn(page)
  await page.getByText('退给供应商：原供应商', { exact: true }).click()
  await page.screenshot({ path: '/tmp/wmspda-return-mobile.png', fullPage: false })
  await page.getByText('确认退货', { exact: true }).click()
  await expect(page.locator('.uni-modal')).toContainText('货主甲 → 原供应商')
  await acceptModal(page)
  await expect(page.getByText('RT-TEST · 已完成', { exact: true })).toBeVisible()
  expect(ctx.payloads[0].lines[0]).toEqual({ inventory_id: 100, available_qty: '2', damaged_qty: '3', new_damage_qty: '1' })
  await page.locator('textarea').last().fill('货物尚未离库，误操作')
  await page.getByText('整单冲销', { exact: true }).click(); await acceptModal(page)
  await expect(page.getByText('RT-TEST · 已冲销', { exact: true })).toBeVisible()
  await page.getByText('退货统计', { exact: true }).click()
  await expect(page.getByText('退货 1 单 · 冲销 1 单', { exact: true })).toBeVisible()
  await expect(page.locator('.stat-row').first()).toContainText('净退货 0')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(ctx.errors).toEqual([])
})

test('unknown supplier only offers owner return and retries the same confirmation', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const ctx = await setup(page, { noSupplier: true, loseConfirmResponse: true })
  await fillReturn(page)
  await expect(page.getByText('退给供应商：原供应商', { exact: true })).toHaveCount(0)
  await page.getByText('确认退货', { exact: true }).click(); await acceptModal(page)
  await expect(page.getByText('继续确认结果', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByText('继续确认结果', { exact: true }).click()
  await expect(page.locator('.error')).toContainText('临时权限核验失败')
  await expect(page.getByText('继续确认结果', { exact: true })).toBeVisible()
  await page.getByText('继续确认结果', { exact: true }).click()
  await expect(page.getByText('RT-TEST · 已完成', { exact: true })).toBeVisible()
  expect(ctx.confirms).toHaveLength(3)
  expect(ctx.confirms[2]).toEqual(ctx.confirms[0])
  expect(ctx.confirms[1]).toEqual(ctx.confirms[0])
  expect(ctx.payloads[0].recipient_type).toBe('OWNER')
  await page.screenshot({ path: '/tmp/wmspda-return-desktop.png', fullPage: false })
  expect(ctx.errors).toEqual([])
})

test('saved draft can select another owner after removing all lines', async ({ page }) => {
  await setup(page)
  await fillReturn(page)
  await page.getByText('保存草稿', { exact: true }).click()
  await expect(page.getByText('RT-TEST · 草稿', { exact: true })).toBeVisible()
  await page.getByText('移除', { exact: true }).click()
  const searched = page.waitForRequest(request => request.url().includes('/return-orders/products/'))
  await page.getByText('搜索', { exact: true }).click()
  expect(new URL((await searched).url()).searchParams.has('owner_id')).toBe(false)
  await expect(page.locator('.product-row')).toHaveCount(2)
})
