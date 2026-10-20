import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  load: null, shows: [], hides: [], unloads: [],
  auth: null,
  api: {
    reorderContext: vi.fn(), validateOrder: vi.fn(), createOutboundOrder: vi.fn(),
    updateOutboundOrder: vi.fn(), submitOutboundOrder: vi.fn(),
    customerAddresses: vi.fn(), saveCustomerAddress: vi.fn(), removeCustomerAddress: vi.fn(),
    warehouses: vi.fn(), customers: vi.fn(),
  },
}))
vi.mock('@dcloudio/uni-app', () => ({
  onLoad: fn => { mocks.load = fn }, onShow: fn => mocks.shows.push(fn),
  onHide: fn => mocks.hides.push(fn), onUnload: fn => mocks.unloads.push(fn),
}))
vi.mock('@/store/auth', () => ({useAuth: () => mocks.auth}))
vi.mock('@/utils/request', () => ({api: mocks.api}))
import Reorder from '@/pages/orders/reorder.vue'
import Cart from '@/pages/orders/cart.vue'
import Addresses from '@/pages/customers/addresses.vue'
import { useCart } from '@/store/cart'

let wrapper
function render(component) {
  wrapper = mount(component, { global: { config: { compilerOptions: {
    isCustomElement: tag => ['view','text','scroll-view','image','picker','switch'].includes(tag),
  } } } })
  return wrapper
}
function button(text) { return wrapper.findAll('button').find(node => node.text() === text) }
function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => {resolve=res; reject=rej})
  return { promise, resolve, reject }
}
function product(overrides = {}) {
  return {id: 5, product_id: 5, name: '商品', qty:'2.000', price:'12.5000', historical_price:'9.0000',
    orig_price:'12.5000', available:'7.0000', base_unit_name:'件', base_uom_id:1,
    catalog_version:'v1', issues:[], line_key:'source-10', source_line_id:10, ...overrides}
}
function context() {
  return {
    source_order_id:10, source_order_no:'OLD-10', source_status:'已审核',
    can_copy:true, can_submit:true, context_issues:[],
    warehouse:{id:3,name:'原仓库'}, customer:{id:4,code:'C',name:'原客户'},
    header:{contact:'原收件人',contact_phone:'13800000000',ship_to:'原地址'},
    items:[product(),product({line_key:'source-11',source_line_id:11,qty:'1.000'})],
  }
}
function prepare() {
  const cart=useCart()
  cart.beginOrder({user_id:1,owner_id:2,warehouse:{id:3,name:'仓库'}})
  cart.setCustomer({id:4,code:'NORMAL',name:'客户'})
  cart.addItem({id:5,name:'原来正在填写的商品',qty:1,price:12.5,available:7,base_unit_name:'件'})
  return cart
}
function checked(payload, extra={}) {
  return {can_submit:true,checked_at:'2026-10-20T10:00:00+08:00',
    items:payload.items.map(item=>({...product(), ...item, entered_price:item.price, issues:[]})),...extra}
}

beforeEach(()=>{
  setActivePinia(createPinia())
  mocks.auth=reactive({user:{id:1,owner_id:2},ensureAuth:vi.fn()})
  mocks.shows=[];mocks.hides=[];mocks.unloads=[]
  Object.values(mocks.api).forEach(fn=>fn.mockReset())
  mocks.api.reorderContext.mockResolvedValue(context())
  mocks.api.customerAddresses.mockResolvedValue({results:[],customer:{id:4,name:'客户'}})
  mocks.api.validateOrder.mockImplementation(payload=>Promise.resolve(checked(payload)))
  mocks.api.warehouses.mockResolvedValue([{id:3,name:'仓库'}])
  mocks.api.customers.mockResolvedValue({results:[],next:null})
  vi.stubGlobal('uni',{showToast:vi.fn(),showModal:vi.fn(),redirectTo:vi.fn(),navigateBack:vi.fn(),navigateTo:vi.fn()})
})
afterEach(()=>{wrapper?.unmount();wrapper=null})

