<template>
  <view class="page">
    <view class="header">
      <view class="header-main">
        <view class="title">POS销售报表</view>
        <view class="subtitle">{{ periodText }}</view>
      </view>
      <view class="header-actions">
        <button class="ghost-btn header-btn" :disabled="statsLoading || salesLoading" @click="loadData">
          刷新
        </button>
      </view>
    </view>

    <view class="segmented">
      <view :class="['seg-item', mode === 'today' ? 'active' : '']" @click="setMode('today')">
        今日
      </view>
      <view :class="['seg-item', mode === 'month' ? 'active' : '']" @click="setMode('month')">
        月度
      </view>
      <view :class="['seg-item', mode === 'range' ? 'active' : '']" @click="setMode('range')">
        时间段
      </view>
    </view>

    <view class="filter-panel">
      <view v-if="mode === 'month'" class="filter-row">
        <text class="filter-label">月份</text>
        <picker mode="date" fields="month" :value="month" @change="onMonthChange">
          <view class="picker-value">{{ month }}</view>
        </picker>
      </view>
      <template v-else-if="mode === 'range'">
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
      </template>
      <view v-else class="filter-row">
        <text class="filter-label">日期</text>
        <picker mode="date" :value="todayDate" @change="onTodayChange">
          <view class="picker-value">{{ todayDate }}</view>
        </picker>
      </view>
    </view>

    <view class="export-row">
      <button class="ghost-btn export-btn" @click="exportStats">导出统计</button>
      <button class="ghost-btn export-btn" @click="exportSales">导出明细</button>
      <button class="ghost-btn export-btn" @click="openAccuracy">POS数据对账</button>
    </view>

    <view class="summary-grid">
      <view class="summary-card primary">
        <text class="card-label">净销售</text>
        <text class="card-value">{{ money(summary.net_amount) }}</text>
        <text class="card-meta">销售 {{ money(summary.sales_amount) }}</text>
      </view>
      <view class="summary-card">
        <text class="card-label">完成单</text>
        <text class="card-value">{{ summary.completed_count || 0 }}</text>
        <text class="card-meta">共 {{ summary.sale_count || 0 }} 单</text>
      </view>
      <view class="summary-card">
        <text class="card-label">退货</text>
        <text class="card-value danger">{{ money(summary.return_amount) }}</text>
        <text class="card-meta">{{ summary.return_count || 0 }} 单</text>
      </view>
      <view class="summary-card">
        <text class="card-label">作废</text>
        <text class="card-value danger">{{ money(summary.voided_amount) }}</text>
        <text class="card-meta">{{ summary.voided_count || 0 }} 单</text>
      </view>
      <view class="summary-card">
        <text class="card-label">净数量</text>
        <text class="card-value">{{ qtyText(summary.net_qty) }}</text>
        <text class="card-meta">销售 {{ qtyText(summary.completed_qty) }}</text>
      </view>
      <view class="summary-card">
        <text class="card-label">销售行</text>
        <text class="card-value">{{ summary.line_count || 0 }}</text>
        <text class="card-meta">退货行 {{ summary.return_line_count || 0 }}</text>
      </view>
    </view>

    <view class="section">
      <view class="section-head">
        <text class="section-title">支付方式</text>
      </view>
      <view v-if="statsLoading" class="empty">加载中...</view>
      <view v-else-if="!paymentRows.length" class="empty">暂无支付数据</view>
      <view v-else class="rank-list">
        <view class="rank-row" v-for="row in paymentRows" :key="row.method || row.method_label">
          <view class="rank-main">
            <text class="rank-title">{{ paymentName(row) }}</text>
            <text class="rank-meta">{{ row.sale_count || 0 }} 单 / 退款 {{ row.refund_count || 0 }} 单</text>
          </view>
          <view class="rank-amount">
            <text>{{ money(row.net_amount || row.amount) }}</text>
            <text class="rank-sub">销售 {{ money(row.sale_amount) }}</text>
          </view>
        </view>
      </view>
    </view>

    <view class="section">
      <view class="section-head">
        <text class="section-title">商品排行</text>
      </view>
      <view v-if="statsLoading" class="empty">加载中...</view>
      <view v-else-if="!productRows.length" class="empty">暂无商品数据</view>
      <view v-else class="rank-list">
        <view class="rank-row" v-for="row in productRows" :key="row.product_id + '-' + row.owner_id">
          <view class="rank-main">
            <text class="rank-title">{{ row.product_name || row.product_code || '-' }}</text>
            <text class="rank-meta">{{ row.product_code || row.product_sku || '-' }} / {{ row.owner_name || '未指定货主' }}</text>
          </view>
          <view class="rank-amount">
            <text>{{ money(row.net_amount) }}</text>
            <text class="rank-sub">{{ qtyText(row.net_qty) }} 件</text>
          </view>
        </view>
      </view>
    </view>

    <view class="split-sections">
      <view class="section compact">
        <view class="section-head">
          <text class="section-title">货主汇总</text>
        </view>
        <view v-if="statsLoading" class="empty">加载中...</view>
        <view v-else-if="!ownerRows.length" class="empty">暂无货主数据</view>
        <view v-else class="rank-list">
          <view class="rank-row compact-row" v-for="row in ownerRows" :key="row.owner_id">
            <view class="rank-main">
              <text class="rank-title">{{ row.owner_name || row.owner_code || '-' }}</text>
              <text class="rank-meta">{{ row.sale_count || 0 }} 单 / 退 {{ row.return_count || 0 }} 单</text>
            </view>
            <view class="rank-amount">
              <text>{{ money(row.net_amount) }}</text>
              <text class="rank-sub">{{ qtyText(row.net_qty) }} 件</text>
            </view>
          </view>
        </view>
      </view>

      <view class="section compact">
        <view class="section-head">
          <text class="section-title">收银员汇总</text>
        </view>
        <view v-if="statsLoading" class="empty">加载中...</view>
        <view v-else-if="!cashierRows.length" class="empty">暂无收银员数据</view>
        <view v-else class="rank-list">
          <view class="rank-row compact-row" v-for="row in cashierRows" :key="row.cashier_id || row.cashier_username">
            <view class="rank-main">
              <text class="rank-title">{{ row.cashier_username || '未记录' }}</text>
              <text class="rank-meta">完成 {{ row.completed_count || 0 }} / 作废 {{ row.voided_count || 0 }}</text>
            </view>
            <view class="rank-amount">
              <text>{{ money(row.net_amount) }}</text>
              <text class="rank-sub">退 {{ money(row.return_amount) }}</text>
            </view>
          </view>
        </view>
      </view>
    </view>

    <view class="section sales-section">
      <view class="section-head">
        <text class="section-title">销售记录</text>
        <text class="section-count">{{ salesCount }} 条</text>
      </view>
      <view class="sales-filter">
        <picker class="status-picker" :range="statusOptions" range-key="label" :value="statusIndex" @change="onStatusChange">
          <view class="status-value">{{ selectedStatusLabel }}</view>
        </picker>
        <input
          class="search-input"
          v-model.trim="saleKeyword"
          placeholder="小票号 / POS单号"
          confirm-type="search"
          @confirm="reloadSales"
        />
        <button class="primary-btn search-btn" :disabled="salesLoading" @click="reloadSales">
          查询
        </button>
      </view>

      <view v-if="salesLoading && !saleRows.length" class="empty">加载中...</view>
      <view v-else-if="!saleRows.length" class="empty">暂无销售记录</view>
      <view v-else class="sale-list">
        <view class="sale-row" v-for="sale in saleRows" :key="sale.id">
          <view class="sale-main">
            <view class="sale-title-row">
              <text class="sale-no">{{ saleDisplayNo(sale) }}</text>
              <text :class="['sale-status', sale.status === 'VOIDED' ? 'voided' : 'completed']">
                {{ saleStatusText(sale.status) }}
              </text>
            </view>
            <text class="sale-meta">{{ saleCreatedText(sale) }} / {{ salePaymentMethod(sale) }}</text>
            <text class="sale-meta">商品 {{ saleLineCount(sale) }} 项 / 出库单 {{ saleOrderCount(sale) }} 张</text>
          </view>
          <view class="sale-amount">
            <text>{{ money(sale.total_amount) }}</text>
          </view>
        </view>
      </view>
      <button v-if="hasMoreSales" class="ghost-btn more-btn" :disabled="salesLoading" @click="loadMoreSales">
        更多销售记录
      </button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const pad = (n) => String(n).padStart(2, '0')
