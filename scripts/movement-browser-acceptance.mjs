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
const browser = await chromium.launch({ headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}) })
const results = []
let server, page, activeScans
try {
  for (const entry of config.cases) {
    const scans = []
    activeScans = scans
    let dropNextScan = false
    const directory = path.join(root, entry.app, 'dist/build/h5')
    server = http.createServer(async (request, response) => {
      if (request.url.startsWith('/api/')) {
        const target = new URL(request.url, config.backend)
        const isScan = /\/api\/(relocation|replenishment)\/pda\/tasks\/\d+\/record\/$/.test(request.url)
        const body = []
        request.on('data', chunk => body.push(chunk))
        const proxy = http.request(target, { method: request.method, headers: { ...request.headers, host: target.host } }, upstream => {
          const received = []
          upstream.on('data', chunk => received.push(chunk))
          upstream.on('end', () => {
            const content = Buffer.concat(received)
            if (isScan) scans.push({ request: JSON.parse(Buffer.concat(body).toString()), status: upstream.statusCode,
              response: JSON.parse(content.toString()) })
            // The real API has completed the write; only the response is lost.
            if (isScan && dropNextScan) {
              dropNextScan = false
              // Send headers and an incomplete body: this prevents Chromium's
              // transparent retry of a connection closed before any response.
              response.writeHead(upstream.statusCode, { ...upstream.headers, 'content-length': content.length })
              response.write(content.subarray(0, Math.floor(content.length / 2)), () => response.destroy())
              return
            }
            response.writeHead(upstream.statusCode, upstream.headers)
            response.end(content)
          })
        })
        proxy.on('error', () => { response.writeHead(502); response.end() })
        request.pipe(proxy)
        return
      }
      try {
        const pathname = new URL(request.url, 'http://localhost').pathname
        const file = path.resolve(directory, `.${pathname === '/' ? '/index.html' : pathname}`)
        if (!file.startsWith(directory + path.sep)) throw new Error('invalid path')
        response.setHeader('Content-Type', ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream')
        response.end(await fs.readFile(file))
      } catch { response.writeHead(404); response.end() }
    })
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(18081, '127.0.0.1', resolve) })
    const viewport = entry.app === 'wmsandroid' ? { width: 390, height: 844 } : { width: 1280, height: 900 }
    page = await browser.newPage({ viewport })
    const errors = [], consoleMessages = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (['error', 'warning'].includes(message.type())) consoleMessages.push(message.text()) })
    await page.goto('http://127.0.0.1:18081/#/pages/login')
    await page.locator('input').first().fill(entry.username)
    await page.locator('input[type=password]').fill('x')
    await page.getByText('登录', { exact: true }).last().click()
    await expect(page).toHaveURL(/pages\/index\/index/, { timeout: 30000 })
    await page.goto(`http://127.0.0.1:18081/#/pages/inventory/${entry.kind}/detail?task_id=${entry.task_id}`)
    await expect(page.getByText(entry.task_no, { exact: false }).first()).toBeVisible({ timeout: 15000 })
    await page.getByText(entry.kind === 'replenish' ? '领取任务' : '领取', { exact: true }).click()
    await page.getByText(entry.kind === 'replenish' ? '开始补货' : '开始', { exact: true }).click()
    const inputs = page.locator('input:visible')
    await expect(inputs).toHaveCount(4)
    await inputs.nth(0).fill(entry.source)
    await inputs.nth(1).fill(entry.product)
    await inputs.nth(2).fill(entry.target)
    const button = page.getByText(entry.kind === 'move' ? '确认移库' : '确认补货', { exact: true })
    await inputs.nth(3).fill('4')
    await button.click()
    await expect.poll(() => scans.length).toBe(1)
    await expect(page.getByText('移库已记录', { exact: true }).or(page.getByText('补货已记录', { exact: true }))).toBeVisible()
    expect(Number(scans[0].response.task.lines[0].qty_done)).toBe(4)
    dropNextScan = true
    await button.click()
    await expect.poll(() => scans.length).toBe(2)
    await expect(page.getByText(/重试|失败|网络|request:fail/).last()).toBeAttached()
    await inputs.nth(3).fill('2')
    await button.click()
    await expect(page.getByText('上次扫描结果尚未确认，请恢复原扫描内容后重试', { exact: true }).first()).toBeAttached()
    expect(scans).toHaveLength(2)
    await inputs.nth(3).fill('4')
    await button.click()
    await expect.poll(() => scans.length).toBe(3)
    expect(scans[2].request).toEqual(scans[1].request)
    expect(scans[2].response.idempotent).toBe(true)
    await expect(page.getByText('已确认此前扫描记录', { exact: true }).first()).toBeAttached()
    await inputs.nth(3).fill('2')
    await button.click()
    await expect.poll(() => scans.length).toBe(4)
    await expect(button).not.toBeVisible()
    expect(Number(scans[3].response.task.lines[0].qty_done)).toBe(10)
    expect(scans[3].response.task.posting_status).toBe('POSTED')
    expect(new Set(scans.map(row => row.request.request_id)).size).toBe(3)
    expect(scans.every(row => row.status === 200)).toBe(true)
    expect(errors).toEqual([])
    expect(await page.locator('vite-error-overlay').count()).toBe(0)
    const identity = `${entry.app}-${entry.kind}`
    await page.screenshot({ path: path.join(config.artifacts, `${identity}.png`), fullPage: true })
    await fs.writeFile(path.join(config.artifacts, `${identity}-scans.json`), JSON.stringify(scans, null, 2))
    results.push({ app: entry.app, kind: entry.kind, status: 'PASS', url: page.url(), title: await page.title(), viewport,
      consoleMessages, scope: 'real API login/claim/start/4+4+2, lost response, edited retry blocked, same-payload replay, final posting' })
    await page.close(); page = null
    await new Promise(resolve => server.close(resolve)); server = null
  }
} catch (error) {
  await fs.writeFile(path.join(config.artifacts, 'failure-scans.json'), JSON.stringify(activeScans, null, 2))
  if (page) {
    await page.screenshot({ path: path.join(config.artifacts, 'failure.png'), fullPage: true })
    await fs.writeFile(path.join(config.artifacts, 'failure.html'), await page.content())
  }
  throw error
} finally {
  await fs.writeFile(path.join(config.artifacts, 'results.json'), JSON.stringify(results, null, 2))
  await browser.close()
  if (server) await new Promise(resolve => server.close(resolve))
}
