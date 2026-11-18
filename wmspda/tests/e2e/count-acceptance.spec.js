import { test, expect } from '@playwright/test'
import { countLine, openCount, openDetail, confirm, healthy } from './helpers/count-acceptance'

test.use({ locale: 'zh-CN', actionTimeout: 15000 })

test('maximum count remains an exact string through H5 save', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await openCount(page)
  await openDetail(page)
  await page.locator('.entry input').fill('99999999999999.9999')
  await page.getByText('保存', { exact: true }).click()
  await expect(page.locator('.result')).toHaveText('已盘：99999999999999.9999')
  expect(state.records[0].qty_counted).toBe('99999999999999.9999')
  await healthy(page, state, info)
})

for (const [outcome, message] of [
  ['PENDING_APPROVAL', '盘点已提交，待审核。'],
  ['AUTO_POSTED_NO_DIFF', '盘点无差异，已完成。'],
  ['POSTING_FAILED', '盘点已提交，自动过账失败，需主管处理。'],
]) {
  test(`operator final submission shows ${outcome} on the work list`, async ({ page }, info) => {
    const state = await openCount(page, { manager: false, submitOutcome: outcome,
      task: { can_record: true, has_active_assignment: true },
      lines: [countLine({ count_status: 'COUNTED', qty_counted: '5.0000' })] })
    await openDetail(page)
    await page.getByText('完成并提交', { exact: true }).click()
    await confirm(page)
    await expect(page).toHaveURL(new RegExp(`submitted=${outcome}`))
    await expect(page).toHaveTitle('盘点任务')
    await expect(page.locator('.submission-result')).toHaveText(message)
    await expect(page.getByText('完成并提交', { exact: true })).toHaveCount(0)
    await expect(page.locator('.error')).toHaveCount(0)
    await healthy(page, state, info)
  })
}

test('no claim capability hides claim and a deleted detail clears old actions', async ({ page }, info) => {
  const state = await openCount(page, { manager: false, task: { can_claim: false } })
  await openDetail(page)
  await expect(page.getByText('认领任务', { exact: true })).toHaveCount(0)
  await page.route('**/api/pda/count-tasks/1/', route => route.fulfill({ status: 404, json: { detail: '任务不可访问' } }))
  await page.reload()
  await expect(page.locator('.error')).toContainText('任务不可访问')
  await expect(page.locator('.line')).toHaveCount(0)
  await expect(page.getByText('完成并提交', { exact: true })).toHaveCount(0)
  await healthy(page, state, info)
})

test('operator claims, records zero, saves edits, and enters the next recount', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const state = await openCount(page, { manager: false, recount: true })
  await expect(page.getByText('新建盘点', { exact: true })).toHaveCount(0)
  await openDetail(page)
  await expect(page.locator('.entry input')).toBeDisabled()
  await expect(page.getByText(/账面：/)).toHaveCount(0)
  await page.getByText('认领任务', { exact: true }).click()
  await expect(page.locator('.entry input')).toBeEnabled()
  await page.locator('.entry input').fill('0')
  await page.getByText('保存', { exact: true }).click()
  await expect(page.getByText('已盘：0', { exact: true })).toBeVisible()
  await page.locator('.entry input').fill('1')
  await expect(page.getByText('完成并提交', { exact: true })).toHaveAttribute('disabled')
  await expect(page.getByText('请先保存修改后的实盘数量。', { exact: true })).toBeVisible()
  await page.getByText('保存', { exact: true }).click()
  await expect(page.getByText('已盘：1', { exact: true })).toBeVisible()
  await page.getByText('完成并提交', { exact: true }).click()
  await confirm(page)
  await expect(page).toHaveURL(/task_id=2/)
  await expect(page.getByText('PD-QA-002', { exact: true })).toBeVisible()
  await expect(page.getByText('未盘', { exact: true })).toBeVisible()
  expect(state.records.map(row => row.qty_counted)).toEqual(['0', '1'])
  await healthy(page, state, info)
})

test('all five supported barcode identifiers locate without changing quantities', async ({ page }, info) => {
  const state = await openCount(page)
  await openDetail(page)
  const scan = page.locator('.scan input')
  await scan.fill('UNKNOWN-CODE')
  await scan.press('Enter')
  await expect(page.getByText('条码不属于当前盘点任务', { exact: true })).toBeVisible()
  for (const code of [' qa-code ', 'QA-SKU', '6901234567892', '16901234567899', '26901234567896']) {
    await scan.fill(code)
    await scan.press('Enter')
    await expect(page.getByText('已定位 验收商品', { exact: true })).toBeVisible()
    await expect(page.locator('.entry input')).toHaveValue('')
  }
  expect(state.records).toEqual([])
  await healthy(page, state, info)
})

