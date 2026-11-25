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
const cases = [
  { username: 'nav-warehouse', tab: '库存/盘点', action: '现存量', target: config.stock },
  { username: 'nav-owner', tab: '入库', action: '入库单', target: config.inbound },
  { username: 'nav-empty' },
]
const results = []
try {
  for (const width of [390, 768, 1440]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: true })
    await context.route('**/*', route =>
      new URL(route.request().url()).origin === config.origin ? route.continue() : route.abort())
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(String(error)))
    for (const row of cases) {
      // Keep browser storage while rotating authenticated sessions on one terminal.
      await context.clearCookies()
      await page.goto(config.origin + config.login + '?next=' + encodeURIComponent(config.home))
      await page.locator('[name=username]').fill(row.username)
      await page.locator('[name=password]').fill('test-navigation')
      await page.locator('[type=submit]').click()
      await expect(page).toHaveURL(config.origin + config.home)
      const nav = page.getByRole('navigation', { name: '控制台导航' })
      await expect(nav).toContainText(row.username)
      const capture = () => nav.screenshot({ path: path.join(config.artifacts, row.username + '-' + width + '.png') })
      if (!row.tab) {
        await expect(nav).toContainText('无可用菜单')
        await expect(nav.getByRole('tab')).toHaveCount(0)
        await expect(nav.locator('a')).toHaveCount(0)
        await capture()
        results.push({ role: row.username, width, empty: true })
        continue
      }
      if (row.username === 'nav-owner') {
        await expect(nav.getByRole('tab', {name: '库存/盘点'})).toHaveCount(0)
      }
      const tabs = nav.getByRole('tab')
      const target = nav.getByRole('tab', { name: row.tab, exact: true })
      const link = nav.getByRole('tabpanel').getByRole('link', { name: row.action, exact: true })
      const tabCount = await tabs.count()
      const index = (await tabs.allTextContents()).map(text => text.trim()).indexOf(row.tab)
      // All authorized second-level entries must be visible within their selected tab.
      for (const tab of await tabs.all()) {
        await tab.tap()
        await expect(tab).toHaveAttribute('aria-selected', 'true')
        const panel = nav.getByRole('tabpanel')
        const links = panel.getByRole('link')
        expect(await links.count()).toBeGreaterThan(0)
        for (const action of await links.all()) {
          await expect(action).toBeVisible()
          if (width <= 768) {
            const box = await action.boundingBox()
            expect(box.height).toBeGreaterThanOrEqual(44)
            expect(box.x).toBeGreaterThanOrEqual(0)
            expect(box.x + box.width).toBeLessThanOrEqual(width)
          }
        }
      }
      await target.tap()
      await capture()
      await link.tap()
      await expect(page).toHaveURL(config.origin + row.target)
      await expect(page.locator('#changelist')).toBeVisible()
      await page.goto(config.origin + config.home)
      await tabs.first().focus()
      await page.keyboard.press('End')
      await expect(tabs.nth(tabCount - 1)).toBeFocused()
      await page.keyboard.press('Home')
      for (let step = 0; step < index; step++) await page.keyboard.press('ArrowRight')
      await expect(target).toBeFocused()
      await page.keyboard.press('Tab')
      await expect(link).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(config.origin + row.target)
      await expect(page.locator('#changelist')).toBeVisible()
      results.push({ role: row.username, width, target: row.target, touch: true, keyboard: true })
    }
    expect(errors).toEqual([])
    await context.close()
  }
  await fs.writeFile(path.join(config.artifacts, 'results.json'), JSON.stringify(results, null, 2))
} finally {
  await browser.close()
}
