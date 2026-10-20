import { makeInventoryAuth, inventoryCapabilities } from './inventoryAuth'
const inventoryAuth = makeInventoryAuth()
vi.mock('@/store/auth', () => ({useAuth: () => inventoryAuth}))
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ hooks: {}, download: vi.fn(), orders: vi.fn(), summary: vi.fn(), detail: vi.fn() }))
vi.mock('@dcloudio/uni-app', () => {
  const hook = key => fn => (mocks.hooks[key] ||= []).push(fn)
  return {onLoad:hook('load'), onShow:hook('show'), onHide:hook('hide'), onUnload:hook('unload'), onReachBottom:hook('bottom'), onPullDownRefresh:hook('refresh')}
})
vi.mock('@/utils/request', () => ({
  downloadAuthenticatedFile:mocks.download,
  api:{orders:mocks.orders, inventorySummary:mocks.summary, inventoryDetail:mocks.detail, inventoryTransactions:vi.fn()},
}))
import Orders from '@/pages/orders/index.vue'
import Inventory from '@/pages/inventory/index.vue'
import Detail from '@/pages/inventory/detail.vue'

let wrappers=[]
const fire = key => (mocks.hooks[key] || []).forEach(fn=>fn({id:'7'}))
const button = (w,text) => w.findAll('button').find(b=>b.text()===text)
function render(component) {const w=mount(component);wrappers.push(w);fire('load');fire('show');return w}
beforeEach(()=>{
  inventoryAuth.capabilities=inventoryCapabilities()
  mocks.hooks={}
  mocks.download.mockReset().mockResolvedValue(true)
  mocks.orders.mockReset().mockResolvedValue({results:[{id:1,order_no:'SO1'}],next:'next',count:2})
  mocks.summary.mockReset().mockResolvedValue({results:[{id:7,product_name:'测试商品',base_unit:'瓶'}],next:'next',count:2})
  mocks.detail.mockReset().mockResolvedValue({summary_id:7,product:{name:'测试商品'},warehouses:[],results:[],totals:[],count:0,next_page:null})
  vi.stubGlobal('uni',{stopPullDownRefresh:vi.fn(),navigateTo:vi.fn()})
})
afterEach(()=>{wrappers.forEach(w=>w.unmount());wrappers=[];vi.unstubAllGlobals()})

it.each([[Orders,'orders'],[Inventory,'inventory']])('%s exports and paginates using applied search only',async(component,kind)=>{
  const w=render(component);await flushPromises()
  await w.find('input').setValue('已搜索');await button(w,'搜索').trigger('click');await flushPromises()
  await w.find('input').setValue('尚未搜索')
  fire('bottom');await flushPromises()
  if(kind==='orders')expect(mocks.orders).toHaveBeenLastCalledWith('已搜索',2)
  else expect(mocks.summary).toHaveBeenLastCalledWith(expect.objectContaining({search:'已搜索',page:2}))
  await button(w,'导出 Excel').trigger('click');await flushPromises()
  expect(decodeURIComponent(mocks.download.mock.calls[0][0])).toContain('search=已搜索')
  expect(mocks.download.mock.calls[0][0]).not.toContain('page=')
  expect(mocks.download.mock.calls[0][2].excel).toBe(true)
})

it('detail exports applied batch filters and hides export on the ledger tab',async()=>{
  const w=render(Detail);await flushPromises()
  await w.find('input').setValue('批次A');await button(w,'筛选').trigger('click');await flushPromises()
  await w.find('input').setValue('未应用')
  await button(w,'导出 Excel').trigger('click');await flushPromises()
  expect(decodeURIComponent(mocks.download.mock.calls[0][0])).toBe('/api/inventory/summary/7/detail/export/?batch=批次A')
  await button(w,'库存流水').trigger('click');await flushPromises()
  expect(button(w,'导出 Excel')).toBeUndefined()
})

it('prevents duplicate exports and discards downloads after filtering or leaving',async()=>{
  let finish;mocks.download.mockImplementation(()=>new Promise(r=>{finish=r}))
  const w=render(Orders);await flushPromises()
  await button(w,'导出 Excel').trigger('click');await flushPromises()
  expect(button(w,'正在导出…').attributes('disabled')).toBeDefined()
  const guard=mocks.download.mock.calls[0][2].isCurrent
  expect(guard()).toBe(true)
  await w.find('input').setValue('下一次筛选');await button(w,'搜索').trigger('click');await flushPromises()
  expect(guard()).toBe(false)
  finish(false);await flushPromises()
  await button(w,'导出 Excel').trigger('click');await flushPromises()
  const next=mocks.download.mock.calls[1][2].isCurrent
  fire('hide');expect(next()).toBe(false)
  finish(false);await flushPromises()
})

it.each([[413,'超过 50000 行'],[404,'导出暂不可用'],[403,'登录状态或导出权限已失效']])('shows %s without clearing existing rows',async(statusCode,message)=>{
  mocks.download.mockRejectedValue({statusCode,message})
  const w=render(Orders);await flushPromises()
  await button(w,'导出 Excel').trigger('click');await flushPromises()
  expect(w.text()).toContain(message)
  expect(w.text()).toContain('SO1')
  expect(button(w,'导出 Excel').attributes('disabled')).toBeUndefined()
})

it('revoked inventory capability clears data and invalidates pending download',async()=>{
  let finish;mocks.download.mockImplementation(()=>new Promise(r=>{finish=r}))
  const w=render(Inventory);await flushPromises()
  await button(w,'导出 Excel').trigger('click');await flushPromises()
  const guard=mocks.download.mock.calls[0][2].isCurrent
  expect(guard()).toBe(true)
  inventoryAuth.revokeInventoryAccess();await flushPromises()
  expect(guard()).toBe(false)
  expect(w.text()).not.toContain('测试商品')
  expect(button(w,'导出 Excel')).toBeUndefined()
  finish(false)
})

it('direct inventory route with missing capability never loads data',async()=>{
  inventoryAuth.capabilities={}
  const w=render(Inventory);await flushPromises()
  expect(mocks.summary).not.toHaveBeenCalled()
  expect(w.text()).toContain('库存访问权限已失效')
})
