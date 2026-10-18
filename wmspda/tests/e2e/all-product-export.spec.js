import { expect, test } from '@playwright/test'

const exportTitle = '全部商品档案导出'
const exportRoute = '**/api/products/export-all-excel/'
test.use({ actionTimeout: 20_000 })

function profile(canExport) {
  return {
    user: { id: 7, username: 'export-operator' },
    perms: [],
    capabilities: { can_export_products: canExport },
  }
}

async function logIn(page) {
  await page.route('**/api/pda/token/', (route) => route.fulfill({
    json: { access: 'export-test-access', refresh: 'export-test-refresh' },
  }))
  await page.goto('/')
  // uni-app renders placeholders and buttons as custom elements in H5.
  await page.locator('uni-input input').nth(0).fill('export-operator')
  await page.locator('uni-input input').nth(1).fill('export-test-password')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/, { timeout: 20_000 })
  await expect(page.getByText('收货(有订单)', { exact: true })).toBeVisible()
}

async function fulfillArchive(route) {
  await route.fulfill({
    contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    headers: {
      'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent('全部商品档案.xlsx')}`,
      'access-control-expose-headers': 'Content-Disposition',
    },
    body: Buffer.from('mock product archive'),
  })
}

test('homepage downloads all products and prevents duplicate exports', async ({ page }) => {
  const errors = []
  const exportRequests = []
  const ownerRequests = []
  let releaseExport
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (['error', 'warning'].includes(message.type())) errors.push(message.text())
  })
  page.on('request', (request) => {
    if (request.url().includes('/api/catalog/owners')) ownerRequests.push(request.url())
  })
  await page.route('**/api/auth/profile/', (route) => route.fulfill({ json: profile(true) }))
  await page.route(exportRoute, async (route) => {
    exportRequests.push(route.request())
    await new Promise((resolve) => { releaseExport = resolve })
    await fulfillArchive(route)
  })
  await logIn(page)
  await expect(page.getByText(exportTitle, { exact: true })).toBeVisible()
  await expect(page).toHaveTitle('金桥融通仓库作业平台')
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(page.viewportSize().width)
  await page.screenshot({ path: '/tmp/wmspda-all-product-export-desktop.png', fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByText(exportTitle, { exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/wmspda-all-product-export-mobile.png', fullPage: true })
  const downloadPending = page.waitForEvent('download')
  await page.getByText(exportTitle, { exact: true }).click()
  await expect(page.getByText('正在导出…', { exact: true })).toBeVisible()
  await expect.poll(() => exportRequests.length).toBe(1)
  await page.getByText('正在导出…', { exact: true }).click({ force: true })
  releaseExport()
  const download = await downloadPending

  expect(download.suggestedFilename()).toBe('全部商品档案.xlsx')
  expect(await download.failure()).toBeNull()
  await expect(page.getByText(exportTitle, { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/pages\/index\/index/)
  expect(exportRequests).toHaveLength(1)
  expect(exportRequests[0].headers().authorization).toBe('Bearer export-test-access')
  expect(new URL(exportRequests[0].url()).search).toBe('')
  expect(ownerRequests).toEqual([])
  expect(errors).toEqual([])
})

test('homepage hides archive export when the account lacks export permission', async ({ page }) => {
  let exportRequests = 0
  await page.route('**/api/auth/profile/', (route) => route.fulfill({ json: profile(false) }))
  await page.route(exportRoute, (route) => {
    exportRequests += 1
    return fulfillArchive(route)
  })
  await logIn(page)

  await expect(page.getByText(exportTitle, { exact: true })).toHaveCount(0)
  await expect(page.getByText('商品导出', { exact: true })).toHaveCount(0)
  expect(exportRequests).toBe(0)
})

test('revoked export permission refreshes the profile and hides the action', async ({ page }) => {
  let canExport = true
  let profileRequests = 0
  await page.route('**/api/auth/profile/', (route) => {
    profileRequests += 1
    return route.fulfill({ json: profile(canExport) })
  })
  await page.route(exportRoute, (route) => {
    canExport = false
    return route.fulfill({ status: 403, json: { detail: '无商品档案导出权限' } })
  })
  await logIn(page)
  await expect(page.getByText(exportTitle, { exact: true })).toBeVisible()
  const profilesBeforeExport = profileRequests
  await page.getByText(exportTitle, { exact: true }).click()

  await expect.poll(() => profileRequests).toBeGreaterThan(profilesBeforeExport)
  await expect(page.getByText('无商品档案导出权限', { exact: true })).toBeVisible()
  await expect(page.getByText(exportTitle, { exact: true })).toHaveCount(0)
  await expect(page.getByText('正在导出…', { exact: true })).toHaveCount(0)
  await expect(page).toHaveURL(/pages\/index\/index/)
})

test('a failed export restores the action so the user can retry', async ({ page }) => {
  let attempts = 0
  await page.route('**/api/auth/profile/', (route) => route.fulfill({ json: profile(true) }))
  await page.route(exportRoute, (route) => {
    attempts += 1
    return attempts === 1
      ? route.fulfill({ status: 503, json: { detail: '导出服务暂不可用，请重试' } })
      : fulfillArchive(route)
  })
  await logIn(page)
  await page.getByText(exportTitle, { exact: true }).click()

  await expect(page.getByText('导出服务暂不可用，请重试', { exact: true }))
    .toBeVisible()
  await expect(page.getByText(exportTitle, { exact: true })).toBeVisible()
  const downloadPending = page.waitForEvent('download')
  await page.getByText(exportTitle, { exact: true }).click()
  const download = await downloadPending

  expect(await download.failure()).toBeNull()
  expect(attempts).toBe(2)
  await expect(page.getByText(exportTitle, { exact: true })).toBeVisible()
})
