<template>
  <view class="page">
    <view class="header">
      <button class="back-btn" @click="goBack">返回</button>
      <view class="header-main">
        <view class="title">{{ titleText }}</view>
        <view class="subtitle">{{ contextText }}</view>
      </view>
      <button class="refresh-btn" :disabled="loading" @click="loadData">刷新</button>
    </view>

    <view :class="['summary-grid', showInbound !== showOutbound ? 'single' : '']">
      <view v-if="showInbound" class="summary-card inbound">
        <view class="card-label">收货数量</view>
        <view class="card-value">{{ summary.inbound_qty }}</view>
        <view class="card-meta">{{ summary.inbound_orders }} 单 / {{ summary.inbound_lines }} 行</view>
      </view>
      <view v-if="showOutbound" class="summary-card outbound">
        <view class="card-label">出货数量</view>
        <view class="card-value">{{ summary.outbound_qty }}</view>
        <view class="card-meta">{{ summary.outbound_orders }} 单 / {{ summary.outbound_lines }} 行</view>
      </view>
    </view>

    <view class="section-head">
      <view class="section-title">来源数据</view>
      <view class="section-count">{{ itemCount }} 行</view>
    </view>

    <view v-if="loading" class="empty">加载中...</view>
    <view v-else-if="items.length === 0" class="empty">当前条件暂无明细数据</view>
    <view v-else class="detail-list">
      <view v-for="item in items" :key="item.id" class="detail-row">
        <view class="row-top">
          <text :class="['kind-badge', item.kind === 'inbound' ? 'in' : 'out']">
            {{ item.kind_label }}
          </text>
          <text class="source-no">{{ item.source_no || item.task_no || '-' }}</text>
          <text class="date-text">{{ item.date }}</text>
        </view>

        <view class="product-name">{{ item.product_name || item.product_code || '未命名商品' }}</view>
        <view class="product-code">{{ productText(item) }}</view>

        <view class="trace-line">
          <text>{{ item.source_type }}</text>
          <text v-if="item.task_no"> · 任务 {{ item.task_no }}</text>
          <text v-if="item.ref_no"> · 来源 {{ item.ref_no }}</text>
        </view>

        <view class="row-bottom">
          <view class="owner-name">{{ item.owner_name }}</view>
          <view class="qty-text">{{ item.qty }}{{ item.base_uom ? ` ${item.base_uom}` : '' }}</view>
        </view>

        <view v-if="extraText(item)" class="extra-line">{{ extraText(item) }}</view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '../../utils/request'

const loading = ref(false)
const params = ref({
  mode: 'month',
  month: '',
  start_date: '',
  end_date: '',
  owner: '',
  metric: 'all',
})
const period = ref({
  start_date: '',
  end_date: '',
})
const ownerOptions = ref([])
const summary = ref({
  inbound_orders: 0,
  inbound_lines: 0,
  inbound_qty: '0.000',
  outbound_orders: 0,
  outbound_lines: 0,
  outbound_qty: '0.000',
  item_count: 0,
})
const items = ref([])

const metricText = computed(() => {
  if (params.value.metric === 'inbound') return '收货明细'
  if (params.value.metric === 'outbound') return '出货明细'
  return '收出货明细'
})

const titleText = computed(() => metricText.value)
const showInbound = computed(() => params.value.metric !== 'outbound')
const showOutbound = computed(() => params.value.metric !== 'inbound')
const itemCount = computed(() => summary.value.item_count || items.value.length)

const ownerText = computed(() => {
  const ownerId = String(params.value.owner || '')
  if (!ownerId) return '全部货主'
  const option = ownerOptions.value.find((item) => String(item.id) === ownerId)
  return option?.name || items.value[0]?.owner_name || `货主 #${ownerId}`
})

const periodText = computed(() => {
  const start = period.value.start_date || params.value.start_date
  const end = period.value.end_date || params.value.end_date
  if (start && end) {
    return start === end ? start : `${start} 至 ${end}`
  }
  return params.value.month ? `${params.value.month} 月` : ''
})

const contextText = computed(() => `${ownerText.value} · ${periodText.value}`)

