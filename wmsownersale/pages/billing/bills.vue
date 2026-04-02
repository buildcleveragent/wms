<template>
  <view class="page">
    <view class="hero">
      <view class="hero-tag">Bill List</view>
      <view class="hero-title">账单列表</view>
      <view class="hero-desc">按账期看所有已生成账单，支持筛选、下钻和导出。</view>
    </view>

    <view v-if="loading && bills.length === 0" class="loading-banner">正在同步账单列表...</view>

    <view class="filter-card">
      <view class="filter-grid">
        <picker :range="ownerPickerOptions" range-key="label" :value="ownerPickerIndex" @change="onOwnerChange">
          <view class="picker-box">
            <text class="picker-label">货主</text>
            <text class="picker-value">{{ ownerPickerOptions[ownerPickerIndex]?.label || '全部货主' }}</text>
          </view>
        </picker>

        <picker :range="warehousePickerOptions" range-key="label" :value="warehousePickerIndex" @change="onWarehouseChange">
          <view class="picker-box">
            <text class="picker-label">仓库</text>
            <text class="picker-value">{{ warehousePickerOptions[warehousePickerIndex]?.label || '全部仓库' }}</text>
          </view>
        </picker>

        <picker :range="periodPickerOptions" range-key="label" :value="periodPickerIndex" @change="onPeriodChange">
          <view class="picker-box">
            <text class="picker-label">账期</text>
            <text class="picker-value">{{ periodPickerOptions[periodPickerIndex]?.label || '全部账期' }}</text>
          </view>
        </picker>

        <picker :range="statusOptions" range-key="label" :value="statusPickerIndex" @change="onStatusChange">
          <view class="picker-box">
            <text class="picker-label">状态</text>
            <text class="picker-value">{{ statusOptions[statusPickerIndex]?.label || '全部状态' }}</text>
          </view>
        </picker>
      </view>

      <input
        v-model="searchKeyword"
        class="keyword-input"
        placeholder="搜索结算单号或账期"
        confirm-type="search"
        @confirm="refreshBills"
      />

      <view class="filter-actions">
        <button class="btn-primary" @click="refreshBills">刷新</button>
        <button class="btn-ghost" :disabled="exporting" @click="exportBills">导出列表</button>
        <button class="btn-ghost" @click="goBackOverview">返回总览</button>
      </view>
    </view>

    <view class="summary-grid">
      <view class="summary-card">
        <view class="summary-label">账单数量</view>
        <view class="summary-value">{{ billsTotal }}</view>
      </view>
      <view class="summary-card">
        <view class="summary-label">当前页金额</view>
        <view class="summary-value">{{ money(pageTotal) }}</view>
      </view>
    </view>

    <view v-if="!loading && bills.length === 0" class="empty-card">
      <view class="empty-title">当前条件下没有账单</view>
      <view class="empty-desc">可以放宽账期、状态或搜索条件后再试。</view>
    </view>

    <view v-for="item in bills" :key="item.id" class="bill-card">
      <view class="bill-head">
        <view>
          <view class="bill-number">{{ item.invoice_no }}</view>
          <view class="bill-meta">{{ item.owner_name }} / {{ item.warehouse_name }}</view>
        </view>
        <view class="status-pill">{{ billStatusLabel(item.status) }}</view>
      </view>

      <view class="bill-period">{{ item.period_label || '未关联账期' }}</view>

      <view class="metric-grid">
        <view class="metric-item">
          <text class="metric-label">开票日期</text>
          <text class="metric-value">{{ item.issue_date || '-' }}</text>
        </view>
        <view class="metric-item">
          <text class="metric-label">到期日期</text>
          <text class="metric-value">{{ item.due_date || '-' }}</text>
        </view>
        <view class="metric-item">
          <text class="metric-label">明细条数</text>
          <text class="metric-value">{{ item.line_count || 0 }}</text>
        </view>
      </view>

      <view class="money-row">
        <view class="money-block">
          <text class="money-label">不含税</text>
          <text class="money-value">{{ money(item.subtotal) }}</text>
        </view>
        <view class="money-block">
          <text class="money-label">税额</text>
          <text class="money-value">{{ money(item.tax_total) }}</text>
        </view>
        <view class="money-block total">
          <text class="money-label">合计</text>
          <text class="money-value">{{ money(item.total) }}</text>
        </view>
      </view>

      <view class="bill-actions">
        <button class="btn-primary" @click="openBillDetail(item)">查看详情</button>
        <button class="btn-ghost" @click="exportSingleBill(item)">导出账单</button>
      </view>
    </view>

    <view v-if="hasMore" class="more-wrap">
      <button class="btn-ghost" :disabled="loadingMore" @click="loadMore">
        {{ loadingMore ? '加载中...' : '加载更多' }}
      </button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const BILL_STATUS_LABELS = {
  DRAFT: '草稿',
  ISSUED: '已开票',
  PAID: '已收款',
  VOID: '作废',
}

