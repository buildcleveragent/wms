import { expect, test } from '@playwright/test'
import { camera, expectMobileHealthy, installLoggedInMocks, product, scan } from './mobile-fixtures'

async function seedPreviousCart(page, pendingPayload) {
  const cartItem = {
    ...product, product_id: product.id, cart_id: 'old-line', unit_index: 0, qty: 3, available_qty: 123,
  }
  await page.addInitScript(({ cartItem, pendingPayload }) => {
    if (sessionStorage.getItem('pos-v1-seeded')) return
    sessionStorage.setItem('pos-v1-seeded', '1')
    const key = 'wmsandroid:pos:draft:17:3'
    const save = (key, data) => localStorage.setItem(key, JSON.stringify({ type: 'object', data }))
    save(key, { saved_at: Date.now(), cart_items: [cartItem] })
    if (pendingPayload) {
      save(key + ':pending-checkout', {
        version: 1, created_at: Date.now(), idempotency_key: pendingPayload.idempotency_key, payload: pendingPayload,
      })
    }
  }, { cartItem, pendingPayload })
}

test('POS search, scan, cart and checkout all use the default saleable zones', async ({ page }, testInfo) => {
  let checkoutPayload = null
  const productQueries = []
  const inventory = [
    { zone: '1', qty: 4 }, // Pick
    { zone: '2', qty: 6 }, // Storage
    { zone: '5', qty: 3 }, // Returns
  ]
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/pos/products/': async (route, url) => {
      productQueries.push(url.searchParams)
      const requestedZone = url.searchParams.get('zone_type')
      const available = inventory
        .filter(row => ['1', '2'].includes(row.zone) && (requestedZone === null || requestedZone === row.zone))
        .reduce((sum, row) => sum + row.qty, 0)
      await route.fulfill({ json: { count: 1, next: null, results: [{ ...product, available_qty: available.toFixed(3) }] } })
    },
    '/api/pos/checkout/': async (route) => {
      checkoutPayload = route.request().postDataJSON()
      await route.fulfill({ status: 201, json: { sale: { id: 71, sale_no: 'XS-20260831-0071' } } })
    },
  })
  await camera(page)
  await page.goto('/#/pages/pos/index')

  await expect(page).toHaveURL(/pages\/pos\/index/)
  await expect(page.getByText('手机收银', { exact: true })).toBeVisible()
  await expect(page.getByText('拣货区＋存储区可售', { exact: true })).toBeVisible()
  const input = page.getByTestId('pos-product-input').locator('input')
  await input.fill('纯牛奶')
  await input.press('Enter')
  await expect(page.getByTestId('pos-product-row')).toHaveCount(1)
  await expect(page.getByTestId('pos-product-row')).toContainText('拣货区＋存储区可售 10 盒')
  await page.getByTestId('pos-product-row').getByText('添加', { exact: true }).click()
  await expect(page.getByTestId('pos-cart-product').locator('.cart-name')).toHaveText('纯牛奶　250ml×12盒')
  await expect(page.locator('.price-input')).toHaveCount(0)
  await scan(page, '690000000001')
  const cart = page.getByTestId('pos-cart-product')
  await expect(cart).toContainText('拣货区＋存储区可售 10 盒 · 本行 2 盒')
  const quantity = cart.locator('.quantity-input input')
  await quantity.fill('7')
  await quantity.blur()
  await expect(cart).toContainText('拣货区＋存储区可售 10 盒 · 本行 7 盒')
  if (testInfo.project.name === 'android-390') {
    await page.screenshot({ path: testInfo.outputPath('pos-saleable-stock.png') })
  }

  await page.getByTestId('pos-checkout').click()
  await expect(page.getByText('确认收款', { exact: true })).toBeVisible()
  await expect(page.getByText('顾客已完成付款后再确认', { exact: true })).toBeVisible()
  if (testInfo.project.name === 'android-390') await page.screenshot({ path: '/tmp/wmsandroid-payment-390.png', fullPage: false })
  await page.getByText('确认已收款并记录销售', { exact: true }).click()
  await expect.poll(() => checkoutPayload).not.toBeNull()
  expect(checkoutPayload.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
  expect(checkoutPayload).not.toHaveProperty('stock_zone_type')
  expect(checkoutPayload).not.toHaveProperty('zone_type')
  expect(checkoutPayload.items[0]).toEqual({ product_id: product.id, qty: '7.000', price: '5.8000' })
  expect(checkoutPayload.payment.amount_received).toBe('40.60')
  expect(productQueries.some(query => query.get('search') === '纯牛奶')).toBe(true)
  expect(productQueries.some(query => query.get('barcode') === '690000000001')).toBe(true)
  expect(productQueries.some(query => query.get('search') === product.code)).toBe(true)
  expect(productQueries.every(query => !query.has('zone_type') && !query.has('stock_zone_type'))).toBe(true)
  expect(checkoutPayload).not.toHaveProperty('customer_id')
  expect(checkoutPayload).not.toHaveProperty('payments')
  expect(checkoutPayload.payment.method).toBe('WECHAT')
  await expect(page.getByTestId('pos-cart-product')).toHaveCount(0)
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
})

