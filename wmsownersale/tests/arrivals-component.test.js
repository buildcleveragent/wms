import {flushPromises,mount} from '@vue/test-utils'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import Workspace from '../../frontend/shared/arrivals/Workspace.vue'
let request;const wrappers=[]
const record=()=>({id:1,order_no:'ASN-001',supplier_name:'供应商',warehouse_name:'一号仓',version:1,status_label:'等待后续到货',allowed_actions:['owner-approve'],lines:[{id:10,product_id:3,product_name:'采购商品',product_code:'P3',unit:'瓶',base_qty:'10.000',qty_ok:'3',qty_damage:'1',qty_reject:'1',waiting_qty:'5',pending_qty:'0',short_qty:'0',over_qty:'0',posted_qty:'4',putaway_qty:'0'}],batches:[{id:2,version:1,number:'RC-001',stage:'DRAFT',allowed_actions:['batch-edit','batch-submit'],lines:[],putaway_lines:[]}],events:[{id:1,action:'创建申请',actor:'业务员',time:'2026-10-20T09:00:00+08:00',reason:'长说明'.repeat(40)}],locations:[{id:9,code:'A01'}]})
const button=(w,t)=>w.findAll('button').find(b=>b.text()===t)
function render(props={}){const w=mount(Workspace,{props:{transport:{request},visible:true,identity:'u1',recordId:1,...props}});wrappers.push(w);return w}
beforeEach(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});request=vi.fn().mockResolvedValue(record());global.uni={showModal:vi.fn(o=>o.success({confirm:true,content:'确认原因'}))}})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.useRealTimers()})
describe('到货预报与PDA共同页面',()=>{
 it('采购参考价预填，无参考价留空仍可保存草稿',async()=>{
  request.mockResolvedValueOnce({
   suppliers:[{id:1,name:'供应商'}],warehouses:[{id:2,name:'仓库'}],
   results:[{id:3,name:'有采购价',purchase_price:'12.5000'},{id:4,name:'无采购价',purchase_price:null}],
  })
  const w=render({recordId:0,creating:true,canCreate:true});await flushPromises()
  const pickers=w.findAll('picker')
  await pickers[0].trigger('change',{detail:{value:'0'}})
  await pickers[1].trigger('change',{detail:{value:'0'}})
  for(const b of w.findAll('button').filter(b=>b.text()==='添加一行'))await b.trigger('click')
  const prices=w.findAll('input[placeholder="未记录可留空"]')
  expect(prices[0].element.value).toBe('12.5000');expect(prices[1].element.value).toBe('')
  const qtys=w.findAll('label').filter(l=>l.text()==='计划数量')
  await qtys[0].find('input').setValue('2');await qtys[1].find('input').setValue('1.25')
  await button(w,'保存草稿').trigger('click');await flushPromises()
  expect(request.mock.lastCall[0].data.lines).toMatchObject([
   {product_id:3,base_qty:'2',base_price:'12.5000'},{product_id:4,base_qty:'1.25',base_price:'0'},
  ])
 })
 it('按原单行展示单位、计划、验收、待到货和入账结果',async()=>{const w=render();await flushPromises();for(const t of ['ASN-001','单位：瓶','已确认破损 1','待到货 5','已入账 4','创建申请'])expect(w.text()).toContain(t)})
 it('首次失败可重试，刷新失败保留数据，权限失效清空',async()=>{request.mockRejectedValueOnce({statusCode:404});const w=render();await flushPromises();expect(w.text()).toContain('暂不可用');await button(w,'重新加载').trigger('click');await flushPromises();expect(w.text()).toContain('ASN-001');request.mockRejectedValueOnce({statusCode:500});await button(w,'刷新').trigger('click');await flushPromises();expect(w.text()).toContain('当前为上次数据');expect(w.text()).toContain('ASN-001');request.mockRejectedValueOnce({statusCode:403});await button(w,'刷新').trigger('click');await flushPromises();expect(w.text()).not.toContain('ASN-001');expect(w.text()).toContain('权限已失效')})
 it('网络重试复用幂等编号，失败保留动作',async()=>{const w=render();await flushPromises();request.mockRejectedValueOnce({message:'网络失败'});await button(w,'审核通过').trigger('click');await flushPromises();const first=request.mock.lastCall[0].data.request_id;await button(w,'审核通过').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.request_id).toBe(first)})
 it('批次草稿显示照片选填，精确数量与原行传到接口',async()=>{const w=render({warehouse:true});await flushPromises();await button(w,'填写／修改本批验收').trigger('click');await flushPromises();expect(w.text()).toContain('破损照片选填');const p=w.findAll('picker');await p[p.length-1].trigger('change',{detail:{value:'0'}});const inputs=w.findAll('input');await inputs[0].setValue('1.125');await button(w,'保存本批草稿').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.lines[0]).toMatchObject({order_line_id:10,location_id:9,qty_ok:'1.125',qty_damage:'0'});expect(request.mock.lastCall[0].url).toContain('batch-edit')})
 it('版本冲突保留表单，刷新不会静默覆盖其他人的修改', async () => {
  const w=render({warehouse:true});await flushPromises();
  await button(w,'填写／修改本批验收').trigger('click');await flushPromises();
  await w.findAll('input')[0].setValue('1.125');
  request.mockRejectedValueOnce({statusCode:409});
  await button(w,'保存本批草稿').trigger('click');await flushPromises();
  expect(w.text()).toContain('填写内容已保留');expect(w.findAll('input')[0].element.value).toBe('1.125');
  const latest=record();latest.batches[0].version=2;request.mockResolvedValueOnce(latest);
  await button(w,'刷新').trigger('click');await flushPromises();
  request.mockRejectedValueOnce({statusCode:409});
  await button(w,'保存本批草稿').trigger('click');await flushPromises();
  expect(request.mock.lastCall[0].data.version).toBe(1);
  await button(w,'填写／修改本批验收').trigger('click');await flushPromises();
  await w.findAll('input')[0].setValue('1.125');
  await button(w,'保存本批草稿').trigger('click');await flushPromises();
  expect(request.mock.lastCall[0].data.version).toBe(2);
 })
 it('无轮询，恢复可见刷新，卸载后停止',async()=>{vi.useFakeTimers();const w=render();await flushPromises();await vi.advanceTimersByTimeAsync(90000);expect(request).toHaveBeenCalledTimes(1);Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(request).toHaveBeenCalledTimes(2);w.unmount();document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(request).toHaveBeenCalledTimes(2)})
 it('账号切换和离页后丢弃旧响应',async()=>{let resolve;request.mockReturnValueOnce(new Promise(r=>resolve=r));const w=render();await w.setProps({identity:'u2'});resolve({...record(),order_no:'旧账号内容'});await flushPromises();expect(w.text()).not.toContain('旧账号内容');let second;request.mockReturnValueOnce(new Promise(r=>second=r));await button(w,'刷新').trigger('click');await w.setProps({visible:false});second({...record(),order_no:'离页响应'});await flushPromises();expect(w.text()).not.toContain('离页响应')})
 it('搜索使用已应用条件，分页去重并打开申请',async()=>{request.mockResolvedValueOnce({results:[record()],next:'next'}).mockResolvedValueOnce({results:[record(),{...record(),id:4}],next:null});const w=render({recordId:0});await flushPromises();await w.find('input').setValue('尚未搜索');await button(w,'加载更多').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.search).toBeUndefined();expect(w.findAll('.clickable')).toHaveLength(2);await w.find('.clickable').trigger('click');expect(w.emitted('open')[0]).toEqual([1])})
})
