import { expect, test } from '@playwright/test'

test('H5 application renders without a framework error overlay', async ({ page }) => {
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await page.goto('/')
  await expect(page).toHaveURL(/127\.0\.0\.1:5173/)
  await expect(page.locator('body')).not.toBeEmpty()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors.filter((message) => /SyntaxError|ReferenceError/.test(message))).toEqual([])
  await page.screenshot({ path: '/tmp/wmspda-h5-smoke.png', fullPage: false })
})

test('committed checkout rotates the key before a failed print is reported', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { CHECKOUT_STATUS, executeCheckoutFlow } = await import('/utils/posCheckoutFlow.js')
    const state = {
      cart: [{ product_id: 1, qty: 1 }],
      key: 'sale-key-1',
      receipt: null,
      message: '',
    }
    const flow = await executeCheckoutFlow({
      submit: async () => ({ receipt: { sale_no: 'POS-1' } }),
      commit: async (response) => {
        state.receipt = response.receipt
        state.cart = []
        state.key = 'sale-key-2'
      },
      print: async () => false,
    })
    if (flow.status === CHECKOUT_STATUS.PRINT_FAILED) {
      state.message = '结账已成功，但打印失败，请点击“打印销售单”重试'
    }
    document.body.innerHTML = `
      <main>
        <p data-testid="receipt">${state.receipt.sale_no}</p>
        <p data-testid="cart-count">${state.cart.length}</p>
        <p data-testid="idempotency-key">${state.key}</p>
        <p data-testid="print-warning">${state.message}</p>
      </main>`
    return { status: flow.status }
  })
  expect(result.status).toBe('print_failed')
  await expect(page.getByTestId('receipt')).toHaveText('POS-1')
  await expect(page.getByTestId('cart-count')).toHaveText('0')
  await expect(page.getByTestId('idempotency-key')).toHaveText('sale-key-2')
  await expect(page.getByTestId('print-warning')).toContainText('结账已成功，但打印失败')
})
