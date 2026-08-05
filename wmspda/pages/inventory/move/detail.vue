<template>
  <view class="page">
    <view v-if="loading" class="state">正在加载…</view>
    <template v-else-if="task">
      <view class="card"><text class="title">{{task.task_no}}</text><text class="meta">{{task.owner_name}} · {{task.warehouse_name}}</text><text class="meta">状态 {{task.status}} · 执行 {{task.execution_state}} · 过账 {{task.posting_status}}</text>
        <view class="actions"><button v-if="task.can_claim" size="mini" @click="claim">领取</button><button v-if="task.can_start" type="primary" size="mini" @click="start">开始</button><button v-if="task.can_record" size="mini" @click="reportException">报告异常</button><button v-if="canManage&&task.execution_state==='EXCEPTION'" size="mini" @click="resume">恢复</button><button v-if="canManage&&task.posting_status==='FAILED'" size="mini" @click="retry">重试过账</button><button v-if="canManage&&task.posting_status!=='POSTED'" size="mini" @click="voidTask">整单作废</button></view>
      </view>
      <view v-for="line in task.lines" :key="line.id" class="card"><text class="product">{{line.product_name||line.product_code}}</text><text class="meta">{{line.from_location_code}} → {{line.to_location_code}}</text><text class="meta">容器 {{line.from_container_no||'散件'}} → {{line.to_container_no||'散件'}}</text><text class="meta">计划 {{line.qty_plan}} · 已完成 {{line.qty_done}} · 剩余 {{line.qty_pending}}</text>
        <template v-if="task.can_record&&!line.finished_at"><input v-model="forms[line.id].from" class="input" placeholder="扫描来源库位码"/><input v-if="line.from_container_no" v-model="forms[line.id].fromContainer" class="input" placeholder="扫描来源容器码"/><input v-model="forms[line.id].product" class="input" placeholder="扫描商品或容器码"/><input v-if="line.to_container_no" v-model="forms[line.id].toContainer" class="input" placeholder="扫描目标容器码"/><input v-model="forms[line.id].to" class="input" placeholder="扫描目标库位码"/><input v-if="line.serial_control" v-model="forms[line.id].serial" class="input" placeholder="扫描序列号"/><input v-model="forms[line.id].qty" class="input" type="digit" placeholder="本次数量"/><button type="primary" :loading="saving===line.id" @click="record(line)">确认移库</button></template>
      </view>
    </template><view v-else class="state">任务不存在或无权访问</view>
  </view>
</template>
<script setup>
import {computed,ref} from 'vue';import{onLoad}from'@dcloudio/uni-app';import{useAuth}from'@/store/auth';import{api,createIdempotencyUuid}from'@/utils/request'
const auth=useAuth(),taskId=ref(null),task=ref(null),forms=ref({}),loading=ref(false),saving=ref(null),canManage=computed(()=>auth.canManageRelocationTasks)
function hydrate(v){task.value=v;const n={};for(const l of v?.lines||[])n[l.id]=forms.value[l.id]||{from:'',to:'',fromContainer:'',toContainer:'',product:'',serial:'',qty:l.serial_control?'1':l.qty_pending,requestId:createIdempotencyUuid()};forms.value=n}
async function load(){loading.value=true;try{hydrate(await api.relocationTask(taskId.value))}catch(_){task.value=null}finally{loading.value=false}}
async function claim(){hydrate(await api.claimRelocationTask(taskId.value))}async function start(){hydrate(await api.startRelocationTask(taskId.value))}async function resume(){hydrate(await api.resumeRelocation(taskId.value))}async function retry(){hydrate(await api.retryRelocationPosting(taskId.value))}
async function record(line){const f=forms.value[line.id];if(!f.from||!f.to||!f.product||(line.from_container_no&&!f.fromContainer)||(line.to_container_no&&!f.toContainer)||Number(f.qty)<=0){uni.showToast({title:'请完整扫描库位、容器、商品并填写数量',icon:'none'});return}saving.value=line.id;try{const r=await api.recordRelocation(taskId.value,{request_id:f.requestId,line_id:line.id,from_location_code:f.from,to_location_code:f.to,from_container_code:f.fromContainer,to_container_code:f.toContainer,product_code:f.product,serial_no:f.serial,qty:Number(f.qty)});hydrate(r.task)}finally{saving.value=null}}
function reportException(){uni.showModal({title:'报告移库异常',editable:true,placeholderText:'请输入异常说明',success:async r=>{if(r.confirm&&r.content)hydrate(await api.reportRelocationException(taskId.value,{note:r.content}))}})}
function voidTask(){uni.showModal({title:'整单作废',editable:true,placeholderText:'请输入作废原因',success:async r=>{if(r.confirm&&r.content)hydrate(await api.voidRelocation(taskId.value,{note:r.content}))}})}
onLoad(async q=>{taskId.value=Number(q.task_id);await auth.loadProfile({force:true});await load()})
</script>
<style scoped>
.page{min-height:100vh;padding:24rpx;background:#f5f7fb;box-sizing:border-box}.card{margin-bottom:18rpx;padding:24rpx;background:#fff;border-radius:16rpx}.title,.product{display:block;color:#172033;font-size:31rpx;font-weight:700}.meta{display:block;margin-top:10rpx;color:#65758b;font-size:24rpx}.input{height:72rpx;margin-top:16rpx;padding:0 18rpx;border:1rpx solid #d7dfeb;border-radius:12rpx}.actions{display:flex;flex-wrap:wrap;gap:14rpx;margin-top:18rpx}.actions button{margin:0}button{margin-top:18rpx}.state{padding:100rpx 0;text-align:center;color:#748197}
</style>
