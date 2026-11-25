import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { calendarPeriod } from '@/utils/stockHistory'
import HistoryCharts from '@/components/HistoryCharts.vue'

const { request } = vi.hoisted(() => ({request:vi.fn()}))
vi.mock('@/utils/request', () => ({api:{stockStatistics:request}}))
const filters={warehouse_id:'3',batch:'B',date_from:'2026-11-02',date_to:'2026-11-22'}
const bottle={code:'EA',key:'base_unit:EA',label:'瓶'}, box={code:'BOX',key:'base_unit:BOX',label:'箱'}
const usd={code:'USD',key:'currency:USD',label:'USD'}
const yuan={code:'CNY',key:'currency:CNY',label:'CNY'}
const point = (day,value,status='PRICED') => ({
  period_start:day,period_end:day,range_start:day,range_end:day,category:'inbound',value,amount_status:status,
})
let wrapper
async function render(factory){
  request.mockReset().mockImplementation(factory)
  wrapper=mount(HistoryCharts,{props:{filters,snapshot:'snapshot'}})
  await flushPromises()
  return wrapper
}
afterEach(()=>wrapper?.unmount())
async function press(label){
  await wrapper.findAll('button').find(b=>b.text()===label).trigger('click')
  await flushPromises()
}
function group(label) {
  return wrapper.get(`[role="group"][aria-label="${label}"]`)
}
async function choose(label, option) {
  const button = group(label).findAll('button').find(item => item.text() === option)
  await button.trigger('click')
  await flushPromises()
  expect(group(label).get('[aria-pressed="true"]').text()).toBe(option)
  expect(group(label).findAll('.active')).toHaveLength(1)
}
function result(rows,dimension=bottle,options=[bottle,box]){
  return {results:rows,options,dimension,excluded_count:0,snapshot:'snapshot'}
}
describe('出入库图表',()=>{
  it('平铺全部可用选项，排行只展示适用分组，限定方向显示为文字', async () => {
    await render(() => result([]))
    expect(group('单位').findAll('button').map(button => button.text())).toEqual(['瓶','箱'])
    expect(group('周期').findAll('button').map(button => button.text())).toEqual(['按天','按周','按月','按年'])
    await choose('周期','按年')
    expect(request.mock.lastCall[1].grain).toBe('year')
    await choose('图表','商品排行')
    expect(group('周期').get('[aria-pressed="true"]').text()).toBe('全时段')
    expect(wrapper.find('[aria-label="图形"]').exists()).toBe(false)
    expect(group('方向').findAll('button').map(button => button.text())).toEqual(['入库','出库'])
    await wrapper.setProps({filters:{...filters,category:'inbound'}})
    expect(group('方向').text()).toBe('入库')
    expect(group('方向').find('button').exists()).toBe(false)
  })
  it('切换币种显示精确金额，缺价断线，零金额可以查看记录', async()=>{
    const rows=[point('2026-11-02','9007199254740993.01','PARTIAL'),point('2026-11-03',null,'UNPRICED'),point('2026-11-04','0.00')]
    await render((s,p)=>p.metric==='amount' ? result(rows,p.dimension===usd.key?usd:yuan,[yuan,usd]) : result(rows))
    await choose('指标','金额')
    await choose('币种','USD')
    await choose('图形','折线图')
    expect(wrapper.findAll('.segment')).toHaveLength(0)
    const points=wrapper.findAll('.point')
    await points[0].trigger('click')
    expect(wrapper.get('.point-info').text()).toContain('9,007,199,254,740,993.01 USD（已知，部分待核对）')
    await points[1].trigger('click')
    expect(wrapper.get('.point-info').text()).toContain('金额待核对')
    await points[2].trigger('click')
    expect(wrapper.get('.point-info').text()).toContain('0.00 USD')
    await press('查看记录 ›')
    expect(wrapper.emitted('records')[0][0]).toMatchObject({...filters,category:'inbound',currency:'USD',
      date_from:'2026-11-04',date_to:'2026-11-04',grain:'all',snapshot:'snapshot'})
  })
  it('切换箱数排行并按商品进入，收起和重试保留选择', async()=>{
    const row={product_id:7,product_name:'测试商品',category:'outbound',value:'12.345'}
    await render((s,p)=>result(p.chart==='ranking'?[row]:[],p.dimension===box.key?box:bottle))
    await choose('图表','商品排行')
    await choose('单位','箱')
    await flushPromises()
    expect(wrapper.get('.rank').text()).toContain('12.345 箱')
    await wrapper.get('.rank').trigger('click')
    expect(wrapper.emitted('records')[0][0]).toMatchObject({...filters,base_unit:'BOX',category:'outbound',product_id:7,snapshot:'snapshot'})
    await press('收起图表')
    request.mockRejectedValueOnce(new Error('offline'))
    await press('展开图表')
    expect(wrapper.text()).toContain('offline')
    await press('重试图表')
    expect(wrapper.get('.rank').text()).toContain('12.345 箱')
  })
  it('周期翻页与筛选联动，最高金额保留十进制精度', async () => {
    const latest = [point('2026-11-20','9007199254740993.01'), point('2026-11-21','9007199254740993.02')]
    await render((s, p) => ({...result(p.page === 1 ? latest : [point('2026-11-02','2')], yuan, [yuan]),
      next_page:p.page === 1 ? 2 : null}))
    await choose('指标','金额')
    expect(wrapper.get('.scale').text()).toContain('9,007,199,254,740,993.02')
    await press('更早周期')
    expect(request.mock.lastCall[1]).toMatchObject({page:2, snapshot:'snapshot'})
    await choose('方向','入库')
    expect(request.mock.lastCall[1]).toMatchObject({page:1, direction:'inbound'})
  })
  it.each([
    ['2024-02-29','day','2024-02-29','2024-02-29','2024-02-28','2024-03-01'],
    ['2024-02-29','month','2024-02-01','2024-02-29','2024-01-31','2024-03-01'],
    ['2027-01-03','week','2026-12-28','2027-01-03','2026-12-27','2027-01-04'],
    ['2024-12-31','year','2024-01-01','2024-12-31','2023-12-31','2025-01-01'],
  ])('按自然周期计算 %s %s', (day, grain, period_start, period_end, previous, next) => {
    expect(calendarPeriod(day,grain)).toEqual({period_start,period_end,previous,next})
  })
  it('排行沿用快照日期、截取自选范围，并将同一时段带入商品明细', async () => {
    const row = {product_id:9,product_name:'测试商品',category:'outbound',value:'8'}
    await render((_,p) => result(p.date_from === '2026-10-15' ? [row] : []))
    await wrapper.setProps({asOf:'2026-12-01T00:10:00+08:00', filters:{...filters,date_from:'',date_to:''}})
    await choose('图表','商品排行')
    await choose('周期','按天')
    expect(request.mock.lastCall[1]).toMatchObject({date_from:'2026-12-01',date_to:'2026-12-01'})
    await wrapper.setProps({filters:{...filters,date_from:'2026-10-15',date_to:'2026-11-10'}})
    await choose('周期','按月')
    expect(request.mock.lastCall[1]).toMatchObject({date_from:'2026-11-01',date_to:'2026-11-10'})
    expect(wrapper.text()).toContain('本期没有可排行的商品')
    expect(wrapper.get('.rank-navigation').findAll('button').at(-1).element.disabled).toBe(true)
    await press('上月')
    expect(wrapper.text()).toContain('统计范围：2026-10-15 至 2026-10-31')
    expect(wrapper.get('.rank-navigation button').element.disabled).toBe(true)
    await wrapper.get('.rank').trigger('click')
    expect(wrapper.emitted('records')[0][0]).toMatchObject({
      warehouse_id:'3',batch:'B',snapshot:'snapshot',category:'outbound',base_unit:'EA',
      date_from:'2026-10-15',date_to:'2026-10-31',grain:'all',product_id:9,
    })
    await choose('图表','按期统计')
    expect(group('周期').get('.active').text()).toBe('按天')
    await choose('图表','商品排行')
    expect(group('周期').get('.active').text()).toBe('按月')
    await wrapper.get('picker').trigger('change',{detail:{value:'2026-11'}})
    await flushPromises()
    expect(request.mock.lastCall[1].date_from).toBe('2026-11-01')
    await choose('周期','全时段')
    expect(request.mock.lastCall[1]).toMatchObject({date_from:'2026-10-15',date_to:'2026-11-10'})
  })
  it('直接选年或跨年周，切换排行指标、方向和单位仍查看同一期', async () => {
    await render((_,p) => p.metric === 'amount' ? result([],yuan,[yuan]) : result([],p.dimension === box.key ? box : bottle))
    await wrapper.setProps({filters:{...filters,date_from:'2023-01-01',date_to:'2026-11-22'}})
    await choose('图表','商品排行')
    await choose('周期','按年')
    await wrapper.get('picker').trigger('change',{detail:{value:'2024'}})
    await flushPromises()
    expect(request.mock.lastCall[1]).toMatchObject({date_from:'2024-01-01',date_to:'2024-12-31'})
    await choose('周期','按周')
    await wrapper.get('picker').trigger('change',{detail:{value:'2024-12-31'}})
    await flushPromises()
    const dates = {date_from:'2024-12-30',date_to:'2025-01-05'}
    await choose('单位','箱')
    expect(request.mock.lastCall[1]).toMatchObject({...dates,dimension:box.key})
    await choose('指标','金额')
    await choose('方向','入库')
    expect(request.mock.lastCall[1]).toMatchObject({...dates,metric:'amount',direction:'inbound'})
    await choose('图表','按期统计')
    expect(request.mock.lastCall[1]).toMatchObject({grain:'day',date_from:'2023-01-01',date_to:'2026-11-22'})
  })
  it('晚到的响应不会覆盖新条件，不支持的分类不请求图表', async()=>{
    let resolve
    await render(()=>new Promise(r=>{resolve=r}))
    await wrapper.setProps({filters:{...filters,category:'reversal'}})
    resolve(result([point('2026-11-02','11')]))
    await flushPromises()
    expect(request).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('撤销回补')
    expect(wrapper.find('.point').exists()).toBe(false)
  })
})
