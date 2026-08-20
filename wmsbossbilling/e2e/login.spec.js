import { expect, test } from '@playwright/test'

test('renders the boss login without framework errors', async ({ page }) => {
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/#/pages/login')
  await expect(page.locator('.app-title')).toHaveText('仓储经营分析中心')
  const inputs = page.getByRole('textbox')
  await expect(inputs).toHaveCount(2)
  const username = inputs.nth(0)
  const password = inputs.nth(1)
  await expect(username).toBeVisible()
  await expect(password).toBeVisible()
  await username.fill('browser-smoke-user')
  await password.fill('not-a-real-credential')
  await page.getByText('显示', { exact: true }).click()
  await expect(page.getByText('隐藏', { exact: true })).toBeVisible()
  await expect(page.locator('.submit-btn')).toBeEnabled()
  expect(errors).toEqual([])
})
