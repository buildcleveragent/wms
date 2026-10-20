import { inventoryCapabilities } from './inventoryAuth'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks=vi.hoisted(() => ({items:vi.fn(),settings:vi.fn(),save:vi.fn(),products:vi.fn(),hooks:{}}))
vi.mock('@dcloudio/uni-app', () => {
  const hook=key => cb => (mocks.hooks[key] ||= []).push(cb)
  return {onShow:hook('show'),onHide:hook('hide'),onUnload:hook('unload'),onLoad:hook('load'),onPullDownRefresh:hook('pull')}
})
vi.mock('@/utils/request', () => ({api:{inventoryAlertItems:mocks.items,inventoryAlertSettings:mocks.settings,saveInventoryAlertSettings:mocks.save,inventoryAlertProducts:mocks.products}}))
const auth=reactive({user:{id:1,owner_id:2},roles:['owner_manager'],capabilities:inventoryCapabilities(),revokeInventoryAccess(){this.capabilities={}}})
vi.mock('@/store/auth', () => ({useAuth:() => auth}))
import Alerts from '@/pages/inventory/alerts.vue'
import Settings from '@/pages/inventory/alert-settings.vue'
const wrappers=[]
const product={id:5,name:'长名称测试商品',code:'A-05',sku:'SKU5',spec:'测试规格',unit_label:'瓶',base_unit:'EA'}
const thresholds={low:{enabled:true,threshold:'10.00'},expiry:{enabled:true,days:30,source:'货主默认'},slow:{enabled:true,days:90,source:'货主默认'}}
function item(id=5) {return {product:{...product,id},summary_id:7,status:'confirmed',reason:'全仓可用库存不高于最低库存',thresholds,quantities:[{base_unit:'EA',onhand_qty:'8.2500',available_qty:'5.2500',allocated_qty:'1',locked_qty:'1',damaged_qty:'1'}]}}
function result() {return {as_of:'2026-10-20T12:00:00+08:00',can_edit:true,count:1,next_page:null,categories:[{code:'low',label:'低库存',count:1,enabled:true},{code:'expiry',label:'临期／已过期',count:1,enabled:true,needs_review_count:1},{code:'slow',label:'长期未动销',count:0,enabled:true}],results:[item()]}}
function config(isProduct=false) {return isProduct ? {version:'p1',can_edit:true,product,min_stock:'10.00',expiry_mode:'inherit',expiry_days:null,slow_mode:'inherit',slow_days:null,defaults:config(),effective:thresholds} : {version:'v1',can_edit:true,low_enabled:true,expiry_enabled:true,slow_enabled:true,expiry_days:30,slow_days:90}}
function hook(name,arg) {(mocks.hooks[name] || []).forEach(cb => cb(arg))}
function render(component=Alerts,options={}) {const w=mount(component);wrappers.push(w);hook('load',options);hook('show');return w}
function deferred() {let resolve;const promise=new Promise(r => {resolve=r});return {promise,resolve}}
function change(w,index,value) {w.findAll('picker')[index].element.dispatchEvent(new CustomEvent('change',{detail:{value}}))}
beforeEach(() => {auth.capabilities=inventoryCapabilities();mocks.hooks={};mocks.items.mockReset().mockResolvedValue(result());mocks.settings.mockReset().mockImplementation(id => Promise.resolve(config(Boolean(id))));mocks.save.mockReset().mockResolvedValue(config());mocks.products.mockReset().mockResolvedValue({count:1,results:[product],next_page:null});auth.user={id:1,owner_id:2};auth.roles=['owner_manager'];vi.stubGlobal('uni',{navigateTo:vi.fn(),stopPullDownRefresh:vi.fn()});Object.defineProperty(document,'hidden',{configurable:true,value:false})})
afterEach(() => {wrappers.splice(0).forEach(w => w.unmount());vi.useRealTimers()})

