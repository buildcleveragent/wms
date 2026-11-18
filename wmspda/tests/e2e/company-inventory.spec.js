import { expectHttpFailure } from '../../../frontend/browser-diagnostics.mjs'
import { test, expect as baseExpect } from './helpers/browser-test.js'

// Keep each scenario independent and bounded; CI exercises the production build.
const expect = baseExpect.configure({ timeout: 15_000 })
test.setTimeout(120_000)

async function setup(page, { ownerFailure = false } = {}) {
  const errors = [], requests = []
  let failOwners = ownerFailure, failInventory = false, pendingInventory = null
  page.on('pageerror', e => errors.push(e.message))
  await page.route('https://cn-shanghai-aliyun-cloudauth.oss-cn-shanghai.aliyuncs.com/**', r => r.fulfill({ body: '', contentType: 'application/javascript' }))
  const owners = Array.from({ length: 24 }, (_, i) => ({ id: i + 1, name: i < 2 ? '同名货主' : `货主名称${i + 1}`, code: `OWNER-${i + 1}` }))
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname, params = Object.fromEntries(url.searchParams)
    if (!path.startsWith('/api/')) return route.continue()
    requests.push({ path, params })
    const json = data => route.fulfill({ json: data })
    if (path.endsWith('/token/')) return json({ access: 'inventory-test', refresh: 'inventory-refresh' })
    if (path.endsWith('/profile/')) return json({ user: { id: 1, username: 'inventory-test' }, perms: [], capabilities: {} })
    if (path.endsWith('/home-todos/')) return json({ items: [] })
    if (path.endsWith('/company-summary/warehouse-options/')) return json([
      { id: 1, code: 'W1', name: '一号仓' }, { id: 2, code: 'W2', name: '二号仓' },
      { id: 3, code: 'EMPTY', name: '暂无库存仓库' },
    ])
    if (/\/catalog\/owners\/?$/.test(path)) {
      if (failOwners) { failOwners = false; expectHttpFailure(page, path, 503); return route.fulfill({ status: 503, json: { detail: '货主暂不可用' } }) }
      const current = Number(params.page || 1)
      return json({ results: owners.slice((current - 1) * 10, current * 10), count: owners.length, next: current < 3 ? `?page=${current + 1}` : null })
    }
    if (path.endsWith('/company-summary/')) {
      if (pendingInventory) { const pending = pendingInventory; pendingInventory = null; await pending }
      if (failInventory) { failInventory = false; expectHttpFailure(page, path, 503); return route.fulfill({ status: 503, json: { detail: '库存暂不可用' } }) }
      const size = Number(params.page_size), current = Number(params.page), count = params.search === '不存在' ? 0 : 250
      return json({ count, results: Array.from({ length: Math.max(0, Math.min(size, count - (current - 1) * size)) }, (_, index) => ({
        product_id: (current - 1) * size + index + 1, warehouse_id: Number(params.warehouse_id) || 1, warehouse_name: params.warehouse_id === '2' ? '二号仓' : '一号仓',
        owner_id: params.owner_id || 1, owner_name: owners.find(x => String(x.id) === params.owner_id)?.name || '同名货主',
        product_name: `库存商品${(current - 1) * size + index + 1}`, product_code: `CODE-${index}`, product_sku: `SKU-${index}`,
        product_spec: '散装', base_unit: index === 1 ? 'UNKNOWN' : 'KG', base_unit_name: index === 1 ? null : '千克',
        onhand_qty: '99999999999999.9999', available_qty: '12345678901234.5678', allocated_qty: '0.0001', locked_qty: '12.5000', damaged_qty: '0.0000',
      })) })
    }
    return json({ results: [], count: 0 })
  })
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.locator('uni-input input').nth(0).fill('inventory-test')
  await page.locator('uni-input input').nth(1).fill('test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await page.locator('[data-action="query"]').click()
  await expect(page).toHaveURL(/pages\/inventory\/company/)
  await expect(page).toHaveTitle('公司库存汇总')
  await expect(page.locator('.table-row')).toHaveCount(100)
  return { errors, requests, failOwners: () => { failOwners = true }, failInventory: () => { failInventory = true }, pauseInventory: () => { let release; pendingInventory = new Promise(resolve => { release = resolve }); return () => release() } }
}