function normalizeQuery(query = {}) {
  params.value = {
    mode: query.mode || 'month',
    month: query.month || '',
    start_date: query.start_date || '',
    end_date: query.end_date || '',
    owner: query.owner || '',
    metric: query.metric || 'all',
  }
}

function productText(item) {
  const parts = [item.product_code, item.product_sku].filter(Boolean)
  return parts.length ? parts.join(' / ') : '无商品编码'
}

function extraText(item) {
  const parts = []
  if (item.kind === 'inbound' && item.location_code) {
    parts.push(`库位 ${item.location_code}`)
  }
  if (item.kind === 'outbound' && item.counterparty_name) {
    parts.push(`客户 ${item.counterparty_name}`)
  }
  if (item.line_no) {
    parts.push(`行号 ${item.line_no}`)
  }
  return parts.join(' · ')
}

function goBack() {
  uni.navigateBack({
    delta: 1,
    fail: () => {
      uni.switchTab({ url: '/pages/reports/index' })
    },
  })
}

async function loadData() {
  if (loading.value) return
  loading.value = true
  try {
    const res = await api.pdaThroughputDetails(params.value)
    summary.value = res.summary || summary.value
    period.value = res.period || period.value
    ownerOptions.value = res.owner_options || []
    items.value = res.items || []
    if (res.metric) {
      params.value.metric = res.metric
    }
  } finally {
    loading.value = false
  }
}

onLoad((query) => {
  normalizeQuery(query)
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
  gap: 14rpx;
  margin-bottom: 20rpx;
}

.header-main {
  flex: 1;
  min-width: 0;
}

.title {
  color: #111827;
  font-size: 36rpx;
  font-weight: 700;
  line-height: 1.35;
}

.subtitle {
  margin-top: 6rpx;
  color: #6b7280;
  font-size: 24rpx;
  line-height: 1.35;
}

.back-btn,
.refresh-btn {
  width: 112rpx;
  height: 60rpx;
  margin: 0;
  padding: 0;
  border-radius: 8rpx;
  font-size: 24rpx;
  line-height: 60rpx;
}

.back-btn {
  background: #fff;
  color: #374151;
}

.refresh-btn {
  background: #111827;
  color: #fff;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16rpx;
  margin-bottom: 26rpx;
}

.summary-grid.single {
  grid-template-columns: 1fr;
}

.summary-card {
  min-height: 170rpx;
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
  margin-top: 16rpx;
  color: #111827;
  font-size: 40rpx;
  font-weight: 700;
  line-height: 1.15;
}

.card-meta {
  margin-top: 12rpx;
  color: #4b5563;
  font-size: 24rpx;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14rpx;
}

.section-title {
  color: #111827;
  font-size: 30rpx;
  font-weight: 700;
}

.section-count {
  color: #6b7280;
  font-size: 24rpx;
}

.empty {
  padding: 44rpx 20rpx;
  border-radius: 8rpx;
  background: #fff;
  color: #6b7280;
  font-size: 28rpx;
  text-align: center;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.detail-row {
  padding: 20rpx;
  border-radius: 8rpx;
  background: #fff;
}

.row-top {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.kind-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 68rpx;
  height: 38rpx;
  border-radius: 6rpx;
  color: #fff;
  font-size: 22rpx;
}

.kind-badge.in {
  background: #16a34a;
}

.kind-badge.out {
  background: #dc2626;
}

.source-no {
  flex: 1;
  min-width: 0;
  color: #111827;
  font-size: 28rpx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.date-text {
  flex-shrink: 0;
  color: #6b7280;
  font-size: 24rpx;
}

.product-name {
  margin-top: 16rpx;
  color: #111827;
  font-size: 30rpx;
  font-weight: 700;
  line-height: 1.35;
}

.product-code,
.trace-line,
.extra-line {
  margin-top: 8rpx;
  color: #6b7280;
  font-size: 24rpx;
  line-height: 1.35;
}

.row-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  margin-top: 14rpx;
}

.owner-name {
  flex: 1;
  min-width: 0;
  color: #374151;
  font-size: 26rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qty-text {
  flex-shrink: 0;
  color: #111827;
  font-size: 34rpx;
  font-weight: 700;
}
</style>
