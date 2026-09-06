<template>
  <view class="page review-page">
    <view class="header-card"><text class="task-no">复核 {{ task.task_no || taskId }}</text><text>{{ task.ref_no || '-' }} · {{ task.executor_name || '未分配' }}</text><text>{{ headerStatusText }}</text></view>
    <view class="scan-panel"><text>扫描商品逐项核对</text><ScanInput v-model="reviewCode" placeholder="商品条码" @scan="locateLine" @confirm="locateLine" /></view>
    <view v-for="line in lines" :key="line.id" :class="['line-card', { abnormal: isAbnormal(line), scanned: scannedLineId === line.id }]">
      <view class="line-head"><text>{{ line.product_name }}</text><text>{{ scannedLineId === line.id ? '已定位' : line.from_location_code }}</text></view>
      <text class="line-code">{{ line.product_code || '-' }}</text>
      <view class="line-qty"><text>计划 {{ line.qty_plan }} {{ line.uom }}</text><text>实拣 {{ line.qty_done }} {{ line.uom }}</text></view>
      <text v-if="isAbnormal(line)" class="warning">{{ abnormalText(line) }}</text>
    </view>
    <view class="footer safe-bottom"><text>共 {{ totalLines }} 行 · 异常 {{ abnormalCount }}</text><button :disabled="posting" @click="approveAndPost">{{ posting ? '正在确认…' : '复核通过' }}</button></view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import ScanInput from '@/components/ScanInput.vue'
import { api } from '@/utils/request'

const taskId=ref(null),task=ref({}),lines=ref([]),posting=ref(false),reviewCode=ref(''),scannedLineId=ref(null)
const totalLines=computed(()=>lines.value.length),abnormalCount=computed(()=>lines.value.filter(isAbnormal).length)
const headerStatusText=computed(()=>task.value.status==='COMPLETED'&&task.value.review_status==='PENDING'?'已拣完，待复核':`${task.value.status||'-'} / ${task.value.review_status||'-'}`)
function isAbnormal(line){return Number(line.qty_plan)!==Number(line.qty_done)}
function abnormalText(line){return Number(line.qty_done)<Number(line.qty_plan)?'数量不足':'数量超出'}
function locateLine(value){const code=String(value||reviewCode.value||'').trim().toUpperCase();const line=lines.value.find((item)=>[item.product_code,item.product_sku,item.unit_barcode,item.carton_barcode,item.gtin].some((candidate)=>String(candidate||'').trim().toUpperCase()===code));if(!line){uni.showToast({title:'条码不属于当前复核任务',icon:'none'});return}scannedLineId.value=line.id;uni.showToast({title:`已定位 ${line.product_name}`,icon:'success'})}
async function loadData(id){const [head,result]=await Promise.all([api.pickTaskDetail(id),api.pickTaskLines(id)]);task.value=head;lines.value=Array.isArray(result)?result:(result?.results||[]);if(!(task.value.status==='COMPLETED'&&task.value.review_status==='PENDING'))uni.showToast({title:'任务当前不在待复核状态',icon:'none'})}
async function approveAndPost(){if(!taskId.value)return;const continuePost=abnormalCount.value===0||await new Promise((resolve)=>uni.showModal({title:'存在异常行',content:`共有 ${abnormalCount.value} 行数量异常，仍要复核通过？`,success:(res)=>resolve(res.confirm),fail:()=>resolve(false)}));if(!continuePost)return;posting.value=true;try{const result=await api.postPickTask(taskId.value);uni.showToast({title:result?.message||'复核通过，已过账',icon:'success'});setTimeout(()=>uni.navigateBack(),700)}catch(error){uni.showToast({title:error?.message||'复核失败',icon:'none'})}finally{posting.value=false}}
onLoad(async options=>{taskId.value=Number(options.task_id);await loadData(taskId.value)})
</script>

<style scoped>
.review-page{padding-bottom:160rpx}.header-card{padding-bottom:24rpx;border-bottom:1rpx solid #e2e8f0}.header-card text{display:block;margin-top:7rpx;color:#64748b;font-size:23rpx}.header-card .task-no{margin:0;color:#0f172a;font-size:34rpx;font-weight:750}.scan-panel{margin-top:24rpx}.scan-panel>text{display:block;margin-bottom:12rpx;font-size:27rpx;font-weight:650}.line-card{margin-top:18rpx;padding:22rpx;border:1rpx solid #e2e8f0;border-radius:20rpx}.line-card.scanned{border:2rpx solid #1677ff;background:#f8fbff}.line-card.abnormal{border-left:6rpx solid #e77800}.line-head{display:flex;align-items:center;justify-content:space-between;gap:16rpx}.line-head text:first-child{font-size:28rpx;font-weight:680}.line-head text:last-child{color:#0f766e;font-size:22rpx}.line-code{display:block;margin-top:6rpx;color:#64748b;font-size:22rpx}.line-qty{display:flex;align-items:center;justify-content:space-between;margin-top:16rpx;font-size:24rpx}.warning{display:block;margin-top:12rpx;color:#e77800;font-size:23rpx}.footer{position:fixed;left:0;right:0;bottom:0;min-height:132rpx;padding:18rpx 28rpx;border-top:1rpx solid #e2e8f0;background:#fff;display:flex;align-items:center;gap:24rpx}.footer>text{flex:1;color:#64748b;font-size:23rpx}.footer button{width:250rpx;height:84rpx;border-radius:16rpx;background:#0f766e;color:#fff;display:flex;align-items:center;justify-content:center;font-size:28rpx;font-weight:700}
</style>
