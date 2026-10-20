<template>
 <view class="tasks-page">
  <view class="heading"><text class="title">{{ kind==='RECEIVE'?'有订单收货':'上架任务' }}</text><button size="mini" :disabled="loading || denied" @click="refresh">刷新</button></view>
  <text class="muted">{{ history?'我的历史任务':'待领取与我的执行中任务' }} · {{ kind==='RECEIVE'?'按原申请登记良品、破损和拒收':'保留批次、效期及残次属性' }}</text>
  <view class="filters"><input v-model="search" placeholder="任务号、原申请单号或商品" @confirm="filter"/><button size="mini" @click="filter">搜索</button><button size="mini" @click="toggle">{{ history?'返回待办':'历史任务' }}</button></view>
  <view v-if="error" class="error"><text>{{ error }}</text><button v-if="!denied" size="mini" @click="refresh">重试</button></view>
  <text v-if="loading && !rows.length" class="muted">正在加载任务…</text>
  <text v-else-if="loaded && !rows.length && !error" class="muted">当前条件下暂无任务</text>
  <view v-for="task in rows" :key="task.id" class="card" @click="open(task)"><view class="heading"><text class="name">{{ task.task_no }}</text><text>{{ task.status_name }}</text></view><text>原申请：{{ task.ref_no || '未记录' }}</text><text>货主：{{ task.owner_name }}</text><text>仓库：{{ task.warehouse_name }}</text><text v-if="task.arrival" class="badge">分批{{ kind==='RECEIVE'?'到货验收':'上架' }}</text><button size="mini" @click.stop="open(task)">{{ task.can_claim?'领取并作业':history?'查看记录':'继续作业' }} ›</button></view>
  <button v-if="hasMore" :disabled="loading" @click="load(page+1)">加载更多</button>
 </view>
</template>
<script setup>
import {ref,watch,onUnmounted} from 'vue'
const props=defineProps({transport:Object,identity:String,visible:Boolean,kind:String})
const rows=ref([]),search=ref(''),history=ref(false),error=ref(''),loading=ref(false),loaded=ref(false),hasMore=ref(false),denied=ref(false)
let page=1,applied='',generation=0,alive=true,queued=false
const active=()=>alive && props.visible && !denied.value && (typeof document==='undefined' || !document.hidden)
async function load(next=1){if(!active())return;if(loading.value){queued=true;return}const tag=generation;loading.value=true;error.value='';queued=false;try{const r=await props.transport.request({url:'/api/inbound/pda/tasks/',data:{task_type:props.kind,search:applied,history:history.value?1:0,page:next,page_size:20},silent:true});if(tag!==generation || !active())return;if(!Array.isArray(r.results))throw new Error('响应不完整');rows.value=next===1?r.results:Array.from(new Map([...rows.value,...r.results].map(t=>[t.id,t])).values());page=next;hasMore.value=Boolean(r.next);loaded.value=true}catch(e){if(tag===generation && active()){if([401,403].includes(Number(e?.statusCode || e?.code))){rows.value=[];denied.value=true;error.value='任务访问权限已失效'}else error.value=rows.value.length?'更新失败，当前为上次任务数据':'任务加载失败，请重试'}}finally{loading.value=false;if(queued && active())load(1)}}
function refresh(){generation++;load(1)}
function filter(){applied=search.value.trim();rows.value=[];loaded.value=false;refresh()}
function toggle(){history.value=!history.value;filter()}
function open(task){uni.navigateTo({url:task.arrival?'/pages/inbound/arrivals?id='+task.arrival.order_id:`/pages/inbound/${props.kind==='RECEIVE'?'receive':'putaway'}_task_detail?task_id=${task.id}`})}
watch(()=>props.identity,()=>{generation++;rows.value=[];denied.value=false;loaded.value=false;error.value='';hasMore.value=false;load(1)},{immediate:true})
watch(()=>props.visible,v=>{generation++;if(v)load(1)})
function visibility(){generation++;if(active())load(1)}
if(typeof document!=='undefined')document.addEventListener('visibilitychange',visibility)
onUnmounted(()=>{alive=false;generation++;if(typeof document!=='undefined')document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.tasks-page{min-height:100vh;background:#f5f7fb;padding:18px 16px;box-sizing:border-box;color:#24334b;max-width:900px;margin:auto}.heading{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.title{font-size:22px;font-weight:700}.muted{display:block;color:#64748b;line-height:1.8;font-size:14px;margin:14px 0}.filters{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.filters input{flex:1;min-width:160px;border:1px solid #cbd5e1;padding:10px;border-radius:7px;background:white}.card{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:16px;margin:16px 0;overflow-wrap:anywhere}.card>text{display:block;line-height:1.8}.name{font-size:17px;font-weight:650}.badge{color:#2563eb}.error{padding:12px;background:#fff1f2;color:#a62338;border-radius:8px}.card button{margin-top:12px}button{font-size:14px}
</style>
