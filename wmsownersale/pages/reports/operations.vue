<template>
  <view class="page">
    <view class="hero">
      <view>
        <view class="title">入出库履约</view>
        <view class="subtitle">{{ basisLabel }} · 数据截至 {{ dataAsOf }}</view>
      </view>
      <button class="refresh" @click="loadAll">刷新</button>
    </view>

    <view class="filters">
      <view class="segments">
        <view :class="['segment', basis === 'actual' && 'active']" @click="setBasis('actual')">实际</view>
        <view :class="['segment', basis === 'plan' && 'active']" @click="setBasis('plan')">计划</view>
      </view>
      <picker mode="date" :value="startDate" @change="onStartChange">
        <view class="picker">开始：{{ startDate }}</view>
      </picker>
      <picker mode="date" :value="endDate" @change="onEndChange">
        <view class="picker">结束：{{ endDate }}</view>
      </picker>
    </view>

    <view class="basis-note">
      {{ basis === 'actual'
        ? '入库按 RECEIVE 库存过账，出库按已完成 DISPATCH 发运；草稿和取消单不计入。'
        : '计划按订单业务日期与需求数量统计，仅用于需求和达成分析。' }}
    </view>

    <view v-if="error" class="load-error">
      <text>{{ error }}</text>
      <button size="mini" @click="loadAll({ clear: true })">重试</button>
    </view>

    <template v-else>
    <view class="summary-grid">
      <view class="summary-card inbound">
        <view class="label">{{ basis === 'actual' ? '实际入库' : '计划入库' }}</view>
        <view class="value">{{ inbound.qty }}</view>
        <view class="meta">{{ inbound.orders }} 单 / {{ inbound.lines }} 行</view>
      </view>
      <view class="summary-card outbound">
        <view class="label">{{ basis === 'actual' ? '实际发运' : '计划出库' }}</view>
        <view class="value">{{ outbound.qty }}</view>
        <view class="meta">{{ outbound.orders }} 单 / {{ outbound.lines }} 行</view>
      </view>
    </view>

    <view class="section-head">
      <view class="section-title">逐行明细</view>
      <view class="count">共 {{ count }} 行</view>
    </view>
    <view v-if="loading" class="empty">正在读取交易事实...</view>
    <view v-else-if="!rows.length" class="empty">当前条件没有业务事实</view>
    <view v-for="row in rows" :key="`${row.direction}-${row.event_at}-${row.task_id || row.order_id}-${row.product.id}`" class="row">
      <view class="row-head">
        <text :class="['badge', row.direction]">{{ row.direction === 'inbound' ? '入库' : '出库' }}</text>
        <text class="order">{{ row.order_no || row.task_no || '-' }}</text>
        <text class="date">{{ dateText(row.event_at) }}</text>
      </view>
      <view class="product">{{ row.product.name || row.product.code }}</view>
      <view class="sub">{{ row.product.code }} · 批次 {{ row.lot_no || '-' }} · {{ row.status }}</view>
      <view class="row-foot">
        <text>{{ row.operator || '系统过账' }}</text>
        <text class="qty">{{ basis === 'plan' ? row.planned_qty : row.actual_qty }}</text>
      </view>
      <view v-if="row.exception_type" class="exception">异常：{{ row.exception_type }}</view>
    </view>

    <view v-if="count > pageSize" class="pager">
      <button :disabled="page <= 1 || loading" @click="changePage(-1)">上一页</button>
      <text>第 {{ page }} 页</text>
      <button :disabled="!nextPage || loading" @click="changePage(1)">下一页</button>
    </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const now = new Date()
