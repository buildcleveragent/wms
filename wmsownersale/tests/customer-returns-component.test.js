import {flushPromises,mount} from '@vue/test-utils'
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import Workspace from '../../frontend/shared/customer-returns/Workspace.vue'
const wrappers=[]
const line={id:1,original_line_id:11,product_name:'长名称退货商品',product_code:'P1',spec:'500ml',unit:'瓶',qty:'5.0000',received_qty:'2',qty_ok:'1',qty_damage:'1',qty_reject:'0',pending_qty:'3',reason:'质量问题',reason_code:'QUALITY',photos:[]}
const record=()=>({id:2,number:'CR-001',original_order_no:'SALE-001',warehouse:'一号仓',customer:'客户',version:1,status:'PARTIAL',status_label:'部分验收',allowed_actions:['note'],lines:[{...line}],receipts:[{id:3,created_at:'2026-10-19T12:00:00+08:00',actor:'仓库操作员',posted_at:null,posting_label:'入账失败，可重试',lines:[{...line,id:4,location:'A01',batch_no:'B01',reason:'破损',photos:[]}]}],events:[{id:1,label:'提交申请',actor:'业务员',occurred_at:'2026-10-19T12:00:00+08:00',reason:'提交退货'}],photos:[]})
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
let request,upload,image
function render(props={}){const w=mount(Workspace,{props:{transport:{request,upload,image},visible:true,identity:'user1',receiptScope:'api1/user1',recordId:2,...props}});wrappers.push(w);return w}
const button=(w,label)=>w.findAll('button').find(b=>b.text()===label)
beforeEach(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});request=vi.fn().mockResolvedValue(record());upload=vi.fn().mockResolvedValue({id:9});image=vi.fn().mockResolvedValue('blob:image');global.uni={showModal:vi.fn(o=>o.success({confirm:true,content:'填写原因'})),chooseImage:vi.fn(o=>o.success({tempFilePaths:['/tmp/photo.png']})),previewImage:vi.fn()}})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.useRealTimers()})
beforeEach(() => {
  const storage = new Map()
  Object.assign(uni, {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, JSON.parse(JSON.stringify(value))),
    removeStorageSync: key => storage.delete(key),
  })
})
describe('客户退货共同页面',()=>{
 it('显示原单、单位、分批数量、入账状态及历史',async()=>{const w=render();await flushPromises();expect(w.text()).toContain('SALE-001');expect(w.text()).toContain('单位：瓶');expect(w.text()).toContain('残次 1');expect(w.text()).toContain('入账失败，可重试');expect(w.text()).toContain('提交退货')})
 it('首次失败重试及后续失败保留记录',async()=>{request.mockRejectedValueOnce({statusCode:404});const w=render();await flushPromises();expect(w.text()).toContain('暂不可用');await button(w,'重试').trigger('click');await flushPromises();expect(w.text()).toContain('CR-001');request.mockRejectedValueOnce({statusCode:500});await button(w,'刷新').trigger('click');await flushPromises();expect(w.text()).toContain('当前为上次数据');expect(w.text()).toContain('CR-001')})
 it('权限失效清空记录并停止刷新',async()=>{const w=render();await flushPromises();request.mockRejectedValueOnce({statusCode:403});await button(w,'刷新').trigger('click');await flushPromises();expect(w.text()).not.toContain('CR-001');expect(w.text()).toContain('权限已失效');const count=request.mock.calls.length;document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(request).toHaveBeenCalledTimes(count)})
 it('隐藏、恢复、账号切换与过期响应隔离',async()=>{const first=deferred();request.mockReturnValueOnce(first.promise);const w=render();await w.setProps({visible:false});first.resolve({...record(),number:'旧响应'});await flushPromises();expect(w.text()).not.toContain('旧响应');await w.setProps({visible:true});await flushPromises();expect(w.text()).toContain('CR-001');const old=deferred();request.mockReturnValueOnce(old.promise);await button(w,'刷新').trigger('click');await w.setProps({identity:'user2'});old.resolve({...record(),number:'旧账号'});await flushPromises();expect(w.text()).not.toContain('旧账号');expect(w.text()).toContain('CR-001')})
 it('浏览器恢复刷新，无定时轮询，卸载后不请求',async()=>{vi.useFakeTimers();const w=render();await flushPromises();const count=request.mock.calls.length;await vi.advanceTimersByTimeAsync(90000);expect(request).toHaveBeenCalledTimes(count);Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(request).toHaveBeenCalledTimes(count+1);w.unmount();document.dispatchEvent(new Event('visibilitychange'));await flushPromises();expect(request).toHaveBeenCalledTimes(count+1)})
 it('清单搜索、分页、去重并打开详情',async()=>{request.mockResolvedValueOnce({results:[record()],next:'next'}).mockResolvedValueOnce({results:[record(),{...record(),id:5}],next:null}).mockResolvedValue({results:[],next:null});const w=render({recordId:0});await flushPromises();await button(w,'加载更多').trigger('click');await flushPromises();expect(w.findAll('.card')).toHaveLength(2);await w.find('.card').trigger('click');expect(w.emitted('open')[0]).toEqual([2]);await w.find('input').setValue('原单');await button(w,'搜索').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.search).toBe('原单')})
 it('草稿保存使用原订单行、精确数量及原因',async()=>{request.mockResolvedValueOnce({original_order_no:'O1',warehouse:'W1',lines:[{...line,can_apply:true,fulfilled_qty:'10',available_qty:'5',returned_qty:'0',pending_qty:'5'}]}).mockResolvedValue(record());const w=render({recordId:0,originalId:8});await flushPromises();await w.find('input').setValue('1.2500');await w.find('textarea').setValue('需要退回');await button(w,'保存草稿').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data).toMatchObject({original_order_id:8,lines:[{original_line_id:11,qty:'1.2500',reason:'需要退回'}]});expect(w.emitted('open')[0]).toEqual([2])})
 it('操作按钮来自后端，网络重试复用请求标识',async()=>{request.mockResolvedValueOnce({...record(),allowed_actions:['owner-approve']}).mockRejectedValueOnce({message:'网络异常'}).mockResolvedValue({...record(),allowed_actions:[]});const w=render();await flushPromises();await button(w,'审核通过').trigger('click');await flushPromises();const key=request.mock.lastCall[0].data.request_id;await button(w,'审核通过').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.request_id).toBe(key);expect(button(w,'审核通过')).toBeUndefined()})
 it('PDA 验收提交数量、批次、库位并清空已成功表单',async()=>{request.mockImplementation(opts=>Promise.resolve(opts.url.includes('receipt-options')?{locations:[{id:7,code:'A01'}]}:{...record(),allowed_actions:['receive','retry-posting']}));const w=render({warehouse:true});await flushPromises();await button(w,'增加验收行').trigger('click');await flushPromises();const inputs=w.findAll('input');await inputs[0].setValue('1.25');await inputs[3].setValue('B01');await button(w,'确认本次验收并入账').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.lines[0]).toMatchObject({line_id:1,location_id:7,qty_ok:'1.25',batch_no:'B01'});expect(w.findAll('input')).toHaveLength(0)})
 it('上传照片和鉴权预览',async()=>{request.mockResolvedValue({...record(),allowed_actions:['edit']});const w=render();await flushPromises();await button(w,'修改申请 / 上传照片').trigger('click');await button(w,'上传申请照片').trigger('click');await flushPromises();expect(upload.mock.lastCall[0].formData).toEqual({line_id:1,purpose:'APPLICATION'});await button(w,'查看照片 9').trigger('click');await flushPromises();expect(uni.previewImage).toHaveBeenCalledWith({urls:['blob:image']})})
 it('修改草稿期间刷新不能覆盖原编辑版本',async()=>{request.mockResolvedValueOnce({...record(),status:'DRAFT',version:3,allowed_actions:['edit']}).mockResolvedValueOnce({...record(),status:'DRAFT',version:4,allowed_actions:['edit']}).mockResolvedValue(record());const w=render();await flushPromises();await button(w,'修改申请 / 上传照片').trigger('click');await button(w,'刷新').trigger('click');await flushPromises();await button(w,'保存草稿').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.version).toBe(3)})
 it('详情失去访问范围后清除上次记录',async()=>{const w=render();await flushPromises();request.mockRejectedValueOnce({statusCode:404});await button(w,'刷新').trigger('click');await flushPromises();expect(w.text()).not.toContain('CR-001');expect(w.text()).toContain('访问权限已失效')})

 it('在原验收行补日期，更新后显示同一批次的入账结果', async () => {
   const batch = {id: 12, posted_at: null, posting_label: '待入账', posting_hold_reason: '待补齐验收行效期资料',
     lines: [{id: 33, product_name: '退回商品', qty_ok: '1', missing_dates: ['expiry_date'], photos: []}]}
   request.mockResolvedValueOnce({...record(), version: 5, allowed_actions: ['retry-posting'], receipts: [batch]})
     .mockResolvedValueOnce({...record(), allowed_actions: [], receipts: [{...batch, posted_at: '2026-11-18T08:00:00+08:00', posting_label: '已回补库存', posting_hold_reason: ''}]})
   const w = render({warehouse: true})
   await flushPromises()
   expect(w.text()).toContain(batch.posting_hold_reason)
   await button(w, '补录效期资料').trigger('click')
   await w.get('input[placeholder="YYYY-MM-DD"]').setValue('2027-02-01')
   await button(w, '保存日期并重试入账').trigger('click')
   await flushPromises()
   expect(request.mock.lastCall[0]).toMatchObject({url: '/api/inbound/customer-returns/2/receipts/12/trace/',
     method: 'POST', data: {version: 5, receipt_line_id: 33, expiry_date: '2027-02-01'}})
   expect(w.text()).toContain('已回补库存')
   expect(button(w, '补录效期资料')).toBeUndefined()
 })

 it('实收应答丢失，刷新与重新打开后重放冻结的原请求', async () => {
   let received = 0
   const commands = []
   const detail = () => ({...record(), version: 5 + received,
     allowed_actions: ['receive'], lines: [{...line, qty: '3', received_qty: String(received), pending_qty: String(3-received)}]})
   request.mockImplementation(async opts => {
     if (opts.url.includes('receipt-options')) return {locations:[{id:7,code:'A01'}]}
     if (opts.method !== 'POST') return detail()
     const body = opts.data
     if (!commands.some(command => command.request_id === body.request_id)) received += Number(body.lines[0].qty_ok)
     commands.push(body)
     if (commands.length === 1) throw {statusCode: 0, message: '网络超时'}
     return detail()
   })
   const first = render({warehouse:true})
   await flushPromises()
   await button(first, '增加验收行').trigger('click')
   await first.find('input').setValue('1')
   await button(first, '确认本次验收并入账').trigger('click')
   await flushPromises()
   await button(first, '刷新').trigger('click')
   await flushPromises()
   expect(button(first, '核对本次验收结果')).toBeDefined()
   expect(button(first, '增加验收行')).toBeUndefined()
   first.unmount()
   const second = render({warehouse:true})
   await flushPromises()
   await second.setProps({identity:'user2', receiptScope:'api1/user2'})
   await flushPromises()
   expect(button(second, '核对本次验收结果')).toBeUndefined()
   await second.setProps({identity:'user1', receiptScope:'api2/user1'})
   await flushPromises()
   expect(button(second, '核对本次验收结果')).toBeUndefined()
   await second.setProps({receiptScope:'api1/user1'})
   await flushPromises()
   await button(second, '核对本次验收结果').trigger('click')
   await flushPromises()
   expect(commands).toHaveLength(2)
   expect(commands[1]).toEqual(commands[0])
   expect(commands[1]).toMatchObject({version:5, request_id:expect.any(String)})
   expect(received).toBe(1)
   expect(button(second, '核对本次验收结果')).toBeUndefined()
   expect(button(second, '增加验收行')).toBeDefined()
 })

 it('实收校验失败仍可修改，存储失败不会发送请求', async () => {
   const detail = {...record(), allowed_actions: ['receive']}
   request.mockImplementation(async opts => {
     if (opts.url.includes('receipt-options')) return {locations:[{id:7,code:'A01'}]}
     if (opts.method === 'POST') throw {statusCode:400, message:'请填写数量'}
     return detail
   })
   const w = render({warehouse:true})
   await flushPromises()
   await button(w, '增加验收行').trigger('click')
   await button(w, '确认本次验收并入账').trigger('click')
   await flushPromises()
   expect(button(w, '核对本次验收结果')).toBeUndefined()
   expect(w.text()).toContain('请填写数量')
   await w.find('input').setValue('1')
   uni.setStorageSync = () => { throw new Error('存储已满') }
   await button(w, '确认本次验收并入账').trigger('click')
   await flushPromises()
   expect(w.text()).toContain('请检查设备存储')
   expect(request.mock.calls.filter(([opts]) => opts.method === 'POST')).toHaveLength(1)
 })

 it('原账号实收回执晚到不会出现在切换后的单据中', async () => {
   const response = deferred()
   const detail = {...record(), allowed_actions:['receive']}
   request.mockImplementation(opts => {
     if (opts.url.includes('receipt-options')) return Promise.resolve({locations:[{id:7,code:'A01'}]})
     if (opts.method === 'POST') return response.promise
     return Promise.resolve(detail)
   })
   const w = render({warehouse:true})
   await flushPromises()
   await button(w, '增加验收行').trigger('click')
   await w.find('input').setValue('1')
   await button(w, '确认本次验收并入账').trigger('click')
   await w.setProps({identity:'user2', receiptScope:'api1/user2'})
   response.resolve({...detail, number:'原账号实收回执'})
   await flushPromises()
   expect(w.text()).not.toContain('原账号实收回执')
   expect(button(w, '核对本次验收结果')).toBeUndefined()
   await w.setProps({identity:'user1', receiptScope:'api1/user1'})
   await flushPromises()
   expect(button(w, '核对本次验收结果')).toBeDefined()
 })

 it('补录冲突后刷新，重新核对原行并仅提交仍缺少的日期', async () => {
   const row = {id:33, product_name:'样品', qty_ok:'1', photos:[], missing_dates:['production_date','expiry_date']}
   const batch = {id:12, posted_at:null, lines:[row]}
   const pending = {...record(), version:5, allowed_actions:['retry-posting'], receipts:[batch]}
   const updated = {...pending, version:6, receipts:[{...batch, lines:[{...row, production_date:'2026-05-10', missing_dates:['expiry_date']}]}]}
   request.mockResolvedValueOnce(pending).mockRejectedValueOnce({statusCode:409, message:'请刷新后重试'})
     .mockResolvedValueOnce(updated).mockResolvedValueOnce({...updated, version:8, allowed_actions:[]})
   const w = render({warehouse:true})
   await flushPromises()
   await button(w, '补录效期资料').trigger('click')
   const dates = w.findAll('input[placeholder="YYYY-MM-DD"]')
   await dates[0].setValue('2026-05-11')
   await dates[1].setValue('2027-05-11')
   await button(w, '保存日期并重试入账').trigger('click')
   await flushPromises()
   expect(w.text()).toContain('请刷新后重试')
   await button(w, '刷新').trigger('click')
   await flushPromises()
   expect(w.text()).toContain('生产日期 2026-05-10')
   const remaining = w.findAll('input[placeholder="YYYY-MM-DD"]')
   expect(remaining).toHaveLength(1)
   expect(remaining[0].element.value).toBe('')
   await remaining[0].setValue('2027-05-10')
   await button(w, '保存日期并重试入账').trigger('click')
   await flushPromises()
   expect(request.mock.lastCall[0].data).toEqual({request_id:expect.any(String), version:6, receipt_line_id:33, expiry_date:'2027-05-10'})
 })

})
