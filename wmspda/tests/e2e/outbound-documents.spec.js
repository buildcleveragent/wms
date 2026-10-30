import { test, expect } from '@playwright/test'
async function setup(page, { failPost = false, blocked = false, failPrint = false, assisted = false, picking = false } = {}) {
  let posted = false, posts = 0, tickets = 0
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  if (blocked) await page.addInitScript(() => { window.open = () => null })
  let task = { id: 7, task_no: 'JH-007', ref_no: 'CK-007', status: 'COMPLETED', posting_status: 'NOT_READY', review_status: 'PENDING', owner_name: '测试货主', warehouse_name: '测试仓库', is_warehouse_assisted: assisted, can_self_review: assisted, can_review: true, can_retry_post: false, review_task: { id: 8, task_no: 'FH-008' } }
  if (picking) task.status = 'IN_PROGRESS'
  const lines = [{ id: 1, product_id: 2, product_name: '测试商品', order_line_id: 3, base_uom_id: 1, base_uom_name: '件', order_qty: '2', qty_plan: '2', qty_done: '2', picking_details: [] }]
  const order = { id: 3, order_no: 'CK-007', customer_name: '客户甲', owner_name: '测试货主', warehouse_name: '测试仓库' }
  await page.context().route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    const json = data => route.fulfill({ json: data })
    if (path.endsWith('/token/')) return json({ access: 'test-access', refresh: 'test-refresh' })
    if (path.endsWith('/profile/')) return json({ user: { id: 3, username: 'warehouse' }, perms: ['tasking.claim_task_as_wh_operator', 'outbound.process_warehouse_assisted_outbound'], capabilities: { can_process_warehouse_assisted_outbound: true, can_self_review_warehouse_assisted_outbound: true } })
    if (path.endsWith('/home-todos/')) return json({ scope: 'test', items: [] })
    if (path.endsWith('/review-context/')) return json({ task, order, lines, content_error: '', quantity_totals: [{ base_uom_id: 1, base_uom_name: '件', order_qty: '2', qty_plan: '2', qty_done: '2' }] })
    if (path.endsWith('/pick-tasks/7/')) return json(task)
    if (path.endsWith('/pick-tasks/7/lines/')) return json(lines)
    if (path.endsWith('/pick-tasks/')) return json({ results: posted === (url.searchParams.get('stage') === 'completed') ? [{ ...task, order }] : [], count: 1, next: null })
    if (path.endsWith('/history-options/')) return json({ owners: [], operators: [], statuses: [] })
    if (path.endsWith('/history/')) return json({ results: [{ id: 3, order_no: 'CK-007', owner: { name: '测试货主' }, customer: { name: '客户甲' }, task, business_status: 'COMPLETED', business_status_label: '已完成出库', can_reprint: posted, can_print_pick: true }], count: 1, next: null })
    if (path.endsWith('/create-review-task/')) {
      task = { ...task, status: 'COMPLETED' }
      return json({ review_task_id: 8 })
    }
    if (path.endsWith('/post/')) {
      posts++
      if (failPost) return route.fulfill({ status: 500, json: { detail: '测试过账失败' } })
      posted = true; task = { ...task, posting_status: 'POSTED', review_status: 'APPROVED', can_review: false, can_print_delivery: true }
      return json({ posted: true, idempotent: posts > 1 })
    }
    if (path.includes('print-ticket')) { tickets++; return failPrint ? route.fulfill({ status: 500, json: { detail: '打印票据暂不可用' } }) : json({ ticket: 'short-ticket', expires_in: 120 }) }
    if (path.endsWith('/print/')) {
      const title = url.searchParams.get('kind') === 'pick' ? '拣货单' : '送货单'
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<title>${title}预览</title><h1>${title}</h1><p>货主：测试货主 客户：客户甲</p>` })
    }
    return json({ results: [], next: null })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('warehouse')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.goto(`/#/pages/${assisted || picking ? 'picking/task_detail' : 'review/pick_task'}?task_id=7`)
  await expect(page.getByText('JH-007', { exact: false }).first()).toBeVisible()
  return { errors, posts: () => posts, tickets: () => tickets }
}
async function confirm(page) {
  await page.locator('uni-button').filter({ hasText: /^复核通过$/ }).click()
  await page.locator('.uni-modal__btn_primary').click()
}
for (const assisted of [false, true]) {
  test(`${assisted ? 'assisted' : 'standard'} picking prints only on request, never on completion`, async ({ page, context }) => {
    const state = await setup(page, { assisted, picking: true })
    await expect(page.getByText('完成拣货', { exact: true })).toBeVisible()
    expect(state.tickets()).toBe(0)
    expect(context.pages()).toHaveLength(1)
    await page.goto('/#/pages/picking/task_list')
    await expect(page.getByText('打印拣货单', { exact: true })).toBeVisible()
    expect(state.tickets()).toBe(0)
    const popup = context.waitForEvent('page')
    await page.getByText('打印拣货单', { exact: true }).click()
    const print = await popup
    await expect(print).toHaveURL(/kind=pick/)
    await expect(print.getByRole('heading', { name: '拣货单' })).toBeVisible()
    await print.close()
    await page.goto('/#/pages/picking/task_detail?task_id=7')
    await page.getByText('完成拣货', { exact: true }).click()
    await expect(page.getByText(assisted ? '复核并确认出库' : '拣货已提交，等待复核', { exact: true })).toBeVisible()
    expect(state.tickets()).toBe(1)
    expect(state.posts()).toBe(0)
    expect(context.pages()).toHaveLength(1)
    await page.reload()
    await expect(page.getByText(assisted ? '复核并确认出库' : '拣货已提交，等待复核', { exact: true })).toBeVisible()
    expect(state.tickets()).toBe(1)
    expect(state.errors).toEqual([])
  })
}
test('automatic delivery, completed search and reprint; reload never prints', async ({ page, context }) => {
  await page.setViewportSize({ width: 1366, height: 900 })
  const state = await setup(page)
  const popup = context.waitForEvent('page')
  await confirm(page)
  const print = await popup
  await expect(print).toHaveURL(/print\/\?kind=delivery/)
  await expect(page.getByText('补打送货单', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('补打送货单', { exact: true })).toBeVisible()
  expect(state.posts()).toBe(1); expect(state.tickets()).toBe(1)
  await page.goto('/#/pages/review/pick_task_list')
  await page.getByText('已完成', { exact: true }).click()
  await page.locator('uni-input input').fill('客户甲')
  await page.getByText('搜索', { exact: true }).click()
  await expect(page.getByText('客户：客户甲')).toBeVisible()
  await page.screenshot({ path: '/tmp/wms-doc-completed-desktop.png', fullPage: true })
  await page.getByText('补打送货单', { exact: true }).click()
  await expect.poll(state.tickets).toBe(2)
  expect(state.posts()).toBe(1); expect(state.errors).toEqual([])
})
test('mobile blocked popup leaves manual print button', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await setup(page, { blocked: true })
  await confirm(page)
  await expect(page.getByText('补打送货单', { exact: true })).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '/tmp/wms-doc-blocked-mobile.png', fullPage: true })
  expect(state.posts()).toBe(1); expect(state.errors).toEqual([])
})
test('cancel does not post; failed posting closes reserved window', async ({ page, context }) => {
  const state = await setup(page, { failPost: true })
  await page.getByText('复核通过', { exact: true }).click()
  await page.locator('.uni-modal__btn_default').click()
  expect(state.posts()).toBe(0); expect(context.pages()).toHaveLength(1)
  await confirm(page)
  await expect(page.getByText('测试过账失败', { exact: true }).first()).toBeVisible()
  await expect.poll(() => context.pages().length).toBe(1)
  expect(state.posts()).toBe(1); expect(state.tickets()).toBe(0)
})
test('print failure preserves posted state and permits reprint', async ({ page, context }) => {
  const state = await setup(page, { failPrint: true })
  await confirm(page)
  await expect(page.getByText('补打送货单', { exact: true })).toBeVisible()
  await expect.poll(() => context.pages().length).toBe(1)
  expect(state.posts()).toBe(1)
})

test('assisted self-review prints delivery and history separates picking and delivery', async ({ page, context }) => {
  const state = await setup(page, { assisted: true })
  await page.getByText('复核并确认出库', { exact: true }).click()
  const popup = context.waitForEvent('page')
  await page.locator('.uni-modal__btn_primary').click()
  await expect(await popup).toHaveURL(/kind=delivery/)
  await expect(page.getByText('补打送货单', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByText('本任务已完成复核并过账')).toBeVisible()
  expect(state.posts()).toBe(1); expect(state.tickets()).toBe(1)
  await page.goto('/#/pages/outbound/assisted_history')
  await expect(page.getByText('打印拣货单', { exact: true })).toBeVisible()
  await expect(page.getByText('补打送货单', { exact: true })).toBeVisible()
  const pick = context.waitForEvent('page')
  await page.getByText('打印拣货单', { exact: true }).click()
  await expect(await pick).toHaveURL(/kind=pick/)
  const delivery = context.waitForEvent('page')
  await page.getByText('补打送货单', { exact: true }).click()
  await expect(await delivery).toHaveURL(/kind=delivery/)
  expect(state.posts()).toBe(1); expect(state.errors).toEqual([])
})
