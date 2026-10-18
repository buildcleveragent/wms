import { expect, test } from '@playwright/test'

const source = { id: 1, code: 'A', name: '原货主甲' }
const target = { id: 2, code: 'B', name: '新货主乙' }
const third = { id: 3, code: 'C', name: '另一个货主' }
const shared = { id: 11, code: 'A-11', name: '共享客户' }
const targetOnly = { id: 12, code: 'B-12', name: '乙的客户' }

async function start(page, { emptySource = false } = {}) {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error' && /SyntaxError|ReferenceError|TypeError|Failed to resolve/.test(message.text())) errors.push(message.text())
  })
  await page.route('**/api/pda/token/', route => route.fulfill({ json: { access: 'test', refresh: 'test-refresh' } }))
  await page.route('**/api/auth/profile/', route => route.fulfill({ json: {
    user: { id: 7, username: 'operator', warehouse_id: 1 }, perms: [],
    capabilities: { can_process_warehouse_assisted_outbound: true },
  } }))
  await page.route('**/api/outbound/assisted-orders/owners/?**', route => route.fulfill({ json: [source, target, third] }))
  await page.route('**/api/outbound/assisted-orders/customers/?**', route => {
    const id = new URL(route.request().url()).searchParams.get('owner_id')
    const json = id === '1' ? (emptySource ? [] : [shared])
      : id === '2' ? [{ ...shared, code: 'B-11' }, targetOnly] : []
    return route.fulfill({ json })
  })
  await page.route('**/api/outbound/assisted-orders/history/?**', route => route.fulfill({ json: { results: [], count: 0 } }))
  await page.route('**/api/outbound/assisted-orders/stats/?**', route => route.fulfill({ json: { summary: {} } }))
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('operator')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.goto('/#/pages/outbound/assisted')
  await page.locator('.choice-row').filter({ hasText: source.name }).click()
  await expect(page.getByTestId('new-owner-section')).toBeVisible()
  return errors
}

async function chooseNewOwner(page, name = target.name) {
  const section = page.getByTestId('new-owner-section')
  await section.locator('input').fill(name)
  await section.locator('uni-button').filter({ hasText: '搜索' }).click()
  await expect(section.locator('.choice-row').filter({ hasText: source.name })).toHaveCount(0)
  await section.locator('.choice-row').filter({ hasText: name }).click()
}