describe('库存预警清单', () => {
  it('展示三类数量、单位、分项数量并跳转明细和商品设置',async () => {
    const w=render();await flushPromises()
    expect(w.findAll('.category')).toHaveLength(3)
    expect(w.text()).toContain('单位：瓶')
    expect(w.text()).toContain('5.25')
    expect(w.text()).toContain('10 瓶')
    await w.findAll('.card-actions button')[0].trigger('click')
    expect(uni.navigateTo).toHaveBeenLastCalledWith({url:'/pages/inventory/detail?id=7'})
    await w.findAll('.card-actions button')[1].trigger('click')
    expect(uni.navigateTo).toHaveBeenLastCalledWith({url:'/pages/inventory/alert-settings?product_id=5'})
  })
  it('零库存商品无明细仍能设置阈值，缺失接口提供重试',async () => {
    mocks.items.mockRejectedValueOnce({statusCode:404}).mockResolvedValue({...result(),results:[{...item(),summary_id:null}]})
    const w=render();await flushPromises();expect(w.text()).toContain('库存预警暂不可用')
    await w.find('.retry').trigger('click');await flushPromises()
    expect(w.text()).toContain('尚无库存记录');expect(w.findAll('.card-actions button')).toHaveLength(1)
  })
  it('效期批次、缺失效期及未动销证据可读',async () => {
    mocks.items.mockResolvedValue({...result(),results:[{...item(),missing_expiry:true,batches:[{warehouse_name:'一号仓',batch_no:'B1',expiry_date:'2026-10-19',remaining_days:-1,status:'expired',onhand_qty:'8',base_unit:'瓶'}]}]})
    const w=render();await flushPromises();await w.findAll('.category')[1].trigger('click');await flushPromises()
    expect(w.find('.batch.expired').text()).toContain('已过期 1 天');expect(w.text()).toContain('效期未记录')
    change(w,1,2);await flushPromises();expect(mocks.items.mock.lastCall[0].expiry).toBe('expired')
    change(w,0,1);await flushPromises();expect(mocks.items.mock.lastCall[0].status).toBe('needs_review')
    mocks.items.mockResolvedValue({...result(),results:[{...item(),observed_since:'2026-01-01T12:00:00+08:00',inactive_days:292,last_sale_at:null}]})
    await w.findAll('.category')[2].trigger('click');await flushPromises();expect(w.text()).toContain('已 292 天未动销')
  })
  it('搜索分页去重，刷新失败保留数据',async () => {
    mocks.items.mockResolvedValueOnce({...result(),next_page:2}).mockResolvedValueOnce({...result(),results:[item(),item(6)]})
    const w=render();await flushPromises();await w.find('.load-more').trigger('click');await flushPromises()
    expect(w.findAll('.alert-card')).toHaveLength(2)
    await w.find('input').setValue('A-05');await w.find('.search').trigger('click');await flushPromises()
    expect(mocks.items.mock.lastCall[0]).toMatchObject({search:'A-05',page:1})
    mocks.items.mockRejectedValueOnce(new Error('offline'));await w.find('.refresh').trigger('click');await flushPromises()
    expect(w.text()).toContain('更新失败，当前为上次数据');expect(w.findAll('.alert-card')).toHaveLength(1)
  })
  it('筛选切换排队，不并发请求，旧响应不覆盖新结果',async () => {
    const pending=deferred();mocks.items.mockReturnValueOnce(pending.promise)
    const w=render();await w.findAll('.category')[1].trigger('click');await flushPromises()
    expect(mocks.items).toHaveBeenCalledTimes(1)
    pending.resolve({...result(),results:[{...item(),product:{...product,name:'过期响应'}}]});await flushPromises()
    expect(mocks.items).toHaveBeenCalledTimes(2);expect(w.text()).not.toContain('过期响应')
    expect(mocks.items.mock.lastCall[0].category).toBe('expiry')
  })
  it('隐藏、恢复、返回、卸载及账号切换隔离，无轮询',async () => {
    vi.useFakeTimers();const pending=deferred();mocks.items.mockReturnValueOnce(pending.promise)
    const w=render();hook('hide');pending.resolve(result());await flushPromises();expect(w.find('.alert-card').exists()).toBe(false)
    hook('show');await flushPromises();expect(w.find('.alert-card').exists()).toBe(true)
    const count=mocks.items.mock.calls.length;await vi.advanceTimersByTimeAsync(90000);expect(mocks.items).toHaveBeenCalledTimes(count)
    Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))
    Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(mocks.items).toHaveBeenCalledTimes(count+1)
    const old=deferred();mocks.items.mockReturnValueOnce(old.promise);await w.find('.refresh').trigger('click');auth.user={id:9,owner_id:10};await flushPromises()
    expect(w.find('.alert-card').exists()).toBe(false);old.resolve({...result(),results:[{...item(),product:{...product,name:'旧账号'}}]});await flushPromises();expect(w.text()).not.toContain('旧账号')
    hook('unload');const before=mocks.items.mock.calls.length;document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(mocks.items).toHaveBeenCalledTimes(before)
  })
  it.each([401,403])('权限失效 %s 清空并停止请求',async statusCode => {
    const w=render();await flushPromises();mocks.items.mockRejectedValueOnce({statusCode});await w.find('.refresh').trigger('click');await flushPromises()
    expect(w.find('.alert-card').exists()).toBe(false);expect(w.find('.retry').exists()).toBe(false)
    const count=mocks.items.mock.calls.length;hook('show');await flushPromises();expect(mocks.items).toHaveBeenCalledTimes(count)
  })
})

