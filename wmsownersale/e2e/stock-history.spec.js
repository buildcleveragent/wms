import { test as base, expect } from '@playwright/test'
import { instrumentBrowserTests } from '../../frontend/browser-diagnostics.mjs'

const test = instrumentBrowserTests(base, expect)
const totals = [
  {category:'inbound',base_unit:'EA',base_unit_name:'瓶',qty:'160',amount:'400',currency:'CNY',priced_count:2,unpriced_count:0,amount_status:'PRICED'},
  {category:'inbound',base_unit:'BOX',base_unit_name:'箱',qty:'8',amount:'40',currency:'CNY',priced_count:1,unpriced_count:0,amount_status:'PRICED'},
  {category:'outbound',base_unit:'EA',base_unit_name:'瓶',qty:'50',amount:'250',currency:'CNY',priced_count:2,unpriced_count:0,amount_status:'PRICED'},
]
const amounts = [
  {category:'inbound',amount:'440',currency:'CNY',amount_status:'PRICED'},
  {category:'outbound',amount:'250',currency:'CNY',amount_status:'PRICED'},
]
const envelope = {snapshot:'browser-statistics-snapshot',as_of:'2026-11-20T10:00:00+08:00',count:1,next_page:null}

for (const width of [320, 390, 768, 1280]) {
  test(`history calendar to directional records and evidence at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({width,height:900})
    const role = width === 390 ? 'owner_salesperson' : width === 768 ? 'owner_finance' : 'owner_manager'
    const capabilities = {can_view_owner_inventory:true,can_view_owner_operations:true,can_export_operations:true}
    await page.addInitScript(({capabilities,role}) => {
      localStorage.setItem('access','synthetic-statistics-token')
      localStorage.setItem('user',JSON.stringify({type:'object',data:{id:1,owner_id:2,roles:[role],capabilities}}))
    }, {capabilities,role})
    const queries = []
    const chartQueries = []
    await page.route('**/api/**', route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/profile/')) return route.fulfill({json:{user:{id:1,owner_id:2,roles:[role]},capabilities}})
      if (!url.pathname.includes('/owner-history/statistics/')) return route.fulfill({json:{count:0,results:[]}})
      const section = url.pathname.split('/statistics/')[1].replace(/\/$/,'')
      const params = Object.fromEntries(url.searchParams)
      if (section === 'chart') {
        chartQueries.push(params)
        const money=params.metric==='amount'
        const unitLabels = width === 320 ? ['独立包装瓶','箱','件','提','包','套','罐','盒','桶','袋'] : ['瓶','箱']
        const options = money ? [{code:'CNY',label:'CNY',key:'currency:CNY'}] : unitLabels.map((label,index) => {
          const code = index === 0 ? 'EA' : index === 1 ? 'BOX' : 'UNIT' + index
          return {code,label,key:'base_unit:' + code}
        })
        const dimension = options.find(option => option.key === params.dimension) || options[0]
        const drinks = {product_id:7,product_name:'合成饮料',category:'outbound',value:money?'250.00':'50',amount_status:'PRICED'}
        const snacks = {product_id:8,product_name:'合成饼干',category:'outbound',value:money?'80.00':'12',amount_status:'PRICED'}
        const october = params.date_from === '2026-10-01'
        const results=params.chart==='ranking'
          ? (october ? [{...snacks,value:money?'320.00':'65'},drinks] : [drinks,snacks])
          : ['2026-11-02','2026-11-03'].flatMap((day,index)=>
              ['inbound','outbound'].map((category,i)=>({category,period_start:day,
                period_end:day,range_start:day,range_end:day,
                value:money ? [['250','150'],['150','100']][index][i] : [['100','30'],['60','20']][index][i],
                amount_status:'PRICED'})))
        return route.fulfill({json:{...envelope,results,dimension,options,excluded_count:0}})
      }
      queries.push({section,...params})
      if (section === 'summary') return route.fulfill({json:{...envelope,totals,amounts,warehouses:[{id:1,name:'一号仓库'}],access_scope:role === 'owner_salesperson' ? 'personal' : 'owner',scope_label:params.date_from ? '期间合计' : '历史累计'}})
      let results
      if (section === 'periods') {
        const bounds = {day:['2026-11-02','2026-11-02'],week:['2026-11-02','2026-11-08'],month:['2026-11-01','2026-11-30'],year:['2026-01-01','2026-12-31']}[params.grain]
        results = [{period_start:bounds[0],period_end:bounds[1],totals,amounts}]
      } else if (section === 'products') {
        results = [{product_id:7,product_name:'合成饮料',product_code:'DEMO-7',product_spec:'500 毫升',totals,amounts,last_at:'2026-11-02T10:00:00+08:00',current_stock:[{base_unit:'EA',base_unit_name:'瓶',current_onhand:'110',current_available:'100'}]}]
      } else if (section === 'events') {
        const october = params.product_id === '8' && params.date_from === '2026-10-01'
        const outbound = params.category === 'outbound'
        results = [{event_id:outbound ? 'review-7' : 'ledger-42',document_no:october ? 'CK-OCT' : outbound ? 'CK-DEMO' : 'RK-DEMO',category_label:outbound ? '正常出库' : '正常入库',product_count:1,product_name:october ? '合成饼干' : '合成饮料',event_at:october ? '2026-10-12T10:00:00+08:00' : '2026-11-02T10:00:00+08:00',warehouse_name:'一号仓库',totals:totals.filter(t => t.category === params.category),amounts:amounts.filter(a => a.category === params.category)}]
      } else {
        return route.fulfill({json:{...envelope,event:{event_id:'ledger-42'},results:[{id:'ledger-42',category:'inbound',category_label:'正常入库',product_id:7,product_name:'合成饮料',product_code:'DEMO-7',base_unit:'EA',base_unit_name:'瓶',qty_delta:'100',amount:'250',currency:'CNY',posting_status:'POSTED',posted_at:'2026-11-02T10:00:00+08:00',document_no:'RK-DEMO',batch_no:'B',inbound_id:9}],receipts:[{id:1,number:'REC-DEMO',lines:[{id:1,product_name:'合成饮料',base_unit:'EA',base_unit_name:'瓶',qty_ok:'98',qty_damage:'2',qty_reject:'3',photos:[]}]}]}})
      }
      return route.fulfill({json:{...envelope,results}})
    })
    await page.goto('/#/pages/reports/stock-history', {waitUntil:'domcontentloaded'})
    await expect(page).toHaveURL(/pages\/reports\/stock-history/)
    await expect(page).not.toHaveTitle('')
    const screen = () => page.locator('.history-page:visible').last()
    await expect(screen().locator('.record')).toHaveCount(1)
    if (role === 'owner_salesperson') await expect(screen()).toContainText('本人经手')
    await expect(screen()).toContainText('440.00 元')
    await expect(screen()).toContainText('250.00 元')
    await expect(screen().locator('.record')).toContainText('160 瓶')
    await expect(screen().locator('.record')).toContainText('8 箱')
    expect(queries.find(q => q.section === 'periods').grain).toBe('day')
    expect(queries[0]).not.toHaveProperty('date_from')
    await expect(screen()).not.toContainText('EA')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const charts = screen().locator('.charts')
    const toolbar = charts.locator('.chart-toolbar')
    const groups = toolbar.locator('.choice-group')
    const group = name => toolbar.getByRole('group',{name,exact:true})
    const select = (name,option) => group(name).getByText(option,{exact:true}).click()
    await expect(groups).toHaveCount(6)
    const rows = await groups.evaluateAll(nodes => new Set(nodes.map(n => Math.round(n.getBoundingClientRect().top))).size)
    if (width === 1280) expect(rows).toBe(1)
    if (width <= 390) expect(rows).toBeGreaterThan(1)
    const unitButtons = group('单位').locator('.choice')
    await expect(unitButtons).toHaveCount(width === 320 ? 10 : 2)
    if (width === 320) {
      const first = await unitButtons.first().boundingBox()
      const last = await unitButtons.last().boundingBox()
      expect(last.y).toBeGreaterThan(first.y)
    }
    expect(await toolbar.evaluate(el => [...el.querySelectorAll('.choice')].every(button => {
      const rect = button.getBoundingClientRect()
      return rect.width > 0 && rect.left >= el.getBoundingClientRect().left - 1 &&
        rect.right <= el.getBoundingClientRect().right + 1 && button.scrollWidth <= button.clientWidth + 1
    }))).toBe(true)
    await select('图形','折线图')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await expect(charts.locator('.segment')).toHaveCount(2)
    await charts.locator('.point.inbound').first().click()
    await expect(charts.locator('.point-info')).toContainText(width === 320 ? '100 独立包装瓶' : '100 瓶')
    await charts.screenshot({path:testInfo.outputPath(`trend-${width}.png`)})
    await charts.getByText('查看记录 ›',{exact:true}).click()
    await expect(screen().locator('.record')).toContainText('RK-DEMO')
    expect(queries.at(-1)).toMatchObject({section:'events',category:'inbound',base_unit:'EA',date_from:'2026-11-02'})
    await page.goBack()
    await select('图表', '商品排行')
    await select('指标', '金额')
    await expect(groups).toHaveCount(5)
    await expect(toolbar.locator('.single')).toHaveText('人民币')
    await expect(group('周期').locator('.active')).toHaveText('全时段')
    const firstRank = charts.locator('.rank').first()
    await select('周期','按月')
    await expect(firstRank).toContainText('合成饮料')
    await expect(firstRank).toContainText('250.00 元')
    await expect.poll(() => chartQueries.at(-1).date_from).toBe('2026-11-01')
    const navigator = charts.locator('.rank-navigation')
    await navigator.getByText('上月',{exact:true}).click()
    await expect(firstRank).toContainText('合成饼干')
    await expect(firstRank).toContainText('320.00 元')
    expect(chartQueries.at(-1)).toMatchObject({date_from:'2026-10-01',date_to:'2026-10-31',metric:'amount'})
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await charts.screenshot({path:testInfo.outputPath(`ranking-${width}.png`)})
    await firstRank.click()
    await expect(screen().locator('.record')).toContainText('CK-OCT')
    expect(queries.at(-1)).toMatchObject({section:'events',category:'outbound',currency:'CNY',product_id:'8',
      date_from:'2026-10-01',date_to:'2026-10-31',snapshot:envelope.snapshot})
    await page.goBack()
    await expect(navigator).toContainText('2026 年 10 月')
    await expect(firstRank).toContainText('320.00 元')
    await navigator.getByText('下月',{exact:true}).click()
    await expect(firstRank).toContainText('250.00 元')
    await navigator.getByText('2026 年 11 月',{exact:false}).click()
    const popup = page.locator('.uni-picker-toggle')
    const monthWheel = popup.locator('.uni-picker-view-group').filter({
      has: page.locator('.uni-picker-view-content > :nth-child(12):last-child')
    })
    await monthWheel.hover()
    await page.mouse.wheel(0, -80)
    await expect.poll(() => monthWheel.evaluate(wheel => {
      const october = wheel.querySelector('.uni-picker-view-content').children[9]
      const indicator = wheel.querySelector('.uni-picker-view-indicator')
      return Math.abs(october.getBoundingClientRect().top - indicator.getBoundingClientRect().top)
    })).toBeLessThan(1)
    await popup.locator('.uni-picker-action-confirm').click()
    await expect(firstRank).toContainText('320.00 元')
    await navigator.getByText('下月',{exact:true}).click()
    await expect(firstRank).toContainText('250.00 元')
    await select('周期','按周')
    await expect(navigator).toContainText('2026-10-26 至 2026-11-01')
    await select('图表', '按期统计')
    const tabs = screen().locator('.view-tabs')
    for (const label of ['按天统计','按商品','按单据','原始收发']) {
      await tabs.getByText(label,{exact:true}).click({trial:true})
    }
    await tabs.getByText('按商品',{exact:true}).click()
    await expect(screen().locator('.record')).toContainText('合成饮料')
    await tabs.getByText('按天统计',{exact:true}).click()
    await expect(screen().locator('.record .period-side')).toHaveCount(2)
    await expect(tabs.locator('.active')).toHaveText('按天统计')
    await page.screenshot({path:testInfo.outputPath(`history-${width}.png`),fullPage:true})
    for (const [label,grain] of [['按周','week'],['按月','month'],['按年','year']]) {
      await screen().locator('.segments').getByText(label,{exact:true}).click()
      await expect.poll(() => queries.at(-1)?.grain).toBe(grain)
      await expect(screen().locator('.record')).toHaveCount(1)
    }
    await expect(screen().locator('.record')).toContainText('2026 年')
    const period = screen().locator('.record')
    const left = await period.locator('.inbound').boundingBox()
    const right = await period.locator('.outbound').boundingBox()
    expect(left.y).toBeCloseTo(right.y, 0)
    expect(left.x + left.width).toBeLessThanOrEqual(right.x)
    await period.locator('.outbound').scrollIntoViewIfNeeded()
    const scrollY = await page.evaluate(() => window.scrollY)
    await period.locator('.outbound').click()
    await expect(screen().locator('.record')).toContainText('CK-DEMO')
    expect(queries.at(-1)).toMatchObject({section:'events',category:'outbound',date_from:'2026-01-01',date_to:'2026-12-31',snapshot:envelope.snapshot})
    await page.goBack()
    await expect(screen().locator('.record')).toContainText('2026 年')
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeCloseTo(scrollY, 0)
    await screen().locator('.record .inbound').click()
    await expect(screen().locator('.record')).toContainText('RK-DEMO')
    expect(queries.at(-1)).toMatchObject({section:'events',category:'inbound',date_from:'2026-01-01',date_to:'2026-12-31'})
    await screen().locator('.record').click()
    await expect(screen()).toContainText('良品 98 · 破损 2 · 拒收 3')
    await expect(screen()).toContainText('250.00 元')
    await expect(screen()).not.toContainText('EA')
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    await page.screenshot({path:testInfo.outputPath(`history-${width}-detail.png`),fullPage:true})
  })
}
