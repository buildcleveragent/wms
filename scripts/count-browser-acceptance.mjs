import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../wmspda/package.json', import.meta.url))
const { chromium, expect: baseExpect } = require('@playwright/test')
const expect = baseExpect.configure({ timeout: 15000 })
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const config = JSON.parse(Buffer.concat(chunks).toString())
const backend = new URL(config.backend)
if (config.database !== 'wms_db_test' || !['localhost', '127.0.0.1'].includes(backend.hostname)) {
  throw new Error('Real browser tests require the canonical isolated database and loopback server')
}
const directory = path.resolve(import.meta.dirname, '../wmspda/dist/build/h5')
const result = { scenario: config.scenario, browser: 'Chromium', browser_plugin: 'Browser plugin not available',
  flow_complete: false, checks: [], findings: [], requests: [], console: [], timings: {} }
const width = config.scenario.endsWith('390') ? 390 : 1280
result.viewport = { width, height: width === 390 ? 844 : 900 }
let browser, server, activePage, origin, dropRecord = false, closing = false
const records = []
const errors = []
const contexts = []
const screenshot = (page, name) => page.screenshot({ path: path.join(config.artifacts, `${name}.png`), fullPage: false })
const button = (page, text) => page.getByText(text, { exact: true })
const confirm = page => page.locator('.uni-modal__btn_primary').click()

async function pageFor(username) {
  const context = await browser.newContext({ viewport: result.viewport, locale: 'zh-CN' })
  contexts.push(context)
  // Intercept ALL requests. API requests go only to the validated test LiveServer;
  // local static assets are allowed, and every other network origin is blocked.
  const forwardRoute = async route => {
    if (closing) return route.abort('blockedbyclient')
    const request = route.request(), url = new URL(request.url())
    if (url.pathname.startsWith('/api/')) {
      const started = performance.now()
      const headers = request.headers()
      const forwarded = Object.fromEntries(Object.entries(headers).filter(([key]) =>
        ['authorization', 'content-type', 'accept'].includes(key)))
      const response = await context.request.fetch(new URL(url.pathname + url.search, backend).href, {
        method: request.method(), headers: forwarded, data: request.postDataBuffer() || undefined,
        maxRedirects: 0, timeout: 30000,
      })
      const body = await response.body()
      result.requests.push({ method: request.method(), path: url.pathname, status: response.status(),
        milliseconds: Math.round(performance.now() - started) })
      if (url.pathname.endsWith('/record/')) {
        const sent = request.postDataJSON()
        const received = JSON.parse(body.toString())
        records.push({ line_id: sent.line_id, client_seq: sent.client_seq, qty_counted: sent.qty_counted,
          idempotent: received.idempotent, status: response.status() })
        if (dropRecord) { dropRecord = false; await response.dispose(); return route.abort('failed') }
      }
      await route.fulfill({ status: response.status(), body, headers: {
        'content-type': response.headers()['content-type'] || 'application/json',
        'access-control-allow-origin': '*',
      } })
      await response.dispose()
      return
    }
    if (url.origin === origin) return route.continue()
    return route.abort('blockedbyclient')
  }
  await context.route('**/*', route => forwardRoute(route).catch(error => {
    if (!closing) throw error
  }))
  const page = await context.newPage()
  activePage = page
  page.setDefaultTimeout(15000)
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (['error', 'warning'].includes(message.type())) {
      // Do not record arbitrary objects or headers from the auth/runtime store.
      const text = message.text()
      if (/Failed to load resource|net::ERR_|status of \d+/.test(text)) result.console.push(text.replace(/https?:\/\/[^ ]+/g, '[URL]'))
      else result.console.push({ type: message.type(), location: message.location().url.split('?')[0] })
    }
  })
  await page.goto(`${origin}/`)
  await page.locator('uni-input input').nth(0).fill(username)
  await page.locator('uni-input input').nth(1).fill('x')
  await page.locator('uni-button').filter({ hasText: '登录' }).click()
  await expect(page).toHaveURL(/pages\/index\/index/)
  await button(page, '盘点').click()
  await expect(page).toHaveTitle('盘点任务')
  result.checks.push(`login and homepage count entry: ${username}`)
  return page
}

