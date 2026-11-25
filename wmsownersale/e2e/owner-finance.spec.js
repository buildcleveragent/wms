import { test, expect } from '@playwright/test'

for (const width of [390, 1280]) {
  test(`finance reads all owner records without write controls at ${width}px`, async ({page}, testInfo) => {
    await page.setViewportSize({width, height:900})
    const capabilities = {owner_read_only:true, can_view_owner_inventory:true,
      can_export_owner_inventory:true, can_view_owner_operations:true, can_export_operations:true,
      can_manage_owner_inventory_alerts:false}
    const user = {id:5, username:'finance-demo', owner_id:2, roles:['owner_finance'], capabilities}
    await page.addInitScript(user => {
      localStorage.setItem('access','finance-demo-token')
      localStorage.setItem('user',JSON.stringify({type:'object',data:user}))
    }, user)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => {
      if (message.type() === 'error') errors.push(message.text())
    })
    const order = {id:9,order_no:'CK-COLLEAGUE',customer_name:'业务员甲的客户',created_by_name:'业务员甲',
      submit_status:'SUBMITTED',approval_status:'OWNER_PENDING',total_qty:'10',total_amount:'50',
      can_owner_review:false,can_edit:false,can_withdraw:false,can_reorder:false,lines:[]}
    await page.route('**/api/**', route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/profile/')) return route.fulfill({json:{user,capabilities}})
      if (url.pathname === '/api/outbound/orders/9/') return route.fulfill({json:order})
      if (url.pathname === '/api/outbound/orders/') return route.fulfill({json:{count:1,next:null,results:[order]}})
      if (url.pathname.endsWith('/addresses/')) return route.fulfill({json:{customer:{id:1,name:'业务员甲的客户'},private:false,results:[{id:1,contact:'收件人',phone:'13800000000',address:'测试地址',is_default:false}]}})
      return route.fulfill({json:{count:0,next:null,results:[]}})
    })
    await page.goto('/#/pages/features/index')
    await expect(page).toHaveURL(/pages\/features\/index/)
    await expect(page).not.toHaveTitle('')
    await expect(page.getByText('货主财务 · 可查询和导出，不可修改业务数据')).toBeVisible()
    await expect(page.getByText('审批记录',{exact:true})).toBeVisible()
    await expect(page.getByText('计费总览',{exact:true})).toBeVisible()
    await expect(page.getByText('访销下单',{exact:true})).toHaveCount(0)
    await page.screenshot({path:testInfo.outputPath(`finance-${width}.png`),fullPage:true})
    await page.getByText('审批记录',{exact:true}).click()
    await expect(page.getByText('CK-COLLEAGUE',{exact:true})).toBeVisible()
    await expect(page.getByText('审核通过',{exact:true})).toHaveCount(0)
    await expect(page.getByText('取消订单',{exact:true})).toHaveCount(0)
    await page.getByText('CK-COLLEAGUE',{exact:true}).click()
    await expect(page.locator('uni-page:visible').last()).toContainText('CK-COLLEAGUE')
    await expect(page.getByText('审核通过',{exact:true})).toHaveCount(0)
    await page.goto('/#/pages/customers/addresses?customer_id=1')
    await expect(page.getByText('测试地址',{exact:true})).toBeVisible()
    for (const label of ['新增地址','修改','停用','设为默认']) {
      await expect(page.getByText(label,{exact:true})).toHaveCount(0)
    }
    await page.goto('/#/pages/orders/cart')
    await expect(page).toHaveURL(/pages\/read-only/)
    await expect(page.getByText('当前账号无操作权限',{exact:true})).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}