const pad = (value) => String(value).padStart(2, '0')
const fmtDate = (value) => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`

const startDate = ref(fmtDate(new Date(now.getFullYear(), now.getMonth(), 1)))
const endDate = ref(fmtDate(now))
const basis = ref('actual')
const loading = ref(false)
const dataAsOfRaw = ref('')
const summary = ref({})
const rows = ref([])
const count = ref(0)
const page = ref(1)
const pageSize = 30
const nextPage = ref(null)
const error = ref('')
let requestGeneration = 0

const inbound = computed(() => summary.value.inbound || { qty: '0', orders: 0, lines: 0 })
const outbound = computed(() => summary.value.outbound || { qty: '0', orders: 0, lines: 0 })
const basisLabel = computed(() => basis.value === 'actual' ? '库存/发运实绩' : '订单计划量')
const dataAsOf = computed(() => dataAsOfRaw.value ? new Date(dataAsOfRaw.value).toLocaleString() : '-')

function params() {
  return {
    start_date: startDate.value,
    end_date: endDate.value,
    direction: 'all',
    metric_basis: basis.value,
  }
}

function clearResults() {
  summary.value = {}
  dataAsOfRaw.value = ''
  rows.value = []
  count.value = 0
  nextPage.value = null
}

async function loadAll({ clear = false } = {}) {
  const generation = ++requestGeneration
  const requestParams = params()
  const requestPage = page.value
  error.value = ''
  if (clear) clearResults()
  loading.value = true
  try {
    const [summaryResponse, detailsResponse] = await Promise.all([
      api.operationsSummary(requestParams),
      api.operationsDetails({ ...requestParams, page: requestPage, page_size: pageSize }),
    ])
    if (generation !== requestGeneration) return
    summary.value = summaryResponse.summary || {}
    dataAsOfRaw.value = summaryResponse.data_as_of || detailsResponse.data_as_of || ''
    rows.value = detailsResponse.results || []
    count.value = Number(detailsResponse.count || 0)
    nextPage.value = detailsResponse.next
  } catch (loadError) {
    if (generation === requestGeneration) {
      clearResults()
      error.value = loadError?.message || '履约报表加载失败，请稍后重试'
    }
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

function setBasis(value) {
  if (basis.value === value) return
  basis.value = value
  page.value = 1
  loadAll({ clear: true })
}

function onStartChange(event) {
  startDate.value = event.detail.value
  page.value = 1
  loadAll({ clear: true })
}

function onEndChange(event) {
  endDate.value = event.detail.value
  page.value = 1
  loadAll({ clear: true })
}

function changePage(delta) {
  page.value += delta
  loadAll({ clear: true })
}

function dateText(value) {
  return value ? String(value).slice(0, 10) : '-'
}

onLoad(loadAll)
</script>

<style scoped>
.page { min-height: 100vh; padding: 24rpx; box-sizing: border-box; background: #f3f6fb; }
.hero, .filters, .basis-note, .row { background: #fff; border-radius: 20rpx; }
.hero { display: flex; justify-content: space-between; align-items: center; padding: 26rpx; }
.title { font-size: 38rpx; font-weight: 700; color: #172033; }
.subtitle, .meta, .sub, .date, .count { margin-top: 8rpx; font-size: 22rpx; color: #6b7280; }
.refresh { width: 120rpx; margin: 0; font-size: 24rpx; }
.filters { margin-top: 18rpx; padding: 20rpx; }
.segments { display: grid; grid-template-columns: 1fr 1fr; gap: 12rpx; margin-bottom: 14rpx; }
.segment { padding: 14rpx; text-align: center; border-radius: 12rpx; background: #edf2f7; color: #526077; }
.segment.active { background: #1768e5; color: #fff; font-weight: 700; }
.picker { padding: 15rpx 4rpx; border-bottom: 1rpx solid #edf0f5; color: #273248; font-size: 25rpx; }
.basis-note { margin: 16rpx 0; padding: 18rpx 22rpx; color: #526077; font-size: 22rpx; line-height: 1.55; }
.summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16rpx; }
.summary-card { padding: 22rpx; border-radius: 18rpx; color: #fff; }
.summary-card.inbound { background: linear-gradient(135deg, #17835f, #32a879); }
.summary-card.outbound { background: linear-gradient(135deg, #d24b45, #ef7464); }
.label { font-size: 23rpx; opacity: .9; }
.value { margin: 13rpx 0; font-size: 40rpx; font-weight: 700; }
.summary-card .meta { color: rgba(255,255,255,.85); }
.section-head { display: flex; justify-content: space-between; align-items: center; margin: 28rpx 4rpx 14rpx; }
.section-title { font-size: 30rpx; font-weight: 700; color: #172033; }
.empty { padding: 60rpx 20rpx; text-align: center; color: #8a94a8; }
.load-error { margin: 18rpx 0; padding: 32rpx; border-radius: 18rpx; background: #fff; color: #b42318; text-align: center; }
.load-error button { margin-top: 18rpx; }
.row { padding: 22rpx; margin-bottom: 14rpx; }
.row-head, .row-foot { display: flex; align-items: center; gap: 12rpx; }
.row-foot { justify-content: space-between; margin-top: 16rpx; color: #647087; font-size: 23rpx; }
.badge { padding: 5rpx 12rpx; border-radius: 999rpx; color: #fff; font-size: 20rpx; }
.badge.inbound { background: #198765; }
.badge.outbound { background: #d9534f; }
.order { flex: 1; font-weight: 700; color: #273248; }
.date { margin-top: 0; }
.product { margin-top: 16rpx; font-size: 28rpx; font-weight: 700; color: #172033; }
.qty { color: #172033; font-size: 30rpx; font-weight: 700; }
.exception { margin-top: 12rpx; color: #bd3e36; font-size: 22rpx; }
.pager { display: flex; align-items: center; justify-content: center; gap: 18rpx; margin-top: 22rpx; }
.pager button { width: 150rpx; margin: 0; font-size: 23rpx; }
</style>
