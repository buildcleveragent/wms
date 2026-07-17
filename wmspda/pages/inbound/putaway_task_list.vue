<template>
  <view class="page">
    <view class="intro">
      <text class="intro-title">上架任务</text>
      <text class="intro-copy">领取任务后，查询目标库位并按实际数量完成上架。</text>
    </view>

    <view class="search-row">
      <input
        v-model="search"
        class="search-input"
        placeholder="任务号、来源单号或商品"
        confirm-type="search"
        @confirm="searchTasks"
      />
      <button class="search-button" size="mini" @click="searchTasks">查询</button>
    </view>

    <view v-if="loading && !rows.length" class="state">正在加载上架任务…</view>
    <view v-else-if="!rows.length" class="state">暂无可见的上架任务</view>

    <view v-else class="task-list">
      <view v-for="task in rows" :key="task.id" class="task-card" @click="openTask(task)">
        <view class="card-head">
          <view>
            <text class="task-no">{{ task.task_no }}</text>
            <text class="ref-no">来源：{{ task.ref_no || '-' }}</text>
          </view>
          <text :class="['status', statusClass(task.status)]">{{ task.status_name || task.status }}</text>
        </view>

        <view class="meta">货主：{{ task.owner_name || '-' }}</view>
        <view class="meta">仓库：{{ task.warehouse_name || '-' }}</view>
        <view class="line-summary">
          {{ task.lines?.length || 0 }} 行 · 已上架 {{ putawayQty(task) }} / 计划 {{ plannedQty(task) }}
        </view>

        <view class="actions">
          <button
            v-if="task.can_claim"
            class="secondary"
            size="mini"
            :loading="busyTaskId === task.id"
            @click.stop="claimTask(task)"
          >
            领取
          </button>
          <button
            v-if="task.can_start"
            class="primary"
            size="mini"
            :loading="busyTaskId === task.id"
            @click.stop="startTask(task)"
          >
            开始上架
          </button>
          <button class="link" size="mini" @click.stop="openTask(task)">查看作业</button>
        </view>
      </view>
    </view>

    <button v-if="hasMore" class="more" :loading="loading" @click="loadTasks(false)">
      加载更多
    </button>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const rows = ref([])
const search = ref('')
const loading = ref(false)
const busyTaskId = ref(null)
const page = ref(1)
const hasMore = ref(false)

function asRows(response) {
  return Array.isArray(response) ? response : (response?.results || [])
}

function toNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number : 0
}

function formatQty(value) {
  return String(Number(toNumber(value).toFixed(3)))
}

function plannedQty(task) {
  return formatQty((task.lines || []).reduce((sum, line) => sum + toNumber(line.qty_plan), 0))
}

function putawayQty(task) {
  return formatQty((task.lines || []).reduce((sum, line) => sum + toNumber(line.qty_done), 0))
}

function statusClass(status) {
  if (status === 'COMPLETED') return 'status-done'
  if (status === 'IN_PROGRESS') return 'status-progress'
  return 'status-ready'
}

async function loadTasks(reset = true) {
  if (loading.value) return
  if (reset) {
    page.value = 1
    hasMore.value = false
  }
  loading.value = true
  try {
    const response = await api.inboundPdaTasks({
      task_type: 'PUTAWAY',
      search: search.value.trim(),
      page: page.value,
    })
    const incoming = asRows(response)
    rows.value = reset ? incoming : rows.value.concat(incoming)
    hasMore.value = Boolean(response?.next)
    if (hasMore.value) page.value += 1
  } catch (error) {
    console.error('加载上架任务失败', error)
  } finally {
    loading.value = false
  }
}

function searchTasks() {
  loadTasks(true)
}

function replaceTask(task) {
  const index = rows.value.findIndex((item) => item.id === task.id)
  if (index >= 0) rows.value.splice(index, 1, task)
}

async function claimTask(task) {
  if (busyTaskId.value) return
  busyTaskId.value = task.id
  try {
    const updated = await api.claimInboundPdaTask(task.id)
    replaceTask(updated)
    uni.showToast({ title: '任务已领取', icon: 'success' })
  } catch (error) {
    console.error('领取上架任务失败', error)
  } finally {
    busyTaskId.value = null
  }
}

async function startTask(task) {
  if (busyTaskId.value) return
  busyTaskId.value = task.id
  try {
    const updated = await api.startInboundPdaTask(task.id)
    replaceTask(updated)
    uni.showToast({ title: '已开始上架', icon: 'success' })
  } catch (error) {
    console.error('开始上架失败', error)
  } finally {
    busyTaskId.value = null
  }
}

function openTask(task) {
  uni.navigateTo({ url: `/pages/inbound/putaway_task_detail?task_id=${task.id}` })
}

onShow(() => loadTasks(true))
</script>

<style scoped>
.page { min-height: 100vh; padding: 24rpx; background: #f6f8fb; box-sizing: border-box; }
.intro { margin: 8rpx 0 24rpx; }
.intro-title { display: block; color: #172033; font-size: 36rpx; font-weight: 700; }
.intro-copy { display: block; margin-top: 8rpx; color: #65758b; font-size: 24rpx; }
.search-row { display: flex; gap: 16rpx; align-items: center; margin-bottom: 24rpx; }
.search-input { flex: 1; height: 72rpx; padding: 0 20rpx; border: 1rpx solid #d7dfeb; border-radius: 12rpx; background: #fff; font-size: 27rpx; }
.search-button { margin: 0; background: #e9f2ff; color: #1463c3; }
.state { padding: 96rpx 20rpx; color: #748197; text-align: center; font-size: 28rpx; }
.task-list { display: flex; flex-direction: column; gap: 18rpx; }
.task-card { padding: 24rpx; border-radius: 16rpx; background: #fff; box-shadow: 0 4rpx 18rpx rgba(15, 23, 42, .06); }
.card-head { display: flex; justify-content: space-between; gap: 16rpx; align-items: flex-start; }
.task-no { display: block; color: #172033; font-size: 29rpx; font-weight: 650; }
.ref-no { display: block; margin-top: 7rpx; color: #718096; font-size: 23rpx; }
.status { flex-shrink: 0; padding: 6rpx 12rpx; border-radius: 999rpx; font-size: 22rpx; }
.status-ready { color: #9a6700; background: #fff6d8; }
.status-progress { color: #0c6b54; background: #dcf8ed; }
.status-done { color: #475569; background: #edf1f5; }
.meta { margin-top: 12rpx; color: #536274; font-size: 25rpx; }
.line-summary { margin-top: 14rpx; color: #1d4f91; font-size: 24rpx; }
.actions { display: flex; justify-content: flex-end; gap: 14rpx; margin-top: 20rpx; }
.actions button { margin: 0; }
.primary { color: #fff; background: #1d70d6; }
.secondary { color: #175db7; background: #e7f0ff; }
.link { color: #475569; background: #f2f4f7; }
.more { margin: 28rpx 0 18rpx; color: #386fa8; background: #fff; }
</style>
