import { test, expect } from '@playwright/test'

for (const width of [390, 1280]) {
  test(`home reminders and navigation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => {
      if (message.type() === 'error' && !message.text().includes('503')) errors.push(message.text())
    })
    let count = 23, failed = false
    const requests = []
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url()), path = url.pathname
      if (!path.startsWith('/api/')) return route.continue()
      requests.push(url.pathname + url.search)
      if (path.endsWith('/token/')) return route.fulfill({ json: { access: 'todo-test', refresh: 'todo-refresh' } })
      if (path.endsWith('/profile/')) return route.fulfill({ json: {
        user: { id: 5, username: 'todo-manager' }, perms: [],
        capabilities: { can_review_warehouse_outbound: true },
      } })
      if (path.endsWith('/home-todos/')) return route.fulfill(failed
        ? { status: 503, json: { detail: '暂不可用' } }
        : { json: { scope: 'test-warehouse', items: [
          { key: 'outbound_review', count }, { key: 'receive', count: 2 },
          { key: 'putaway', count: 3 }, { key: 'pick', count: 4 },
          { key: 'review', count: 1 }, { key: 'replenish', count: 5 },
        ] } })
      if (path.endsWith('/options/')) return route.fulfill({ json: { owners: [], warehouses: [] } })
      return route.fulfill({ json: { count: 0, results: [], next: null } })
    })
    await page.goto('/')
    await page.locator('uni-input input').nth(0).fill('todo-manager')
    await page.locator('uni-input input').nth(1).fill('test-password')
    await page.locator('uni-button').filter({ hasText: '登录' }).click()
    await expect(page).toHaveURL(/pages\/index\/index/)
    await expect(page).toHaveTitle('金桥融通仓库作业平台')
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('dialog')).toContainText('23 个订单')
    await page.screenshot({ path: `/tmp/wms-home-todos-popup-${width}.png`, fullPage: false })
    await page.getByText('查看待办', { exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('.todo-item')).toHaveCount(6)
    const reminder = await page.locator('.todo-panel').boundingBox()
    const grid = await page.locator('.page > .grid').boundingBox()
    expect(reminder.y + reminder.height).toBeLessThan(grid.y + 1)
    await page.locator('.todo-heading').getByText('刷新', { exact: true }).click()
    await expect(page.locator('.todo-heading')).toContainText('刷新')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    count = 24
    await page.locator('.todo-heading').getByText('刷新', { exact: true }).click()
    await expect(page.getByRole('dialog')).toContainText('24 个订单')
    await page.getByText('稍后处理', { exact: true }).click()
    failed = true
    await page.locator('.todo-heading').getByText('刷新', { exact: true }).click()
    await expect(page.getByText('更新失败，请重试')).toBeVisible()
    await expect(page.locator('.todo-item')).toHaveCount(0)
    failed = false
    await page.locator('.todo-heading').getByText('刷新', { exact: true }).click()
    await expect(page.locator('.todo-item')).toHaveCount(6)
    await page.screenshot({ path: `/tmp/wms-home-todos-card-${width}.png`, fullPage: false })
    for (const [label, target, empty] of [
      ['出库待审核', 'outbound/review/index', '暂无订单'], ['待收货', 'inbound/receive_task_list', '当前条件下暂无任务'],
      ['待上架', 'inbound/putaway_task_list', '当前条件下暂无任务'], ['待拣货', 'picking/task_list', '暂无可拣货任务'],
      ['待复核／过账', 'review/pick_task_list', '暂无可复核任务'], ['待补货', 'inventory/replenish/index', '暂无可执行补货任务'],
    ]) {
      await page.locator('.todo-item').filter({ hasText: label }).click()
      await expect(page).toHaveURL(new RegExp(target))
      await expect(page.getByText(empty, { exact: true })).toBeVisible()
      if (label === '待补货') await expect.poll(() => requests.some(url => url.includes('/replenishment/pda/tasks/') && url.includes('pending=1'))).toBe(true)
      await page.locator('.uni-page-head-btn').first().click()
      await expect(page).toHaveURL(/pages\/index\/index/)
      await expect(page.locator('.todo-item')).toHaveCount(6)
      await expect(page.getByRole('dialog')).toHaveCount(0)
    }
    expect(errors).toEqual([])
  })
}
