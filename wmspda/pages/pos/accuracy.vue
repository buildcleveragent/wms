<template>
  <view class="page">
    <view class="header">
      <view class="header-main">
        <view class="title">POS数据对账</view>
        <view class="subtitle">{{ periodText }}</view>
      </view>
      <view class="header-actions">
        <button class="ghost-btn header-btn" @click="openReport">销售报表</button>
        <button class="primary-btn header-btn" :disabled="loading" @click="loadAccuracy">校验</button>
      </view>
    </view>

    <view class="filter-panel">
      <view class="filter-row">
        <text class="filter-label">开始日期</text>
        <picker mode="date" :value="startDate" @change="onStartDateChange">
          <view class="picker-value">{{ startDate }}</view>
        </picker>
      </view>
      <view class="filter-row">
        <text class="filter-label">结束日期</text>
        <picker mode="date" :value="endDate" @change="onEndDateChange">
          <view class="picker-value">{{ endDate }}</view>
        </picker>
      </view>
    </view>

    <view :class="['status-panel', statusClass]">
      <view class="status-main">
        <text class="status-label">{{ statusText }}</text>
        <text class="status-time">{{ checkedText }}</text>
      </view>
      <view class="status-count">
        <text class="status-number">{{ summary.issue_count || 0 }}</text>
        <text class="status-unit">异常</text>
      </view>
    </view>

    <view class="summary-grid">
      <view class="summary-card">
        <text class="card-label">销售单</text>
        <text class="card-value">{{ summary.sale_count || 0 }}</text>
      </view>
      <view class="summary-card">
        <text class="card-label">退货单</text>
        <text class="card-value">{{ summary.return_count || 0 }}</text>
      </view>
      <view class="summary-card">
        <text class="card-label">校验项</text>
        <text class="card-value">{{ summary.check_count || 0 }}</text>
      </view>
      <view class="summary-card">
        <text class="card-label">显示异常</text>
        <text class="card-value">{{ summary.shown_issue_count || issues.length || 0 }}</text>
      </view>
    </view>

    <view class="section">
      <view class="section-head">
        <text class="section-title">校验项目</text>
      </view>
      <view v-if="loading && !loaded" class="empty">校验中...</view>
      <view v-else-if="!checks.length" class="empty">暂无校验结果</view>
      <view v-else class="check-list">
        <view class="check-row" v-for="row in checks" :key="row.code">
          <text :class="['check-dot', row.status === 'passed' ? 'passed' : 'failed']"></text>
          <text class="check-name">{{ row.label }}</text>
          <text :class="['check-result', row.status === 'passed' ? 'passed' : 'failed']">
            {{ row.status === 'passed' ? '通过' : `${row.issue_count} 条异常` }}
          </text>
        </view>
      </view>
    </view>

    <view class="section">
      <view class="section-head">
        <text class="section-title">异常明细</text>
      </view>
      <view v-if="loading && !loaded" class="empty">校验中...</view>
      <view v-else-if="!issues.length" class="empty">暂无异常</view>
      <view v-else class="issue-list">
        <view
          class="issue-row"
          v-for="issue in issues"
          :key="issue.code + '-' + issue.object_type + '-' + issue.object_id + '-' + issue.message"
        >
          <view class="issue-title-row">
            <text class="issue-title">{{ issue.label }}</text>
            <text class="issue-object">{{ issue.object_no || issue.object_id || '-' }}</text>
          </view>
          <text class="issue-message">{{ issue.message }}</text>
          <view v-if="issue.expected || issue.actual" class="issue-values">
            <text>应为 {{ issue.expected || '-' }}</text>
            <text>实际 {{ issue.actual || '-' }}</text>
          </view>
        </view>
        <text v-if="summary.truncated" class="issue-more">
          异常较多，仅显示前 {{ summary.shown_issue_count || issues.length }} 条
        </text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const pad = (n) => String(n).padStart(2, '0')
const today = new Date()

const startDate = ref(formatDate(today))
const endDate = ref(formatDate(today))
const loading = ref(false)
const loaded = ref(false)
const accuracy = ref(defaultAccuracy())

const summary = computed(() => accuracy.value.summary || defaultAccuracy().summary)
const checks = computed(() => accuracy.value.checks || [])
const issues = computed(() => accuracy.value.issues || [])
const periodText = computed(() => `${startDate.value} 至 ${endDate.value}`)
const statusClass = computed(() => {
  if (!loaded.value) return 'unknown'
  return accuracy.value.status === 'passed' ? 'passed' : 'failed'
})
const statusText = computed(() => {
  if (loading.value) return '正在校验'
  if (!loaded.value) return '尚未校验'
  return accuracy.value.status === 'passed' ? '校验通过' : '发现异常'
})
const checkedText = computed(() => {
  if (!accuracy.value.checked_at) return ''
  return `最近校验 ${formatDateTime(accuracy.value.checked_at)}`
})

function defaultAccuracy() {
  return {
    status: 'unknown',
    checked_at: '',
    summary: {
      sale_count: 0,
      return_count: 0,
      check_count: 0,
      issue_count: 0,
      shown_issue_count: 0,
      truncated: false,
    },
    checks: [],
    issues: [],
  }
}

