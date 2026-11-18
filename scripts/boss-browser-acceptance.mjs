import http from 'node:http'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'

const root = path.resolve(import.meta.dirname, '..')
const require = createRequire(path.join(root, 'wmsbossbilling/package.json'))
const { chromium, expect } = require('@playwright/test')
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const config = JSON.parse(Buffer.concat(chunks).toString())
const scope = new URLSearchParams({ warehouse: config.warehouse, owner: config.owner,
  date_from: config.dateFrom, date_to: config.dateTo }).toString()
const directory = path.join(root, 'wmsbossbilling/dist/build/h5')
await fs.access(path.join(directory, 'index.html'))
await fs.mkdir(config.artifacts, { recursive: true })
const server = http.createServer(async (request, response) => {
  if (request.url.startsWith('/api/')) {
    const target = new URL(request.url, config.backend)
    const proxy = http.request(target, { method: request.method,
      headers: { ...request.headers, host: target.host } }, upstream => {
      response.writeHead(upstream.statusCode, upstream.headers)
      upstream.pipe(response)
    })
    proxy.on('error', () => { response.writeHead(502); response.end() })
    request.pipe(proxy)
    return
  }
  try {
    const pathname = new URL(request.url, 'http://localhost').pathname
    const file = path.resolve(directory, `.${pathname === '/' ? '/index.html' : pathname}`)
    if (!file.startsWith(directory + path.sep)) throw new Error('invalid path')
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon' }
    response.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream')
    response.end(await fs.readFile(file))
  } catch { response.writeHead(404); response.end() }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ headless: true })
const results = [], diagnostics = []
let activePage
try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 }, acceptDownloads: true })
    activePage = page
    const errors = [], responses = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => {
      if (['error', 'warning'].includes(message.type())) diagnostics.push(message.text())
    })
    page.on('response', response => {
      if (new URL(response.url()).pathname.startsWith('/api/')) responses.push({ path: new URL(response.url()).pathname, status: response.status() })
    })
    await page.goto(`${origin}/#/pages/login`)
    await expect(page).not.toHaveTitle('')
    await page.locator('input').nth(0).fill(config.username)
    await page.locator('input[type=password]').fill(config.password)
    await page.locator('.submit-btn').click()
    await expect(page.locator('.home-today')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.order-card.inbound .order-number')).toHaveText('1 单', { timeout: 20000 })
    await expect(page.locator('.order-card.outbound .order-number')).toHaveText('1 单')
    await page.evaluate(() => localStorage.setItem('access', 'expired-test-token'))
    await page.reload()
    await expect(page.locator('.order-card.inbound .order-number')).toHaveText('1 单', { timeout: 20000 })
    expect(responses.filter(row => row.path === '/api/auth/refresh/' && row.status === 200)).toHaveLength(1)
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: path.join(config.artifacts, `home-${width}.png`) })
    await page.getByText('今日实际入库 ›', { exact: true }).click()
    await expect(page).toHaveURL(/type=today.*metric=inbound/)
    await expect(page.getByText('INB-BOSS-1', { exact: false }).first()).toBeVisible({ timeout: 15000 })

    await page.goto(`${origin}/#/pages/billing/overview?${scope}`)
    await expect(page.getByText('BILL-BOSS-A', { exact: true })).toBeVisible({ timeout: 20000 })
    await expect(page.getByText('$9007199254740993.10', { exact: true }).first()).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(await page.locator('.kpi-value, .trend-total').evaluateAll(elements => elements.every(element => element.scrollWidth <= element.clientWidth))).toBe(true)
    await page.screenshot({ path: path.join(config.artifacts, `precision-${width}.png`), fullPage: true })
    await expect(page.getByText('BILL-BOSS-B', { exact: true })).toHaveCount(0)
    await page.getByText('BILL-BOSS-A', { exact: true }).click()
    await expect(page).toHaveURL(/pages\/billing\/bill_detail/)
    await expect(page.locator('.headline-card').filter({ hasText: '价税合计' })).toContainText('¥110.00')
    await expect(page.getByText('合成测试发运费用', { exact: true })).toBeVisible()
    expect(responses.filter(row => row.path === `/api/billing/bills/${config.bill}/`)).toHaveLength(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: path.join(config.artifacts, `bill-${width}.png`), fullPage: true })
    await page.locator('.line-card.clickable').first().click()
    await expect(page).toHaveURL(/pages\/billing\/accrual_detail/)
    await expect(page.locator('.headline-grid')).toContainText('不含税金额¥100.00')
    await expect(page.locator('.headline-grid')).toContainText('税额 ¥10.00')
    expect(responses.filter(row => row.path === `/api/billing/accruals/${config.accrual}/`)).toHaveLength(1)
    await page.getByText('返回总览', { exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`warehouse=${config.warehouse}`))

    await page.goto(`${origin}/#/pages/cockpit/index?tab=receivables&${scope}`)
    await expect(page.locator('.money').first()).toContainText('¥110.00', { timeout: 20000 })
    const downloadReady = page.waitForEvent('download')
    await page.getByText('导出当前报表', { exact: true }).click()
    const download = await downloadReady
    expect(await download.failure()).toBeNull()
    await download.saveAs(path.join(config.artifacts, `receivables-${width}.xlsx`))
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)

    // Exercise authorization using the session produced by real UI login.
    const forbidden = await page.evaluate(async warehouse => {
      const response = await fetch(`/api/reports/boss/today/?warehouse=${warehouse}`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('access')}` },
      })
      return response.status
    }, config.otherWarehouse)
    expect(forbidden).toBe(403)
    await page.goto(`${origin}/#/pages/settings/refresh`)
    await expect(page.locator('.save-local')).toBeVisible()
    await expect(page.locator('.save-global')).toHaveCount(0)
    await expect(page.getByText('已保存：自动刷新关闭', { exact: false })).toBeVisible()
    await page.goto(`${origin}/#/pages/home/index`)
    await page.locator('.account-row').getByText(/▾/).click()
    await page.getByText('退出登录', { exact: true }).click()
    await expect(page).toHaveURL(/pages\/login/)
    expect(await page.evaluate(() => localStorage.getItem('access'))).toBeNull()
    await page.locator('input').nth(0).fill(config.otherUsername)
    await page.locator('input[type=password]').fill(config.password)
    await page.locator('.submit-btn').click()
    await expect(page.locator('.home-today')).toBeVisible({ timeout: 30000 })
    await expect(page.locator('.order-card.inbound .order-number')).toHaveText('0 单', { timeout: 20000 })
    await page.goto(`${origin}/#/pages/billing/bill_detail?id=${config.bill}`)
    await expect(page.getByText('重新加载', { exact: true })).toBeVisible({ timeout: 15000 })
    await expect(page.locator('.headline-grid')).toHaveCount(0)
    expect(errors).toEqual([])
    expect(responses.filter(row => row.status >= 500)).toEqual([])
    results.push({ width, status: 'PASS', apiRequests: responses.length, responses })
    await page.close()
  }
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({ path: path.join(config.artifacts, 'failure.png'), fullPage: true })
    await fs.writeFile(path.join(config.artifacts, 'failure.txt'), await activePage.locator('body').innerText())
  }
  throw error
} finally {
  await fs.writeFile(path.join(config.artifacts, 'results.json'), JSON.stringify({ results, diagnostics }, null, 2))
  await browser.close()
  await new Promise(resolve => server.close(resolve))
}
console.log(JSON.stringify({ status: 'PASS', viewports: results.map(row => row.width) }))
