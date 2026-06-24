<template>
  <view class="page">
    <view class="hero">
      <view class="hero-tag">Owner Billing</view>
      <view class="hero-title">{{ ownerName }}</view>
      <view class="hero-desc">{{ scopeWarehouseName }} · {{ scopeDateText }}</view>
    </view>

    <view class="filter-card">
      <view class="filter-grid">
        <picker mode="date" :value="draftDateFrom" @change="onDateFromChange">
          <view class="picker-box">
            <text class="picker-label">开始日期</text>
            <text class="picker-value">{{ draftDateFrom || '不限' }}</text>
          </view>
        </picker>

        <picker mode="date" :value="draftDateTo" @change="onDateToChange">
          <view class="picker-box">
            <text class="picker-label">结束日期</text>
            <text class="picker-value">{{ draftDateTo || '不限' }}</text>
          </view>
        </picker>

        <picker :range="chargeTypeOptions" range-key="label" :value="chargeTypePickerIndex" @change="onChargeTypeChange">
          <view class="picker-box">
            <text class="picker-label">收费类型</text>
            <text class="picker-value">{{ chargeTypeOptions[chargeTypePickerIndex]?.label || '全部类型' }}</text>
          </view>
        </picker>

        <picker :range="statusOptions" range-key="label" :value="statusPickerIndex" @change="onStatusChange">
          <view class="picker-box">
            <text class="picker-label">应计状态</text>
            <text class="picker-value">{{ statusOptions[statusPickerIndex]?.label || '全部状态' }}</text>
          </view>
        </picker>
      </view>

      <input
        v-model="keyword"
        class="keyword-input"
        placeholder="搜索账单号、指纹或收费类型"
      />

      <view class="filter-actions">
        <button class="btn-primary" @click="refreshAll">刷新</button>
        <button class="btn-ghost" @click="goBackOverview">返回总览</button>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步货主计费明细...</view>

    <template v-else>
      <view class="kpi-grid">
        <view class="kpi-card blue">
          <view class="kpi-label">应计条数</view>
          <view class="kpi-value">{{ summary.accrualCount }}</view>
          <view class="kpi-sub">当前筛选范围</view>
        </view>
        <view class="kpi-card gold">
          <view class="kpi-label">不含税小计</view>
          <view class="kpi-value">{{ money(summary.subtotal) }}</view>
          <view class="kpi-sub">税额 {{ money(summary.taxTotal) }}</view>
        </view>
        <view class="kpi-card green">
          <view class="kpi-label">价税合计</view>
          <view class="kpi-value">{{ money(summary.total) }}</view>
          <view class="kpi-sub">账单 {{ filteredBills.length }} 张</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">账期列表</view>
          <view class="section-desc">当前货主在本仓已有的账期。</view>
        </view>
        <view v-for="period in periods" :key="period.id" class="row-card">
          <view class="row-main">
            <view class="row-title">{{ period.label }}</view>
            <view class="row-sub">{{ period.start_date }} 至 {{ period.end_date }} · {{ period.status }}</view>
          </view>
          <view class="row-money">{{ period.currency }}</view>
        </view>
        <view v-if="!periods.length" class="empty-inline">没有账期数据。</view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">账单列表</view>
          <view class="section-desc">点击进入账单详情。</view>
        </view>
        <view
          v-for="bill in filteredBills"
          :key="bill.id"
          class="row-card clickable"
          @click="openBillDetail(bill)"
        >
          <view class="row-main">
            <view class="row-title">{{ bill.invoice_no }}</view>
            <view class="row-sub">{{ bill.period_label || '未绑定账期' }} · {{ billStatusLabel(bill.status) }}</view>
          </view>
          <view class="row-money">{{ money(bill.total) }}</view>
        </view>
        <view v-if="!filteredBills.length" class="empty-inline">没有匹配的账单。</view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">全部收费记录</view>
          <view class="section-desc">点击任一记录查看来源事件、账期和账单关联。</view>
        </view>
        <view
          v-for="item in filteredAccruals"
          :key="item.id"
          class="row-card clickable"
          @click="openAccrualDetail(item)"
        >
          <view class="row-main">
            <view class="row-title">{{ chargeTypeLabel(item.charge_type) }}</view>
            <view class="row-sub">{{ item.service_date }} · {{ accrualStatusLabel(item.status) }} · 指纹 {{ item.acc_fingerprint }}</view>
          </view>
          <view class="row-money">{{ money(item.amount) }}</view>
        </view>
        <view v-if="!filteredAccruals.length" class="empty-inline">当前筛选下没有收费记录。</view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { api, buildQuery } from '@/utils/request'
