import { expect, test } from './helpers/browser-test.js'
import { expectHttpFailure } from '../../../frontend/browser-diagnostics.mjs'

async function openOperatorSession(page) {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => localStorage.setItem('access', 'fixture-access'))
  await page.route('**/api/auth/profile/', route => route.fulfill({ json: {
    user: { id: 7, username: 'operator', roles: ['warehouse_operator'] },
    roles: ['warehouse_operator'], capabilities: {}, warehouse: { id: 1 },
  } }))
}

test('original receipt can be completed after missing expiry evidence is supplied', async ({ page }) => {
  await openOperatorSession(page)
  let posted = false
  const traceRequests = []
  const row = { id: 31, product_name: '样品', unit: '件', location: 'A1', batch_no: 'B1',
    qty_ok: '1', qty_damage: '0', qty_reject: '0', photos: [] }
  function detail() {
    return { id: 2, number: 'CR-2', status_label: posted ? '已完成' : '待入账',
      version: posted ? 7 : 5, allowed_actions: posted ? [] : ['retry-posting'],
      lines: [], events: [], photos: [], receipts: [{ id: 12, actor: 'operator',
        posted_at: posted ? '2026-11-18T12:00:00+08:00' : null,
        posting_label: posted ? '已回补库存' : '待入账',
        posting_hold_reason: posted ? '' : '待补齐验收行效期资料。',
        lines: [{ ...row, expiry_date: posted ? '2027-02-01' : null, missing_dates: posted ? [] : ['expiry_date'] }],
      }] }
  }
  await page.route('**/api/inbound/customer-returns/2/', route => route.fulfill({ json: detail() }))
  await page.route('**/api/inbound/customer-returns/2/receipts/12/trace/', route => {
    traceRequests.push(route.request().postDataJSON())
    posted = true
    return route.fulfill({ json: detail() })
  })
  await page.goto('/#/pages/customer-returns/index?id=2')
  await expect(page.getByText('待补齐验收行效期资料。')).toBeVisible()
  await page.getByText('补录效期资料', { exact: true }).click()
  await page.getByRole('textbox').fill('2027-02-01')
  await page.getByText('保存日期并重试入账').click()
  await expect(page.getByText('已回补库存')).toBeVisible()
  expect(traceRequests).toEqual([{
    request_id: expect.any(String), version: 5, receipt_line_id: 31, expiry_date: '2027-02-01',
  }])
})

test('partial receipt survives a lost confirmation and page reload with the original request', async ({ page }) => {
  await openOperatorSession(page)
  const base = '/api/inbound/customer-returns/2/'
  const requests = []
  function detail() {
    const received = requests.length > 0
    return {
      id: 2, number: 'CR-2', version: received ? 7 : 5,
      status_label: received ? '部分验收' : '待到货', allowed_actions: ['receive'],
      lines: [{ id: 11, original_line_no: 1, product_name: '样品', unit: '件', qty: '3',
        qty_ok: received ? '1' : '0', received_qty: received ? '1' : '0',
        pending_qty: received ? '2' : '3', qty_damage: '0', qty_reject: '0', photos: [] }],
      receipts: received ? [{id: 12, actor: 'operator', posted_at: '2026-11-18T12:00:00+08:00',
        posting_label: '已回补库存', lines: []}] : [],
      events: [], photos: [], pending_photos: [],
    }
  }
  await page.route('**' + base, route => route.fulfill({ json: detail() }))
  await page.route('**' + base + 'receipt-options/', route => route.fulfill({
    json: { locations: [{ id: 7, code: 'A1' }] },
  }))
  expectHttpFailure(page, base + 'receipts/', 503)
  await page.route('**' + base + 'receipts/', route => {
    requests.push(route.request().postDataJSON())
    return requests.length === 1
      ? route.fulfill({ status: 503, json: {detail: '网关未收到仓库回执'} })
      : route.fulfill({ json: detail() })
  })
  await page.goto('/#/pages/customer-returns/index?id=2')
  await page.getByText('增加验收行', { exact: true }).click()
  await page.locator('uni-label').filter({ hasText: '合格数量' }).locator('input').fill('1')
  await page.getByText('确认本次验收并入账', { exact: true }).click()
  await expect(page.locator('.returns-page .error')).toContainText('网关未收到仓库回执')
  await page.reload()
  await page.getByText('核对本次验收结果', { exact: true }).click()
  await expect(page.getByText('增加验收行', { exact: true })).toBeVisible()
  await expect(page.getByText('待到货 2', { exact: true })).toBeVisible()
  expect(requests).toHaveLength(2)
  expect(requests[0]).toMatchObject({request_id: expect.any(String), version: 5,
    lines: [{ line_id: 11, qty_ok: '1', location_id: 7 }]})
  expect(requests[1]).toEqual(requests[0])
})