const today = new Date()
const SALES_PAGE_SIZE = 10

const mode = ref('today')
const todayDate = ref(formatDate(today))
const month = ref(formatMonth(today))
const startDate = ref(formatDate(new Date(today.getFullYear(), today.getMonth(), 1)))
const endDate = ref(formatDate(today))
const statsLoading = ref(false)
const salesLoading = ref(false)
const saleKeyword = ref('')
const saleRows = ref([])
const salesCount = ref(0)
const salesPage = ref(1)
const posStats = ref(defaultStats())
const selectedStatus = ref('')
let loaded = false

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已完成', value: 'COMPLETED' },
  { label: '已作废', value: 'VOIDED' },
]

const dateRange = computed(() => buildDateRange())
const periodText = computed(() => {
  if (mode.value === 'month') return `${month.value} 月度`
  if (mode.value === 'range') return `${startDate.value} 至 ${endDate.value}`
  return `${todayDate.value} 今日`
})
const summary = computed(() => posStats.value.summary || defaultStats().summary)
const paymentRows = computed(() => posStats.value.payments || [])
const ownerRows = computed(() => posStats.value.owners || [])
const productRows = computed(() => posStats.value.products || [])
const cashierRows = computed(() => posStats.value.cashiers || [])
const statusIndex = computed(() => {
  const index = statusOptions.findIndex((item) => item.value === selectedStatus.value)
  return index >= 0 ? index : 0
})
const selectedStatusLabel = computed(() => statusOptions[statusIndex.value]?.label || '全部状态')
const hasMoreSales = computed(() => saleRows.value.length < salesCount.value)