import { useAuth } from '@/store/auth'
import {
  ACCRUAL_STATUS_LABELS,
  CHARGE_TYPE_LABELS,
  accrualStatusLabel,
  asList,
  billStatusLabel,
  chargeTypeLabel,
  defaultDateRange,
  money,
  toNumber,
} from '@/utils/billing'

const auth = useAuth()
const chargeTypeOptions = [
  { code: '', label: '全部类型' },
  ...Object.entries(CHARGE_TYPE_LABELS).map(([code, label]) => ({ code, label })),
]
const statusOptions = [
  { code: '', label: '全部状态' },
  ...Object.entries(ACCRUAL_STATUS_LABELS).map(([code, label]) => ({ code, label })),
]

const loading = ref(false)
const ownerId = ref('')
const warehouseId = ref('')
const draftDateFrom = ref('')
const draftDateTo = ref('')
const draftChargeType = ref('')
const draftStatus = ref('')
const keyword = ref('')

const overview = ref(null)
const periods = ref([])
const bills = ref([])
const accruals = ref([])

const summary = computed(() => {
  const source = overview.value?.summary || {}
  return {
    accrualCount: Number(source.accrual_count || 0),
    subtotal: toNumber(source.subtotal),
    taxTotal: toNumber(source.tax_total),
    total: toNumber(source.total),
  }
})

const ownerName = computed(() => overview.value?.scope?.owner_name || '货主明细')
const scopeWarehouseName = computed(() => overview.value?.scope?.warehouse_name || '当前仓库')
const scopeDateText = computed(() => {
  if (draftDateFrom.value && draftDateTo.value) return `${draftDateFrom.value} 至 ${draftDateTo.value}`
  return '全部日期'
})

const chargeTypePickerIndex = computed(() => {
  const index = chargeTypeOptions.findIndex((item) => item.code === draftChargeType.value)
  return index >= 0 ? index : 0
})

const statusPickerIndex = computed(() => {
  const index = statusOptions.findIndex((item) => item.code === draftStatus.value)
  return index >= 0 ? index : 0
})

const filteredBills = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return bills.value
  return bills.value.filter((bill) =>
    [bill.invoice_no, bill.period_label, bill.status, billStatusLabel(bill.status)]
      .join(' ')
      .toLowerCase()
      .includes(q)
  )
})

const filteredAccruals = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return accruals.value
  return accruals.value.filter((item) =>
    [
      item.acc_fingerprint,
      item.service_date,
      item.status,
      item.charge_type,
      chargeTypeLabel(item.charge_type),
    ]
      .join(' ')
      .toLowerCase()
      .includes(q)
  )
})

function overviewParams() {
  return {
    owner: ownerId.value || undefined,
    warehouse: warehouseId.value || undefined,
    date_from: draftDateFrom.value || undefined,
    date_to: draftDateTo.value || undefined,
    charge_type: draftChargeType.value || undefined,
    status: draftStatus.value || undefined,
    recent_limit: 12,
  }
}

function accrualParams(activeOwnerId) {
  return {
    owner: activeOwnerId || undefined,
    warehouse: warehouseId.value || undefined,
    charge_type: draftChargeType.value || undefined,
    status: draftStatus.value || undefined,
    service_date__gte: draftDateFrom.value || undefined,
    service_date__lte: draftDateTo.value || undefined,
  }
}