test('retry sends a saved checkout with its original explicit zone and request ID', async ({ page }) => {
  const payload = {
    idempotency_key: '06b71cca-9bcb-4d73-9912-8ae4eb9c6a03', src_bill_no: '',
    stock_zone_type: 3, remark: '原收款备注', // Historical receiving-zone request.
    items: [{ product_id: product.id, qty: '1.000', price: '5.8000', amount: '5.80' }],
    payment: { method: 'CASH', amount_received: '6.00', reference_no: '' },
  }
  const sent = []
  const productQueries = []
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/pos/products/': async (route, url) => {
      productQueries.push(url.pathname)
      await route.fulfill({ json: { count: 0, next: null, results: [] } })
    },
    '/api/pos/checkout/': async route => {
      sent.push(route.request().postDataJSON())
      await route.fulfill({ status: 200, json: { sale: { id: 71, sale_no: 'ORIGINAL-71' } } })
    },
  })
  await seedPreviousCart(page, payload)
  await page.goto('/#/pages/pos/index')
  await expect(page.getByText('按原销售请求核对', { exact: true })).toBeVisible()
  await page.getByText('重试确认', { exact: true }).click()
  await expect(page.getByText('销售已记录', { exact: true })).toBeVisible()
  expect(sent).toEqual([payload])
  expect(productQueries).toEqual([])
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
})

test('a disputed legacy zero-price checkout stays locked across reloads', async ({ page }) => {
  const payload = {
    idempotency_key: 'legacy-uncertain', stock_zone_type: 1,
    items: [{ product_id: product.id, qty: '1.000', price: '0.0000', amount: '0.00' }],
    payment: { method: 'CASH', amount_received: '0.00', reference_no: '' },
  }
  const sent = []
  await installLoggedInMocks(page, { '/api/pos/checkout/': route => {
    sent.push(route.request().postDataJSON())
    return route.fulfill(sent.length === 1
      ? { status: 400, json: { idempotency_key: ['原销售需要核对'] } }
      : { status: 201, json: { sale: { id: 71, sale_no: 'ORIGINAL-71' } } })
  } })
  await seedPreviousCart(page, payload)
  await page.goto('/#/pages/pos/index')
  await page.getByText('重试确认', { exact: true }).click()
  await expect(page.locator('.uni-modal__title')).toHaveText('原销售需要核对')
  await page.reload()
  await page.getByText('重试确认', { exact: true }).click()
  await expect(page.getByText('销售已记录', { exact: true })).toBeVisible()
  expect(sent).toEqual([payload, payload])
})

for (const mode of ['unit', 'onsite', 'gift']) {
  test(`mobile cashier confirms ${mode} pricing and half-unit rounding`, async ({ page }) => {
    let body
    await installLoggedInMocks(page, {
      '/api/pos/products/': route => route.fulfill({ json: {
        count: 1, results: [{ ...product, price: mode === 'onsite' ? null : '1.23', min_price: null, max_discount: null }],
      } }),
      '/api/pos/checkout/': route => {
        body = route.request().postDataJSON()
        return route.fulfill({ status: 201, json: { sale: { id: 6, sale_no: 'XS-6' } } })
      },
    })
    await page.goto('/#/pages/pos/index')
    const search = page.getByTestId('pos-product-input').locator('input')
    await search.fill(product.name)
    await search.press('Enter')
    const result = page.getByTestId('pos-product-row')
    if (mode === 'onsite') await expect(result).toContainText('待定价')
    await result.getByText('添加', { exact: true }).click()
    const cart = page.getByTestId('pos-cart-product')
    const qty = cart.locator('.quantity-input input')
    await qty.fill('0.5')
    await qty.blur()
    if (mode === 'onsite') {
      await expect(cart.getByText('本行赠送')).toHaveCount(0)
      await page.getByTestId('pos-checkout').click()
      await expect(page.getByText('确认收款', { exact: true })).toHaveCount(0)
      await cart.locator('.manual-price input').fill('1.23')
    } else if (mode === 'gift') {
      await cart.getByText('本行赠送').click()
      await expect(cart).toContainText('赠品')
    }
    await page.getByTestId('pos-checkout').click()
    await page.getByText('确认已收款并记录销售', { exact: true }).click()
    await expect.poll(() => body).toBeTruthy()
    expect(body.items).toEqual([{
      product_id: product.id, qty: '0.500', price: mode === 'gift' ? '0.0000' : '1.2300',
      ...(mode === 'onsite' ? { onsite_price: true } : {}),
      ...(mode === 'gift' ? { is_gift: true } : {}),
    }])
    expect(body.payment.amount_received).toBe(mode === 'gift' ? '0.00' : '0.62')
  })
}

