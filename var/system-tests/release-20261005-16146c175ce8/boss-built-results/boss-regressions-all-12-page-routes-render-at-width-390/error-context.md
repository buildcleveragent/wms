# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: boss-regressions.spec.js >> all 12 page routes render at width 390
- Location: e2e/boss-regressions.spec.js:325:7

# Error details

```
Error: expect(received).toEqual(expected) // deep equality

- Expected  - 1
+ Received  + 3

- Array []
+ Array [
+   "Failed to load resource: net::ERR_BLOCKED_BY_CLIENT.Inspector",
+ ]
```

# Page snapshot

```yaml
- generic [ref=e4]:
  - generic [ref=e6]:
    - img [ref=e9] [cursor=pointer]
    - generic [ref=e12]: 仓储经营分析中心 - 收费记录详情
  - generic [ref=e16]:
    - generic [ref=e18]:
      - generic [ref=e19]:
        - generic [ref=e20]: Accrual Detail
        - generic [ref=e21]: "-"
      - generic [ref=e22]: "-"
    - generic [ref=e23]:
      - generic [ref=e24]:
        - generic [ref=e25]: 不含税金额
        - generic [ref=e26]: ¥0.00
        - generic [ref=e27]: 税额 ¥0.00
      - generic [ref=e28]:
        - generic [ref=e29]: 数量
        - generic [ref=e30]: "-"
        - generic [ref=e31]: 单价/费率 -
    - generic [ref=e32]:
      - generic [ref=e33]:
        - generic [ref=e34]: 基础信息
        - generic [ref=e35]: 这条收费记录的归属、规则和账期信息。
      - generic [ref=e37]: 应计指纹
      - generic [ref=e39]:
        - generic [ref=e40]: 收费类型
        - generic [ref=e41]: "-"
      - generic [ref=e42]:
        - generic [ref=e43]: 规则算法
        - generic [ref=e44]: "-"
      - generic [ref=e45]:
        - generic [ref=e46]: 规则备注
        - generic [ref=e47]: "-"
      - generic [ref=e48]:
        - generic [ref=e49]: 账期
        - generic [ref=e50]: 未锁账期
      - generic [ref=e51]:
        - generic [ref=e52]: 创建人
        - generic [ref=e53]: "-"
      - generic [ref=e54]:
        - generic [ref=e55]: 创建时间
        - generic [ref=e56]: "-"
      - generic [ref=e57]:
        - generic [ref=e58]: 是否冲销
        - generic [ref=e59]: 否
    - generic [ref=e60]:
      - generic [ref=e61]:
        - generic [ref=e62]: 账单关联
        - generic [ref=e63]: 如果这条收费已经开票，会在这里展示账单信息。
      - generic [ref=e64]: 当前收费记录还没有关联账单。
    - generic [ref=e65]:
      - generic [ref=e66] [cursor=pointer]: 返回货主明细
      - generic [ref=e67] [cursor=pointer]: 返回总览
```

# Test source

