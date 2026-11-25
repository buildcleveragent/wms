import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../wmspda/package.json', import.meta.url))
const { chromium, expect: baseExpect } = require('@playwright/test')
const expect = baseExpect.configure({ timeout: 15000 })
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const config = JSON.parse(Buffer.concat(chunks).toString())
const backend = new URL(config.backend)
if (config.database !== 'wms_db_test' || !['127.0.0.1', 'localhost'].includes(backend.hostname)) {
  throw new Error('POS browser acceptance requires the isolated MySQL test server on loopback')
}

const built = path.resolve(import.meta.dirname, '../wmspda/dist/build/h5')
await fs.access(path.join(built, 'index.html'))
const context = await chromium.launchPersistentContext(config.profile, {
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
  viewport: { width: 390, height: 844 }, locale: 'zh-CN', serviceWorkers: 'block',
})
const returns = []
const problems = []
let page

// Use the LiveServer origin for the H5 assets and send every API call there,
// including calls the PDA addressed to its configured LAN host.
async function serve(route) {
  const request = route.request()
  const url = new URL(request.url())
  if (url.pathname.startsWith('/api/')) {
    const target = new URL(backend)
    target.pathname = url.pathname
    target.search = url.search
    const headers = Object.fromEntries(Object.entries(request.headers()).filter(([name]) =>
      ['authorization', 'content-type', 'accept'].includes(name)))
    const upstream = await context.request.fetch(target.href, {
      method: request.method(), headers, data: request.postDataBuffer() || undefined,
      maxRedirects: 0, timeout: 30000,
    })
    try {
      const body = await upstream.body()
      if (request.method() === 'POST' && url.pathname === '/api/pos/returns/') {
        expect(upstream.status(), 'real return endpoint').toBe(201)
        const id = JSON.parse(body.toString()).return?.id
        expect(id).toBeGreaterThan(0)
        returns.push({ id, payload: request.postDataJSON() })
        if (config.phase === 'lose-receipt' && returns.length === 1) {
          // The service already committed and returned its ID. Drop only the downstream reply.
          await route.abort('connectionreset')
          return
        }
      }
      await route.fulfill({ status: upstream.status(), body, headers: {
        'content-type': upstream.headers()['content-type'] || 'application/json',
        'access-control-allow-origin': '*',
      } })
    } finally {
      await upstream.dispose()
    }
    return
  }
  if (url.origin !== backend.origin) return route.abort('blockedbyclient')
  const asset = path.resolve(built, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname))
  if (!asset.startsWith(built + path.sep)) throw new Error('Invalid static asset path')
  await route.fulfill({ path: asset })
}

async function enterRegister() {
  if (config.phase === 'lose-receipt') {
    await page.goto(backend.origin, { waitUntil: 'domcontentloaded' })
    const inputs = page.locator('uni-input input')
    await inputs.nth(0).fill(config.username)
    await inputs.nth(1).fill('x')
    await page.locator('uni-button').filter({ hasText: '登录' }).click()
    await page.locator('[data-action="pos"]').click()
  } else {
    await page.goto(backend.origin + '/#/pages/pos/index', { waitUntil: 'domcontentloaded' })
  }
  await expect(page).toHaveURL(/pages\/pos\/index/)
  await expect(page).toHaveTitle(/POS收银/)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
}

try {
  await context.route('**/*', route => serve(route).catch(async error => {
    problems.push(error.message)
    await route.abort().catch(() => {})
  }))
  page = await context.newPage()
  page.setDefaultTimeout(15000)
  page.on('pageerror', error => problems.push(error.message))
  page.on('console', message => {
    if (message.type() !== 'error') return
    const pathname = message.location().url && new URL(message.location().url).pathname
    if (config.phase === 'lose-receipt' && pathname === '/api/pos/returns/' &&
        message.text().includes('net::ERR_CONNECTION_RESET')) return
    // The POS-only test account has no warehouse dashboard permission on the login landing page.
    if (pathname === '/api/pda/home-todos/' && /^Failed to load resource:.*\b403\b/.test(message.text())) return
    problems.push(`Browser console error at ${pathname || 'application'}`)
  })
  await enterRegister()
  const pending = page.locator(`[data-testid="pos-return-pending"][data-sale-id="${config.sale_id}"]`)
  const form = page.getByTestId('pos-return-form')
  if (config.phase === 'retry') {
    await expect(pending).toBeVisible()
    await expect(pending).toContainText(/×\s*1\s*件/)
    await expect(pending).toContainText(config.refund_amount === '0.00' ? '无需退款' : /退款\s*[^\d]*9\.00/)
    await pending.screenshot({ path: path.join(config.artifacts, 'restored-return.png') })
    await pending.getByTestId('pos-return-retry').click()
  } else {
    await expect(pending).toHaveCount(0)
    const row = page.locator('.sale-history-actions')
    await expect(row).toHaveCount(1)
    await row.getByText('退货', { exact: true }).click()
    const quantities = form.locator('.return-qty-input input')
    await expect(quantities).toHaveCount(config.line_count)
    for (const input of await quantities.all()) await input.fill('1')
    await form.locator('.void-input input').fill('验收部分退货')
    await expect(form.getByTestId('pos-return-confirm')).toContainText(config.refund_amount)
    await form.getByTestId('pos-return-confirm').click()
    if (config.refund_amount === '0.00') {
      await expect(page.locator('.uni-modal__bd')).toContainText('仅退回商品，无需退款')
    }
    await page.locator('.uni-modal__btn_primary').click()
  }
  await expect.poll(() => returns.length).toBe(1)
  if (config.phase === 'lose-receipt') {
    await expect(pending).toBeVisible()
    await expect(pending.getByTestId('pos-return-retry')).toBeEnabled()
  } else {
    await expect(pending).toHaveCount(0)
    await expect(form).toHaveCount(0)
  }
  if (config.phase === 'lose-receipt') await pending.scrollIntoViewIfNeeded()
  await page.screenshot({ path: path.join(config.artifacts, config.phase + '.png') })
  expect(problems).toEqual([])
  expect(returns).toHaveLength(1)
  process.stdout.write(JSON.stringify(returns[0]))
} catch (error) {
  if (page) await page.screenshot({ path: path.join(config.artifacts, config.phase + '-failed.png') }).catch(() => {})
  if (problems.length) process.stderr.write(problems.join('\n') + '\n')
  throw error
} finally {
  // Close the actual browser between phases while keeping its private profile on disk.
  await context.close()
}