test('restored drafts hide old warehouse stock until the default saleable stock is verified', async ({ page }, testInfo) => {
  const queries = []
  let finishLookup
  const waiting = new Promise(resolve => { finishLookup = resolve })
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/pos/products/': async (route, url) => {
      queries.push(url.searchParams)
      if (queries.length === 1) {
        await waiting
        await route.fulfill({ json: { count: 0, next: null, results: [] } })
      } else {
        await route.fulfill({ json: { count: 1, next: null, results: [{ ...product, available_qty: 2 }] } })
      }
    },
  })
  await seedPreviousCart(page)
  await page.goto('/#/pages/pos/index')
  const cart = page.getByTestId('pos-cart-product')
  await expect(cart).toContainText('库存待核对')
  await expect(cart).not.toContainText('123')
  await expect(page.getByTestId('pos-checkout')).toHaveAttribute('disabled', /.*/)
  finishLookup()
  const retry = page.getByText('重新核对库存', { exact: true })
  await expect(retry).toBeVisible()
  await expect(cart).toContainText('库存待核对')
  await expect(page.getByTestId('pos-checkout')).toHaveAttribute('disabled', /.*/)
  if (testInfo.project.name === 'android-390') {
    await page.screenshot({ path: testInfo.outputPath('pos-restored-stock-retry.png') })
  }
  await retry.click()
  await expect(cart).toContainText('拣货区＋存储区可售 2 盒 · 本行 3 盒')
  await expect(page.locator('.scan-feedback')).toContainText('各包装合计超过拣货区＋存储区可售库存')
  await cart.getByText('−', { exact: true }).click()
  await page.getByTestId('pos-checkout').click()
  await expect(page.getByText('确认收款', { exact: true })).toBeVisible()
  expect(queries.length).toBeGreaterThanOrEqual(3)
  expect(queries.every(query => !query.has('zone_type') && !query.has('stock_zone_type'))).toBe(true)
  expect(runtimeErrors).toEqual([])
})

test('sales history exposes detail, return and void only with their permissions', async ({ page }, testInfo) => {
  const sale = {
    id: 71,
    sale_no: 'XS-20260831-0071',
    status: 'COMPLETED',
    created_at: '2026-08-31T08:30:00+08:00',
    total_amount: '69.60',
    item_count: 12,
    payment: { method: 'WECHAT' },
  }
  const detail = {
    ...sale,
    lines: [{ id: 701, product_name: '纯牛奶', product_code: 'MILK-250', qty: '12.000', amount: '69.60', returnable_qty: '12.000', returned_qty: '0', returned_amount: '0' }],
  }
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/pos/sales/': async (route) => route.fulfill({ json: { count: 1, next: null, results: [sale] } }),
    '/api/pos/sales/71/': async (route) => route.fulfill({ json: detail }),
  })
  await page.goto('/#/pages/pos/history')

  await expect(page.getByText('XS-20260831-0071', { exact: true })).toBeVisible()
  await page.getByText('XS-20260831-0071', { exact: true }).click()
  await expect(page.getByText('纯牛奶', { exact: true })).toBeVisible()
  await expect(page.getByText('退货', { exact: true })).toBeVisible()
  await expect(page.getByText('作废', { exact: true })).toBeVisible()
  await page.getByText('退货', { exact: true }).click()
  await expect(page.getByText('销售退货', { exact: true })).toBeVisible()
  await expect(page.getByText('确认退货并记录退款', { exact: true })).toBeVisible()
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
  if (testInfo.project.name === 'android-390') await page.screenshot({ path: '/tmp/wmsandroid-history-390.png', fullPage: false })
})
