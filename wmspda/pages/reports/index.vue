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
      <view class="filter-row owner-filter-row">
        <view class="filter-label">货主</view>
        <picker class="owner-picker" :range="ownerPickerOptions" range-key="label" :value="ownerPickerIndex" @change="onOwnerChange">
          <view class="picker-value owner-picker-value">{{ ownerPickerOptions[ownerPickerIndex]?.label || '全部货主' }}</view>
        </picker>
      </view>

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
      <view class="summary-card inbound clickable" @click="openMetricDetail('inbound')">
        <view class="card-label">收货数量</view>
        <view class="card-value">{{ summary.inbound_qty }}</view>
        <view class="card-meta">{{ summary.inbound_orders }} 单 / {{ summary.inbound_lines }} 行</view>
      </view>
      <view class="summary-card outbound clickable" @click="openMetricDetail('outbound')">
        <view class="card-label">出货数量</view>
        <view class="card-value">{{ summary.outbound_qty }}</view>
        <view class="card-meta">{{ summary.outbound_orders }} 单 / {{ summary.outbound_lines }} 行</view>
      </view>
    </view>

    <template v-if="visibleOwnerRows.length">
      <view class="section-title">货主明细</view>
      <view class="owner-list">
        <view v-for="row in visibleOwnerRows" :key="row.owner" class="owner-row clickable" @click="openOwnerDetail(row)">
          <view class="owner-main">
            <view class="owner-name">{{ row.owner_name || `货主 #${row.owner}` }}</view>
            <view class="owner-sub">{{ row.inbound_orders }} 收货单 / {{ row.outbound_orders }} 出货单</view>
          </view>
          <view class="owner-stats">
            <view class="owner-stat-line">
              <text class="stat-name in">收</text>
              <text class="owner-stat-main">{{ row.inbound_qty }}</text>
              <text class="stat-sub">{{ row.inbound_lines }} 行</text>
            </view>
            <view class="owner-stat-line">
              <text class="stat-name out">出</text>
              <text class="owner-stat-main">{{ row.outbound_qty }}</text>
              <text class="stat-sub">{{ row.outbound_lines }} 行</text>
            </view>
          </view>
        </view>
      </view>
    </template>

    <view class="section-title">每日明细</view>
    <view v-if="loading" class="empty">加载中...</view>
    <view v-else-if="visibleDays.length === 0" class="empty">当前时间段暂无收货或出货数据</view>
    <view v-else class="day-list">
      <view v-for="day in visibleDays" :key="day.date" class="day-row clickable" @click="openDayDetail(day)">
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
const selectedOwnerId = ref('')
const summary = ref({
  inbound_orders: 0,
  inbound_lines: 0,
  inbound_qty: '0.000',
  outbound_orders: 0,
  outbound_lines: 0,
  outbound_qty: '0.000',
})
const ownerOptions = ref([])
const byOwner = ref([])
const days = ref([])
let loaded = false

const ownerPickerOptions = computed(() => {
  const rows = ownerOptions.value.map((item) => ({
    id: String(item.id),
    label: item.name || `货主 #${item.id}`,
  }))
  return [{ id: '', label: '全部货主' }, ...rows]
})

const ownerPickerIndex = computed(() => {
  const index = ownerPickerOptions.value.findIndex((item) => item.id === String(selectedOwnerId.value || ''))
  return index >= 0 ? index : 0
})

const selectedOwnerLabel = computed(() => ownerPickerOptions.value[ownerPickerIndex.value]?.label || '全部货主')

const periodText = computed(() => {
  const period = mode.value === 'month' ? `${month.value} 月度收发货` : `${startDate.value} 至 ${endDate.value}`
  return selectedOwnerId.value ? `${selectedOwnerLabel.value} · ${period}` : period
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

const visibleOwnerRows = computed(() =>
  byOwner.value.filter(
    (row) =>
      Number(row.inbound_orders) ||
      Number(row.inbound_lines) ||
      Number(row.outbound_orders) ||
      Number(row.outbound_lines)
  )
)

function buildParams() {
  const owner = selectedOwnerId.value || ''
  if (mode.value === 'month') {
    return { mode: 'month', month: month.value, owner }
  }
  return { mode: 'range', start_date: startDate.value, end_date: endDate.value, owner }
}

function buildQuery(params = {}) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
}

function openDetail(params = {}) {
  const qs = buildQuery({
    ...buildParams(),
    metric: 'all',
    ...params,
  })
  uni.navigateTo({ url: `/pages/reports/detail?${qs}` })
}

function openMetricDetail(metric) {
  openDetail({ metric })
}

function openOwnerDetail(row) {
  openDetail({ owner: row.owner || '', metric: 'all' })
}

function openDayDetail(day) {
  openDetail({
    mode: 'range',
    start_date: day.date,
    end_date: day.date,
    metric: 'all',
  })
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

function onOwnerChange(e) {
  const next = ownerPickerOptions.value[Number(e.detail.value) || 0]
  selectedOwnerId.value = next?.id || ''
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
    ownerOptions.value = res.owner_options || []
    byOwner.value = res.by_owner || []
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

.owner-filter-row {
  justify-content: flex-start;
  gap: 32rpx;
}

.owner-filter-row .filter-label {
  width: 84rpx;
  flex-shrink: 0;
}

.owner-picker {
  width: 520rpx;
  max-width: calc(100% - 116rpx);
}

.picker-value {
  min-width: 190rpx;
  color: #111827;
  font-size: 28rpx;
  text-align: right;
}

.owner-picker-value {
  width: 100%;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.clickable:active {
  opacity: 0.72;
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

.owner-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
  margin-bottom: 26rpx;
}

.owner-row {
  display: flex;
  gap: 18rpx;
  padding: 20rpx;
  border-radius: 8rpx;
  background: #fff;
}

.owner-main {
  flex: 1;
  min-width: 0;
}

.owner-name {
  color: #111827;
  font-size: 28rpx;
  font-weight: 700;
  line-height: 40rpx;
}

.owner-sub {
  margin-top: 8rpx;
  color: #6b7280;
  font-size: 24rpx;
}

.owner-stats {
  width: 300rpx;
  flex-shrink: 0;
}

.owner-stat-line {
  display: flex;
  align-items: center;
  min-height: 44rpx;
}

.owner-stat-main {
  min-width: 116rpx;
  color: #111827;
  font-size: 28rpx;
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
