<template>
  <view class="page">
    <view v-if="checkingPermission" class="state-card">正在读取代办出库权限…</view>
    <view v-else-if="!authorized" class="state-card">
      <view>当前账号无代办出库权限。</view>
      <button class="retry-button" @click="initialize">重新读取权限</button>
    </view>

    <template v-else>
      <view class="header-card">
        <view>
          <view class="page-title">代办出库统计</view>
          <view class="period-text">{{ period.start_date }} 至 {{ period.end_date }}</view>
        </view>
        <button class="refresh-button" :disabled="loading" @click="loadStats">
          {{ loading ? '刷新中…' : '刷新' }}
        </button>
      </view>

      <view class="mode-row">
        <view :class="['mode-item', { active: mode === 'today' }]" @click="setMode('today')">今日</view>
        <view :class="['mode-item', { active: mode === 'month' }]" @click="setMode('month')">月度</view>
        <view :class="['mode-item', { active: mode === 'range' }]" @click="setMode('range')">时间段</view>
      </view>

      <view class="filter-card">
        <picker v-if="mode === 'today'" mode="date" :value="todayDate" @change="setToday">
          <view class="picker-field">日期：{{ todayDate }}</view>
        </picker>
        <picker v-else-if="mode === 'month'" mode="date" fields="month" :value="month" @change="setMonth">
          <view class="picker-field">月份：{{ month }}</view>
        </picker>
        <template v-else>
          <picker mode="date" :value="rangeStart" @change="setRangeStart">
            <view class="picker-field">开始：{{ rangeStart }}</view>
          </picker>
          <picker mode="date" :value="rangeEnd" @change="setRangeEnd">
            <view class="picker-field">结束：{{ rangeEnd }}</view>
          </picker>
        </template>
        <picker :range="ownerLabels" :value="ownerIndex" @change="setOwner">
          <view class="picker-field">{{ ownerLabels[ownerIndex] || '全部货主' }}</view>
        </picker>
        <picker :range="operatorLabels" :value="operatorIndex" @change="setOperator">
          <view class="picker-field">{{ operatorLabels[operatorIndex] || '全部操作员' }}</view>
        </picker>
        <button class="query-button" :disabled="loading" @click="loadStats">统计</button>
      </view>

      <view v-if="errorMessage" class="error-card">
        <text>{{ errorMessage }}</text>
        <button class="retry-button" @click="loadStats">重试</button>
      </view>

      <view class="summary-grid">
        <view class="summary-card primary" @click="openHistory({})">
          <text class="summary-label">代办单</text>
          <text class="summary-value">{{ summary.order_count || 0 }}</text>
        </view>
        <view class="summary-card success" @click="openHistory({ status: 'COMPLETED' })">
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
        <view class="summary-card muted" @click="openHistory({ status: 'CANCELLED' })">
          <text class="summary-label">已取消</text>
          <text class="summary-value">{{ summary.cancelled_count || 0 }}</text>
        </view>
        <view class="summary-card">
          <text class="summary-label">商品行</text>
          <text class="summary-value">{{ summary.line_count || 0 }}</text>
        </view>
        <view class="summary-card">
          <text class="summary-label">基本数量*</text>
          <text class="summary-value small-value">{{ qtyText(summary.total_base_qty) }}</text>
        </view>
      </view>
      <view class="quantity-hint">* 不同商品基本单位可能不同，该合计仅作为仓库作业量参考。</view>

      <view class="section">
        <view class="section-title">状态分布</view>
        <view class="rank-list">
          <view
            v-for="row in nonEmptyStatusRows"
            :key="row.status"
            class="rank-row clickable"
            @click="openHistory({ status: row.status })"
          >
            <view>
              <view class="rank-title">{{ row.label }}</view>
              <view class="rank-meta">点击查看对应历史出库单</view>
            </view>
            <view class="rank-value">{{ row.order_count }} 张</view>
          </view>
          <view v-if="!nonEmptyStatusRows.length" class="empty">暂无状态数据</view>
        </view>
      </view>

      <view class="split-grid">
        <view class="section">
          <view class="section-title">每日汇总</view>
          <view
            v-for="row in stats.daily_rows || []"
            :key="row.date"
            class="rank-row clickable"
            @click="openHistory({ start_date: row.date, end_date: row.date })"
          >
            <view>
              <view class="rank-title">{{ row.date }}</view>
              <view class="rank-meta">完成 {{ row.completed_count }} / 待处理 {{ row.pending_count }}</view>
            </view>
            <view class="rank-value">{{ row.order_count }} 张</view>
          </view>
          <view v-if="!stats.daily_rows?.length" class="empty">暂无每日数据</view>
        </view>

        <view class="section">
          <view class="section-title">货主汇总</view>
          <view
            v-for="row in stats.owner_rows || []"
            :key="row.owner_id"
            class="rank-row clickable"
            @click="openHistory({ owner_id: row.owner_id })"
          >
            <view>
              <view class="rank-title">{{ row.owner_name || row.owner_code || '-' }}</view>
              <view class="rank-meta">完成 {{ row.completed_count }} / 异常 {{ row.exception_count }}</view>
            </view>
            <view class="rank-value">{{ row.order_count }} 张</view>
          </view>
          <view v-if="!stats.owner_rows?.length" class="empty">暂无货主数据</view>
        </view>

        <view class="section">
          <view class="section-title">操作员汇总</view>
          <view
            v-for="row in stats.operator_rows || []"
            :key="row.operator_id || 'missing'"
            class="rank-row clickable"
            @click="row.operator_id && openHistory({ operator_id: row.operator_id })"
          >
            <view>
              <view class="rank-title">{{ row.operator_name || '未记录' }}</view>
              <view class="rank-meta">完成 {{ row.completed_count }} / 异常 {{ row.exception_count }}</view>
            </view>
            <view class="rank-value">{{ row.order_count }} 张</view>
          </view>
          <view v-if="!stats.operator_rows?.length" class="empty">暂无操作员数据</view>
        </view>

        <view class="section">
          <view class="section-title">商品排行</view>
          <view v-for="row in stats.product_rows || []" :key="row.product_id" class="rank-row">
            <view>
              <view class="rank-title">{{ row.product_name || row.product_code || '-' }}</view>
              <view class="rank-meta">{{ row.product_sku || row.product_code || '-' }} · {{ row.order_count }} 张单</view>
            </view>
            <view class="rank-value">
              {{ qtyText(row.total_base_qty) }} {{ row.base_uom_name || row.base_uom_code }}
            </view>
          </view>
          <view v-if="!stats.product_rows?.length" class="empty">暂无商品数据</view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'

