import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../wmspda/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const config = JSON.parse(Buffer.concat(chunks).toString())
const browser = await chromium.launch({
  headless: true,
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
})
const errors = []

async function useRole(role, width) {
  const context = await browser.newContext({ viewport: { width, height: 900 } })
  await context.addCookies([{ url: config.origin, name: config.cookie, value: config.sessions[role] }])
  await context.route('**/*', route => new URL(route.request().url()).origin === config.origin
    ? route.continue() : route.abort())
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(String(error)))
  return page
}

async function capture(page, name) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: path.join(config.artifacts, name + '.png'), fullPage: true })
}

try {
  const manager = await useRole('manager', 390)
  await manager.goto(config.origin + config.list)
  await manager.getByLabel('数据质量').selectOption('TRUSTED')
  await manager.getByRole('button', { name: '查询' }).click()
  await expect(manager.getByText('当前范围内没有符合条件的日结记录。')).toBeVisible()
  await manager.getByRole('link', { name: '清除筛选' }).click()
  await expect(manager.getByText('日结乙货主')).toHaveCount(0)
  await capture(manager, 'warehouse-list-mobile')
  await manager.getByRole('link', { name: /查看 .* 日结详情/ }).click()
  await manager.getByRole('link', { name: '仓管补证' }).click()
  await manager.getByLabel('复核意见').fill('仓管对照原件确认收发流水')
  await manager.getByRole('button', { name: '确认仓管补证' }).click()
  await expect(manager.getByRole('alert')).toContainText('请附上复核凭据')
  await manager.getByLabel('关联单据或外部凭证').fill('外部凭证 DC-E2E')
  await manager.getByRole('button', { name: '确认仓管补证' }).click()
  await expect(manager).toHaveURL(config.origin + config.detail)
  await expect(manager.getByText('待财务签核', { exact: true })).toBeVisible()
  const finance = await useRole('finance', 1440)
  await finance.goto(config.origin + config.detail)
  await expect(finance.getByText('关联凭据 · 外部凭证 DC-E2E')).toBeVisible()
  await capture(finance, 'finance-review-desktop')
  await finance.getByRole('link', { name: '财务签核' }).click()
  await finance.getByLabel('计费影响').selectOption('NO_CHANGE')
  await finance.getByLabel('复核意见').fill('财务核准')
  await finance.getByRole('button', { name: '确认财务签核' }).click()
  await expect(finance.getByText('已签核', { exact: true })).toBeVisible()
  const owner = await useRole('owner_finance', 390)
  await owner.goto(config.origin + config.detail)
  await expect(owner.getByText('已签核', { exact: true })).toBeVisible()
  await expect(owner.getByText('DC-E2E', { exact: false })).toHaveCount(0)
  await expect(owner.getByRole('link', { name: '仓管补证' })).toHaveCount(0)
  await capture(owner, 'owner-progress-mobile')
  expect((await owner.goto(config.origin + config.externalDetail)).status()).toBe(404)
  expect(errors).toEqual([])
  await fs.writeFile(path.join(config.artifacts, 'results.json'), JSON.stringify({
    browser: browser.version(), widths: [390, 1440],
    checked: ['filters', 'warehouse invalid and valid submission', 'finance sign-off', 'owner read-only and scope'],
    pageErrors: errors,
  }, null, 2))
} finally {
  await browser.close()
}