const periods = ref([])
const bills = ref([])
const billsTotal = ref(0)
const loading = ref(false)
const loadingMore = ref(false)
const exporting = ref(false)
const page = ref(1)
const pageSize = ref(20)

const selectedOwnerId = ref('')
const selectedWarehouseId = ref('')
const selectedPeriodId = ref('')
const selectedStatus = ref('')
const searchKeyword = ref('')

function asList(payload) {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.results)) return payload.results
  return []
}

function toNumber(value) {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

function money(value) {
  return `¥${toNumber(value).toFixed(2)}`
}

function billStatusLabel(code) {
  return BILL_STATUS_LABELS[code] || code || '-'
}

const ownerPickerOptions = computed(() => {
  const map = new Map()
  periods.value.forEach((period) => {
    if (!map.has(period.owner)) {
      map.set(period.owner, {
        id: String(period.owner),
        label: period.owner_name || `货主 #${period.owner}`,
      })
    }
  })
  return [{ id: '', label: '全部货主' }, ...Array.from(map.values())]
})

const warehousePickerOptions = computed(() => {
  const map = new Map()
  periods.value
    .filter((period) => !selectedOwnerId.value || String(period.owner) === String(selectedOwnerId.value))
    .forEach((period) => {
      if (!map.has(period.warehouse)) {
        map.set(period.warehouse, {
          id: String(period.warehouse),
          label: period.warehouse_name || `仓库 #${period.warehouse}`,
        })
      }
    })
  return [{ id: '', label: '全部仓库' }, ...Array.from(map.values())]
})

const visiblePeriods = computed(() => {
  return periods.value.filter((period) => {
    if (selectedOwnerId.value && String(period.owner) !== String(selectedOwnerId.value)) return false
    if (selectedWarehouseId.value && String(period.warehouse) !== String(selectedWarehouseId.value)) return false
    return true
  })
})

const periodPickerOptions = computed(() => {
  return [{ id: '', label: '全部账期' }].concat(
    visiblePeriods.value.map((period) => ({
      id: String(period.id),
      label: period.label,
    }))
  )
})

const statusOptions = [
  { code: '', label: '全部状态' },
  { code: 'DRAFT', label: '草稿' },
  { code: 'ISSUED', label: '已开票' },
  { code: 'PAID', label: '已收款' },
  { code: 'VOID', label: '作废' },
]

const ownerPickerIndex = computed(() => {
  const index = ownerPickerOptions.value.findIndex((item) => String(item.id) === String(selectedOwnerId.value))
  return index >= 0 ? index : 0
})

const warehousePickerIndex = computed(() => {
  const index = warehousePickerOptions.value.findIndex((item) => String(item.id) === String(selectedWarehouseId.value))
  return index >= 0 ? index : 0
})

const periodPickerIndex = computed(() => {
  const index = periodPickerOptions.value.findIndex((item) => String(item.id) === String(selectedPeriodId.value))
  return index >= 0 ? index : 0
})

const statusPickerIndex = computed(() => {
  const index = statusOptions.findIndex((item) => String(item.code) === String(selectedStatus.value))
  return index >= 0 ? index : 0
})

const hasMore = computed(() => bills.value.length < billsTotal.value)
const pageTotal = computed(() => bills.value.reduce((sum, item) => sum + toNumber(item.total), 0))

function buildBillParams(currentPage = 1) {
  return {
    owner: selectedOwnerId.value || undefined,
    warehouse: selectedWarehouseId.value || undefined,
    period: selectedPeriodId.value || undefined,
    status: selectedStatus.value || undefined,
    search: (searchKeyword.value || '').trim() || undefined,
    page: currentPage,
    page_size: pageSize.value,
  }
}

async function loadPeriods() {
  periods.value = asList(await api.billingPeriods())
}

async function loadBills(reset = false) {
  if (loading.value || loadingMore.value) return

  if (reset) {
    page.value = 1
    loading.value = true
  } else {
    loadingMore.value = true
  }

  try {
    const payload = await api.billingBills(buildBillParams(page.value))
    const list = asList(payload)
    billsTotal.value = Number(payload?.count || list.length)
    bills.value = reset ? list : bills.value.concat(list)
    if (!reset && list.length) {
      page.value += 1
    } else if (reset && list.length < billsTotal.value) {
      page.value = 2
    }
  } catch (error) {
    if (reset) {
      bills.value = []
      billsTotal.value = 0
    }
    console.error('load billing bills failed:', error)
  } finally {
    loading.value = false
    loadingMore.value = false
    uni.stopPullDownRefresh()
  }
}

async function refreshBills() {
  await loadBills(true)
}

function openBillDetail(item) {
  const owner = selectedOwnerId.value || item.owner
  const warehouse = selectedWarehouseId.value || item.warehouse
  const period = selectedPeriodId.value || item.period
  uni.navigateTo({
    url: `/pages/billing/bill_detail?id=${item.id}&owner=${owner}&warehouse=${warehouse}&period=${period}`,
  })
}

async function exportBills() {
  exporting.value = true
  try {
    await api.billingBillsExport(buildBillParams(1))
  } catch (error) {
    console.error('export billing bills failed:', error)
  } finally {
    exporting.value = false
  }
}

async function exportSingleBill(item) {
  try {
    await api.billingBillExport(item.id)
  } catch (error) {
    console.error('export single billing bill failed:', error)
  }
}

function goBackOverview() {
  const query = []
  if (selectedOwnerId.value) query.push(`owner=${encodeURIComponent(selectedOwnerId.value)}`)
  if (selectedWarehouseId.value) query.push(`warehouse=${encodeURIComponent(selectedWarehouseId.value)}`)
  if (selectedPeriodId.value) query.push(`period=${encodeURIComponent(selectedPeriodId.value)}`)

  uni.redirectTo({
    url: query.length ? `/pages/billing/overview?${query.join('&')}` : '/pages/billing/overview',
  })
}

async function onOwnerChange(event) {
  const next = ownerPickerOptions.value[Number(event.detail.value)]
  selectedOwnerId.value = next?.id || ''
  if (selectedWarehouseId.value && !warehousePickerOptions.value.some((item) => String(item.id) === String(selectedWarehouseId.value))) {
    selectedWarehouseId.value = ''
  }
  if (selectedPeriodId.value && !periodPickerOptions.value.some((item) => String(item.id) === String(selectedPeriodId.value))) {
    selectedPeriodId.value = ''
  }
  await refreshBills()
}

async function onWarehouseChange(event) {
  const next = warehousePickerOptions.value[Number(event.detail.value)]
  selectedWarehouseId.value = next?.id || ''
  if (selectedPeriodId.value && !periodPickerOptions.value.some((item) => String(item.id) === String(selectedPeriodId.value))) {
    selectedPeriodId.value = ''
  }
  await refreshBills()
}

async function onPeriodChange(event) {
  const next = periodPickerOptions.value[Number(event.detail.value)]
  selectedPeriodId.value = next?.id || ''
  await refreshBills()
}

async function onStatusChange(event) {
  const next = statusOptions[Number(event.detail.value)]
  selectedStatus.value = next?.code || ''
  await refreshBills()
}

async function loadMore() {
  if (!hasMore.value) return
  await loadBills(false)
}

async function refreshAll() {
  try {
    await loadPeriods()
  } catch (error) {
    periods.value = []
    console.error('load billing periods for bill list failed:', error)
  }
  await refreshBills()
}

onLoad((query) => {
  selectedOwnerId.value = query?.owner ? String(query.owner) : ''
  selectedWarehouseId.value = query?.warehouse ? String(query.warehouse) : ''
  selectedPeriodId.value = query?.period ? String(query.period) : ''
  selectedStatus.value = query?.status ? String(query.status) : ''
  searchKeyword.value = query?.search ? String(query.search) : ''
  refreshAll()
})

onPullDownRefresh(() => {
  refreshAll()
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: linear-gradient(180deg, #f7f8fc 0%, #eef3ff 100%);
  padding: 24rpx;
  box-sizing: border-box;
}

.hero {
  padding: 28rpx;
  border-radius: 28rpx;
  margin-bottom: 20rpx;
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.14), transparent 35%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 55%, #fdf7ef 100%);
  box-shadow: 0 14rpx 40rpx rgba(17, 24, 39, 0.08);
}

.hero-tag {
  display: inline-flex;
  align-items: center;
  padding: 8rpx 16rpx;
  margin-bottom: 12rpx;
  border-radius: 999rpx;
  background: #fff;
  color: #0b5fff;
  font-size: 20rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.hero-title {
  font-size: 42rpx;
  font-weight: 700;
  color: #182033;
}

.hero-desc {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #61708b;
}

.loading-banner,
.filter-card,
.summary-card,
.bill-card,
.empty-card {
  background: #fff;
  border-radius: 24rpx;
  padding: 22rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, 0.05);
}

.loading-banner {
  color: #0b5fff;
  font-size: 24rpx;
}

.filter-grid,
.summary-grid,
.metric-grid {
  display: grid;
  gap: 14rpx;
}

.filter-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.summary-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.metric-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 18rpx;
}

.picker-box {
  min-height: 118rpx;
  padding: 18rpx;
  border-radius: 20rpx;
  background: #f7f9fd;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.picker-label,
.summary-label,
.metric-label,
.money-label {
  font-size: 22rpx;
  color: #8290a9;
}

.picker-value,
.summary-value,
.metric-value,
.money-value {
  font-size: 28rpx;
  font-weight: 700;
  color: #1f2940;
}

.keyword-input {
  margin-top: 16rpx;
  min-height: 84rpx;
  padding: 0 20rpx;
  border-radius: 18rpx;
  background: #f7f9fd;
  font-size: 26rpx;
}

.filter-actions,
.bill-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  margin-top: 16rpx;
}

.btn-primary,
.btn-ghost {
  flex: 1;
  border-radius: 18rpx;
}

.btn-primary {
  background: linear-gradient(135deg, #0b5fff, #378dff);
  color: #fff;
}

.btn-ghost {
  background: #eef3fb;
  color: #243149;
}

.bill-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
}

.bill-number {
  font-size: 32rpx;
  font-weight: 700;
  color: #162034;
}

.bill-meta,
.bill-period {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #70809a;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 10rpx 18rpx;
  border-radius: 999rpx;
  background: #eef4ff;
  color: #0b5fff;
  font-size: 22rpx;
  font-weight: 700;
}

.metric-item,
.money-block {
  padding: 16rpx;
  border-radius: 18rpx;
  background: #f7f9fd;
}

.money-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12rpx;
  margin-top: 16rpx;
}

.money-block.total {
  background: linear-gradient(160deg, #eef5ff, #f8fbff);
}

.empty-card {
  text-align: center;
}

.empty-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #1b2438;
}

.empty-desc {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #7d8aa1;
}

.more-wrap {
  padding-bottom: 28rpx;
}
</style>
