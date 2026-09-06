<template>
  <view class="page">
    <view class="hero between">
      <view><text class="title">移库任务</text><text class="meta">库内库存层与容器移动</text></view>
      <button v-if="canRequest" size="mini" @click="openRequest">{{auth.canManageRelocation?'移库管理':'申请移库'}}</button>
    </view>
    <view class="search-row"><input v-model="search" class="input" placeholder="任务号或商品" @confirm="load"/><button size="mini" @click="load">查询</button></view>
    <view v-if="loading" class="state">正在加载…</view>
    <view v-else-if="!tasks.length" class="state">暂无可执行移库任务</view>
    <view v-for="task in tasks" :key="task.id" class="card" @click="open(task)">
      <view class="between"><text class="task-no">{{ task.task_no }}</text><text class="badge">{{ task.status }}</text></view>
      <text class="meta">{{ task.owner_name }} · {{ task.warehouse_name }}</text>
      <text class="meta">{{ task.lines.length }} 行 · {{ task.execution_state }}</text>
      <view class="actions">
        <button v-if="task.can_claim" size="mini" @click.stop="claim(task)">领取</button>
        <button v-if="task.can_start" type="primary" size="mini" @click.stop="start(task)">开始</button>
        <button size="mini" @click.stop="open(task)">查看</button>
      </view>
    </view>
  </view>
</template>
<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
const auth=useAuth(), tasks=ref([]), search=ref(''), loading=ref(false)
const canRequest=computed(()=>auth.canRequestRelocation||auth.canManageRelocation)
const canLoadTasks=computed(()=>auth.canExecuteRelocation||auth.canManageRelocationTasks)
const rows=v=>Array.isArray(v)?v:(v?.results||[])
async function load(){if(!canLoadTasks.value){tasks.value=[];return}loading.value=true;try{tasks.value=rows(await api.relocationTasks({search:search.value}))}finally{loading.value=false}}
async function claim(task){await api.claimRelocationTask(task.id);await load()}
async function start(task){await api.startRelocationTask(task.id);open(task)}
function open(task){uni.navigateTo({url:`/pages/inventory/move/detail?task_id=${task.id}`})}
function openRequest(){uni.navigateTo({url:'/pages/inventory/move/request'})}
onShow(async()=>{await auth.loadProfile({force:true});await load()})
</script>
<style scoped>
.page{min-height:100vh;padding:24rpx;background:#f5f7fb;box-sizing:border-box}.hero{margin:8rpx 0 24rpx}.title{display:block;font-size:38rpx;font-weight:700;color:#172033}.meta{display:block;margin-top:8rpx;color:#65758b;font-size:24rpx}.search-row,.between,.actions{display:flex;align-items:center;justify-content:space-between;gap:14rpx}.input{flex:1;height:72rpx;padding:0 20rpx;background:#fff;border-radius:12rpx}.card{margin-top:18rpx;padding:24rpx;background:#fff;border-radius:16rpx}.task-no{font-size:29rpx;font-weight:650}.badge{padding:6rpx 12rpx;border-radius:999rpx;background:#e8f2ff;color:#1762b8;font-size:22rpx}.actions{justify-content:flex-end;margin-top:20rpx}.actions button{margin:0}.state{padding:100rpx 0;text-align:center;color:#748197}
</style>