describe('预警阈值设置', () => {
  it('管理员保存默认值并搜索商品，业务员无保存入口',async () => {
    const w=render(Settings);await flushPromises();await w.find('.expiry-days').setValue('7');await w.find('.save').trigger('click');await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(null,expect.objectContaining({version:'v1',expiry_days:7,slow_days:90}))
    expect(w.text()).toContain('已保存');await w.find('.search-row input').setValue('A-05');await w.find('.product-search').trigger('click');await flushPromises();expect(mocks.products).toHaveBeenLastCalledWith({search:'A-05',page:1,page_size:20})
    await w.find('.product-row').trigger('click');expect(uni.navigateTo).toHaveBeenLastCalledWith({url:'/pages/inventory/alert-settings?product_id=5'})
    mocks.settings.mockResolvedValue({...config(),can_edit:false});hook('show');await flushPromises();expect(w.find('.save').exists()).toBe(false);expect(w.text()).toContain('当前为只读')
  })
  it('商品配置支持自定义、关闭、继承与基本单位最低库存',async () => {
    const w=render(Settings,{product_id:'5'});await flushPromises();expect(w.text()).toContain('单位：瓶')
    await w.find('.minimum').setValue('2.50');change(w,0,1);change(w,1,2);await flushPromises();await w.find('.expiry-days').setValue('10');await w.find('.save').trigger('click');await flushPromises()
    expect(mocks.save).toHaveBeenCalledWith(5,{version:'p1',min_stock:'2.50',expiry_mode:'custom',expiry_days:10,slow_mode:'off',slow_days:null})
  })
  it('验证输入并阻止重复保存，409 后要求重新加载',async () => {
    const w=render(Settings);await flushPromises();await w.find('.slow-days').setValue('0');await w.find('.save').trigger('click');expect(mocks.save).not.toHaveBeenCalled()
    expect(w.text()).toContain('1～3650');await w.find('.slow-days').setValue('90')
    mocks.save.mockRejectedValueOnce({statusCode:409});await w.find('.save').trigger('click');await flushPromises();expect(w.text()).toContain('配置已被修改');expect(w.find('.save').attributes('disabled')).toBeDefined()
    await w.find('.heading button').trigger('click');await flushPromises();expect(w.find('.save').attributes('disabled')).toBeUndefined()
    const pending=deferred();mocks.save.mockReturnValueOnce(pending.promise);await w.find('.save').trigger('click');await w.find('.save').trigger('click');expect(mocks.save).toHaveBeenCalledTimes(2);pending.resolve(config());await flushPromises()
  })
  it('保存失败保留输入，权限失效清空配置，账号切换不接受旧保存结果',async () => {
    const w=render(Settings);await flushPromises();await w.find('.expiry-days').setValue('14');mocks.save.mockRejectedValueOnce(new Error('保存失败'));await w.find('.save').trigger('click');await flushPromises();expect(w.find('.expiry-days').element.value).toBe('14')
    const pending=deferred();mocks.save.mockReturnValueOnce(pending.promise);await w.find('.save').trigger('click');auth.user={id:11,owner_id:12};await flushPromises();pending.resolve(config());await flushPromises();expect(w.text()).not.toContain('已保存')
    mocks.save.mockRejectedValueOnce({statusCode:403});await w.find('.save').trigger('click');await flushPromises();expect(w.find('.save').exists()).toBe(false)
    expect(w.text()).toContain('权限已失效')
  })
  it('保存过程中恢复页面不会同时读取旧配置',async () => {
    const w=render(Settings);await flushPromises()
    const pending=deferred();mocks.save.mockReturnValueOnce(pending.promise)
    await w.find('.save').trigger('click');const previous=mocks.settings.mock.calls.length
    document.dispatchEvent(new Event('visibilitychange'));await flushPromises()
    expect(mocks.settings).toHaveBeenCalledTimes(previous)
    pending.resolve(config());await flushPromises();expect(mocks.settings).toHaveBeenCalledTimes(previous+1)
  })
})