async function choose(page, label, value) {
  await expect(page.locator('.save')).not.toHaveAttribute('disabled')
  await page.locator('.select').filter({ hasText: label }).click()
  if (width > 1000) {
    await page.locator('.uni-picker-container .uni-picker-item:visible').filter({ hasText: value }).click()
  } else {
    await page.locator('.uni-picker-toggle .uni-picker-action-confirm').click()
  }
}

async function createDraft(page) {
  await button(page, '新建盘点').click()
  await expect(page).toHaveTitle('新建盘点')
  await choose(page, '选择仓库', config.warehouse)
  await choose(page, '选择货主', config.owner)
  await button(page, '保存草稿').click()
  await expect(page.locator('.task-no')).toBeVisible()
  const taskNo = await page.locator('.task-no').innerText()
  await page.reload()
  await expect(page.locator('.task-no')).toHaveText(taskNo)
  return taskNo
}

async function publish(page) {
  await button(page, '确认发布').click()
  await confirm(page)
  await expect(button(page, '待认领')).toBeVisible()
}

async function save(page, value) {
  await page.locator('.entry input').fill(value)
  await button(page, '保存').click()
  await expect(page.locator('.result')).toContainText(`已盘：${Number(value)}`)
}

async function submit(page) {
  await button(page, '完成并提交').click()
  const response = page.waitForResponse(r => r.url().endsWith('/submit/') && r.request().method() === 'POST')
  await confirm(page)
  return (await response).json()
}

async function postAction(page, label, action, expectedStatus = 200) {
  const response = page.waitForResponse(
    r => r.url().endsWith(`/${action}/`) && r.request().method() === 'POST',
    { timeout: 45000 },
  )
  await button(page, label).click()
  await confirm(page)
  expect((await response).status()).toBe(expectedStatus)
}

async function checkPage(page) {
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(await page.locator('body').innerText()).not.toBe('')
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  result.checks.push('page content, no error overlay, no runtime exceptions, no horizontal overflow')
}

