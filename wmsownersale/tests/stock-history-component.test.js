import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ history: vi.fn(), download: vi.fn(), photo: vi.fn(), hooks: {} }))
const auth = reactive({ user: {id:1,owner_id:2}, roles:['owner_manager'], capabilities:{}, bootstrap:vi.fn() })
vi.mock('@/store/auth', () => ({useAuth: () => auth}))
vi.mock('@dcloudio/uni-app', () => Object.fromEntries(['onLoad','onShow','onHide','onUnload'].map(key => [key, cb => (mocks.hooks[key] ||= []).push(cb)])))
vi.mock('@/utils/request', () => ({api: {stockStatistics:mocks.history}, downloadAuthenticatedFile:mocks.download, returnPhotoURL:mocks.photo}))

import History from '@/pages/reports/stock-history.vue'
import { periodFilters } from '@/utils/stockHistory'

const total = (category, qty, amount) => ({category, qty, amount, base_unit:'EA', base_unit_name:'瓶',currency:'CNY',amount_status:'PRICED',priced_count:2,unpriced_count:0,unpriced_qty:'0'})
const totals = () => [total('inbound','160','400'),total('outbound','50','250')]
const amounts = () => totals().map(({category,amount,currency,amount_status}) => ({category,amount,currency,amount_status}))
const summary = () => ({snapshot:'signed-snapshot',as_of:'2026-11-12T12:00:00+08:00',scope_label:'历史累计',warehouses:[{id:1,name:'一号仓库'}],totals:totals(),amounts:amounts()})
const product = () => ({product_id:7,product_name:'测试饮料',product_code:'P-7',totals:totals(),amounts:amounts(),current_stock:[{base_unit:'EA',base_unit_name:'瓶',current_onhand:'110',current_available:'100'}],last_at:'2026-11-04T10:00:00+08:00'})
const event = () => ({event_id:'review-42',category:'outbound',category_label:'正常出库',document_no:'CK-1',totals:[total('outbound','50','250')],amounts:[amounts()[1]],event_at:'2026-11-02T10:00:00+08:00',warehouse_name:'一号仓库',product_name:'测试饮料',product_count:1})
function data(section) {
  if(section === 'summary') return summary()
  const envelope = {snapshot:'signed-snapshot',as_of:summary().as_of,count:1,next_page:null}
  if(section === 'products') return {...envelope,results:[product()]}
  if(section === 'periods') return {...envelope,results:[{totals:totals(),amounts:amounts(),period_start:'2026-11-01',period_end:'2026-11-30'}]}
  if(section === 'documents') return {...envelope,results:[{...product(),document_key:'inbound:9',document_no:'RK-1',warehouse_id:1,warehouse_name:'一号仓库',counterparty:'供应商甲'}]}
  if(section === 'events') return {...envelope,results:[event()]}
  return {...envelope,event:{event_id:'review-42'},results:[{id:'ledger-101',product_id:7,product_name:'测试饮料',qty_delta:'60',base_unit:'EA',base_unit_name:'瓶',category:'inbound',category_label:'正常入库',amount:'150',currency:'CNY',posting_status:'POSTED',document_no:'RK-1',batch_no:'B',inbound_id:9,posted_at:event().event_at}],receipts:[{id:6,number:'REC-6',lines:[{id:1,product_name:'测试饮料',base_unit:'EA',base_unit_name:'瓶',qty_ok:'58',qty_damage:'2',qty_reject:'3',damage_reason:'外包装破损',photos:[{id:1,url:'/photo/1'}]}]}]}
}
const wrappers = []
async function render(options = {}) {
  const w = mount(History, {global:{stubs:{HistoryCharts:true}}}); wrappers.push(w)
  mocks.hooks.onLoad.forEach(cb => cb(options))
  await Promise.all(mocks.hooks.onShow.map(cb => cb()))
  await flushPromises()
  return w
}
const button = (w,text) => w.findAll('button').find(b => b.text() === text)

beforeEach(() => {
  mocks.hooks = {}
  auth.roles = ['owner_manager']; auth.user = {id:1,owner_id:2}
  auth.capabilities = {can_view_owner_inventory:true,can_view_owner_operations:true,can_export_operations:true}
  auth.bootstrap.mockReset().mockResolvedValue({status:'authenticated'})
  mocks.history.mockReset().mockImplementation(async section => data(section))
  mocks.download.mockReset().mockResolvedValue()
  mocks.photo.mockReset().mockResolvedValue({url:'private-photo',revoke:vi.fn()})
  vi.stubGlobal('uni',{navigateTo:vi.fn(),previewImage:vi.fn(),redirectTo:vi.fn()})
})
afterEach(() => wrappers.splice(0).forEach(w => w.unmount()))