describe('再次下单',()=>{
  it('预览显示价差，不修改购物车，取消替换保留原输入',async()=>{
    const cart=prepare()
    render(Reorder);mocks.load({id:10});await flushPromises()
    expect(wrapper.text()).toContain('历史价：¥9.0000')
    expect(wrapper.text()).toContain('当前参考价：¥12.5000')
    expect(cart.items[0].name).toBe('原来正在填写的商品')
    uni.showModal.mockImplementation(({success})=>success({confirm:false}))
    await button('确认复制并编辑').trigger('click');await flushPromises()
    expect(cart.items[0].name).toBe('原来正在填写的商品')
    expect(uni.redirectTo).not.toHaveBeenCalled()
  })
  it('确认后保留重复原行，新建幂等键并清空平台单号',async()=>{
    const cart=prepare(), oldKey=cart.idempotency_key
    cart.order_header.src_bill_no='old-platform'
    render(Reorder);mocks.load({id:10});await flushPromises()
    uni.showModal.mockImplementation(({success})=>success({confirm:true}))
    await button('确认复制并编辑').trigger('click');await flushPromises()
    expect(cart.items).toHaveLength(2)
    expect(cart.items[0].line_key).not.toBe(cart.items[1].line_key)
    expect(cart.order_header.src_bill_no).toBe('')
    expect(cart.order_header.ship_to).toBe('原地址')
    expect(cart.idempotency_key).not.toBe(oldKey)
    expect(cart.editing_order_id).toBeNull()
    expect(uni.redirectTo).toHaveBeenCalledWith({url:'/pages/orders/cart'})
  })
  it('首次失败可重试，离开期间的旧响应不能覆盖',async()=>{
    const delayed=deferred()
    mocks.api.reorderContext.mockRejectedValueOnce(new Error('网络失败')).mockReturnValueOnce(delayed.promise)
    render(Reorder);mocks.load({id:10});await flushPromises()
    expect(wrapper.text()).toContain('网络失败')
    await button('重试').trigger('click')
    mocks.hides.forEach(fn=>fn())
    delayed.resolve(context());await flushPromises()
    expect(wrapper.text()).not.toContain('OLD-10')
    mocks.api.reorderContext.mockResolvedValue(context())
    mocks.shows.forEach(fn=>fn());await flushPromises()
    expect(wrapper.text()).toContain('OLD-10')
  })
  it('切换账号清除已加载来源，旧响应不进入新账号',async()=>{
    const delayed=deferred()
    mocks.api.reorderContext.mockReturnValueOnce(delayed.promise).mockRejectedValue({statusCode:403})
    render(Reorder);mocks.load({id:10});await flushPromises()
    mocks.auth.user={id:2,owner_id:3};await flushPromises()
    delayed.resolve(context());await flushPromises()
    expect(wrapper.text()).not.toContain('OLD-10')
    expect(wrapper.text()).toContain('访问权限已失效')
  })
})

