<template>
  <view class="page task-page">
    <view v-if="loading" class="state">正在加载…</view>
    <template v-else-if="task">
      <view class="task-head">
        <view><text class="task-no">任务 {{ task.task_no }}</text><text class="task-meta">{{ task.owner_name }} · {{ task.warehouse_name }}</text></view>
        <text class="status">{{ task.status }}</text>
      </view>
      <view class="task-actions"><button v-if="task.can_claim" @click="claim">领取任务</button><button v-if="task.can_start" class="primary" @click="start">开始补货</button><button v-if="canRetry && task.posting_status === 'FAILED'" @click="retryPosting">重试过账</button></view>

      <view v-for="line in task.lines" :key="line.id" class="line-card">
        <view class="line-title"><view><text>{{ line.product_name || line.product_code }}</text><text>{{ line.from_location_code }} → {{ line.to_location_code }}</text></view><text>剩余 {{ line.qty_pending }}</text></view>
        <view v-if="task.can_record && !line.finished_at" class="steps">
          <view v-for="(step, index) in stepsFor(line)" :key="step.key" :class="['step', { done: step.value, current: activeStep(line) === step.key }]">
            <view class="step-index">{{ step.value ? '✓' : index + 1 }}</view>
            <view class="step-body"><text>{{ step.label }}</text><ScanInput v-model="forms[line.id][step.key]" :placeholder="step.placeholder" :active="activeStep(line) === step.key" @focus="activeTargets[line.id] = step.key" @scan="advance(line, step.key)" /></view>
          </view>
          <view class="qty-row"><text>本次数量</text><input v-model="forms[line.id].qty" type="digit" /></view>
          <button class="confirm" :loading="saving === line.id" @click="record(line)">确认补货</button>
        </view>
        <view v-else class="completed">{{ line.finished_at ? '该行已完成' : '请先领取并开始任务' }}</view>
      </view>
    </template>
    <view v-else class="state">任务不存在或无权访问</view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import ScanInput from '@/components/ScanInput.vue'
import { useAuth } from '@/store/auth'
import { api, createIdempotencyUuid } from '@/utils/request'

const auth=useAuth(),taskId=ref(null),task=ref(null),forms=ref({}),activeTargets=ref({}),loading=ref(false),saving=ref(null)
const canRetry=computed(()=>auth.canRetryReplenishmentPosting)
function hydrate(value){task.value=value;const next={};for(const line of value?.lines||[]){next[line.id]=forms.value[line.id]||{from:'',product:'',to:'',serial:'',qty:line.serial_control?'1':String(line.qty_pending||''),requestId:createIdempotencyUuid()};activeTargets.value[line.id]=activeTargets.value[line.id]||'from'}forms.value=next}
function stepsFor(line){const form=forms.value[line.id]||{};return[{key:'from',label:'扫来源库位',placeholder:line.from_location_code||'来源库位码',value:form.from},{key:'product',label:'扫商品',placeholder:line.product_code||'商品条码',value:form.product},{key:'to',label:'扫目标库位',placeholder:line.to_location_code||'目标库位码',value:form.to},...(line.serial_control?[{key:'serial',label:'扫序列号',placeholder:'商品序列号',value:form.serial}]:[])]}
function activeStep(line){const steps=stepsFor(line);return activeTargets.value[line.id]||steps.find((step)=>!step.value)?.key||steps[steps.length-1]?.key}
function advance(line,key){const steps=stepsFor(line),index=steps.findIndex((step)=>step.key===key);activeTargets.value[line.id]=steps[index+1]?.key||key}
async function load(){loading.value=true;try{hydrate(await api.replenishmentTask(taskId.value))}catch(error){task.value=null}finally{loading.value=false}}
async function claim(){hydrate(await api.claimReplenishmentTask(taskId.value))}async function start(){hydrate(await api.startReplenishmentTask(taskId.value))}async function retryPosting(){hydrate(await api.retryReplenishmentPosting(taskId.value))}
async function record(line){const form=forms.value[line.id];if(!form.from||!form.to||!form.product||(line.serial_control&&!form.serial)||Number(form.qty)<=0){uni.showToast({title:'请按步骤完整扫描并填写数量',icon:'none'});return}saving.value=line.id;try{const response=await api.recordReplenishment(taskId.value,{request_id:form.requestId,line_id:line.id,from_location_code:form.from,to_location_code:form.to,product_code:form.product,serial_no:form.serial,qty:Number(form.qty)});hydrate(response.task);uni.showToast({title:'补货已记录',icon:'success'})}catch(error){uni.showToast({title:error?.message||'补货失败',icon:'none'})}finally{saving.value=null}}
onLoad(async query=>{taskId.value=Number(query.task_id);await auth.loadProfile({force:true});await load()})
</script>

<style scoped>
.task-page{padding-top:28rpx}.state{padding:100rpx 0;color:#64748b;text-align:center}.task-head{display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:26rpx;border-bottom:1rpx solid #e2e8f0}.task-no,.task-meta{display:block}.task-no{font-size:34rpx;font-weight:750}.task-meta{margin-top:6rpx;color:#64748b;font-size:23rpx}.status{padding:8rpx 16rpx;border-radius:12rpx;background:#edf9f3;color:#159357;font-size:23rpx}.task-actions{display:flex;flex-wrap:wrap;gap:12rpx;margin:22rpx 0}.task-actions button{min-height:70rpx;padding:0 20rpx;border:1rpx solid #94a3b8;border-radius:14rpx;background:#fff;font-size:23rpx;display:flex;align-items:center}.task-actions button.primary{border:0;background:#0f766e;color:#fff}.line-card{margin-top:24rpx;padding:24rpx;border:1rpx solid #e2e8f0;border-radius:22rpx}.line-title{display:flex;align-items:flex-start;justify-content:space-between;gap:18rpx}.line-title view{flex:1}.line-title view text{display:block}.line-title view text:first-child{font-size:29rpx;font-weight:700}.line-title view text:last-child{margin-top:5rpx;color:#64748b;font-size:23rpx}.line-title>text{color:#0f766e;font-size:23rpx}.steps{margin-top:26rpx}.step{position:relative;display:flex;gap:18rpx;padding-bottom:22rpx}.step:not(:last-of-type)::before{content:'';position:absolute;left:25rpx;top:52rpx;bottom:0;width:3rpx;background:#dbe3ed}.step.done::before{background:#159357}.step-index{width:52rpx;height:52rpx;flex:0 0 auto;border-radius:50%;background:#e2e8f0;color:#64748b;display:flex;align-items:center;justify-content:center;font-size:23rpx;font-weight:700}.step.done .step-index{background:#159357;color:#fff}.step.current .step-index{background:#1677ff;color:#fff}.step-body{flex:1;min-width:0}.step-body>text{display:block;margin:5rpx 0 12rpx;font-size:27rpx;font-weight:650}.qty-row{min-height:84rpx;display:flex;align-items:center;justify-content:space-between;border-top:1rpx solid #e2e8f0}.qty-row input{width:180rpx;height:64rpx;border:1rpx solid #cbd5e1;border-radius:12rpx;text-align:center}.confirm{width:100%;height:86rpx;margin-top:16rpx;border-radius:16rpx;background:#0f766e;color:#fff;display:flex;align-items:center;justify-content:center;font-size:29rpx;font-weight:700}.completed{margin-top:20rpx;color:#64748b;font-size:24rpx}
</style>