test('optional owner follows customer section and retains or clears customer by relationship', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const errors = await start(page)
  const section = page.getByTestId('new-owner-section')
  expect(await section.evaluate(el => el.previousElementSibling.textContent)).toContain('2. 选择客户')
  await page.locator('.choice-row').filter({ hasText: shared.name }).click()
  await expect(page).toHaveURL(/pages\/outbound\/assisted$/)
  await section.locator('input').fill('未选定')
  await page.getByText('继续选品', { exact: true }).click()
  await expect(page.getByText('请选择搜索到的新货主，或清空新货主输入框', { exact: true })).toBeVisible()
  await chooseNewOwner(page)
  await expect(page.locator('.summary-card')).toContainText('原货主甲 → 新货主乙 → 共享客户')
  await expect(page.getByText('B-11', { exact: true })).toBeVisible()
  await expect(page.getByText('请选择搜索到的新货主，或清空新货主输入框', { exact: true })).toBeHidden()
  await page.screenshot({ path: '/tmp/wmspda-transfer-mobile.png', fullPage: true })
  await section.getByText('清空新货主', { exact: true }).click()
  await expect(page.getByText('A-11', { exact: true })).toBeVisible()
  await chooseNewOwner(page, third.name)
  await expect(page.locator('.summary-card')).toHaveCount(0)
  await expect(page.getByText('没有匹配客户', { exact: true })).toBeVisible()
  await section.getByText('清空新货主', { exact: true }).click()
  await page.locator('.choice-row').filter({ hasText: shared.name }).click()
  await page.getByText('继续选品', { exact: true }).click()
  await expect(page).toHaveURL(/assisted_products/)
  await expect(page.locator('.transfer-price-input')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('no source customers still allows transfer, two prices and stable retry payload', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  const errors = await start(page, { emptySource: true })
  await expect(page.getByText('没有匹配客户', { exact: true })).toBeVisible()
  await chooseNewOwner(page)
  await page.locator('.choice-row').filter({ hasText: targetOnly.name }).click()
  const requestedOwners = []
  await page.route('**/api/outbound/assisted-orders/products/?**', route => {
    requestedOwners.push(new URL(route.request().url()).searchParams.get('owner_id'))
    return route.fulfill({ json: [{ id: 21, name: '原货主商品', sku: 'A-21', code: '21',
      available_qty: 100, base_unit_name: '件', default_price: '12', unit_options: [] }] })
  })
  const payloads = []
  await page.route('**/api/outbound/assisted-orders/', async route => {
    payloads.push(route.request().postDataJSON())
    await route.fulfill(payloads.length < 3
      ? { status: 503, json: { detail: '测试重试' } }
      : { status: 201, json: { order_id: 800, task_id: 900, transfer_no: 'HZ-TEST' } })
  })
  await page.route('**/api/outbound/pda/pick-tasks/900/', route => route.fulfill({ json: {
    id: 900, task_no: 'PICK-TEST', owner_name: target.name, warehouse_name: '测试仓库',
    status: 'RELEASED', review_status: 'PENDING', posting_status: 'UNPOSTED',
  } }))
  await page.route('**/api/outbound/pda/pick-tasks/900/lines/', route => route.fulfill({ json: [] }))
  await page.getByText('继续选品', { exact: true }).click()
  await page.locator('.assisted-product-search input').fill('原货主商品')
  await page.locator('.search-button').filter({ hasText: '搜索' }).click()
  const result = page.locator('.result-row')
  await result.locator('input').nth(0).fill('10')
  await result.locator('input').nth(1).fill('12')
  await result.getByText('加入', { exact: true }).click()
  await page.locator('.selected-row .transfer-price-input input').fill('8')
  await expect(page.locator('.transfer-route')).toContainText('原货主甲 → 新货主乙 → 乙的客户')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await expect(page.getByText('已加入 10 件', { exact: true })).toBeHidden()
  await page.screenshot({ path: '/tmp/wmspda-transfer-prices-desktop.png', fullPage: true })
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await page.getByText('创建代办出库单', { exact: true }).click()
    await page.getByText('确认创建', { exact: true }).click()
    await expect.poll(() => payloads.length).toBe(attempt + 1)
    await expect(page.getByText('创建代办出库单', { exact: true })).toBeVisible()
  }
  expect(payloads[0]).toEqual(payloads[1])
  expect(payloads[0]).toMatchObject({ owner_id: 1, new_owner_id: 2, customer_id: 12,
    items: [{ product_id: 21, qty: 10, price: 12, transfer_price: 8 }] })
  expect(requestedOwners.every(id => id === '1')).toBe(true)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByText('查看出库单', { exact: true }).click()
  await expect(page).toHaveURL(/assisted_cart/)
  await expect(page.locator('.transfer-price-input input')).toHaveValue('8')
  await page.locator('.transfer-price-input input').fill('')
  await expect(page.locator('.transfer-route')).toContainText('原货主甲 → 新货主乙 → 乙的客户')
  await expect(page.getByText('测试重试', { exact: true })).toBeHidden()
  await page.screenshot({ path: '/tmp/wmspda-transfer-cart-mobile.png', fullPage: true })
  await page.getByText('创建代办出库单', { exact: true }).click()
  await page.getByText('确认创建', { exact: true }).click()
  await expect(page).toHaveURL(/picking\/task_detail\?task_id=900/)
  expect(payloads[2].request_id).not.toBe(payloads[0].request_id)
  expect(payloads[2].items[0]).toEqual({ product_id: 21, qty: 10, price: 12 })
  expect(errors).toEqual([])
})
