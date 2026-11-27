import { expect, test } from '@playwright/test'

test('operator receives each serial in the original task and replays a lost response', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const errors = [], requests = [], receipts = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error' && !/net::ERR_FAILED|保存收货失败/.test(message.text())) {
      errors.push(message.text())
    }
  })
  const lines = [
    { id: 11, product_name: '序列号商品', serial_control: true, qty_plan: 2, qty_done: 0, qty_pending: 2 },
    { id: 12, product_name: '普通商品', serial_control: false, qty_plan: 3, qty_done: 0, qty_pending: 3 },
  ]
  const task = {
    id: 5, task_no: 'RCV-5', status: 'IN_PROGRESS', is_assigned_to_me: true, can_record: true, lines,
  }
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    requests.push(url)
    if (path === '/api/pda/token/') return route.fulfill({ json: { access: 'test', refresh: 'test-refresh' } })
    if (path === '/api/auth/profile/') return route.fulfill({ json: {
      user: { id: 7, username: 'operator' }, perms: [], capabilities: { can_export_products: true },
    } })
    if (path === '/api/pda/home-todos/') return route.fulfill({ json: { scope: 'warehouse-1', items: [] } })
    if (path === '/api/products/export-all-excel/') return route.fulfill({ body: 'mock workbook' })
    if (path === '/api/inbound/pda/tasks/') return route.fulfill({ json: { results: [task] } })
    if (path === '/api/inbound/pda/tasks/5/') return route.fulfill({ json: task })
    if (path === '/api/inbound/pda/tasks/5/locations/') {
      return route.fulfill({ json: [{ id: 21, code: 'RCV-A', name: '收货区' }] })
    }
    if (path === '/api/inbound/pda/tasks/5/record-receipt/') {
      const payload = route.request().postDataJSON()
      receipts.push(payload)
      const replay = receipts.slice(0, -1).some(item => item.request_id === payload.request_id)
      if (!replay) {
        const line = lines.find(item => item.id === payload.line_id)
        line.qty_done += payload.qty_ok
        line.qty_pending = line.qty_plan - line.qty_done
        line.receive = { qty_ok: line.qty_done, lot_no: payload.lot_no }
        if (line.qty_pending === 0) line.finished_at = '2026-11-25T10:00:00Z'
      }
      if (receipts.length === 1) return route.abort('failed')
      return route.fulfill({ json: { task, idempotent: replay } })
    }
    return route.fulfill({ status: 404, json: { detail: 'unexpected test endpoint' } })
  })

  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('operator')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  const download = page.waitForEvent('download')
  await page.getByText('全部商品档案导出', { exact: true }).click()
  await download
  await page.getByText('收货(有订单)', { exact: true }).click()
  await page.getByText('RCV-5', { exact: true }).click()
  await expect(page).toHaveURL(/receive_task_detail\?task_id=5/)
  await expect(page).toHaveTitle('有订单收货')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)

  const serialLine = page.locator('.line-card').filter({ hasText: '序列号商品' })
  const normalLine = page.locator('.line-card').filter({ hasText: '普通商品' })
  await expect(normalLine.locator('.quantity-grid input').first()).toHaveValue('3')
  await expect(normalLine.getByText('本件序列号', { exact: false })).toHaveCount(0)
  await expect(serialLine.locator('.quantity-grid')).toHaveCount(0)
  await serialLine.getByText('查询库位').click()
  await page.getByText('RCV-A', { exact: true }).click()
  const submit = serialLine.getByText('登记本件', { exact: true })
  await submit.click()
  await expect(page.getByText('请扫描或输入本件序列号', { exact: true })).toBeVisible()
  expect(receipts).toHaveLength(0)
  const serial = serialLine.locator('.field-group').filter({ hasText: '本件序列号' }).locator('input')
  const lot = serialLine.locator('.field-group').filter({ hasText: '批次号' }).locator('input')
  await lot.fill('LOT-A')
  await serial.fill('SN-001')
  await serial.press('Enter')
  await expect.poll(() => receipts.length).toBe(1)
  await expect(serial).toHaveValue('SN-001')
  await expect(serial).toBeDisabled()
  await expect(lot).toBeDisabled()
  await expect(serialLine.getByText('收货结果待确认，请原样再次提交本行。')).toBeVisible()
  await submit.click()
  await expect(serialLine.locator('.quantity-summary')).toContainText('已登记 1')
  expect(receipts[1]).toEqual(receipts[0])
  expect(receipts[0]).toMatchObject({ serial_no: 'SN-001', line_id: 11, location_id: 21,
    qty_ok: 1, qty_damage: 0, qty_reject: 0, lot_no: 'LOT-A', finalize: false })
  await expect(serial).toHaveValue('')
  await expect(serialLine.getByText('RCV-A · 收货区')).toBeVisible()
  await expect(lot).toHaveValue('LOT-A')
  await serial.fill('SN-002')
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: testInfo.outputPath('serial-receipt.png') })
  await serialLine.getByText('保存并结束该行').click()
  await expect(serialLine.locator('.line-status')).toHaveText('已完成')
  expect(receipts[2]).toMatchObject({ serial_no: 'SN-002', line_id: 11, location_id: 21,
    qty_ok: 1, qty_damage: 0, qty_reject: 0, finalize: true, variance_reason: '' })
  expect(receipts[2].request_id).not.toBe(receipts[0].request_id)
  await normalLine.getByText('查询库位').click()
  await page.getByText('RCV-A', { exact: true }).click()
  await normalLine.getByText('保存收货数量').click()
  await expect(normalLine.locator('.line-status')).toHaveText('已完成')
  expect(receipts[3]).toMatchObject({ qty_ok: 3, line_id: 12 })
  expect(receipts[3]).not.toHaveProperty('serial_no')
  const origins = new Set(requests.map(url => url.origin))
  expect(origins.size).toBe(1)
  if (process.env.VITE_API_BASE_URL) {
    expect([...origins]).toEqual([new URL(process.env.VITE_API_BASE_URL).origin])
  }
  expect(errors).toEqual([])
})
