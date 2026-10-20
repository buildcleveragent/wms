import { test, expect } from '@playwright/test'

async function setup(page, { allowed = true, waiting = false } = {}) {
  const errors = [], mutations = []
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', e => { if (e.type() === 'error' && /SyntaxError|ReferenceError|TypeError|Failed to resolve|Unhandled/.test(e.text())) errors.push(e.text()) })
  let order = {
    id: 7, order_no: 'OB-REVIEW-007', owner_name: '测试货主', warehouse_name: '测试仓库',
    customer_name: '客户甲', total_qty: '97.2500', biz_date: '2026-10-21',
    approval_status: 'OWNER_APPROVED', updated_at: '2026-10-21T00:00:00Z',
    can_warehouse_confirm: true, can_warehouse_reject: true, delivery_method: 'OWN_TRUCK',
    contact: '张先生', contact_phone: '13800000000', ship_to: '测试配送地址',
    amount_status: 'PARTIAL', priced_amount: '12.50', unpriced_line_count: 1,
    replenishment_wait: { shortages: [], task_nos: [] },
    lines: [
      { id: 1, product_name: '包材', product_sku: 'BOX', base_qty: '96.0000', allocated_qty: '96.0000', shortage_qty: '0.0000', is_priced: false, base_price: '0', amount: '0' },
      { id: 2, product_name: '计价商品', product_sku: 'PRICED', base_qty: '1.2500', allocated_qty: '1.2500', shortage_qty: '0.0000', is_priced: true, base_price: '10.0000', amount: '12.50' },
    ],
  }
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    if (path.endsWith('/token/')) return route.fulfill({ json: { access: 'test-access', refresh: 'test-refresh' } })
    if (path.endsWith('/profile/')) return route.fulfill({ json: {
      user: { id: 3, username: 'warehouse-manager' }, perms: ['outbound.approve_outbound_as_wh_manager'],
      capabilities: { can_review_warehouse_outbound: allowed },
    } })
    if (path.endsWith('/warehouse-review/options/')) return route.fulfill({ json: {
      owners: [{ id: 1, name: '测试货主' }], warehouses: [{ id: 2, name: '测试仓库' }],
    } })
    if (path.endsWith('/warehouse-review/')) return route.fulfill({ json: { results: [order], next: null, count: 1 } })
    if (path.endsWith('/warehouse-review/7/')) return route.fulfill({ json: order })
    if (/warehouse-(confirm|reject)\/$/.test(path)) {
      const payload = route.request().postDataJSON()
      mutations.push({ path, payload })
      const reject = path.endsWith('warehouse-reject/')
      order = {
        ...order, updated_at: '2026-10-21T00:01:00Z',
        approval_status: reject ? 'WHS_REJECTED' : waiting ? 'WHS_PENDING' : 'WHS_APPROVED',
        can_warehouse_confirm: waiting && !reject, can_warehouse_reject: waiting && !reject,
        approved_by_warehouse_name: '仓库管理员', approved_at_warehouse: '2026-10-21T00:01:00Z',
        warehouse_reject_reason: reject ? payload.reason : '',
        replenishment_wait: { shortages: [], task_nos: waiting ? ['RPL-007'] : [] },
      }
      return route.fulfill({ json: order })
    }
    errors.push(`Unexpected API: ${path}`)
    return route.fulfill({ status: 404, json: { detail: 'unexpected endpoint' } })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('warehouse-manager')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  return { errors, mutations }
}

async function openDetail(page) {
  await page.getByText('出库确认', { exact: true }).click()
  await expect(page).toHaveURL(/outbound\/review\/index/)
  await expect(page.locator('.order-row')).toHaveCount(1)
  await page.locator('.order-row').click()
  await expect(page).toHaveURL(/outbound\/review\/detail/)
  await expect(page.locator('.product-line')).toHaveCount(2)
}

test('desktop confirms an optional-price order and shows released state', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const context = await setup(page)
  await page.locator('.uni-tabbar__item').filter({ hasText: '功能' }).click()
  await expect(page).toHaveURL(/pages\/features\/index/)
  await openDetail(page)
  await expect(page.locator('.amount-summary')).toContainText('已计价小计 ¥12.50，另有 1 行未计价')
  await expect(page.locator('.product-line').first()).toContainText('出库数量 96')
  await expect(page.locator('.product-line').first()).not.toContainText('96.0000')
  await expect(page.locator('.product-line').first()).not.toContainText('¥')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await page.screenshot({ path: '/tmp/wms-warehouse-review-desktop.png', fullPage: true })
  await page.locator('.confirm-button').click()
  await page.locator('.uni-modal').getByText('确定', { exact: true }).click()
  await expect(page.locator('.status')).toHaveText('已确认')
  await expect(page.locator('.confirm-button')).toHaveCount(0)
  expect(context.mutations).toHaveLength(1)
  expect(context.mutations[0].payload).toEqual({ expected_updated_at: '2026-10-21T00:00:00Z' })
  expect(context.errors).toEqual([])
})

test('mobile rejects with a mandatory reason and shows a read-only result', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const context = await setup(page)
  await openDetail(page)
  await page.locator('.reject-button').click()
  await expect(page.getByText('请填写 1 至 500 字的退回原因', { exact: true })).toBeVisible()
  expect(context.mutations).toHaveLength(0)
  await page.locator('textarea').fill('配送地址需确认')
  await page.locator('.reject-button').click()
  await page.locator('.uni-modal').getByText('确定', { exact: true }).click()
  await expect(page.locator('.status')).toHaveText('已退回')
  await expect(page.getByText('最近仓库退回原因：配送地址需确认')).toBeVisible()
  await expect(page.locator('.reject-button')).toHaveCount(0)
  await expect(page.getByText('订单已退回', { exact: true })).not.toBeVisible()
  await page.evaluate(() => uni.pageScrollTo({ scrollTop: 0, duration: 0 }))
  await page.screenshot({ path: '/tmp/wms-warehouse-review-mobile.png', fullPage: true })
  expect(context.mutations[0].payload.reason).toBe('配送地址需确认')
  expect(context.errors).toEqual([])
})

test('shortfall confirmation displays waiting and the replenishment task', async ({ page }) => {
  const context = await setup(page, { waiting: true })
  await openDetail(page)
  await page.locator('.confirm-button').click()
  await page.locator('.uni-modal').getByText('确定', { exact: true }).click()
  await expect(page.locator('.status')).toHaveText('等待补货')
  await expect(page.getByText('补货任务：RPL-007')).toBeVisible()
  expect(context.errors).toEqual([])
})

test('unauthorized role has no entry and direct access remains blocked', async ({ page }) => {
  const context = await setup(page, { allowed: false })
  await expect(page.getByText('出库确认', { exact: true })).toHaveCount(0)
  await page.goto('/#/pages/outbound/review/detail?id=7')
  await expect(page.getByText('没有当前仓库的出库审核权限', { exact: true })).toBeVisible()
  await expect(page.locator('.confirm-button')).toHaveCount(0)
  expect(context.mutations).toHaveLength(0)
  expect(context.errors).toEqual([])
})
