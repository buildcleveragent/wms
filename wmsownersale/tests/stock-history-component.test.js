import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ history: vi.fn(), download: vi.fn(), photo: vi.fn(), hooks: {} }))
const auth = reactive({ user: {id:1,owner_id:2}, roles:['owner_manager'], capabilities:{}, bootstrap:vi.fn() })
vi.mock('@/store/auth', () => ({useAuth: () => auth}))
vi.mock('@dcloudio/uni-app', () => Object.fromEntries(['onLoad','onShow','onHide','onUnload'].map(key => [key, cb => (mocks.hooks[key] ||= []).push(cb)])))
vi.mock('@/utils/request', () => ({api: {stockHistory:mocks.history}, downloadAuthenticatedFile:mocks.download, returnPhotoURL:mocks.photo}))

import History from '@/pages/reports/stock-history.vue'
import { periodFilters } from '@/utils/stockHistory'

const summary = () => ({snapshot:99,as_of:'2026-11-12T12:00:00+08:00',scope_label:'历史累计',warehouses:[{id:1,name:'一号仓库'}],
  totals:[{category:'inbound',qty:'160.0000',base_unit:'瓶',event_count:2},{category:'outbound',qty:'50.0000',base_unit:'瓶',event_count:2}]})
const product = () => ({product_id:7,product_name:'测试饮料',product_code:'P-7',base_unit:'瓶',inbound_qty:'160',inbound_count:2,outbound_qty:'50',outbound_count:2,current_onhand:'110',current_available:'100',last_at:'2026-11-04T10:00:00+08:00'})
const event = () => ({event_id:42,category:'inbound',category_label:'正常入库',document_no:'RK-1',base_unit:'瓶',qty:'60',countable:1,product_sequence:2,document_sequence:2,event_at:'2026-11-02T10:00:00+08:00',warehouse_name:'一号仓库',product_name:'测试饮料',product_count:1})
function data(section) {
  if(section === 'summary') return summary()
  const envelope = {snapshot:99,as_of:summary().as_of,count:1,next_page:null}
  if(section === 'products') return {...envelope,results:[product()]}
  if(section === 'periods') return {...envelope,results:[{...product(),period_start:'2026-11-01',period_end:'2026-11-30'}]}
  if(section === 'documents') return {...envelope,results:[{...product(),document_key:'inbound:9',document_no:'RK-1',warehouse_id:1,warehouse_name:'一号仓库',counterparty:'供应商甲'}]}
  if(section === 'events') return {...envelope,results:[event()]}
  return {...envelope,event:{event_id:42},results:[{id:101,product_id:7,product_name:'测试饮料',qty_delta:'60',base_unit:'瓶',category_label:'正常入库',document_no:'RK-1',batch_no:'B',inbound_id:9,posted_at:event().event_at}],receipts:[{id:6,number:'REC-6',lines:[{id:1,product_name:'测试饮料',base_unit:'瓶',qty_ok:'58',qty_damage:'2',qty_reject:'3',damage_reason:'外包装破损',photos:[{id:1,url:'/photo/1'}]}]}]}
}
const wrappers = []
async function render(options = {}) {
  const w = mount(History); wrappers.push(w)
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
  it('默认全部历史显示实际入出库和商品汇总，不以发运统计', async () => {
    const w = await render()
    expect(w.text()).toContain('全部历史')
    expect(w.text()).toContain('160')
    expect(w.text()).toContain('50')
    expect(w.text()).toContain('当前现有 110 瓶')
    expect(w.text()).not.toContain('发运')
    expect(mocks.history.mock.calls[0]).toEqual(['summary',{grain:'all',order:'desc',page_size:30,page:1}])
    expect(mocks.history).toHaveBeenLastCalledWith('products',expect.objectContaining({snapshot:99}))
  })
  it('自选时间段可再按月周天统计，点时间段不会扩大已选范围', async () => {
    const w = await render()
    const dates = w.findAll('.date-range picker')
    await dates[0].trigger('change',{detail:{value:'2026-11-03'}})
    await dates[1].trigger('change',{detail:{value:'2026-11-07'}})
    await button(w,'查询').trigger('click'); await flushPromises()
    for (const [label,grain] of [['按月','month'],['按周','week'],['按天','day']]) {
      await button(w,label).trigger('click'); await flushPromises()
      expect(mocks.history).toHaveBeenLastCalledWith('periods',expect.objectContaining({grain,date_from:'2026-11-03',date_to:'2026-11-07'}))
    }
    await w.find('.record').trigger('click')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('date_from=2026-11-03')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('date_to=2026-11-07')
    await button(w,'全部历史').trigger('click'); await flushPromises()
    expect(mocks.history.mock.lastCall[1]).not.toHaveProperty('date_from')
    expect(mocks.history.mock.lastCall[1].grain).toBe('day')
  })
  it('商品、单据和每次明细都携带同一筛选条件和快照', async () => {
    const w = await render({warehouse_id:'1',date_from:'2026-10-01'})
    await w.find('.record').trigger('click')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('product_id=7')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('snapshot=99')
    await button(w,'按单据').trigger('click'); await flushPromises()
    expect(w.text()).toContain('供应商甲')
    await w.find('.record').trigger('click')
    expect(decodeURIComponent(uni.navigateTo.mock.lastCall[0].url)).toContain('document_key=inbound:9')
    await button(w,'每次收发').trigger('click'); await flushPromises()
    await w.find('.record').trigger('click')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('event_id=42')
    expect(uni.navigateTo.mock.lastCall[0].url).toContain('date_from=2026-10-01')
  })
  it('显示本次验收的良品破损拒收与凭据照片', async () => {
    const w = await render({event_id:'42',product_id:'7',snapshot:'99'})
    expect(mocks.history).toHaveBeenCalledWith('events/42',expect.objectContaining({snapshot:'99',product_id:'7'}))
    expect(w.text()).toContain('良品 58 · 破损 2 · 拒收 3')
    await button(w,'查看原始单据').trigger('click')
    expect(uni.navigateTo).toHaveBeenCalledWith({url:'/pages/inbound/index?id=9'})
    await button(w,'查看照片').trigger('click'); await flushPromises()
    expect(uni.previewImage).toHaveBeenCalledWith(expect.objectContaining({urls:['private-photo']}))
  })
  it('导出使用已应用条件，编辑未查询的字段不改变导出', async () => {
    const w = await render({batch:'B',date_from:'2026-11-01'})
    await w.find('input[placeholder="搜索商品批次"]').setValue('尚未查询')
    await button(w,'导出商品汇总').trigger('click'); await flushPromises()
    const url = mocks.download.mock.lastCall[0]
    expect(url).toContain('batch=B')
    expect(url).toContain('date_from=2026-11-01')
    expect(url).toContain('snapshot=99')
    expect(url).not.toContain('尚未查询')
  })
  it('翻页失败保留结果并重试同一页，返回时保留筛选及列表', async () => {
    mocks.history.mockImplementation(async section => section === 'products' ? {...data(section),count:2,next_page:2} : data(section))
    const w = await render({batch:'B'})
    mocks.history.mockRejectedValueOnce(new Error('offline'))
    await button(w,'加载更多').trigger('click'); await flushPromises()
    expect(w.text()).toContain('加载更多失败')
    expect(w.findAll('.record')).toHaveLength(1)
    mocks.history.mockResolvedValueOnce({...data('products'),results:[{...product(),product_id:8}],count:2,next_page:null})
    await button(w,'重试').trigger('click'); await flushPromises()
    expect(mocks.history.mock.lastCall[1]).toMatchObject({batch:'B',page:2,snapshot:99})
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
  it('未知次数与旧凭据的质量记录不显示成零', async () => {
    mocks.history.mockImplementation(async section => {
      const payload = data(section)
      if(section === 'summary') payload.totals[0] = {...payload.totals[0],event_count:0,unverified_count:2}
      if(section === 'products') payload.results = [{...product(),unknown_qty:'3',unknown_count:0,unknown_unverified_count:1}]
      if(section === 'events/42') payload.receipts[0].lines[0].quality_recorded = false
      return payload
    })
    const w = await render()
    expect(w.text()).toContain('次数待核对')
    expect(w.find('.inbound .count').text()).toBe('次数待核对')
    expect(w.find('.history-quantities').text()).not.toContain('0 次')
    w.unmount(); mocks.hooks = {}
    const detail = await render({event_id:'42'})
    expect(detail.text()).toContain('旧凭据未记录良品、破损及拒收分类')
    expect(detail.text()).not.toContain('良品 58')
  })
  it('导出和照片失败的重试按钮重做相应操作', async () => {
    const w = await render()
    mocks.download.mockRejectedValueOnce(new Error('导出网络错误'))
    await button(w,'导出商品汇总').trigger('click'); await flushPromises()
    expect(w.text()).toContain('导出网络错误')
    await button(w,'重试').trigger('click'); await flushPromises()
    expect(mocks.download).toHaveBeenCalledTimes(2)
    expect(w.text()).not.toContain('导出网络错误')
    w.unmount(); mocks.hooks = {}
    const detail = await render({event_id:'42'})
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
    const w = await render({event_id:'42'})
    await button(w,'查看照片').trigger('click')
    w.unmount()
    const revoke = vi.fn()
    resolve({url:'private-photo',revoke}); await flushPromises()
    expect(uni.previewImage).not.toHaveBeenCalled()
    expect(revoke).toHaveBeenCalledOnce()
  })
})