for (const width of [1280, 1920, 390]) {
  test(`inventory warehouse filters and owner shortcuts at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const state = await setup(page)
    await expect(page.locator('.table-row').first()).toContainText('千克')
    await expect(page.locator('.table-row').nth(1)).toContainText('单位未设置')
    await expect(page.locator('.table-row').first()).not.toContainText('KG')
    await expect(page.locator('.owner-shortcut')).toHaveCount(20)
    await expect(page.locator('.owner-shortcut').first()).toHaveText('同名货主（OWNER-1）')
    await expect(page.locator('.owner-shortcut').last()).toHaveText('货主名称20')
    await expect(page.locator('.owner-shortcuts')).not.toContainText('货主名称21')
    const warehouseInput = page.locator('.warehouse-filter input')
    await expect(warehouseInput).toHaveValue('全部仓库')
    await warehouseInput.click()
    await expect(page.getByRole('option')).toHaveCount(5)
    await expect(page.getByRole('option', { name: '暂无库存仓库', exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath(`wms-inventory-warehouses-${width}.png`), fullPage: false })
    await warehouseInput.fill('二号')
    await expect(page.getByRole('option')).toHaveCount(3)
    await page.getByRole('option', { name: '二号仓', exact: true }).click()
    await expect(page.locator('.table-row').first()).toContainText('二号仓')
    expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params.warehouse_id).toBe('2')
    await page.getByText('下一页', { exact: true }).click()
    await expect(page.locator('.pager-info')).toContainText('第 2 / 3 页')
    await page.locator('.owner-shortcut').filter({ hasText: /^货主名称20$/ }).click()
    await expect(page.locator('.owner-filter input')).toHaveValue('货主名称20')
    await expect(page.locator('.owner-shortcut.selected')).toHaveText('货主名称20')
    await expect(page.locator('.table-row').first()).toContainText('货主名称20')
    await expect(page.locator('.pager-info')).toContainText('第 1 / 3 页')
    expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params).toMatchObject({ owner_id: '20', warehouse_id: '2', page: '1' })
    await page.screenshot({ path: testInfo.outputPath(`wms-inventory-shortcuts-${width}.png`), fullPage: false })
    await page.locator('.owner-clear').click()
    await expect(page.locator('.owner-filter input')).toHaveValue('全部货主')
    await expect(page.locator('.table-row').first()).toContainText('同名货主')
    await warehouseInput.click(); await page.getByRole('option', { name: '全部仓库', exact: true }).click()
    await expect(page.locator('.table-row').first()).toContainText('一号仓')
    await expect(page.locator('.pager-info')).toContainText('第 1 / 3 页')
    expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params.warehouse_id || '').toBe('')
    expect(state.errors).toEqual([])
  })
  test(`inventory owner search and keyboard selection at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const state = await setup(page)
    const input = page.locator('.owner-filter input')
    await expect(input).toHaveValue('全部货主')
    await page.getByText('下一页', { exact: true }).click()
    await expect(page.locator('.pager-info')).toContainText('第 2 / 3 页')
    await input.click()
    await expect(page.getByRole('option')).toHaveCount(25)
    await expect(page.getByRole('option', { name: '同名货主（OWNER-1）', exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath(`wms-company-inventory-owners-${width}.png`), fullPage: false })
    await input.fill('名称12')
    await expect(page.getByRole('option')).toHaveCount(2)
    await input.press('ArrowDown'); await input.press('Enter')
    await expect(input).toHaveValue('货主名称12')
    await expect(page.locator('.owner-shortcut.selected')).toHaveText('货主名称12')
    await expect.poll(() => state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params.owner_id).toBe('12')
    await expect(page.locator('.table-row').first()).toContainText('货主名称12')
    await expect(page.locator('.pager-info')).toContainText('第 1 / 3 页')
    const queriesBeforeTyping = state.requests.filter(x => x.path.endsWith('/company-summary/')).length
    await input.click(); await input.fill('无匹配的货主')
    await expect(page.getByText('无匹配货主', { exact: true })).toBeVisible()
    await input.press('Escape'); await expect(input).toHaveValue('货主名称12')
    expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).length).toBe(queriesBeforeTyping)
    await input.click(); await page.getByRole('option', { name: '全部货主', exact: true }).click()
    await expect.poll(() => state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params.owner_id || '').toBe('')
    await expect(page.locator('.table-row').first()).toContainText('同名货主')
    await expect(input).toHaveValue('全部货主')
    expect(state.errors).toEqual([])
  })
  test(`inventory fixed regions, precise quantities and paging at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const state = await setup(page)
    const warehouseInput = page.locator('.warehouse-filter input')
    await expect(page.locator('.table-row').first().locator('.number')).toHaveText(['99999999999999.9999', '12345678901234.5678', '0.0001', '12.5', '0'])
    const positions = async () => Promise.all(['.toolbar', '.table-header', '.pager', '.owner-shortcuts'].map(selector => page.locator(selector).boundingBox()))
    const before = await positions()
    await page.locator('.table-scroll').evaluate(el => { el.scrollTop = 1400; el.scrollLeft = 500 })
    await expect.poll(() => page.locator('.table-scroll').evaluate(el => el.scrollTop)).toBeGreaterThan(1000)
    const after = await positions()
    before.forEach((box, index) => expect(Math.abs(box.y - after[index].y)).toBeLessThan(2))
    expect(after[2].y + after[2].height).toBeLessThanOrEqual(901)
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1 && document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    const headerX = await page.locator('.table-header .inventory-cell').first().evaluate(el => el.getBoundingClientRect().x)
    const rowX = await page.locator('.table-row').first().locator('.inventory-cell').first().evaluate(el => el.getBoundingClientRect().x)
    expect(Math.abs(headerX - rowX)).toBeLessThan(1)
    await page.screenshot({ path: testInfo.outputPath(`wms-company-inventory-scrolled-${width}.png`), fullPage: false })
    await page.getByText('下一页', { exact: true }).click()
    await expect(page.locator('.pager-info')).toContainText('第 2 / 3 页')
    await expect(page.locator('.table-row').first()).toContainText('库存商品101')
    await expect.poll(() => page.locator('.table-scroll').evaluate(el => el.scrollTop)).toBe(0)
    await warehouseInput.click()
    await page.getByRole('option', { name: '全仓汇总', exact: true }).click()
    await expect(page.locator('.pager-info')).toContainText('第 1 / 3 页')
    await expect(page.locator('.table-header .inventory-cell')).toHaveCount(11)
    await expect(warehouseInput).toHaveValue('全仓汇总')
    expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params.mode).toBe('all')
    await warehouseInput.click(); await page.getByRole('option', { name: '二号仓', exact: true }).click()
    await expect(page.locator('.table-header .inventory-cell')).toHaveCount(12)
    await expect(page.locator('.table-row').first()).toContainText('二号仓')
    await expect(warehouseInput).toHaveValue('二号仓')
    expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params).toMatchObject({ mode: 'warehouse', warehouse_id: '2' })
    expect(state.errors).toEqual([])
  })
  test(`inventory page size and empty results at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const state = await setup(page)
    await page.locator('.page-size-box').click()
    if (width < 600) {
      const centered = async label => {
        await expect.poll(async () => {
          const item = await page.locator('.uni-picker-view-content .uni-picker-item').filter({ hasText: new RegExp(`^${label}$`) }).boundingBox()
          const indicator = await page.locator('.uni-picker-view-indicator').boundingBox()
          return item && indicator ? Math.abs(item.y + item.height / 2 - indicator.y - indicator.height / 2) : Infinity
        }).toBeLessThan(2)
      }
      await centered('100')
      await page.locator('.uni-picker-view-group').hover()
      await page.mouse.wheel(0, -50); await centered('50')
      await page.mouse.wheel(0, -50); await centered('20')
      await page.locator('.uni-picker-action-confirm:visible').click()
    } else {
      await page.locator('.uni-picker-select .uni-picker-item:visible').filter({ hasText: /^20$/ }).click()
    }
    await expect(page.locator('.table-row')).toHaveCount(20)
    await expect(page.locator('.pager-info')).toContainText('第 1 / 13 页')
    await page.locator('.search input').fill('不存在'); await page.locator('.query-btn').click()
    await expect(page.getByText('暂无数据', { exact: true })).toBeVisible()
    await expect(page.locator('.pager-info')).toContainText('共 0 条')
    await expect(page.locator('.pager')).toBeInViewport()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(state.errors).toEqual([])
  })
}