test('same barcode in two batches warns the operator to select the correct line', async ({ page }, info) => {
  const state = await openCount(page, { lines: [countLine(), countLine({ id: 12, location_code: 'B-02', lot_no: 'OTHER' })] })
  await openDetail(page)
  await page.locator('.scan input').fill('QA-CODE')
  await page.locator('.scan input').press('Enter')
  await expect(page.getByText('存在多个库位或批次，请核对明细后录入', { exact: true })).toBeVisible()
  await page.locator('.count-line-12 .entry input').fill('3')
  await page.locator('.count-line-12').getByText('保存', { exact: true }).click()
  await expect(page.locator('.count-line-12 .result')).toHaveText('已盘：3')
  await expect(page.locator('.count-line-11 .result')).toHaveText('未盘')
  expect(state.records[0].line_id).toBe(12)
  await healthy(page, state, info)
})

test('same location and batch with different expiry dates remain distinguishable', async ({ page }, info) => {
  const state = await openCount(page, { lines: [countLine(), countLine({ id: 12, exp_date: '2031-01-01' })] })
  await openDetail(page)
  await page.screenshot({ path: info.outputPath('expiry-lines.png'), fullPage: true })
  await expect(page.locator('.count-line-11')).toContainText('2030-01-01')
  await expect(page.locator('.count-line-12')).toContainText('2031-01-01')
  await healthy(page, state, info)
})

test('invalid quantities never reach the API and unfinished lines cannot submit', async ({ page }, info) => {
  const state = await openCount(page)
  await openDetail(page)
  await expect(page.getByText('完成并提交', { exact: true })).toHaveAttribute('disabled')
  for (const value of ['-1', '1.23456', '100000000000000']) {
    await page.locator('.entry input').fill(value)
    await page.getByText('保存', { exact: true }).click()
    await expect(page.locator('.error')).toContainText('实盘数量须在 0～99999999999999.9999 之间，最多四位小数')
  }
  expect(state.records).toHaveLength(0)
  await page.locator('.entry input').fill('')
  await expect(page.getByText('保存', { exact: true })).toHaveAttribute('disabled')
  await healthy(page, state, info)
})

test('failed save retries the original idempotency key and clears loading', async ({ page }, info) => {
  const state = await openCount(page, { recordFailures: 1 })
  await openDetail(page)
  await page.locator('.entry input').fill('4')
  await page.getByText('保存', { exact: true }).click()
  await expect(page.locator('.error')).toBeVisible()
  await expect(page.locator('.entry input')).toHaveValue('4')
  await page.getByText('保存', { exact: true }).click()
  await expect(page.getByText('已盘：4', { exact: true })).toBeVisible()
  expect(state.records).toHaveLength(2)
  expect(state.records[0]).toEqual(state.records[1])
  await healthy(page, state, info)
})

test('list failure offers retry and empty management stages render clearly', async ({ page }, info) => {
  const state = await openCount(page, { listFailures: 1 })
  await expect(page.locator('.error')).toContainText('列表暂时不可用')
  await page.getByText('重试', { exact: true }).click()
  await expect(page.getByText('PD-QA-001', { exact: true })).toBeVisible()
  for (const label of ['草稿/待发布', '待审核', '待过账']) {
    await page.getByText(label, { exact: true }).click()
    await expect(page.getByText(`暂无${label}`, { exact: true })).toBeVisible()
  }
  await healthy(page, state, info)
})

test('revoked permission removes task contents and prevents a second write', async ({ page }, info) => {
  const state = await openCount(page, { deny: 'post', task: { status: 'COMPLETED', review_status: 'APPROVED', posting_status: 'PENDING' } })
  await page.getByText('待过账', { exact: true }).click()
  await openDetail(page)
  await page.getByText('确认盘盈盘亏过账', { exact: true }).click()
  await confirm(page)
  await expect(page.locator('.error')).toContainText('权限已撤销')
  await expect(page.getByText('PD-QA-001', { exact: true })).toHaveCount(0)
  await expect(page.getByText('确认盘盈盘亏过账', { exact: true })).toHaveCount(0)
  expect(state.writes.filter(row => row.action === 'post')).toHaveLength(1)
  await healthy(page, state, info)
})

test('changing scope drops a previously selected product before saving the draft', async ({ page }, info) => {
  const state = await openCount(page, { empty: true })
  await page.getByText('新建盘点', { exact: true }).click()
  async function choose(label, selection) {
    await expect(page.locator('.save')).not.toHaveAttribute('disabled')
    await page.locator('.select').filter({ hasText: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) }).click()
    await page.locator('.uni-picker-container .uni-picker-item:visible').filter({ hasText: selection }).click()
  }
  await choose('选择仓库', /^验收仓库$/)
  await choose('选择货主', /^验收货主$/)
  await choose('全仓（所选货主）', /^商品$/)
  await page.locator('.choice').filter({ hasText: 'QA-CODE' }).click()
  await expect(page.locator('.card > .selected')).toContainText('验收商品')
  await choose('商品', /^全仓（所选货主）$/)
  await page.getByText('保存草稿', { exact: true }).click()
  await expect(page.getByText('PD-QA-001', { exact: true })).toBeVisible()
  const payload = state.writes.find(row => row.action === 'create').body
  expect(payload.scope).toBe('ALL')
  expect(payload).not.toHaveProperty('product_id')
  await healthy(page, state, info)
})
