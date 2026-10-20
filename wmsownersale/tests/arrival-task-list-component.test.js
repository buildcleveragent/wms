import {flushPromises,mount} from '@vue/test-utils'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import TaskList from '../../frontend/shared/arrivals/TaskList.vue'
let request;const mounted=[]
function render(){const w=mount(TaskList,{props:{transport:{request},identity:'u1',visible:true,kind:'RECEIVE'}});mounted.push(w);return w}
const button=(w,t)=>w.findAll('button').find(b=>b.text()===t)
beforeEach(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});request=vi.fn();global.uni={navigateTo:vi.fn()}})
afterEach(()=>{mounted.splice(0).forEach(w=>w.unmount())})
it('加载失败明确提示并重试，不能显示暂无任务',async()=>{request.mockRejectedValueOnce({statusCode:500}).mockResolvedValue({results:[],next:null});const w=render();await flushPromises();expect(w.text()).toContain('加载失败');expect(w.text()).not.toContain('暂无任务');await button(w,'重试').trigger('click');await flushPromises();expect(w.text()).toContain('暂无任务')})
it('有订单收货打开真实申请批次，历史筛选独立应用',async()=>{request.mockResolvedValue({results:[{id:3,task_no:'RC-001',arrival:{order_id:7},can_claim:true}],next:null});const w=render();await flushPromises();await button(w,'领取并作业 ›').trigger('click');expect(uni.navigateTo).toHaveBeenCalledWith({url:'/pages/inbound/arrivals?id=7'});await button(w,'历史任务').trigger('click');await flushPromises();expect(request.mock.lastCall[0].data.history).toBe(1)})
it('离页及切换账号清除旧响应，权限失效清空列表',async()=>{let resolve;request.mockReturnValueOnce(new Promise(r=>resolve=r)).mockResolvedValue({results:[],next:null});const w=render();await w.setProps({identity:'u2'});resolve({results:[{id:3,task_no:'旧任务'}],next:null});await flushPromises();expect(w.text()).not.toContain('旧任务');request.mockRejectedValueOnce({statusCode:403});await button(w,'刷新').trigger('click');await flushPromises();expect(w.text()).toContain('权限已失效')})