function defaultStats() {
  return {
    summary: {
      sale_count: 0,
      completed_count: 0,
      voided_count: 0,
      return_count: 0,
      line_count: 0,
      return_line_count: 0,
      completed_qty: '0.000',
      net_qty: '0.000',
      sales_amount: '0.00',
      voided_amount: '0.00',
      return_amount: '0.00',
      net_amount: '0.00',
    },
    payments: [],
    owners: [],
    products: [],
    cashiers: [],
  }
}

function formatDate(value) {
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function formatMonth(value) {
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

function monthBounds(value) {
  const [year, monthValue] = String(value || '').split('-').map((part) => Number(part))
  if (!year || !monthValue) {
    return { start_date: todayDate.value, end_date: todayDate.value }
  }
  const start = new Date(year, monthValue - 1, 1)
  const end = new Date(year, monthValue, 0)
  return { start_date: formatDate(start), end_date: formatDate(end) }
}

function buildDateRange() {
  if (mode.value === 'month') return monthBounds(month.value)
  if (mode.value === 'range') return { start_date: startDate.value, end_date: endDate.value }
  return { start_date: todayDate.value, end_date: todayDate.value }
}

function money(value) {
  const num = Number(value || 0)
  return `¥${Number.isFinite(num) ? num.toFixed(2) : '0.00'}`
}

function qtyText(value) {
  const num = Number(value || 0)
  if (!Number.isFinite(num)) return '0'
  return num.toFixed(3).replace(/\.?0+$/, '')
}

function formatDateTime(value) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function normalizeRows(data) {
  if (Array.isArray(data)) return data
  return data && Array.isArray(data.results) ? data.results : []
}

function normalizeCount(data, rows) {
  const count = Number(data?.count)
  return Number.isFinite(count) ? count : rows.length
}

function paymentName(row = {}) {
  if (row.method_label) return row.method_label
  const names = {
    CASH: '现金',
    WECHAT: '微信',
    ALIPAY: '支付宝',
    BANK_CARD: '银行卡',
    OTHER: '其他',
  }
  return names[row.method] || '未收款'
}

function saleDisplayNo(sale = {}) {
  return sale.src_bill_no || sale.sale_no || String(sale.id || '')
}

function saleStatusText(status) {
  if (status === 'VOIDED') return '已作废'
  if (status === 'COMPLETED') return '已完成'
  return status || '-'
}

function saleCreatedText(sale = {}) {
  return formatDateTime(sale.created_at)
}

function salePaymentMethod(sale = {}) {
  return paymentName(sale.payment || sale.receipt?.payment || {})
}

function saleLineCount(sale = {}) {
  return Array.isArray(sale.lines) ? sale.lines.length : 0
}

function saleOrderCount(sale = {}) {
  return Array.isArray(sale.orders) ? sale.orders.length : 0
}

function setMode(nextMode) {
  if (mode.value === nextMode) return
  mode.value = nextMode
  loadData()
}

function onTodayChange(event) {
  todayDate.value = event.detail.value
  loadData()
}

function onMonthChange(event) {
  month.value = event.detail.value
  loadData()
}

function onStartDateChange(event) {
  startDate.value = event.detail.value
  loadData()
}

function onEndDateChange(event) {
  endDate.value = event.detail.value
  loadData()
}

function onStatusChange(event) {
  const next = statusOptions[Number(event.detail.value) || 0]
  selectedStatus.value = next?.value || ''
  reloadSales()
}

function reloadSales() {
  loadSales({ reset: true })
}

async function loadData() {
  await Promise.all([loadStats(), loadSales({ reset: true })])
}

async function loadStats() {
  if (statsLoading.value) return
  statsLoading.value = true
  try {
    const range = dateRange.value
    const res = await api.posStats({
      start_date: range.start_date,
      end_date: range.end_date,
      top_n: 10,
    })
    posStats.value = res || defaultStats()
  } finally {
    statsLoading.value = false
  }
}

async function loadSales(options = {}) {
  if (salesLoading.value) return
  salesLoading.value = true
  const nextPage = options.reset ? 1 : salesPage.value + 1
  try {
    const range = dateRange.value
    const res = await api.posSales({
      search: saleKeyword.value || '',
      status: selectedStatus.value || '',
      start_date: range.start_date,
      end_date: range.end_date,
      page: nextPage,
      page_size: SALES_PAGE_SIZE,
    })
    const rows = normalizeRows(res)
    salesPage.value = nextPage
    salesCount.value = normalizeCount(res, rows)
    saleRows.value = options.reset ? rows : [...saleRows.value, ...rows]
  } finally {
    salesLoading.value = false
  }
}

function loadMoreSales() {
  loadSales()
}

function openAccuracy() {
  const range = dateRange.value
  uni.navigateTo({
    url: `/pages/pos/accuracy?start_date=${encodeURIComponent(range.start_date)}&end_date=${encodeURIComponent(range.end_date)}`,
  })
}

function authHeader() {
  try {
    const token = uni.getStorageSync('access') || ''
    return token ? { Authorization: `Bearer ${token}` } : {}
  } catch (e) {
    return {}
  }
}

function downloadExcel(url, filename) {
  if (typeof uni.downloadFile !== 'function') {
    if (typeof window !== 'undefined' && window.open) {
      window.open(api.authUrl(url), '_blank')
    }
    return
  }
  uni.downloadFile({
    url,
    header: authHeader(),
    success: (res) => {
      if (res.statusCode && res.statusCode !== 200) {
        uni.showToast({ title: '导出失败', icon: 'none' })
        return
      }
      if (typeof uni.openDocument === 'function') {
        uni.openDocument({
          filePath: res.tempFilePath,
          fileType: 'xlsx',
          showMenu: true,
          fail: () => uni.showToast({ title: `${filename} 已下载`, icon: 'none' }),
        })
      } else {
        uni.showToast({ title: `${filename} 已下载`, icon: 'none' })
      }
    },
    fail: () => uni.showToast({ title: '导出失败', icon: 'none' }),
  })
}

function exportStats() {
  const range = dateRange.value
  downloadExcel(
    api.posStatsExport({ start_date: range.start_date, end_date: range.end_date, top_n: 50 }),
    'pos-stats.xlsx'
  )
}

function exportSales() {
  const range = dateRange.value
  downloadExcel(
    api.posSalesExport({
      search: saleKeyword.value || '',
      status: selectedStatus.value || '',
      start_date: range.start_date,
      end_date: range.end_date,
    }),
    'pos-sales.xlsx'
  )
}

function initPage(options = {}) {
  if (options.start_date || options.end_date) {
    mode.value = 'range'
    startDate.value = String(options.start_date || options.end_date || startDate.value)
    endDate.value = String(options.end_date || options.start_date || endDate.value)
  }
  loadData()
}

onLoad(initPage)
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
  flex: 0 0 auto;
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

.header-btn {
  width: 112rpx;
}

.segmented {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
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

.filter-panel,
.section {
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  background: #fff;
}

.filter-panel {
  padding: 4rpx 20rpx;
  margin-bottom: 16rpx;
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

.export-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12rpx;
  margin-bottom: 18rpx;
}

.export-btn {
  height: 58rpx;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14rpx;
  margin-bottom: 20rpx;
}

.summary-card {
  min-height: 168rpx;
  padding: 20rpx;
  border: 1rpx solid #e5e7eb;
  border-left: 8rpx solid #94a3b8;
  border-radius: 8rpx;
  background: #fff;
  box-sizing: border-box;
}

.summary-card.primary {
  border-left-color: #0f766e;
}

.card-label {
  color: #6b7280;
  font-size: 24rpx;
}

.card-value {
  display: block;
  margin-top: 16rpx;
  color: #111827;
  font-size: 40rpx;
  font-weight: 700;
  line-height: 1.15;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-value.danger {
  color: #b42318;
}

.card-meta {
  display: block;
  margin-top: 12rpx;
  color: #4b5563;
  font-size: 23rpx;
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

.section-count {
  color: #6b7280;
  font-size: 24rpx;
}

.rank-list,
.sale-list {
  display: flex;
  flex-direction: column;
}

.rank-row,
.sale-row {
  display: flex;
  align-items: center;
  gap: 14rpx;
  padding: 16rpx 0;
  border-top: 1rpx solid #edf0f4;
}

.rank-row:first-child,
.sale-row:first-child {
  border-top: 0;
}

.rank-main,
.sale-main {
  flex: 1;
  min-width: 0;
}

.rank-title,
.sale-no {
  display: block;
  color: #111827;
  font-size: 27rpx;
  font-weight: 700;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-meta,
.sale-meta,
.rank-sub {
  display: block;
  margin-top: 6rpx;
  color: #6b7280;
  font-size: 23rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-amount,
.sale-amount {
  flex: 0 0 180rpx;
  color: #111827;
  font-size: 27rpx;
  font-weight: 700;
  text-align: right;
}

.rank-amount text,
.sale-amount text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.split-sections {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
}

.compact-row {
  min-height: 88rpx;
}

.sales-filter {
  display: grid;
  grid-template-columns: 170rpx 1fr 104rpx;
  gap: 10rpx;
  margin-bottom: 12rpx;
}

.status-picker,
.status-value,
.search-input,
.search-btn {
  height: 58rpx;
}

.status-value,
.search-input {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 0 16rpx;
  border: 1rpx solid #d1d5db;
  border-radius: 8rpx;
  background: #fff;
  color: #111827;
  font-size: 24rpx;
  box-sizing: border-box;
}

.status-value {
  justify-content: center;
}

.sale-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10rpx;
}

.sale-status {
  flex: 0 0 auto;
  font-size: 22rpx;
  font-weight: 700;
}

.sale-status.completed {
  color: #0f766e;
}

.sale-status.voided {
  color: #b42318;
}

.more-btn {
  width: 100%;
  height: 58rpx;
  margin-top: 12rpx;
}

.empty {
  padding: 36rpx 16rpx;
  border-radius: 8rpx;
  background: #f9fafb;
  color: #6b7280;
  font-size: 26rpx;
  text-align: center;
}

@media (min-width: 900px) {
  .summary-grid {
    grid-template-columns: repeat(3, 1fr);
  }

  .split-sections {
    grid-template-columns: 1fr 1fr;
    gap: 18rpx;
  }
}
</style>