async function refreshAll() {
  loading.value = true
  try {
    const overviewPayload = await api.billingWarehouseOverview(overviewParams())
    overview.value = overviewPayload || null

    const activeOwnerId = ownerId.value || overviewPayload?.scope?.owner
    if (activeOwnerId && !ownerId.value) {
      ownerId.value = String(activeOwnerId)
    }
    if (overviewPayload?.scope?.warehouse && !warehouseId.value) {
      warehouseId.value = String(overviewPayload.scope.warehouse)
    }

    if (!activeOwnerId) {
      periods.value = []
      bills.value = []
      accruals.value = []
      return
    }

    const [periodRes, billRes, accrualRes] = await Promise.all([
      api.billingPeriods({
        owner: activeOwnerId,
        warehouse: warehouseId.value || undefined,
      }),
      api.billingBills({
        owner: activeOwnerId,
        warehouse: warehouseId.value || undefined,
      }),
      api.billingAccruals(accrualParams(activeOwnerId)),
    ])

    periods.value = asList(periodRes)
    bills.value = asList(billRes)
    accruals.value = asList(accrualRes)
  } catch (error) {
    overview.value = null
    periods.value = []
    bills.value = []
    accruals.value = []
    console.error('load owner billing detail failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function onDateFromChange(event) {
  draftDateFrom.value = event.detail.value || ''
  refreshAll()
}

function onDateToChange(event) {
  draftDateTo.value = event.detail.value || ''
  refreshAll()
}

function onChargeTypeChange(event) {
  const next = chargeTypeOptions[Number(event.detail.value)]
  draftChargeType.value = next?.code || ''
  refreshAll()
}

function onStatusChange(event) {
  const next = statusOptions[Number(event.detail.value)]
  draftStatus.value = next?.code || ''
  refreshAll()
}

function openBillDetail(bill) {
  const query = buildQuery({
    id: bill.id,
    owner: ownerId.value || bill.owner,
    warehouse: warehouseId.value || bill.warehouse,
    period: bill.period,
  })
  uni.navigateTo({
    url: `/pages/billing/bill_detail?${query}`,
  })
}

function openAccrualDetail(item) {
  const query = buildQuery({
    id: item.id,
    owner: ownerId.value || item.owner,
    warehouse: warehouseId.value || item.warehouse,
    date_from: draftDateFrom.value,
    date_to: draftDateTo.value,
  })
  uni.navigateTo({
    url: `/pages/billing/accrual_detail?${query}`,
  })
}

function goBackOverview() {
  const query = buildQuery({
    owner: ownerId.value,
    warehouse: warehouseId.value,
    date_from: draftDateFrom.value,
    date_to: draftDateTo.value,
    charge_type: draftChargeType.value,
    status: draftStatus.value,
  })
  uni.redirectTo({
    url: query ? `/pages/billing/overview?${query}` : '/pages/billing/overview',
  })
}

onLoad((query) => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  const range = defaultDateRange()
  ownerId.value = query?.owner ? String(query.owner) : ''
  warehouseId.value = query?.warehouse ? String(query.warehouse) : ''
  draftDateFrom.value = query?.date_from ? String(query.date_from) : range.start
  draftDateTo.value = query?.date_to ? String(query.date_to) : range.end
  draftChargeType.value = query?.charge_type ? String(query.charge_type) : ''
  draftStatus.value = query?.status ? String(query.status) : ''
  refreshAll()
})

onPullDownRefresh(() => {
  refreshAll()
})
</script>

<style scoped>
.page {
  padding: 24rpx;
}

.hero,
.filter-card,
.section {
  background: #fff;
  border-radius: 24rpx;
  padding: 22rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, 0.05);
}

.hero {
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.14), transparent 35%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 55%, #fff7ed 100%);
}

.hero-tag {
  display: inline-flex;
  align-items: center;
  padding: 8rpx 16rpx;
  border-radius: 999rpx;
  background: #fff;
  color: #0b5fff;
  font-size: 20rpx;
  font-weight: 700;
}

.hero-title {
  margin-top: 14rpx;
  font-size: 40rpx;
  font-weight: 700;
  color: #162034;
}

.hero-desc {
  margin-top: 10rpx;
  font-size: 24rpx;
  color: #5f6d87;
}

.filter-grid,
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
}

.picker-box,
.kpi-card {
  min-height: 116rpx;
  padding: 18rpx;
  border-radius: 20rpx;
  background: #f7f9fd;
}

.picker-box {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.picker-label,
.kpi-label {
  font-size: 22rpx;
  color: #8290a9;
}

.picker-value,
.kpi-value {
  font-size: 28rpx;
  font-weight: 600;
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

.filter-actions {
  display: flex;
  gap: 12rpx;
  margin-top: 16rpx;
}

.filter-actions button {
  flex: 1;
}

.loading-banner {
  margin-bottom: 16rpx;
  padding: 16rpx 20rpx;
  border-radius: 20rpx;
  background: rgba(11, 95, 255, 0.08);
  color: #0b5fff;
  font-size: 24rpx;
}

.kpi-card.blue {
  background: linear-gradient(160deg, #eef5ff, #f8fbff);
}

.kpi-card.gold {
  background: linear-gradient(160deg, #fff8e9, #fffcf4);
}

.kpi-card.green {
  background: linear-gradient(160deg, #eefdf3, #f8fffb);
}

.kpi-value {
  margin-top: 12rpx;
  font-size: 40rpx;
}

.kpi-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #7e8ca6;
}

.section-head {
  margin-bottom: 14rpx;
}

.section-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #1c253a;
}

.section-desc {
  margin-top: 6rpx;
  font-size: 22rpx;
  color: #73819c;
}

.row-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  padding: 20rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.clickable:active {
  opacity: 0.72;
}

.row-main {
  flex: 1;
}

.row-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #1b2438;
}

.row-sub {
  margin-top: 6rpx;
  font-size: 22rpx;
  color: #7a879f;
  line-height: 1.5;
}

.row-money {
  font-size: 28rpx;
  font-weight: 700;
  color: #0f172a;
}

.empty-inline {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #7d8aa1;
}
</style>