function formatDate(value) {
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function formatDateTime(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function onStartDateChange(event) {
  startDate.value = event.detail.value
  loadAccuracy()
}

function onEndDateChange(event) {
  endDate.value = event.detail.value
  loadAccuracy()
}

async function loadAccuracy() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await api.posAccuracy({
      start_date: startDate.value,
      end_date: endDate.value,
    })
    accuracy.value = res || defaultAccuracy()
    loaded.value = true
  } finally {
    loading.value = false
  }
}

function openReport() {
  uni.navigateTo({
    url: `/pages/pos/report?start_date=${encodeURIComponent(startDate.value)}&end_date=${encodeURIComponent(endDate.value)}`,
  })
}

onLoad((options = {}) => {
  if (options.start_date) startDate.value = String(options.start_date)
  if (options.end_date) endDate.value = String(options.end_date)
  loadAccuracy()
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
  gap: 16rpx;
  margin-bottom: 18rpx;
}

.header-main {
  min-width: 0;
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

.header-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 12rpx;
}

button {
  margin: 0;
  padding: 0;
  border-radius: 8rpx;
  line-height: 1;
}

button::after {
  border: 0;
}

.ghost-btn,
.primary-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 132rpx;
  height: 60rpx;
  border-radius: 8rpx;
  font-size: 24rpx;
  box-sizing: border-box;
}

.ghost-btn {
  border: 1rpx solid #d1d5db;
  background: #fff;
  color: #111827;
}

.primary-btn {
  background: #2563eb;
  color: #fff;
}

.filter-panel,
.section {
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  background: #fff;
}

.filter-panel {
  padding: 4rpx 20rpx;
  margin-bottom: 18rpx;
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
  min-width: 210rpx;
  color: #111827;
  font-size: 28rpx;
  text-align: right;
}

.status-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18rpx;
  min-height: 154rpx;
  padding: 24rpx;
  margin-bottom: 18rpx;
  border: 1rpx solid #e5e7eb;
  border-left: 8rpx solid #94a3b8;
  border-radius: 8rpx;
  background: #fff;
  box-sizing: border-box;
}

.status-panel.passed {
  border-left-color: #0f766e;
}

.status-panel.failed {
  border-left-color: #b42318;
}

.status-main {
  flex: 1;
  min-width: 0;
}

.status-label {
  display: block;
  color: #111827;
  font-size: 40rpx;
  font-weight: 700;
  line-height: 1.2;
}

.status-panel.passed .status-label {
  color: #0f766e;
}

.status-panel.failed .status-label {
  color: #b42318;
}

.status-time {
  display: block;
  margin-top: 10rpx;
  color: #6b7280;
  font-size: 24rpx;
}

.status-count {
  width: 142rpx;
  text-align: right;
  flex-shrink: 0;
}

.status-number {
  display: block;
  color: #111827;
  font-size: 52rpx;
  font-weight: 700;
  line-height: 1;
}

.status-unit {
  display: block;
  margin-top: 8rpx;
  color: #6b7280;
  font-size: 22rpx;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14rpx;
  margin-bottom: 18rpx;
}

.summary-card {
  min-height: 138rpx;
  padding: 20rpx;
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  background: #fff;
  box-sizing: border-box;
}

.card-label {
  display: block;
  color: #6b7280;
  font-size: 24rpx;
}

.card-value {
  display: block;
  margin-top: 14rpx;
  color: #111827;
  font-size: 40rpx;
  font-weight: 700;
  line-height: 1.15;
}

.section {
  padding: 18rpx;
  margin-bottom: 18rpx;
  box-sizing: border-box;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12rpx;
  margin-bottom: 12rpx;
}

.section-title {
  display: block;
  color: #111827;
  font-size: 30rpx;
  font-weight: 700;
}

.empty {
  padding: 30rpx 0;
  color: #6b7280;
  font-size: 26rpx;
  text-align: center;
}

.check-list,
.issue-list {
  display: flex;
  flex-direction: column;
}

.check-row {
  display: grid;
  grid-template-columns: 24rpx 1fr 132rpx;
  align-items: center;
  gap: 10rpx;
  min-height: 64rpx;
  border-top: 1rpx solid #f3f4f6;
}

.check-row:first-child {
  border-top: 0;
}

.check-dot {
  width: 14rpx;
  height: 14rpx;
  border-radius: 50%;
  background: #94a3b8;
}

.check-dot.passed {
  background: #0f766e;
}

.check-dot.failed {
  background: #b42318;
}

.check-name {
  min-width: 0;
  color: #111827;
  font-size: 26rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check-result {
  color: #6b7280;
  font-size: 23rpx;
  text-align: right;
}

.check-result.passed {
  color: #0f766e;
}

.check-result.failed {
  color: #b42318;
}

.issue-list {
  gap: 12rpx;
}

.issue-row {
  padding: 16rpx;
  border: 1rpx solid #fecaca;
  border-radius: 8rpx;
  background: #fff5f5;
}

.issue-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14rpx;
}

.issue-title {
  color: #991b1b;
  font-size: 26rpx;
  font-weight: 700;
}

.issue-object {
  max-width: 260rpx;
  color: #6b7280;
  font-size: 22rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.issue-message {
  display: block;
  margin-top: 8rpx;
  color: #111827;
  font-size: 24rpx;
  line-height: 1.4;
}

.issue-values {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  margin-top: 8rpx;
  color: #6b7280;
  font-size: 22rpx;
}

.issue-more {
  display: block;
  color: #6b7280;
  font-size: 22rpx;
}
</style>