describe('购物车服务端复核',()=>{
  it('未计价草稿连续保存使用新行标识，并保持未计价',async()=>{
    const cart=prepare()
    cart.editing_order_id=12;cart.editing_updated_at='2026-10-20T10:00:00'
    Object.assign(cart.items[0],{line_key:'edit-10',is_priced:false,price:null})
    mocks.api.validateOrder.mockImplementation(payload=>Promise.resolve(checked(payload,{
      items:payload.items.map(item=>({...product(),...item,is_priced:false,price:null,issues:[]})),
    })))
    mocks.api.updateOutboundOrder
      .mockResolvedValueOnce({updated_at:'2026-10-20T10:01:00',lines:[{id:20,is_priced:false}]})
      .mockResolvedValueOnce({updated_at:'2026-10-20T10:02:00',lines:[{id:30,is_priced:false}]})
    render(Cart);mocks.load();await flushPromises()
    expect(wrapper.text()).toContain('未计价')
    expect(wrapper.text()).not.toContain('基本单价')
    await button('保存草稿').trigger('click');await flushPromises()
    expect(cart.items[0].line_key).toBe('edit-20')
    await button('保存草稿').trigger('click');await flushPromises()
    expect(mocks.api.updateOutboundOrder.mock.calls[1][1].items[0]).toMatchObject({
      line_key:'edit-20',price:null,qty:'1',
    })
    expect(cart.items[0]).toMatchObject({line_key:'edit-30',is_priced:false,price:null})
  })
  it('重复商品分别编辑，缺货不自动改数量且不提交',async()=>{
    const cart=useCart()
    const source=context();source.items[0].qty='7.000'
    cart.beginReorder({user_id:1,owner_id:2,context:source})
    mocks.api.validateOrder.mockImplementation(payload=>Promise.resolve(checked(payload,{
      can_submit:false,items:payload.items.map(item=>({...product(),...item,issues:['合计需求超过库存，请调整数量']})),
    })))
    render(Cart);mocks.load();await flushPromises()
    expect(wrapper.findAll('.cart-item')).toHaveLength(2)
    expect(cart.items[0].qty).toBe('7.000')
    expect(wrapper.text()).toContain('合计需求超过库存')
    expect(wrapper.find('.primary-button').attributes('disabled')).toBeDefined()
    expect(mocks.api.createOutboundOrder).not.toHaveBeenCalled()
  })
  it('提交失败保留表单，网络重试复用原请求与幂等键',async()=>{
    const cart=prepare()
    mocks.api.createOutboundOrder.mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({id:99,order_no:'NEW-99'})
    render(Cart);mocks.load();await flushPromises()
    await wrapper.find('.primary-button').trigger('click');await flushPromises()
    expect(cart.items).toHaveLength(1)
    expect(cart.pending_submission).toBeTruthy()
    expect(wrapper.text()).toContain('重试确认提交结果')
    const validations=mocks.api.validateOrder.mock.calls.length
    await wrapper.find('.primary-button').trigger('click');await flushPromises()
    expect(mocks.api.createOutboundOrder.mock.calls[0]).toEqual(mocks.api.createOutboundOrder.mock.calls[1])
    expect(mocks.api.validateOrder).toHaveBeenCalledTimes(validations)
    expect(cart.items).toEqual([])
    expect(uni.redirectTo).toHaveBeenCalledWith({url:'/pages/orders/detail?id=99'})
  })
  it('修改另一条重复商品行后，合计缺口和数量错误一起解除',async()=>{
    const cart=useCart()
    cart.beginReorder({user_id:1,owner_id:2,context:context()})
    render(Cart);mocks.load();await flushPromises()
    const quantities=wrapper.findAll('.cart-item .field-input').filter((_,index)=>index%2===1)
    await quantities[0].trigger('input',{detail:{value:'6.500'}})
    expect(cart.items[0].qty).toBe(6.5)
    expect(wrapper.find('.primary-button').attributes('disabled')).toBeDefined()
    await quantities[1].trigger('input',{detail:{value:'0.500'}})
    await quantities[1].trigger('blur');await flushPromises()
    expect(cart.totalQty).toBe(7)
    expect(wrapper.find('.primary-button').attributes('disabled')).toBeUndefined()
  })
  it('刷新失败保留库存，但提交不能绕过复核',async()=>{
    prepare();render(Cart);mocks.load();await flushPromises()
    mocks.api.validateOrder.mockRejectedValue(new Error('网络失败'))
    await button('确认当前信息并重新校验').trigger('click');await flushPromises()
    expect(wrapper.text()).toContain('更新失败，当前为上次数据')
    expect(wrapper.text()).toContain('可用库存：7 件')
    await wrapper.find('.primary-button').trigger('click');await flushPromises()
    expect(mocks.api.createOutboundOrder).not.toHaveBeenCalled()
  })
  it('草稿重新提交的响应丢失后直接重放提交，不重复保存草稿',async()=>{
    const cart=prepare()
    cart.editing_order_id=12;cart.editing_updated_at='2026-10-20T10:00:00'
    mocks.api.updateOutboundOrder.mockResolvedValue({updated_at:'2026-10-20T10:01:00'})
    mocks.api.submitOutboundOrder.mockRejectedValueOnce(new Error('网络中断'))
      .mockResolvedValueOnce({id:12,order_no:'DRAFT-12',idempotent:true})
    render(Cart);mocks.load();await flushPromises()
    await wrapper.find('.primary-button').trigger('click');await flushPromises()
    expect(cart.pending_submission.kind).toBe('resubmit')
    await wrapper.find('.primary-button').trigger('click');await flushPromises()
    expect(mocks.api.updateOutboundOrder).toHaveBeenCalledTimes(1)
    expect(mocks.api.submitOutboundOrder.mock.calls[0]).toEqual(mocks.api.submitOutboundOrder.mock.calls[1])
    expect(cart.items).toEqual([])
  })
  it('普通开单使用默认地址，再次下单保留历史地址',async()=>{
    const cart=prepare()
    mocks.api.customerAddresses.mockResolvedValue({results:[{contact:'默认收件人',phone:'13812345678',address:'默认地址',is_default:true}]})
    render(Cart);mocks.load();await flushPromises()
    expect(cart.order_header.ship_to).toBe('默认地址')
    wrapper.unmount()
    cart.beginReorder({user_id:1,owner_id:2,context:context()})
    render(Cart);mocks.load();await flushPromises()
    expect(cart.order_header.ship_to).toBe('原地址')
  })
})