try {
  await fs.access(path.join(directory, 'index.html'))
  server = http.createServer(async (request, response) => {
    try {
      const pathname = new URL(request.url, 'http://localhost').pathname
      const file = path.resolve(directory, `.${pathname === '/' ? '/index.html' : pathname}`)
      if (!file.startsWith(directory + path.sep)) throw new Error('invalid path')
      response.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
        '.svg': 'image/svg+xml', '.png': 'image/png' })[path.extname(file)] || 'application/octet-stream')
      response.end(await fs.readFile(file))
    } catch { response.writeHead(404); response.end() }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  origin = `http://127.0.0.1:${server.address().port}`
  result.origin = origin
  browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}) })
  const manager = await pageFor(config.manager)
  if (['expiry', 'thousand'].includes(config.scenario)) {
    const started = performance.now()
    await button(manager, config.task_no).click()
    await expect(manager.locator('.line')).toHaveCount(config.scenario === 'thousand' ? 1000 : 2, { timeout: 30000 })
    result.timings.detail_load_ms = Math.round(performance.now() - started)
    await screenshot(manager, 'detail')
    if (config.scenario === 'expiry') {
      const texts = await manager.locator('.line').allTextContents()
      if (!texts[0].includes('2030-01-01') || !texts[1].includes('2031-01-01')) {
        result.findings.push('Different expiry layers with the same SKU, location and batch have no visible expiry label')
      }
    } else {
      const line = manager.locator('.line').last()
      const saving = performance.now()
      await line.locator('input').fill('7.0001')
      await line.getByText('保存', { exact: true }).click()
      await expect(line.locator('.result')).toHaveText('已盘：7.0001')
      result.timings.last_line_save_ms = Math.round(performance.now() - saving)
      await screenshot(manager, 'last-line-saved')
    }
    await checkPage(manager)
  } else if (config.scenario === 'reject-cancel') {
    await createDraft(manager)
    await button(manager, '取消草稿').click(); await confirm(manager)
    await expect(button(manager, '已取消')).toBeVisible()
    await button(manager, '返回盘点列表').click()
    await createDraft(manager); await publish(manager); await save(manager, '4')
    expect((await submit(manager)).outcome).toBe('PENDING_APPROVAL')
    await button(manager, '驳回').click()
    await expect(manager.locator('.error')).toContainText('请填写驳回原因')
    await manager.locator('.note input, input.note').fill('验收：重新核对')
    await button(manager, '驳回').click(); await confirm(manager)
    await expect(button(manager, '已驳回')).toBeVisible()
    await screenshot(manager, 'rejected')
    await checkPage(manager)
  } else {
    const taskNo = await createDraft(manager)
    await screenshot(manager, 'draft')
    await publish(manager)
    if (config.scenario === 'no-difference') {
      await save(manager, '5')
      expect((await submit(manager)).outcome).toBe('AUTO_POSTED_NO_DIFF')
      await expect(button(manager, '已过账')).toBeVisible()
      await screenshot(manager, 'auto-posted')
    } else {
      const worker = await pageFor(config.operator)
      await expect(button(worker, '新建盘点')).toHaveCount(0)
      await button(worker, taskNo).click()
      await button(worker, '认领任务').click()
      await expect(worker.locator('.entry input')).toBeEnabled()
      await expect(worker.getByText(/账面：/)).toHaveCount(0)
      dropRecord = true
      await worker.locator('.entry input').fill('4')
      await button(worker, '保存').click()
      await expect(worker.locator('.error')).toBeVisible()
      await button(worker, '保存').click()
      await expect(worker.locator('.result')).toContainText('已盘：4')
      expect(records).toHaveLength(2)
      expect(records[0].client_seq).toBe(records[1].client_seq)
      expect(records[1].idempotent).toBe(true)
      result.checks.push('response lost after real DB commit: retry used the same key and was idempotent')
      const first = await submit(worker)
      expect(first.outcome).toBe('RECOUNT_RELEASED')
      await expect(worker).toHaveURL(new RegExp(`task_id=${first.next_task_id}`))
      await expect(worker.locator('.entry input')).toBeEnabled()
      const recountNo = await worker.locator('.task-no').innerText()
      await screenshot(worker, 'recount')
      await save(worker, '4')
      const submissionRequestStart = result.requests.length
      expect((await submit(worker)).outcome).toBe('PENDING_APPROVAL')
      await expect(worker).toHaveURL(/stocktake\/index\?mode=work&submitted=PENDING_APPROVAL/)
      await expect(worker).toHaveTitle('盘点任务')
      await expect(worker.locator('.submission-result')).toHaveText('盘点已提交，待审核。')
      await expect(worker.locator('.error')).toHaveCount(0)
      await expect(button(worker, '完成并提交')).toHaveCount(0)
      expect(result.requests.slice(submissionRequestStart).filter(r => r.status === 404)).toEqual([])
      await screenshot(worker, 'operator-submitted')
      result.checks.push('operator final submission returns to the work list with an explicit pending-review result and no stale actions or 404')
      activePage = manager
      await button(manager, '返回盘点列表').click()
      await button(manager, '待审核').click()
      await button(manager, recountNo).click()
      await postAction(manager, '审核通过', 'approve')
      await expect(button(manager, '确认盘盈盘亏过账')).toBeVisible()
      await postAction(manager, '确认盘盈盘亏过账', 'post', 500)
      await expect(button(manager, '重试过账')).toBeVisible()
      await screenshot(manager, 'posting-failed')
      await postAction(manager, '重试过账', 'post')
      await expect(button(manager, '已过账')).toBeVisible()
      await screenshot(manager, 'posted')
      result.checks.push('real automatic recount, manager approval, transactional posting failure and successful retry')
    }
    await checkPage(manager)
  }
  result.flow_complete = true
} catch (error) {
  result.error = String(error.message).replace(/Bearer\s+[^\s"']+/g, '[REDACTED]')
  if (activePage && !activePage.isClosed()) await screenshot(activePage, 'failure').catch(() => {})
} finally {
  result.status = result.flow_complete && !result.findings.length ? 'PASS' : 'FAIL'
  await fs.writeFile(path.join(config.artifacts, 'result.json'), JSON.stringify(result, null, 2))
  // Keep isolation in place until pages are closed; never let API calls bypass the proxy.
  closing = true
  for (const context of contexts) {
    for (const page of context.pages()) await page.close()
    await context.unrouteAll({ behavior: 'wait' })
  }
  if (browser) await browser.close()
  if (server) await new Promise(resolve => server.close(resolve))
}
if (result.status !== 'PASS') process.exitCode = 1
