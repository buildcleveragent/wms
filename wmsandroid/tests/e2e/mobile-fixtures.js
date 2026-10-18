import { expect } from '@playwright/test'

export const fullProfile = {
  user: {
    id: 17,
    username: 'mobile_cashier',
    display_name: '张师傅',
    warehouse_id: 3,
    scopes: { warehouse_ids: [3] },
  },
  perms: [
    'pos.add_possale',
    'pos.view_possale',
    'pos.change_possale',
    'pos.add_posreturn',
    'pos.add_posrefund',
    'tasking.view_wmstask',
    'tasking.claim_task_as_wh_operator',
    'inventory.view_inventorydetail',
  ],
  capabilities: {
    can_process_warehouse_assisted_outbound: true,
    can_receive_without_order: true,
    can_request_replenishment: true,
    can_execute_replenishment: true,
    can_request_relocation: true,
    can_manage_relocation: true,
    can_execute_relocation: true,
    can_manage_relocation_tasks: true,
  },
}

export const product = {
  id: 101,
  owner_id: 8,
  owner_name: '金桥食品',
  code: 'MILK-250',
  sku: 'SKU-MILK-250',
  name: '纯牛奶',
  spec: '250ml×12盒',
  price: '5.80',
  min_price: '5.80',
  available_qty: '120.000',
  base_unit: { code: 'EA', name: '盒' },
  matched_barcode: { package_id: null, label: '盒', multiplier: 1, code_type: 'UNIT' },
  barcode_error: '',
  unit_options: [
    { kind: 'base', package_id: null, label: '盒', multiplier: 1, barcode: '690000000001' },
    { kind: 'package', package_id: 22, label: '箱', multiplier: 12, barcode: 'CASE-690000000001' },
  ],
}

export async function installLoggedInMocks(page, overrides = {}) {
  const runtimeErrors = []
  page.on('pageerror', (error) => runtimeErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') runtimeErrors.push(message.text())
  })
  await page.addInitScript(() => {
    // Seed one login, without silently logging back in after a 401/reLaunch.
    if (!sessionStorage.getItem('mobile-test-login-seeded')) {
      localStorage.setItem('access', 'e2e-access')
      sessionStorage.setItem('mobile-test-login-seeded', '1')
    }
  })
  await page.route('http://127.0.0.1:8001/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const custom = overrides[url.pathname]
    if (custom) {
      await custom(route, url)
      return
    }
    if (url.pathname === '/api/auth/profile/') {
      await route.fulfill({ json: overrides.profile || fullProfile })
      return
    }
    if (url.pathname === '/api/pos/shifts/current/') {
      await route.fulfill({ json: { shift: { id: 6, shift_no: '20260831-01', status: 'OPEN', summary: { expected_cash_amount: '100.00', payments: [] } } } })
      return
    }
    await route.fulfill({ json: { count: 0, next: null, results: [] } })
  })
  return runtimeErrors
}

export async function expectMobileHealthy(page) {
  await expect(page.locator('body')).not.toBeEmpty()
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
  }))
  expect(widths.page).toBeLessThanOrEqual(widths.viewport + 1)
}