test('inventory and owner errors retry without losing the applied page or selection', async ({ page }, testInfo) => {
  const state = await setup(page, { ownerFailure: true })
  await expect(page.locator('.owner-shortcuts-state')).toContainText('货主加载失败')
  await page.locator('.owner-shortcuts-state').getByText('重试', { exact: true }).click()
  await expect(page.locator('.owner-shortcut')).toHaveCount(20)
  await page.locator('.owner-filter input').click()
  await expect(page.getByRole('option')).toHaveCount(25)
  await page.locator('.owner-filter input').press('Escape')
  state.failInventory(); await page.getByText('下一页', { exact: true }).click()
  await expect(page.locator('.error')).toBeVisible()
  await expect(page.locator('.pager-info')).toContainText('第 1 / 3 页')
  await expect(page.locator('.table-row').first()).toContainText('库存商品1')
  await page.locator('.error').getByText('重试', { exact: true }).click()
  await expect(page.locator('.pager-info')).toContainText('第 2 / 3 页')
  const release = state.pauseInventory()
  await page.locator('.search input').fill('尚未查询的新条件')
  await page.getByText('下一页', { exact: true }).click()
  await expect(page.locator('.query-btn')).toHaveAttribute('disabled')
  await expect(page.locator('.owner-shortcut').first()).toHaveAttribute('disabled')
  await expect(page.locator('.pager-info')).toContainText('第 2 / 3 页')
  expect(state.requests.filter(x => x.path.endsWith('/company-summary/')).at(-1).params.search || '').toBe('')
  release()
  await expect(page.locator('.pager-info')).toContainText('第 3 / 3 页')
  expect(state.errors).toEqual([])
})
