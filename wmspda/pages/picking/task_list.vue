<template>
  <view class="page">
    <view class="header">拣货任务列表</view>

    <view v-if="loading" class="hint">加载中...</view>
    <view v-else-if="rows.length === 0" class="hint">暂无可拣货任务</view>

    <view v-else class="list">
      <view v-for="t in rows" :key="t.id" class="card" @click="openTask(t)">
        <view class="row-first">
          <text class="task-no">任务号：{{ t.task_no }}</text>
          <text class="status">{{ t.status }}</text>
        </view>
        <view class="row-second">
          <text>货主：{{ t.owner_name }}</text>
        </view>
        <view class="row-second">
          <text>仓库：{{ t.warehouse_name }}</text>
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
    console.log("pickTasks")
	const res: any = await api.pickTasks()
	
    // DRF list: { count, next, previous, results } 或直接数组
    rows.value = Array.isArray(res) ? res : (res.results || [])
	console.log("pickTasks rows.value",rows.value)
  } catch (e) {
    console.error(e)
    uni.showToast({ title: '加载拣货任务失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function openTask(t: any) {
  uni.navigateTo({ url: `/pages/picking/task_detail?task_id=${t.id}` })
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
  margin-top: 40rpx;
  text-align: center;
  color: #64748b;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.card {
  background: #fff;
  border-radius: 16rpx;
  padding: 16rpx;
  box-shadow: 0 2rpx 6rpx rgba(15, 23, 42, 0.08);
}
.row-first {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8rpx;
}
.row-second {
  font-size: 24rpx;
  color: #475569;
}
.task-no {
  font-weight: 600;
}
.status {
  font-size: 24rpx;
  color: #0f766e;
}
</style>
