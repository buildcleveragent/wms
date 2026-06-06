<template>
  <view class="page">
    <view class="header">
      <view>
        <view class="title">仓库统计</view>
        <view class="subtitle">{{ periodText }}</view>
      </view>
      <button class="refresh-btn" :disabled="loading" @click="loadData">刷新</button>
    </view>

    <view class="segmented">
      <view :class="['seg-item', mode === 'month' ? 'active' : '']" @click="setMode('month')">
        按月
      </view>
      <view :class="['seg-item', mode === 'range' ? 'active' : '']" @click="setMode('range')">
        时间段
      </view>
    </view>

    <view class="filter-panel">
      <view v-if="mode === 'month'" class="filter-row">
        <view class="filter-label">月份</view>
        <picker mode="date" fields="month" :value="month" @change="onMonthChange">
          <view class="picker-value">{{ month }}</view>
        </picker>
      </view>

      <view v-else>
        <view class="filter-row">
          <view class="filter-label">开始日期</view>
          <picker mode="date" :value="startDate" @change="onStartChange">
            <view class="picker-value">{{ startDate }}</view>
          </picker>
        </view>
        <view class="filter-row">
          <view class="filter-label">结束日期</view>
          <picker mode="date" :value="endDate" @change="onEndChange">
            <view class="picker-value">{{ endDate }}</view>
          </picker>
        </view>
      </view>
    </view>

    <view class="summary-grid">
      <view class="summary-card inbound">
        <view class="card-label">收货数量</view>
        <view class="card-value">{{ summary.inbound_qty }}</view>
        <view class="card-meta">{{ summary.inbound_orders }} 单 / {{ summary.inbound_lines }} 行</view>
      </view>
      <view class="summary-card outbound">
        <view class="card-label">出货数量</view>
        <view class="card-value">{{ summary.outbound_qty }}</view>
        <view class="card-meta">{{ summary.outbound_orders }} 单 / {{ summary.outbound_lines }} 行</view>
      </view>
    </view>

    <view class="section-title">每日明细</view>
    <view v-if="loading" class="empty">加载中...</view>
    <view v-else-if="visibleDays.length === 0" class="empty">当前时间段暂无收货或出货数据</view>
    <view v-else class="day-list">
      <view v-for="day in visibleDays" :key="day.date" class="day-row">
        <view class="day-date">{{ day.date }}</view>
        <view class="day-stats">
          <view class="stat-line">
            <text class="stat-name in">收</text>
            <text class="stat-main">{{ day.inbound_qty }}</text>
            <text class="stat-sub">{{ day.inbound_orders }} 单 / {{ day.inbound_lines }} 行</text>
          </view>
          <view class="stat-line">
            <text class="stat-name out">出</text>
            <text class="stat-main">{{ day.outbound_qty }}</text>
            <text class="stat-sub">{{ day.outbound_orders }} 单 / {{ day.outbound_lines }} 行</text>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { api } from '../../utils/request'