```ts
  244 |     await expect(page.getByText('重试', { exact: true })).toBeVisible()
  245 |     expect(requests.filter(url => url.pathname.endsWith('/revenue-assurance/'))).toHaveLength(0)
  246 |     await expect(page.getByText('当前范围暂无数据', { exact: true })).toHaveCount(0)
  247 |     await expect(page.getByText('导出当前报表', { exact: true })).toHaveAttribute('disabled', 'true')
  248 |     await expect(page.getByText('创建例会快照', { exact: true })).toHaveAttribute('disabled', 'true')
  249 |     await page.getByText('重试', { exact: true }).click()
  250 |     await expect(page.locator('.card .value')).toHaveText('3')
  251 |     await expect(page.getByText('重试', { exact: true })).toHaveCount(0)
  252 |     expect(attempts).toBe(2)
  253 |     expect(errors).toEqual([])
  254 |   })
  255 | }
  256 | 
  257 | test('export capability controls both buttons; revoked permission shows the server reason', async ({ page }) => {
  258 |   await mockBoss(page, { respond: url => {
  259 |     if (url.pathname.endsWith('/exports/')) return { status: 403, json: { detail: '没有导出权限' } }
  260 |   } })
  261 |   await open(page, 'cockpit/index')
  262 |   await expect(page.getByText('导出当前报表', { exact: true })).not.toHaveAttribute('disabled', 'true')
  263 |   await page.getByText('导出当前报表', { exact: true }).click()
  264 |   await expect(page.getByText('没有导出权限', { exact: true })).toBeVisible()
  265 |   await page.evaluate(() => {
  266 |     const user = JSON.parse(localStorage.getItem('user'))
  267 |     user.capabilities.can_export_operations = false
  268 |     localStorage.setItem('user', JSON.stringify(user))
  269 |   })
  270 |   // Keep the modified storage; init scripts run on a full reload, so navigate inside the app.
  271 |   await page.getByText('首页总览', { exact: true }).click()
  272 |   await page.getByText('经营驾驶舱', { exact: true }).click()
  273 |   await expect(page.getByText('导出当前报表', { exact: true })).toHaveCount(0)
  274 |   await page.getByText('运营履约', { exact: true }).click()
  275 |   await expect(page.getByText('导出运营明细', { exact: true })).toHaveCount(0)
  276 | })
  277 | 
  278 | test('inventory search replaces a pending page and keeps the new result', async ({ page }) => {
  279 |   const old = deferred(), arrived = deferred()
  280 |   await mockBoss(page, { respond: async url => {
  281 |     if (!url.pathname.endsWith('/inventory/details/')) return
  282 |     const search = url.searchParams.get('search')
  283 |     if (!search) { arrived.resolve(); await old.promise }
  284 |     return { json: { meta, count: 1, next_page: null, results: [{
  285 |       id: search ? 2 : 1, product_id: search ? 2 : 1,
  286 |       product_name: search ? '新商品' : '旧商品', base_unit: 'PCS', onhand_qty: '10',
  287 |     }] } }
  288 |   } })
  289 |   await open(page, 'inventory/detail')
  290 |   await arrived.promise
  291 |   await page.getByRole('searchbox').fill('新商品')
  292 |   await page.getByText('查询', { exact: true }).click()
  293 |   await expect(page.locator('.product-name')).toHaveText('新商品')
  294 |   const finished = page.waitForResponse(response => response.url().includes('/inventory/details/') && !response.url().includes('search='))
  295 |   old.resolve()
  296 |   await finished
  297 |   await expect(page.locator('.product-name')).toHaveText('新商品')
  298 |   await expect(page.getByText('正在加载...', { exact: true })).toHaveCount(0)
  299 | })
  300 | 
  301 | test('owner billing commits all responses from the latest refresh together', async ({ page }) => {
  302 |   const old = deferred(), arrived = deferred()
  303 |   let overviewCalls = 0, billCalls = 0
  304 |   await mockBoss(page, { respond: async url => {
  305 |     if (url.pathname.endsWith('/warehouse-overview/')) return { json: { meta,
  306 |       scope: { owner: 31 }, summary: { accrual_count: ++overviewCalls === 1 ? 111 : 222 },
  307 |     } }
  308 |     if (url.pathname.endsWith('/bills/') && ++billCalls === 1) {
  309 |       arrived.resolve(); await old.promise
  310 |       return { json: [{ id: 1, invoice_no: '旧账单', currency: 'CNY', total: '100' }] }
  311 |     }
  312 |   } })
  313 |   await open(page, 'billing/owner_detail?owner=31')
  314 |   await arrived.promise
  315 |   await page.getByText('刷新', { exact: true }).click()
  316 |   await expect(page.locator('.kpi-card').first()).toContainText('222')
  317 |   const finished = page.waitForResponse(response => response.url().includes('/bills/'))
  318 |   old.resolve()
  319 |   await finished
  320 |   await expect(page.locator('.kpi-card').first()).toContainText('222')
  321 |   await expect(page.getByText('旧账单', { exact: true })).toHaveCount(0)
  322 | })
  323 | 
  324 | for (const width of [390, 1440]) {
  325 |   test(`all 12 page routes render at width ${width}`, async ({ page }, testInfo) => {
  326 |     await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
  327 |     const { errors, consoleErrors } = await mockBoss(page)
  328 |     const routes = ['login', 'home/index', 'inventory/index', 'alerts/index', 'cockpit/index',
  329 |       'billing/overview', 'billing/owner_detail?owner=31', 'inventory/detail',
  330 |       'alerts/section?section=review_differences',
  331 |       'alerts/detail?section=review_differences&item_type=review_difference&id=1',
  332 |       'billing/bill_detail?id=1', 'billing/accrual_detail?id=1']
  333 |     for (const route of routes) {
  334 |       await open(page, route)
  335 |       await expect(page.locator('uni-page-body').last()).toContainText(/仓储|计费|预警|收费|账单|Owner|Accrual|Bill/)
  336 |       await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  337 |       expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  338 |       if (route === 'cockpit/index') {
  339 |         await expect(page.locator('.card .value')).toHaveText('3')
  340 |         await page.screenshot({ path: testInfo.outputPath(`cockpit-${width}.png`) })
  341 |       }
  342 |     }
  343 |     expect(errors).toEqual([])
> 344 |     expect(consoleErrors).toEqual([])
      |                           ^ Error: expect(received).toEqual(expected) // deep equality
  345 |   })
  346 | }
  347 | 
```