import { expect, test } from '@playwright/test'
import { expectMobileHealthy, fullProfile, installLoggedInMocks } from './mobile-fixtures'

test('mobile workbench shows only retained warehouse operations without desktop leftovers', async ({ page }, testInfo) => {
  const runtimeErrors = await installLoggedInMocks(page)
  await page.goto('/#/pages/index/index')

  await expect(page).toHaveURL(/pages\/index\/index/)
  await expect(page.locator('uni-page-body').getByText('仓库工作台', { exact: true })).toBeVisible()
  await expect(page.getByText('扫码收银', { exact: true })).toBeVisible()
  for (const label of ['有订单收货', '无订单收货', '上架', '代货主出库', '拣货', '复核', '库存查询', '补货', '移库', '盘点', '要货申请']) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible()
  }
  for (const removed of ['POS 报表', '准确率', '对账', '客户管理', '赊账', '欠款', '打印机配置']) {
    await expect(page.getByText(removed, { exact: true })).toHaveCount(0)
  }
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
  if (testInfo.project.name === 'android-390') await page.screenshot({ path: '/tmp/wmsandroid-workbench-390.png', fullPage: false })
})

test('operation tab lists the retained workflows and hides forbidden entries', async ({ page }) => {
  const restrictedProfile = {
    ...fullProfile,
    perms: [],
    capabilities: Object.fromEntries(Object.keys(fullProfile.capabilities).map((key) => [key, false])),
  }
  const runtimeErrors = await installLoggedInMocks(page, { profile: restrictedProfile })
  await page.goto('/#/pages/features/index')

  await expect(page.locator('uni-page-body').getByText('现场作业', { exact: true })).toBeVisible()
  await expect(page.getByText('无订单收货', { exact: true })).toHaveCount(0)
  await expect(page.getByText('代货主出库', { exact: true })).toHaveCount(0)
  await expect(page.getByText('要货申请', { exact: true })).toHaveCount(0)
  await expect(page.getByText('扫码收银', { exact: true })).toHaveCount(0)
  await page.goto('/#/pages/picking/task_list')
  await expect(page).toHaveURL(/pages\/index\/index/)
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
})

test('relocation task renders an explicit source-product-target scan sequence', async ({ page }, testInfo) => {
  const task = {
    id: 99,
    task_no: 'YK-20260831-0099',
    owner_name: '金桥食品',
    warehouse_name: '一号仓',
    status: 'IN_PROGRESS',
    execution_state: 'IN_PROGRESS',
    can_record: true,
    can_claim: false,
    can_start: false,
    posting_status: 'PENDING',
    lines: [{
      id: 991,
      product_name: '纯牛奶',
      product_code: 'MILK-250',
      from_location_code: 'A01-01-01',
      to_location_code: 'B02-03-04',
      qty_pending: '12.000',
      serial_control: false,
    }],
  }
  const runtimeErrors = await installLoggedInMocks(page, {
    '/api/relocation/pda/tasks/99/': async (route) => route.fulfill({ json: task }),
  })
  await page.goto('/#/pages/inventory/move/detail?task_id=99')

  await expect(page.getByText('任务 YK-20260831-0099', { exact: true })).toBeVisible()
  for (const step of ['扫来源库位', '扫商品或容器', '扫目标库位', '本次数量']) {
    await expect(page.getByText(step, { exact: true })).toBeVisible()
  }
  await expect(page.getByText('确认移库', { exact: true })).toBeVisible()
  await expectMobileHealthy(page)
  expect(runtimeErrors).toEqual([])
  if (testInfo.project.name === 'android-390') await page.screenshot({ path: '/tmp/wmsandroid-relocation-390.png', fullPage: false })
})