describe('货主管理员出入库历史', () => {
  it('业务员使用相同统计界面并显示本人经手范围', async () => {
    auth.roles = ['owner_salesperson']
    mocks.history.mockImplementation(async section => ({...data(section),access_scope:'personal'}))
    const w = await render()
    expect(w.text()).toContain('本人经手')
    expect(w.text()).toContain('400.00 元')
    expect(w.text()).toContain('按年')
    await button(w,'导出周期汇总').trigger('click'); await flushPromises()
    expect(mocks.download).toHaveBeenCalledOnce()
  })
  it('默认全部历史按天显示数量和金额，单位为中文', async () => {
    const w = await render()
    expect(w.text()).toContain('全部历史')
    expect(w.text()).toContain('160')
    expect(w.text()).toContain('50')
    expect(w.text()).toContain('400.00 元')
    expect(w.text()).toContain('250.00 元')
    expect(w.text()).not.toContain('EA')
    expect(w.text()).not.toContain('发运')
    expect(mocks.history.mock.calls[0]).toEqual(['summary',{grain:'day',order:'desc',page_size:30,page:1}])
    expect(mocks.history).toHaveBeenLastCalledWith('periods',expect.objectContaining({snapshot:'signed-snapshot'}))
  })
  it.each(['按商品','按单据','原始收发','按周','按月','按年'])('从%s切回按天双栏并保留已查询的条件', async label => {
    const filters = {date_from:'2026-11-03',date_to:'2026-11-07',warehouse_id:'1',batch:'B',order:'asc'}
    const w = await render(filters)
    const daily = w.get('.view-tabs button:first-child')
    expect(daily.text()).toBe('按天统计')
    await button(w,label).trigger('click')
    await flushPromises()
    expect(daily.classes()).not.toContain('active')
    await w.get('input[placeholder="搜索商品批次"]').setValue('未提交')
    await daily.trigger('click')
    await flushPromises()
    expect(daily.classes()).toContain('active')
    expect(mocks.history).toHaveBeenLastCalledWith('periods',expect.objectContaining({...filters,grain:'day'}))
    expect(w.findAll('.record .period-side')).toHaveLength(2)
    expect(w.find('.list-heading .section-title').exists()).toBe(false)
    expect(w.get('input[placeholder="搜索商品批次"]').element.value).toBe('未提交')
  })
  it('自选时间段可再按月周天统计，点时间段不会扩大已选范围', async () => {
    const w = await render()
    const dates = w.findAll('.date-range picker')
    await dates[0].trigger('change',{detail:{value:'2026-11-03'}})
    await dates[1].trigger('change',{detail:{value:'2026-11-07'}})
    await button(w,'查询').trigger('click'); await flushPromises()
    for (const [label,grain] of [['按年','year'],['按月','month'],['按周','week'],['按天','day']]) {
      await button(w,label).trigger('click'); await flushPromises()
      expect(mocks.history).toHaveBeenLastCalledWith('periods',expect.objectContaining({grain,date_from:'2026-11-03',date_to:'2026-11-07'}))
    }
    for (const category of ['inbound', 'outbound']) {
      await w.get('.record .' + category).trigger('click')
      const url = uni.navigateTo.mock.lastCall[0].url
      expect(Object.fromEntries(new URLSearchParams(url.split('?')[1]))).toMatchObject({
        category, view:'events', grain:'all', date_from:'2026-11-03', date_to:'2026-11-07',
        snapshot:'signed-snapshot',
      })
    }
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('date_to=2026-11-07')
    await button(w,'全部历史').trigger('click'); await flushPromises()
    expect(mocks.history.mock.lastCall[1]).not.toHaveProperty('date_from')
    expect(mocks.history.mock.lastCall[1].grain).toBe('day')
  })
  it('只有出库时左栏禁用，缺价、多单位和其他库存变化各自显示', async () => {
    const quantities = [['包', 5], ['个', 36], ['件', 6], ['提', 4]].map(([unit, qty]) => ({
      ...total('outbound', String(qty), null), base_unit:unit, base_unit_name:unit,
      unpriced_count:1, unpriced_qty:String(qty),
    }))
    mocks.history.mockImplementation(async section => {
      const result = data(section)
      if (section === 'periods') Object.assign(result.results[0], {
        period_start:'2026-11-02', period_end:'2026-11-02',
        totals:[...quantities, total('reversal','3',null)],
        amounts:[{category:'outbound',currency:'CNY',amount:null,amount_status:'UNPRICED'}],
      })
      return result
    })
    const w = await render({ warehouse_id:'1', batch:'B', order:'asc' })
    const left = w.get('.record .inbound'), right = w.get('.record .outbound')
    expect(left.element.disabled).toBe(true)
    expect(left.text()).toContain('本日无入库')
    expect(right.text()).toContain('金额待核对')
    expect(right.findAll('.quantity').map(n => n.text())).toEqual(['5 包','36 个','6 件','4 提'])
    expect(right.text()).not.toContain('撤销回补')
    await w.get('.record .other-change').trigger('click')
    expect(Object.fromEntries(new URLSearchParams(uni.navigateTo.mock.lastCall[0].url.split('?')[1])))
      .toMatchObject({ category:'reversal', view:'events', warehouse_id:'1', batch:'B', order:'asc',
        date_from:'2026-11-02', date_to:'2026-11-02' })
  })
  it('采购金额按币种分别显示，部分缺价只提示在入库侧', async () => {
    mocks.history.mockImplementation(async section => {
      const payload = data(section)
      if (section === 'periods') {
        const entries = payload.results[0].amounts
        entries[0].amount_status = 'PARTIAL'
        entries.push({category:'inbound',currency:'USD',amount:'9.25',amount_status:'PRICED'})
      }
      return payload
    })
    const w = await render()
    const left = w.get('.record .inbound'), right = w.get('.record .outbound')
    expect(left.text()).toContain('400.00 元')
    expect(left.text()).toContain('9.25 USD')
    expect(left.text()).toContain('部分金额待核对')
    expect(right.text()).toContain('250.00 元')
    expect(right.text()).not.toContain('待核对')
  })
  it('只有其他库存变化且时间未记录时，按分类打开无日期明细', async () => {
    mocks.history.mockImplementation(async section => {
      const payload = data(section)
      if (section === 'periods') payload.results = [{
        period_start:null, totals:[total('transfer_in','7',null)], amounts:[],
      }]
      return payload
    })
    const w = await render()
    expect(w.get('.record .inbound').element.disabled).toBe(true)
    expect(w.get('.record .outbound').element.disabled).toBe(true)
    await w.get('.record .other-change').trigger('click')
    const query = new URLSearchParams(uni.navigateTo.mock.lastCall[0].url.split('?')[1])
    expect(Object.fromEntries(query)).toMatchObject({category:'transfer_in',undated:'true',view:'events'})
    expect(query.has('date_from')).toBe(false)
    expect(query.has('date_to')).toBe(false)
  })
  it('商品、单据和每次明细都携带同一筛选条件和快照', async () => {
    const w = await render({grain:'all',warehouse_id:'1',date_from:'2026-10-01'})
    await w.find('.record').trigger('click')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('product_id=7')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('snapshot=signed-snapshot')
    await button(w,'按单据').trigger('click'); await flushPromises()
    expect(w.text()).toContain('供应商甲')
    await w.find('.record').trigger('click')
    expect(decodeURIComponent(uni.navigateTo.mock.lastCall[0].url)).toContain('document_key=inbound:9')
    await button(w,'原始收发').trigger('click'); await flushPromises()
    await w.find('.record').trigger('click')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('event_id=review-42')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('date_from=2026-10-01')
  })
  it('显示本次验收的良品破损拒收与凭据照片', async () => {
    const w = await render({event_id:'review-42',product_id:'7',snapshot:'signed-snapshot'})
    expect(mocks.history).toHaveBeenCalledWith('events/review-42',expect.objectContaining({snapshot:'signed-snapshot',product_id:'7'}))
    expect(w.text()).toContain('良品 58 · 破损 2 · 拒收 3')
    await button(w,'查看原始单据').trigger('click')
    expect(uni.navigateTo).toHaveBeenCalledWith({url:'/pages/inbound/index?id=9'})
    await button(w,'查看照片').trigger('click'); await flushPromises()
    expect(uni.previewImage).toHaveBeenCalledWith(expect.objectContaining({urls:['private-photo']}))
  })
  it('导出使用已应用条件，编辑未查询的字段不改变导出', async () => {
    const w = await render({grain:'all',batch:'B',date_from:'2026-11-01'})
    await w.find('input[placeholder="搜索商品批次"]').setValue('尚未查询')
    await button(w,'导出商品汇总').trigger('click'); await flushPromises()
    const url = mocks.download.mock.lastCall[0]
    expect(url).toContain('batch=B')
    expect(url).toContain('date_from=2026-11-01')
    expect(url).toContain('snapshot=signed-snapshot')
    expect(url).not.toContain('尚未查询')
  })
  it('翻页失败保留结果并重试同一页，返回时保留筛选及列表', async () => {
    mocks.history.mockImplementation(async section => section === 'products' ? {...data(section),count:2,next_page:2} : data(section))
    const w = await render({grain:'all',batch:'B'})
    mocks.history.mockRejectedValueOnce(new Error('offline'))
    await button(w,'加载更多').trigger('click'); await flushPromises()
    expect(w.text()).toContain('加载更多失败')
    expect(w.findAll('.record')).toHaveLength(1)
    mocks.history.mockResolvedValueOnce({...data('products'),results:[{...product(),product_id:8}],count:2,next_page:null})
    await button(w,'重试').trigger('click'); await flushPromises()
    expect(mocks.history.mock.lastCall[1]).toMatchObject({batch:'B',page:2,snapshot:'signed-snapshot'})
    expect(w.findAll('.record')).toHaveLength(2)
    const calls = mocks.history.mock.calls.length
    mocks.hooks.onHide.forEach(cb => cb())
    await Promise.all(mocks.hooks.onShow.map(cb => cb())); await flushPromises()
    expect(mocks.history).toHaveBeenCalledTimes(calls)
    expect(w.findAll('.record')).toHaveLength(2)
  })
  it('筛选失败不显示旧结果，权限丢失清空且不展示导出', async () => {
    const w = await render()
    mocks.history.mockRejectedValueOnce(new Error('offline'))
    await button(w,'按周').trigger('click'); await flushPromises()
    expect(w.findAll('.record')).toHaveLength(0)
    expect(w.text()).toContain('offline')
    await button(w,'重试').trigger('click'); await flushPromises()
    auth.capabilities = {}
    await flushPromises()
    expect(w.text()).not.toContain('测试饮料')
    expect(w.text()).toContain('访问权限已失效')
    expect(w.text()).not.toContain('导出商品汇总')
  })
  it('账号切换后迟到的响应不能显示原货主的数据', async () => {
    let resolve
    mocks.history.mockReturnValueOnce(new Promise(r => {resolve=r}))
    const w = await render()
    auth.user = {id:2,owner_id:3}
    auth.roles = []
    resolve(summary()); await flushPromises()
    expect(w.text()).not.toContain('160')
    expect(w.findAll('.record')).toHaveLength(0)
  })
  it('时间未记录分组不会携带日期条件', () => {
    expect(periodFilters({date_from:'2026-01-01',date_to:'2026-01-31'}, {period_start:null})).toMatchObject({date_from:'',date_to:'',undated:true})
  })
  it('未记录时间可改选日期查询，清除无效日期仍能回到全部历史', async () => {
    const w = await render({undated:'true'})
    const dates = w.findAll('.date-range picker')
    await dates[0].trigger('change',{detail:{value:'2026-11-03'}})
    await button(w,'查询').trigger('click'); await flushPromises()
    expect(mocks.history.mock.lastCall[1]).not.toHaveProperty('undated')
    expect(mocks.history.mock.lastCall[1].date_from).toBe('2026-11-03')
    await dates[1].trigger('change',{detail:{value:'2026-11-01'}})
    await button(w,'查询').trigger('click'); await flushPromises()
    expect(w.text()).toContain('开始日期不能晚于结束日期')
    await button(w,'全部历史').trigger('click'); await flushPromises()
    expect(w.text()).not.toContain('开始日期不能晚于结束日期')
    expect(mocks.history.mock.lastCall[1]).not.toHaveProperty('date_from')
  })
  it('未计价、扣库异常与旧凭据不伪装为正常记录', async () => {
    mocks.history.mockImplementation(async section => {
      const payload = data(section)
      if(section === 'summary') payload.amounts[0] = {...payload.amounts[0],amount_status:'UNPRICED'}
      if(section === 'periods') payload.results[0].totals[1].posting_pending_count = 1
      if(section === 'events/review-42') payload.receipts[0].lines[0].quality_recorded = false
      return payload
    })
    const w = await render()
    expect(w.text()).toContain('金额待核对')
    expect(w.text()).toContain('库存扣减异常／待入账')
    expect(w.text()).not.toContain('第 2 次')
    w.unmount(); mocks.hooks = {}
    const detail = await render({event_id:'review-42'})
    expect(detail.text()).toContain('旧凭据未记录良品、破损及拒收分类')
    expect(detail.text()).not.toContain('良品 58')
  })
  it('导出和照片失败的重试按钮重做相应操作', async () => {
    const w = await render({grain:'all'})
    mocks.download.mockRejectedValueOnce(new Error('导出网络错误'))
    await button(w,'导出商品汇总').trigger('click'); await flushPromises()
    expect(w.text()).toContain('导出网络错误')
    await button(w,'重试').trigger('click'); await flushPromises()
    expect(mocks.download).toHaveBeenCalledTimes(2)
    expect(w.text()).not.toContain('导出网络错误')
    w.unmount(); mocks.hooks = {}
    const detail = await render({event_id:'review-42'})
    mocks.photo.mockRejectedValueOnce(new Error('offline'))
    await button(detail,'查看照片').trigger('click'); await flushPromises()
    expect(detail.text()).toContain('照片加载失败')
    await button(detail,'重试').trigger('click'); await flushPromises()
    expect(mocks.photo).toHaveBeenCalledTimes(2)
    expect(detail.text()).not.toContain('照片加载失败')
  })
  it('离开详情后迟到的照片响应只释放资源，不弹出预览', async () => {
    let resolve
    mocks.photo.mockReturnValueOnce(new Promise(r => { resolve = r }))
    const w = await render({event_id:'review-42'})
    await button(w,'查看照片').trigger('click')
    w.unmount()
    const revoke = vi.fn()
    resolve({url:'private-photo',revoke}); await flushPromises()
    expect(uni.previewImage).not.toHaveBeenCalled()
    expect(revoke).toHaveBeenCalledOnce()
  })
  it('同一日期合并中文单位且保留超大金额精度，缺失名称不显示代码', async () => {
    mocks.history.mockImplementation(async section => {
      const payload = data(section)
      if (section === 'periods') {
        payload.results[0].totals.push({...total('inbound','8','40'),base_unit:'BOX',base_unit_name:'箱'})
        payload.results[0].totals.push({...total('inbound','2','10'),base_unit:'MISSING',base_unit_name:null})
        payload.results[0].amounts[0].amount = '9007199254740993.01'
      }
      return payload
    })
    const w = await render()
    expect(w.findAll('.record')).toHaveLength(1)
    expect(w.find('.record').text()).toContain('8 箱')
    expect(w.find('.record').text()).toContain('2 单位待核对')
    expect(w.find('.record').text()).toContain('9,007,199,254,740,993.01 元')
    expect(w.text()).not.toContain('MISSING')
    expect(w.text()).not.toContain('BOX')
  })
  it('按年统计中的自选日期只显示覆盖部分，周期导出保持相同条件', async () => {
    mocks.history.mockImplementation(async section => {
      const payload = data(section)
      if (section === 'periods') Object.assign(payload.results[0],{
        period_start:'2026-01-01',period_end:'2026-12-31',
        range_start:'2026-02-03',range_end:'2026-08-09',partial_period:true,
      })
      return payload
    })
    const w = await render({grain:'year',date_from:'2026-02-03',date_to:'2026-08-09'})
    expect(w.find('.record').text()).toContain('2026 年')
    expect(w.find('.record').text()).toContain('实际统计：2026-02-03 至 2026-08-09')
    await button(w,'导出周期汇总').trigger('click'); await flushPromises()
    expect(mocks.download.mock.lastCall[0]).toContain('view=periods')
    expect(mocks.download.mock.lastCall[0]).toContain('date_to=2026-08-09')
    await w.get('.record .inbound').trigger('click')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('view=events')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('date_from=2026-02-03')
  })
})