describe('地址簿',()=>{
  it('客户转交后的404清除旧地址，不继续显示上次数据',async()=>{
    mocks.api.customerAddresses.mockResolvedValueOnce({customer:{id:4,name:'客户'},results:[
      {id:1,contact:'旧收件人',phone:'13800000000',address:'旧权限范围的地址',version:1},
    ]}).mockRejectedValue({statusCode:404})
    render(Addresses);mocks.load({customer_id:4});await flushPromises()
    expect(wrapper.text()).toContain('旧权限范围的地址')
    await button('刷新').trigger('click');await flushPromises()
    expect(wrapper.text()).not.toContain('旧权限范围的地址')
    expect(wrapper.text()).toContain('该记录已不可访问')
  })
  it('选择地址写入本单快照，不修改地址记录',async()=>{
    const cart=prepare()
    mocks.api.customerAddresses.mockResolvedValue({customer:{id:4,name:'客户'},results:[
      {id:1,contact:'收件人',phone:'13800000000',address:'常用地址',version:2,is_default:true},
    ]})
    render(Addresses);mocks.load({customer_id:4,select:'1'});await flushPromises()
    await button('使用此地址').trigger('click')
    expect(cart.order_header.ship_to).toBe('常用地址')
    expect(mocks.api.saveCustomerAddress).not.toHaveBeenCalled()
  })
  it('保存期间客户转交，404清除地址和编辑内容',async()=>{
    mocks.api.customerAddresses.mockResolvedValue({customer:{id:4,name:'客户'},results:[
      {id:1,contact:'收件人',phone:'13800000000',address:'原客户私有信息',version:2,is_default:true},
    ]})
    mocks.api.saveCustomerAddress.mockRejectedValue({statusCode:404})
    render(Addresses);mocks.load({customer_id:4});await flushPromises()
    await button('修改').trigger('click')
    await button('保存地址').trigger('click');await flushPromises()
    expect(wrapper.text()).not.toContain('原客户私有信息')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.text()).toContain('该地址已不可访问')
    expect(button('新增地址').attributes('disabled')).toBeDefined()
  })
  it('并发保存冲突保留正在编辑的内容和旧版本',async()=>{
    mocks.api.customerAddresses.mockResolvedValue({customer:{id:4,name:'客户'},results:[
      {id:1,contact:'收件人',phone:'13800000000',address:'常用地址',version:2,is_default:true},
    ]})
    mocks.api.saveCustomerAddress.mockRejectedValue({statusCode:409})
    render(Addresses);mocks.load({customer_id:4});await flushPromises()
    await button('修改').trigger('click')
    await wrapper.find('textarea').setValue('我输入的新地址')
    await button('保存地址').trigger('click');await flushPromises()
    expect(wrapper.find('textarea').element.value).toBe('我输入的新地址')
    expect(wrapper.text()).toContain('地址已变化')
    expect(mocks.api.saveCustomerAddress.mock.calls[0][2].version).toBe(2)
  })
})
