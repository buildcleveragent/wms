<template>
  <view class="page">
    <view class="header">待复核拣货任务</view>

    <view v-if="loading" class="hint">加载中...</view>
    <view v-else-if="rows.length === 0" class="hint">暂无可复核任务</view>

    <view v-else class="list">
      <view v-for="t in rows" :key="t.id" class="card" @click="openTask(t)">
        <view class="row-first">
          <text class="task-no">任务号：{{ t.task_no }}</text>
          <text class="status">{{ t.review_status }}</text>
        </view>
        <view class="row-second">
          <text>货主：{{ t.owner_name }}</text>
        </view>
        <view class="row-second">
          <text>来源单号：{{ t.ref_no || '-' }}</text>
        </view>
        <view class="row-second">
          <text>拣货人：{{ t.executor_name || t.picked_by_name || '-' }}</text>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const loading = ref(false)
const rows = ref<any[]>([])

async function fetchTasks() {
  loading.value = true
  try {
    const res: any = await api.reviewPickTasks()
    // DRF list: { count, next, previous, results } 或直接数组
    rows.value = Array.isArray(res) ? res : (res.results || [])
  } catch (e) {
    console.error(e)
    uni.showToast({ title: '加载复核任务失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function openTask(t: any) {
  // 进入你写的复核详情页，约定用 task_id 传参
  uni.navigateTo({ url: `/pages/review/pick_task?task_id=${t.id}` })
}

onLoad(() => {
  fetchTasks()
})
</script>

<style scoped>
.page {
  padding: 16rpx;
}
.header {
  font-size: 32rpx;
  font-weight: 600;
  margin-bottom: 16rpx;
}
.hint {
  margin-top: 80rpx;
  text-align: center;
  color: #6b7280;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.card {
  background: #ffffff;
  border-radius: 12rpx;
  padding: 16rpx;
  box-shadow: 0 2rpx 6rpx rgba(0, 0, 0, 0.04);
}
.row-first {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8rpx;
}
.row-second {
  font-size: 24rpx;
  color: #475569;
  margin-top: 4rpx;
}
.task-no {
  font-weight: 600;
}
.status {
  font-size: 24rpx;
  color: #0f766e;
}
</style>
