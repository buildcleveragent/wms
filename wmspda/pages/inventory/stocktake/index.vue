<template>
  <view class="page">
    <view class="tabs">
      <button :class="['tab', { active: mode === 'work' }]" @click="setMode('work')">盘点任务</button>
      <button :class="['tab', { active: mode === 'review' }]" @click="setMode('review')">待审核</button>
    </view>
    <view v-if="loading" class="empty">加载中...</view>
    <view v-else-if="!rows.length" class="empty">暂无盘点任务</view>
    <view v-else class="list">
      <view v-for="task in rows" :key="task.id" class="card" @click="openTask(task)">
        <view class="title-row">
          <text class="task-no">{{ task.task_no }}</text>
          <text class="status">{{ statusText(task) }}</text>
        </view>
        <view class="meta">{{ task.owner_name }} · {{ task.warehouse_name }}</view>
        <view class="meta">第 {{ task.round_no || 1 }} 轮 · {{ task.blind ? '盲盘' : '明盘' }}</view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const rows = ref<any[]>([])
const loading = ref(false)
const mode = ref<'work' | 'review'>('work')

async function load() {
  loading.value = true
  try {
    const params = mode.value === 'review' ? { status: 'COMPLETED' } : {}
    const result: any = await api.countTasks(params)
    const list = Array.isArray(result) ? result : (result?.results || [])
    rows.value = mode.value === 'review'
      ? list.filter((item: any) => item.review_status === 'PENDING')
      : list.filter((item: any) => ['RELEASED', 'IN_PROGRESS'].includes(item.status))
  } catch (error) {
    console.error(error)
    uni.showToast({ title: '加载盘点任务失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function setMode(value: 'work' | 'review') {
  mode.value = value
  load()
}

function openTask(task: any) {
  uni.navigateTo({ url: `/pages/inventory/stocktake/detail?task_id=${task.id}` })
}

function statusText(task: any) {
  if (task.review_status === 'PENDING') return '待审核'
  return task.status === 'IN_PROGRESS' ? '盘点中' : '待认领'
}

onLoad((query: any) => {
  if (query?.mode === 'review') mode.value = 'review'
})
onShow(load)
</script>

<style scoped>
.page { padding: 24rpx; background: #f5f7fa; min-height: 100vh; }
.tabs { display: flex; gap: 16rpx; margin-bottom: 24rpx; }
.tab { flex: 1; font-size: 28rpx; background: #fff; }
.tab.active { color: #fff; background: #2563eb; }
.list { display: flex; flex-direction: column; gap: 18rpx; }
.card { padding: 24rpx; border-radius: 16rpx; background: #fff; box-shadow: 0 2rpx 8rpx rgba(15,23,42,.08); }
.title-row { display: flex; justify-content: space-between; margin-bottom: 12rpx; }
.task-no { font-weight: 700; }
.status { color: #2563eb; }
.meta { color: #64748b; font-size: 24rpx; margin-top: 6rpx; }
.empty { padding: 80rpx 0; text-align: center; color: #94a3b8; }
</style>
