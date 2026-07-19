<template>
  <view class="history-panel">
    <view class="panel-head">
      <view>
        <view class="panel-title">今日代办出库</view>
        <view class="panel-subtitle">{{ today }}</view>
      </view>
      <button class="small-button" :disabled="loading" @click="refresh(true)">
        {{ loading ? '刷新中' : '刷新' }}
      </button>
    </view>

    <view class="summary-grid">
      <view class="summary-card primary">
        <text class="summary-label">代办单</text>
        <text class="summary-value">{{ summary.order_count || 0 }}</text>
      </view>
      <view class="summary-card success">
        <text class="summary-label">已完成</text>
        <text class="summary-value">{{ summary.completed_count || 0 }}</text>
      </view>
      <view class="summary-card warning">
        <text class="summary-label">待处理</text>
        <text class="summary-value">{{ summary.pending_count || 0 }}</text>
      </view>
      <view class="summary-card danger">
        <text class="summary-label">异常</text>
        <text class="summary-value">{{ summary.exception_count || 0 }}</text>
      </view>
    </view>
    <view class="workload-line">
      商品行 {{ summary.line_count || 0 }}，基本数量 {{ qtyText(summary.total_base_qty) }}
    </view>

    <view class="section-head recent-head">
      <view class="panel-title">最近出库单</view>
      <text class="recent-count">最近 {{ history.length }} 张</text>
    </view>

    <view v-if="errorMessage" class="error-card">
      <text>{{ errorMessage }}</text>
      <button class="retry-button" @click="refresh(true)">重试</button>
    </view>
    <view v-else-if="loading && !history.length" class="empty">正在读取最近出库单…</view>
    <view v-else-if="!history.length" class="empty">暂无代办出库记录</view>
    <view v-else class="history-list">
      <view v-for="row in history" :key="row.id" class="history-row">
        <text class="order-no" :title="row.order_no">{{ row.order_no }}</text>
        <text
          class="history-meta"
          :title="`${row.owner?.name || '-'} · ${row.receiver_name || row.customer?.name || '-'}`"
        >
          {{ row.owner?.name || '-' }} · {{ row.receiver_name || row.customer?.name || '-' }}
        </text>
        <text
          class="history-meta"
          :title="`${dateTimeText(row.assisted_at)} · ${row.line_count || 0} 种 · 基本数量 ${qtyText(row.total_base_qty)}`"
        >
          {{ dateTimeText(row.assisted_at) }} · {{ row.line_count || 0 }} 种 · 基本数量
          {{ qtyText(row.total_base_qty) }}
        </text>
        <text :class="['status-tag', statusClass(row.business_status)]">
          {{ row.business_status_label }}
        </text>
        <view class="history-actions">
          <button
            class="action-button"
            :disabled="!row.task?.id"
            @click="openTask(row)"
          >
            查看任务
          </button>
          <button
            :class="[
              'action-button',
              'print-button',
              { 'is-unavailable': !row.can_reprint || !row.task?.id },
            ]"
            :aria-disabled="!row.can_reprint || !row.task?.id"
            @click="reprint(row)"
          >
            重打出库单
          </button>
        </view>
      </view>
    </view>

    <view class="navigation-row">
      <button class="navigation-button" @click="openHistory">历史出库单</button>
      <button class="navigation-button" @click="openStats">出库统计</button>
    </view>
  </view>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { openOutboundPrintPage } from '@/utils/outboundPrint'

const emit = defineEmits(['authorization-denied'])
const auth = useAuth()
const loading = ref(false)
const history = ref([])
const stats = ref({ summary: {} })
const errorMessage = ref('')
let requestSequence = 0

function localDateText(date = new Date()) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const today = localDateText()
const summary = computed(() => stats.value?.summary || {})

function normalizeRows(result) {
  if (Array.isArray(result)) return result
  return Array.isArray(result?.results) ? result.results : []
}

function qtyText(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? String(Number(number.toFixed(3))) : '0'
}

function dateTimeText(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').slice(0, 16)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hour}:${minute}`
}

function statusClass(value) {
  if (value === 'COMPLETED') return 'completed'
  if (['POSTING_FAILED', 'NEED_RECOUNT', 'INCONSISTENT'].includes(value)) return 'exception'
  if (value === 'CANCELLED') return 'cancelled'
  return 'pending'
}

function handleError(error) {
  if (Number(error?.statusCode || error?.code) === 403) {
    auth.invalidateAssistedCapability()
    emit('authorization-denied')
    return
  }
  errorMessage.value = error?.message || '最近出库单加载失败'
}

