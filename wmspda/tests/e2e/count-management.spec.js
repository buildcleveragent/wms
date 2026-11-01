import { test, expect as baseExpect } from '@playwright/test'
const expect = baseExpect.configure({ timeout: 20000 })
test.setTimeout(180000)
test.use({ actionTimeout: 20000, locale: 'zh-CN' })
async function setup(page, manager = true) {
  const errors = [], calls = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', event => { if (event.type() === 'error' && !event.text().includes('400') && !event.text().includes('500')) errors.push(event.text()) })
  let task = null, failedRelease = true, failedPosting = true
  const line = { id: 11, product_id: 1, product_code: 'COUNT-1', product_display_name: '汽水 · 测试品牌·橙味', product_name: '汽水', product_spec: '480ml', location_code: 'A-01', count_status: 'NOT_COUNTED', qty_book: '5', qty_diff: '0', qty_counted: '0' }
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    const json = data => route.fulfill({ json: data })
    if (path.endsWith('/token/')) return json({ access: 'count-e2e', refresh: 'count-refresh' })
    if (path.endsWith('/profile/')) return json({ user: { id: 1, username: 'count' }, perms: [], capabilities: { can_manage_count: manager } })
    if (path.endsWith('/home-todos/')) return json({ items: [] })
    if (path.endsWith('/count-tasks/options/')) return json({ warehouses: [{ id: 1, name: '测试仓库' }], owners: url.searchParams.has('warehouse_id') ? [{ id: 1, name: '测试货主' }] : [], subwarehouses: [], zones: [{ id: 2, name: '存储区' }] })
    if (path === '/api/pda/count-tasks/') {
      if (request.method() === 'POST') {
        calls.push('create')
        task = { id: 1, task_no: 'PD-E2E-001', status: 'DRAFT', review_status: 'NOT_READY', posting_status: 'NOT_READY', owner_name: '测试货主', warehouse_name: '测试仓库', scope: 'ALL', blind: true, can_record: true, round_no: 1, line_count: 1, recount_threshold: '0', scope_payload: request.postDataJSON(), scope_labels: {} }
        return json({ ...task, truncated: false })
      }
      const stage = url.searchParams.get('stage')
      const matches = task && (stage === 'draft' && task.status === 'DRAFT' || stage === 'work' && ['RELEASED', 'IN_PROGRESS'].includes(task.status) || stage === 'review' && task.review_status === 'PENDING' || stage === 'posting' && ['PENDING', 'FAILED'].includes(task.posting_status))
      return json(matches ? [task] : [])
    }
    if (path.endsWith('/count-tasks/1/')) return json(task)
    if (path.endsWith('/lines/')) return json([line])
    if (path.endsWith('/release/')) {
      calls.push('release')
      if (failedRelease) { failedRelease = false; return route.fulfill({ status: 400, json: ['范围冲突，请稍后重试'] }) }
      task.status = 'RELEASED'; return json(task)
    }
    if (path.endsWith('/record/')) { calls.push('record'); line.qty_counted = request.postDataJSON().qty_counted; line.qty_diff = '-1'; line.count_status = 'COUNTED'; task.status = 'IN_PROGRESS'; return json(line) }
    if (path.endsWith('/submit/')) { task.status = 'COMPLETED'; task.review_status = 'PENDING'; return json({ outcome: 'PENDING_APPROVAL' }) }
    if (path.endsWith('/approve/')) { task.review_status = 'APPROVED'; task.posting_status = 'PENDING'; return json(task) }
    if (path.endsWith('/post/')) {
      calls.push('post')
      if (failedPosting) { failedPosting = false; task.posting_status = 'FAILED'; return route.fulfill({ status: 500, json: { detail: '过账失败，请重试' } }) }
      task.posting_status = 'POSTED'; return json({ posted: true })
    }
    return json({ results: [] })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('count')
  await page.locator('uni-input input').nth(1).fill('password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.getByText('盘点', { exact: true }).click()
  await expect(page).toHaveTitle('盘点任务')
  return { errors, calls }
}
async function confirm(page) { await page.locator('.uni-modal__btn_primary').click() }
async function picker(page, text) {
  await page.getByText(text, { exact: true }).click()
  if (page.viewportSize().width > 1000) {
    // Uni-app uses a direct dropdown on desktop and a wheel with Done on phones.
    await page.locator('.uni-picker-container .uni-picker-item:visible').filter({ hasText: text === '选择仓库' ? /^测试仓库$/ : /^测试货主$/ }).click()
  } else {
    await page.locator('.uni-picker-toggle .uni-picker-action-confirm').click()
  }
}
for (const width of [390, 1280]) {
  test(`create, revisit draft, publish, approve and retry posting at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const state = await setup(page)
    await page.getByText('新建盘点', { exact: true }).click()
    await expect(page).toHaveTitle('新建盘点')
    await picker(page, '选择仓库')
    await picker(page, '选择货主')
    await page.screenshot({ path: `/tmp/count-create-${width}.png`, fullPage: true })
    await page.getByText('保存草稿', { exact: true }).click()
    await expect(page.getByText('PD-E2E-001', { exact: true })).toBeVisible()
    await expect(page.getByText('汽水 · 测试品牌·橙味', { exact: true })).toBeVisible()
    await page.getByText('返回盘点列表', { exact: true }).click()
    await page.getByText('PD-E2E-001', { exact: true }).click()
    await page.getByText('确认发布', { exact: true }).click(); await confirm(page)
    await expect(page.locator('.error')).toContainText('范围冲突')
    await page.getByText('确认发布', { exact: true }).click(); await confirm(page)
    await expect(page.getByText('待认领', { exact: true })).toBeVisible()
    await page.locator('.entry input').fill('4')
    await page.getByText('保存', { exact: true }).click()
    await expect(page.getByText('已盘：4', { exact: true })).toBeVisible()
    await page.getByText('完成并提交', { exact: true }).click(); await confirm(page)
    await page.getByText('审核通过', { exact: true }).click(); await confirm(page)
    await page.getByText('返回盘点列表', { exact: true }).click()
    await expect(page.getByText('PD-E2E-001', { exact: true })).toBeVisible()
    await page.getByText('PD-E2E-001', { exact: true }).click()
    await page.getByText('确认盘盈盘亏过账', { exact: true }).click(); await confirm(page)
    await expect(page.locator('.error')).toContainText('过账失败')
    await page.getByText('返回盘点列表', { exact: true }).click()
    await expect(page.getByText('过账失败，可重试', { exact: true })).toBeVisible()
    await page.screenshot({ path: `/tmp/count-posting-${width}.png`, fullPage: true })
    await page.getByText('PD-E2E-001', { exact: true }).click()
    await page.getByText('重试过账', { exact: true }).click(); await confirm(page)
    await expect(page.getByText('已过账', { exact: true })).toBeVisible()
    await page.getByText('返回盘点列表', { exact: true }).click()
    await expect(page.getByText('暂无待过账', { exact: true })).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(state.calls.filter(call => call === 'create')).toHaveLength(1)
    expect(state.calls.filter(call => call === 'post')).toHaveLength(2)
    expect(state.errors).toEqual([])
  })
}
test('operator cannot access creation or management tabs', async ({ page }) => {
  await setup(page, false)
  await expect(page.getByText('新建盘点', { exact: true })).toHaveCount(0)
  await expect(page.getByText('待过账', { exact: true })).toHaveCount(0)
  await page.goto('/#/pages/inventory/stocktake/create')
  await expect(page.locator('.card')).toContainText('无盘点管理权限')
  await expect(page.getByText('保存草稿', { exact: true })).toHaveCount(0)
})
