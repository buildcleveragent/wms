<template>
  <view class="page">
    <view class="heading"><text class="title">盘点任务</text><button v-if="manager" size="mini" type="primary" @click="create">新建盘点</button></view>
    <view class="tabs"><button v-for="tab in tabs" :key="tab.key" :class="{ active: mode === tab.key }" @click="setMode(tab.key)">{{ tab.label }}</button></view>
    <view v-if="error" class="error">{{ error }}<button size="mini" @click="refresh">重试</button></view>
    <view v-if="loading" class="empty">加载中…</view>
    <view v-else-if="!rows.length && !error" class="empty">暂无{{ tabs.find(t => t.key === mode)?.label || '盘点任务' }}</view>
    <view v-for="task in rows" :key="task.id" class="card" @click="open(task)">
      <view class="heading"><text class="task-no">{{ task.task_no }}</text><text :class="['status', { failed: task.posting_status === 'FAILED' }]">{{ countStatus(task) }}</text></view>
      <view class="meta">{{ task.owner_name }} · {{ task.warehouse_name }}</view>
      <view class="meta">第 {{ task.round_no || 1 }} 轮 · {{ task.blind ? '盲盘' : '明盘' }}</view>
    </view>
  </view>
</template>
<script setup>
import { computed, ref } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useCountSession, countStages, countStatus } from '@/utils/counting'
const auth = useAuth(), mode = ref('work')
const session = useCountSession({ api, auth })
const { rows, error, loading, manager } = session
const tabs = computed(() => countStages.filter(tab => manager.value || tab.key === 'work'))
async function refresh() { await session.load({ stage: mode.value, refreshAuth: true }) }
function setMode(value) { mode.value = value; session.load({ stage: value }) }
function open(task) { uni.navigateTo({ url: `/pages/inventory/stocktake/detail?task_id=${task.id}` }) }
function create() { uni.navigateTo({ url: '/pages/inventory/stocktake/create' }) }
onLoad(query => { if (countStages.some(tab => tab.key === query?.mode)) mode.value = query.mode })
onShow(refresh)
onHide(session.invalidate)
onUnload(session.dispose)
</script>
<style scoped>
.page{padding:24rpx;background:#f5f7fa;min-height:100vh;box-sizing:border-box}.heading{display:flex;align-items:center;justify-content:space-between;gap:16rpx}.heading button{margin:0}.title{font-size:36rpx;font-weight:700}.tabs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12rpx;margin:24rpx 0}.tabs button{width:100%;font-size:26rpx;background:white}.tabs .active{background:#2563eb;color:white}.card{background:white;padding:24rpx;border-radius:16rpx;margin-bottom:18rpx}.task-no{font-weight:700;overflow-wrap:anywhere}.status{color:#2563eb;font-size:24rpx;flex-shrink:0}.failed,.error{color:#b91c1c}.meta{color:#64748b;font-size:24rpx;margin-top:10rpx}.empty{padding:80rpx 0;text-align:center;color:#64748b}.error{padding:20rpx;background:#fff1f2;border-radius:12rpx;margin-bottom:18rpx}
</style>