const auth = useAuth()
const checkingPermission = ref(true)
const authorized = ref(false)
const loading = ref(false)
const errorMessage = ref('')
const options = ref({ owners: [], operators: [] })
const stats = ref({ summary: {}, status_rows: [], daily_rows: [], owner_rows: [], operator_rows: [], product_rows: [] })
const mode = ref('today')
const ownerId = ref('')
const operatorId = ref('')
let statsRequestSequence = 0

function localDateText(date = new Date()) {
  const year = date.getFullYear()
  const monthValue = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${monthValue}-${day}`
}

const now = new Date()
const todayDate = ref(localDateText(now))
const month = ref(localDateText(now).slice(0, 7))
const rangeStart = ref(localDateText(new Date(now.getFullYear(), now.getMonth(), 1)))
const rangeEnd = ref(localDateText(now))

const period = computed(() => {
  if (mode.value === 'today') return { start_date: todayDate.value, end_date: todayDate.value }
  if (mode.value === 'month') {
    const [year, monthValue] = month.value.split('-').map(Number)
    const end = new Date(year, monthValue, 0)
    return { start_date: `${month.value}-01`, end_date: localDateText(end) }
  }
  return { start_date: rangeStart.value, end_date: rangeEnd.value }
})

const summary = computed(() => stats.value?.summary || {})
const nonEmptyStatusRows = computed(() => (stats.value?.status_rows || []).filter((row) => Number(row.order_count || 0) > 0))
const ownerChoices = computed(() => [{ id: '', name: '全部货主' }, ...(options.value.owners || [])])
const operatorChoices = computed(() => [{ id: '', name: '全部操作员' }, ...(options.value.operators || [])])
const ownerLabels = computed(() => ownerChoices.value.map((item) => item.name || item.code || '-'))
const operatorLabels = computed(() => operatorChoices.value.map((item) => item.name || item.username || '-'))
const ownerIndex = computed(() => Math.max(0, ownerChoices.value.findIndex((item) => String(item.id) === String(ownerId.value))))
const operatorIndex = computed(() => Math.max(0, operatorChoices.value.findIndex((item) => String(item.id) === String(operatorId.value))))

function eventValue(event) { return event?.detail?.value ?? '' }
function setToday(event) { todayDate.value = eventValue(event); loadStats() }
function setMonth(event) { month.value = eventValue(event); loadStats() }
function setRangeStart(event) { rangeStart.value = eventValue(event) }
function setRangeEnd(event) { rangeEnd.value = eventValue(event) }
function setOwner(event) { ownerId.value = ownerChoices.value[Number(eventValue(event))]?.id || '' }
function setOperator(event) { operatorId.value = operatorChoices.value[Number(eventValue(event))]?.id || '' }

function setMode(value) {
  if (!['today', 'month', 'range'].includes(value) || mode.value === value) return
  mode.value = value
  loadStats()
}

function qtyText(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? String(Number(number.toFixed(3))) : '0'
}

function handleError(error) {
  if (Number(error?.statusCode || error?.code) === 403) {
    auth.invalidateAssistedCapability()
    authorized.value = false
  }
  errorMessage.value = error?.message || '代办出库统计加载失败'
}

async function loadStats() {
  const sequence = ++statsRequestSequence
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await api.assistedOutboundStats({
      ...period.value,
      owner_id: ownerId.value,
      operator_id: operatorId.value,
      top_n: 10,
    })
    if (sequence === statsRequestSequence) stats.value = result
  } catch (error) {
    if (sequence === statsRequestSequence) handleError(error)
  } finally {
    if (sequence === statsRequestSequence) loading.value = false
  }
}

function queryString(params) {
  return Object.entries(params)
    .filter(([, value]) => value !== '' && value !== null && value !== undefined)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
}

function openHistory(extra = {}) {
  const params = {
    ...period.value,
    owner_id: ownerId.value,
    operator_id: operatorId.value,
    ...extra,
  }
  uni.navigateTo({ url: `/pages/outbound/assisted_history?${queryString(params)}` })
}

async function initialize() {
  checkingPermission.value = true
  try {
    await auth.ensureProfile()
    authorized.value = auth.canProcessAssistedOutbound
    if (!authorized.value) return
    options.value = await api.assistedOutboundHistoryOptions()
    await loadStats()
  } catch (error) {
    handleError(error)
  } finally {
    checkingPermission.value = false
  }
}

onLoad(initialize)
</script>

<style scoped>
.page { box-sizing: border-box; min-height: 100vh; padding: 18rpx; background: #f6f7fb; }
.state-card, .header-card, .filter-card, .section { padding: 22rpx; border-radius: 16rpx; background: #fff; }
.header-card { display: flex; align-items: center; justify-content: space-between; gap: 16rpx; }
.page-title, .section-title { font-size: 30rpx; font-weight: 700; }
.period-text, .quantity-hint, .rank-meta { color: #697386; font-size: 22rpx; }
.period-text { margin-top: 6rpx; }
.refresh-button, .query-button, .retry-button { margin: 0; color: #1677ff; background: #eef5ff; font-size: 23rpx; }
.mode-row { display: flex; margin: 18rpx 0; border-radius: 12rpx; background: #e9edf3; }
.mode-item { flex: 1; padding: 16rpx; color: #64748b; text-align: center; }
.mode-item.active { border-radius: 12rpx; color: #1677ff; background: #fff; font-weight: 700; box-shadow: 0 2rpx 8rpx rgba(0,0,0,.08); }
.filter-card { display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 12rpx; }
.picker-field { overflow: hidden; box-sizing: border-box; height: 66rpx; padding: 0 14rpx; border: 1rpx solid #d9dee7; border-radius: 8rpx; line-height: 66rpx; color: #475569; text-overflow: ellipsis; white-space: nowrap; }
.query-button { color: #fff; background: #1677ff; }
.summary-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 12rpx; margin-top: 18rpx; }
.summary-card { padding: 18rpx 10rpx; border-radius: 14rpx; text-align: center; background: #fff; box-shadow: 0 3rpx 12rpx rgba(15,23,42,.05); }
.summary-card.primary { background: #eaf3ff; }
.summary-card.success { background: #edf9f0; }
.summary-card.warning { background: #fff7e6; }
.summary-card.danger { background: #fff1f0; }
.summary-card.muted { background: #f1f5f9; }
.summary-label { display: block; color: #64748b; font-size: 21rpx; }
.summary-value { display: block; margin-top: 8rpx; font-size: 38rpx; font-weight: 700; }
.small-value { font-size: 30rpx; }
.quantity-hint { margin: 10rpx 2rpx 0; text-align: right; }
.section { margin-top: 18rpx; }
.split-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18rpx; }
.split-grid .section { min-width: 0; }
.rank-row { display: flex; align-items: center; justify-content: space-between; gap: 16rpx; padding: 16rpx 0; border-bottom: 1rpx solid #e5e7eb; }
.rank-row.clickable:active { background: #f8fafc; }
.rank-title { font-size: 25rpx; font-weight: 600; }
.rank-meta { margin-top: 5rpx; }
.rank-value { flex: none; color: #1677ff; font-size: 25rpx; font-weight: 700; }
.empty { padding: 34rpx 0; color: #8a94a6; text-align: center; }
.error-card { margin-top: 18rpx; padding: 18rpx; border-radius: 12rpx; color: #b42318; background: #fff1f0; }
.retry-button { margin-top: 12rpx; background: #fff; }
@media (max-width: 1023px) {
  .filter-card, .summary-grid, .split-grid { grid-template-columns: 1fr 1fr; }
  .query-button { grid-column: 1 / -1; }
  .summary-grid { gap: 10rpx; }
}
</style>