const today = new Date()
const pad = (n) => String(n).padStart(2, '0')
const fmtDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const fmtMonth = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`

const mode = ref('month')
const month = ref(fmtMonth(today))
const startDate = ref(fmtDate(new Date(today.getFullYear(), today.getMonth(), 1)))
const endDate = ref(fmtDate(today))
const loading = ref(false)
const summary = ref({
  inbound_orders: 0,
  inbound_lines: 0,
  inbound_qty: '0.000',
  outbound_orders: 0,
  outbound_lines: 0,
  outbound_qty: '0.000',
})
const days = ref([])
let loaded = false

const periodText = computed(() => {
  if (mode.value === 'month') return `${month.value} 月度收发货`
  return `${startDate.value} 至 ${endDate.value}`
})

const visibleDays = computed(() =>
  days.value.filter(
    (day) =>
      Number(day.inbound_orders) ||
      Number(day.inbound_lines) ||
      Number(day.outbound_orders) ||
      Number(day.outbound_lines)
  )
)

function buildParams() {
  if (mode.value === 'month') {
    return { mode: 'month', month: month.value }
  }
  return { mode: 'range', start_date: startDate.value, end_date: endDate.value }
}

function setMode(nextMode) {
  if (mode.value === nextMode) return
  mode.value = nextMode
  loadData()
}

function onMonthChange(e) {
  month.value = e.detail.value
  loadData()
}

function onStartChange(e) {
  startDate.value = e.detail.value
  loadData()
}

function onEndChange(e) {
  endDate.value = e.detail.value
  loadData()
}

async function loadData() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await api.pdaThroughputStats(buildParams())
    summary.value = res.summary || summary.value
    days.value = res.days || []
  } finally {
    loading.value = false
  }
}

onLoad(loadData)
onShow(() => {
  if (!loaded) {
    loaded = true
    return
  }
  loadData()
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  background: #f3f4f6;
  box-sizing: border-box;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20rpx;
}

.title {
  color: #111827;
  font-size: 38rpx;
  font-weight: 700;
  line-height: 1.35;
}

.subtitle {
  margin-top: 6rpx;
  color: #6b7280;
  font-size: 24rpx;
}

.refresh-btn {
  width: 132rpx;
  height: 64rpx;
  margin: 0;
  padding: 0;
  border-radius: 8rpx;
  background: #111827;
  color: #fff;
  font-size: 26rpx;
  line-height: 64rpx;
}

.segmented {
  display: grid;
  grid-template-columns: 1fr 1fr;
  height: 72rpx;
  padding: 6rpx;
  margin-bottom: 18rpx;
  border: 1rpx solid #d1d5db;
  border-radius: 8rpx;
  background: #fff;
  box-sizing: border-box;
}

.seg-item {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6rpx;
  color: #4b5563;
  font-size: 28rpx;
}

.seg-item.active {
  background: #2563eb;
  color: #fff;
  font-weight: 600;
}

.filter-panel {
  padding: 4rpx 20rpx;
  margin-bottom: 20rpx;
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  background: #fff;
}

.filter-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 82rpx;
  border-bottom: 1rpx solid #f3f4f6;
}

.filter-row:last-child {
  border-bottom: 0;
}

.filter-label {
  color: #374151;
  font-size: 28rpx;
}

.picker-value {
  min-width: 190rpx;
  color: #111827;
  font-size: 28rpx;
  text-align: right;
}

.summary-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16rpx;
  margin-bottom: 26rpx;
}

.summary-card {
  min-height: 180rpx;
  padding: 22rpx;
  border-radius: 8rpx;
  background: #fff;
  box-sizing: border-box;
}

.summary-card.inbound {
  border-left: 8rpx solid #16a34a;
}

.summary-card.outbound {
  border-left: 8rpx solid #dc2626;
}

.card-label {
  color: #6b7280;
  font-size: 24rpx;
}

.card-value {
  margin-top: 18rpx;
  color: #111827;
  font-size: 42rpx;
  font-weight: 700;
  line-height: 1.15;
}

.card-meta {
  margin-top: 14rpx;
  color: #4b5563;
  font-size: 24rpx;
}

.section-title {
  margin-bottom: 14rpx;
  color: #111827;
  font-size: 30rpx;
  font-weight: 700;
}

.empty {
  padding: 44rpx 20rpx;
  border-radius: 8rpx;
  background: #fff;
  color: #6b7280;
  font-size: 28rpx;
  text-align: center;
}

.day-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.day-row {
  display: flex;
  padding: 20rpx;
  border-radius: 8rpx;
  background: #fff;
}

.day-date {
  width: 180rpx;
  flex-shrink: 0;
  color: #111827;
  font-size: 26rpx;
  font-weight: 600;
  line-height: 44rpx;
}

.day-stats {
  flex: 1;
  min-width: 0;
}

.stat-line {
  display: flex;
  align-items: center;
  min-height: 44rpx;
}

.stat-name {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42rpx;
  height: 36rpx;
  margin-right: 12rpx;
  border-radius: 6rpx;
  color: #fff;
  font-size: 22rpx;
}

.stat-name.in {
  background: #16a34a;
}

.stat-name.out {
  background: #dc2626;
}

.stat-main {
  min-width: 120rpx;
  color: #111827;
  font-size: 28rpx;
  font-weight: 700;
}

.stat-sub {
  margin-left: 16rpx;
  color: #6b7280;
  font-size: 24rpx;
}
</style>
