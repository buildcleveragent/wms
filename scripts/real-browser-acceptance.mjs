import http from 'node:http'
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../wmspda/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const config = JSON.parse(Buffer.concat(chunks).toString())
const root = path.resolve(import.meta.dirname, '..')
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
})
const results = []
const servers = []
let activePage
const diagnostics = []
async function serve(app) {
  const directory = path.join(root, app, 'dist/build/h5')
  await fs.access(path.join(directory, 'index.html'))
  const server = http.createServer(async (request, response) => {
    if (request.url.startsWith('/api/')) {
      const target = new URL(request.url, config.backend)
      const proxy = http.request(target, {
        method: request.method, headers: { ...request.headers, host: target.host },
      }, upstream => {
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
      const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' }
      response.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream')
      response.end(await fs.readFile(file))
    } catch { response.writeHead(404); response.end() }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  servers.push(server)
  return `http://127.0.0.1:${server.address().port}`
}
try {
  for (const app of ['wmsownersale', 'sales-miniapp']) {
    const origin = await serve(app)
    const page = await browser.newPage()
    activePage = page
    page.on('console', message => { if (message.type() === 'error') diagnostics.push(message.text()) })
    page.on('pageerror', error => diagnostics.push(error.message))
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const mall = app === 'sales-miniapp'
    await page.goto(`${origin}/#/${mall ? 'pages/login/login' : 'pages/login'}`)
    await page.locator('input').nth(0).fill(mall ? 'mini-buyer' : 'browser-owner')
    await page.locator('input[type=password]').fill(mall ? 'pw' : 'browser-test-password')
    const login = page.waitForResponse(response => response.url().includes('/api/') && response.request().method() === 'POST' && response.status() === 200)
    await page.getByText(mall ? '账号登录' : '登录', { exact: true }).last().click()
    await login
    await expect(page).toHaveURL(new RegExp(mall ? '/#/$' : 'pages/home/index'), { timeout: 30000 })
    if (mall) {
      await page.locator('input[type=search]').fill('小程序上架商品')
      await page.getByText('搜索', { exact: true }).click()
      await expect(page.getByText('小程序上架商品', { exact: true })).toBeVisible({ timeout: 15000 })
      await page.getByText('小程序上架商品', { exact: true }).click()
      await expect(page.locator('.detail-page .name')).toHaveText('小程序上架商品')
      await expect(page.locator('.detail-page .total-price')).toHaveText('¥9.50')
      const addResponse = page.waitForResponse(response => response.url().endsWith('/api/sale-mini/cart/add/'))
      await page.getByText('立即购买', { exact: true }).click()
      const added = await addResponse
      expect(added.status(), await added.text()).toBe(200)
      await expect(page).toHaveURL(/pages\/order-confirm\/order-confirm/, { timeout: 15000 })
      await page.locator('.inline-address input').nth(0).fill('合成测试收件人')
      await page.locator('.inline-address input').nth(1).fill('13800000000')
      await page.locator('.inline-address input').nth(2).fill('合成测试地址 1 号')
      await page.getByText('线下付款', { exact: true }).click()
      await page.getByText('提交订单', { exact: true }).click()
      await expect(page).toHaveURL(/pages\/order-result\/order-result.*result=offline/, { timeout: 20000 })
      await expect(page.getByText('订单已提交', { exact: true })).toBeVisible()
      await expect(page.getByText('¥9.50', { exact: true })).toBeVisible()
    }
    await page.screenshot({ path: path.join(config.artifacts, `${app}.png`), fullPage: true })
    expect(errors).toEqual([])
    results.push({ app, status: 'PASS', scope: mall
      ? 'real API login, product search, cart and offline order submission'
      : 'real API login and authenticated landing page' })
    await page.close()
  }
  const page = await browser.newPage()
  activePage = page
  await page.goto(`${config.backend}/admin/login/`)
  await page.locator('#id_username').fill('browser-admin')
  await page.locator('#id_password').fill('browser-test-password')
  await page.locator('input[type=submit]').click()
  await expect(page).toHaveURL(`${config.backend}/admin/tasking/wmstask/`)
  await page.screenshot({ path: path.join(config.artifacts, 'admin.png'), fullPage: true })
  results.push({ app: 'admin', status: 'PASS', scope: 'real session login' })
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({ path: path.join(config.artifacts, 'failure.png'), fullPage: true })
    await fs.writeFile(path.join(config.artifacts, 'failure.html'), await activePage.content())
  }
  await fs.writeFile(path.join(config.artifacts, 'diagnostics.json'), JSON.stringify(diagnostics))
  throw error
} finally {
  await fs.writeFile(path.join(config.artifacts, 'results.json'), JSON.stringify(results, null, 2))
  await browser.close()
  for (const server of servers) await new Promise(resolve => server.close(resolve))
}
