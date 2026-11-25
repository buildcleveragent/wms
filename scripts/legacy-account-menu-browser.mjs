import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

const require = createRequire(new URL('../wmspda/package.json', import.meta.url))
const { chromium, expect } = require('@playwright/test')
const [origin, artifacts] = process.argv.slice(2)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch()
try {
  for (const variant of ['no-menu', 'with-menu', 'link-only', 'existing-group']) {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(`${origin}/${variant}.html`)
    await expect(page.getByRole('heading', { name: '密码已修改' })).toBeVisible()
    expect(errors).toEqual([])

    const sidebar = page.locator('#sidebar')
    const toggle = page.getByRole('button', { name: 'Toggle Sidebar' })
    await expect(sidebar).toBeHidden()
    await toggle.click()
    await expect(sidebar).toBeVisible()
    const groups = page.getByRole('link', { name: '组', exact: true })
    if (variant === 'with-menu' || variant === 'existing-group') {
      await expect(sidebar.locator('.dropdown-menu').getByRole('link', { name: '组' })).toHaveCount(1)
      await expect(groups).toHaveAttribute('href', '/admin/auth/group/')
      if (variant === 'existing-group') await expect(groups).toHaveClass('existing-group')
    } else {
      await expect(groups).toHaveCount(0)
    }
    await page.screenshot({ path: path.join(artifacts, `legacy-accounts-${variant}.png`) })
    await toggle.click()
    await expect(sidebar).toBeHidden()
    expect(errors).toEqual([])
    await page.close()
    console.log(`PASS ${variant} · sidebar toggled · no page errors`)
  }
} finally {
  await browser.close()
}
