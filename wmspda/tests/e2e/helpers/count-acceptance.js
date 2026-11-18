import { expect } from '@playwright/test'

export function countLine(changes = {}) {
  return { id: 11, product_id: 1, product_code: 'QA-CODE', product_sku: 'QA-SKU',
    product_name: '验收商品', product_display_name: '验收商品', product_spec: '480ml',
    unit_barcode: '6901234567892', carton_barcode: '16901234567899', gtin: '26901234567896',
    location_code: 'A-01', lot_no: 'BATCH', exp_date: '2030-01-01',
    count_status: 'NOT_COUNTED', qty_counted: '0', ...changes }
}

export async function openCount(page, options = {}) {
  const manager = options.manager ?? true
  const state = { records: [], writes: [], errors: [], console: [], listFailures: options.listFailures || 0,
    recordFailures: options.recordFailures || 0,
    task: { id: 1, task_no: 'PD-QA-001', status: 'RELEASED', review_status: 'NOT_READY',
      posting_status: 'NOT_READY', owner_name: '验收货主', warehouse_name: '验收仓库', blind: true,
      can_record: manager, can_claim: true, has_active_assignment: false, round_no: 1, scope: 'ALL', ...options.task },
    lines: options.lines || [countLine()], }
  if (options.empty) state.task = null
  page.on('pageerror', error => state.errors.push(error.message))
  page.on('console', entry => { if (['warning', 'error'].includes(entry.type())) state.console.push(entry.text()) })
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname
    if (!path.startsWith('/api/')) return route.continue()
    const json = data => route.fulfill({ json: data })
    if (path.endsWith('/token/')) return json({ access: 'count-qa-mock', refresh: 'count-qa-mock-refresh' })
    if (path.endsWith('/profile/')) return json({ user: { id: 1, username: 'qa' }, perms: [], capabilities: { can_manage_count: manager } })
    if (path.endsWith('/home-todos/')) return json({ items: [] })
    if (path.endsWith('/options/')) {
      const kind = url.searchParams.get('kind')
      if (kind === 'products') return json({ results: [{ id: 1, code: 'QA-CODE', product_display_name: '验收商品', spec: '480ml' }], next: null })
      if (kind === 'locations') return json({ results: [{ id: 3, code: 'A-01', name: '验收库位' }], next: null })
      return json({ warehouses: [{ id: 1, name: '验收仓库' }], owners: [{ id: 1, name: '验收货主' }],
        subwarehouses: [], zones: [{ id: 2, name: '存储区' }] })
    }
    if (path === '/api/pda/count-tasks/') {
      if (request.method() === 'POST') {
        state.writes.push({ action: 'create', body: request.postDataJSON() })
        state.task = { id: 1, task_no: 'PD-QA-001', status: 'DRAFT', scope: 'ALL', can_record: true }
        return json(state.task)
      }
      if (state.listFailures-- > 0) return route.fulfill({ status: 500, json: { detail: '列表暂时不可用' } })
      const stage = url.searchParams.get('stage') || 'work'
      const task = state.task
      const matches = task && ({ work: ['RELEASED', 'IN_PROGRESS'].includes(task.status),
        draft: ['DRAFT', 'READY'].includes(task.status), review: task.review_status === 'PENDING',
        posting: ['PENDING', 'FAILED'].includes(task.posting_status) })[stage]
      return json(matches ? [task] : [])
    }
    if (/\/count-tasks\/\d+\/$/.test(path)) return json(state.task)
    if (path.endsWith('/lines/')) return json(state.lines)
    if (path.includes('/count-tasks/') && request.method() === 'POST') {
      const action = path.split('/').filter(Boolean).at(-1)
      const body = request.postData() ? request.postDataJSON() : {}
      state.writes.push({ action, body })
      if (options.deny === action) return route.fulfill({ status: 403, json: { detail: '权限已撤销' } })
      if (action === 'claim') { state.task.can_record = true; state.task.has_active_assignment = true }
      if (action === 'record') {
        state.records.push(body)
        if (state.recordFailures-- > 0) return route.abort('failed')
        const line = state.lines.find(row => row.id === body.line_id)
        Object.assign(line, { qty_counted: body.qty_counted, count_status: 'COUNTED' })
        state.task.status = 'IN_PROGRESS'
        return json(line)
      }
      if (action === 'submit') {
        if (options.recount) {
          Object.assign(state.task, { id: 2, task_no: 'PD-QA-002', round_no: 2, status: 'RELEASED' })
          state.lines = state.lines.map(row => ({ ...row, id: row.id + 100, count_status: 'NOT_COUNTED', qty_counted: '0' }))
          return json({ outcome: 'RECOUNT_RELEASED', next_task_id: 2 })
        }
        Object.assign(state.task, { status: 'COMPLETED', review_status: 'APPROVED', posting_status: 'POSTED' })
        return json({ outcome: options.submitOutcome || 'AUTO_POSTED_NO_DIFF' })
      }
      return json(state.task)
    }
    return json({ results: [] })
  })
  await page.goto('/')
  await page.locator('uni-input input').nth(0).fill('qa')
  await page.locator('uni-input input').nth(1).fill('synthetic')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.getByText('盘点', { exact: true }).click()
  await expect(page).toHaveTitle('盘点任务')
  return state
}

export async function openDetail(page) {
  await page.getByText('PD-QA-001', { exact: true }).click()
  await expect(page).toHaveTitle('盘点作业')
  await expect(page.locator('.line-title').first()).toBeVisible()
}

export async function confirm(page) {
  await page.locator('.uni-modal__btn_primary').click()
}

export async function healthy(page, state, testInfo) {
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(state.errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('page.png'), fullPage: true })
  await testInfo.attach('console', { body: JSON.stringify(state.console), contentType: 'application/json' })
}