async function refresh(force = false) {
  if (loading.value && !force) return
  const sequence = ++requestSequence
  loading.value = true
  errorMessage.value = ''
  const [historyResult, statsResult] = await Promise.allSettled([
    api.assistedOutboundHistory({ page: 1, page_size: 5 }),
    api.assistedOutboundStats({ start_date: today, end_date: today, top_n: 5 }),
  ])
  if (sequence !== requestSequence) return
  if (historyResult.status === 'fulfilled') {
    history.value = normalizeRows(historyResult.value)
  } else {
    handleError(historyResult.reason)
  }
  if (statsResult.status === 'fulfilled') {
    stats.value = statsResult.value || { summary: {} }
  } else if (!errorMessage.value) {
    handleError(statsResult.reason)
  }
  if (sequence === requestSequence) loading.value = false
}

function reprint(row) {
  if (!row?.can_reprint || !row?.task?.id) {
    uni.showToast({ title: row?.reprint_unavailable_reason || '该出库单暂时不能打印', icon: 'none' })
    return
  }
  openOutboundPrintPage(row.task.id)
}

function openTask(row) {
  if (!row?.task?.id) return
  uni.navigateTo({ url: `/pages/picking/task_detail?task_id=${row.task.id}` })
}

function openHistory() {
  uni.navigateTo({ url: '/pages/outbound/assisted_history' })
}

function openStats() {
  uni.navigateTo({ url: '/pages/outbound/assisted_stats' })
}

defineExpose({ refresh })
onMounted(() => refresh())
</script>

<style scoped>
.history-panel { margin-top: 18rpx; padding: 18rpx; border-radius: 16rpx; background: #fff; box-shadow: 0 4rpx 16rpx rgba(0,0,0,.04); }
.panel-head, .section-head, .navigation-row { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; }
.panel-title { font-size: 28rpx; font-weight: 700; }
.panel-subtitle, .recent-count, .history-meta, .workload-line { color: #697386; font-size: 21rpx; }
.small-button, .retry-button, .action-button, .navigation-button { margin: 0; padding: 0 16rpx; height: 54rpx; line-height: 54rpx; font-size: 21rpx; }
.small-button, .action-button, .navigation-button { color: #1677ff; background: #eef5ff; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10rpx; margin-top: 16rpx; }
.summary-card { min-width: 0; padding: 12rpx 8rpx; border-radius: 10rpx; text-align: center; background: #f1f5f9; }
.summary-card.primary { background: #eaf3ff; }
.summary-card.success { background: #edf9f0; }
.summary-card.warning { background: #fff7e6; }
.summary-card.danger { background: #fff1f0; }
.summary-label { display: block; color: #64748b; font-size: 19rpx; white-space: nowrap; }
.summary-value { display: block; margin-top: 5rpx; font-size: 31rpx; font-weight: 700; }
.workload-line { margin-top: 10rpx; text-align: right; }
.recent-head { margin-top: 24rpx; padding-bottom: 10rpx; border-bottom: 1rpx solid #e5e7eb; }
.history-row { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, .9fr) minmax(0, 1.2fr) auto auto; align-items: center; gap: 10rpx; min-width: 0; padding: 8rpx 0; border-bottom: 1rpx solid #edf0f4; white-space: nowrap; }
.order-no { overflow: hidden; min-width: 0; font-size: 23rpx; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.status-tag { padding: 4rpx 10rpx; border-radius: 999rpx; font-size: 19rpx; white-space: nowrap; }
.status-tag.completed { color: #237804; background: #f0f9e8; }
.status-tag.pending { color: #ad6800; background: #fff7e6; }
.status-tag.exception, .status-tag.cancelled { color: #b42318; background: #fff1f0; }
.history-meta { overflow: hidden; min-width: 0; text-overflow: ellipsis; white-space: nowrap; }
.history-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8rpx; white-space: nowrap; }
.print-button { color: #fff; background: #1677ff; }
.action-button[disabled] { opacity: .45; }
.print-button.is-unavailable { color: #8a94a6; background: #e5e7eb; opacity: .65; }
.navigation-row { margin-top: 18rpx; }
.navigation-button { flex: 1; }
.empty { padding: 30rpx 0; color: #8a94a6; text-align: center; }
.error-card { margin-top: 12rpx; padding: 16rpx; border-radius: 10rpx; color: #b42318; background: #fff1f0; }
.retry-button { margin-top: 10rpx; color: #b42318; background: #fff; }
</style>
