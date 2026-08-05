<template>
  <view class="page">
    <BossNav active="revenue" />
    <view class="hero">
      <view class="hero-head">
        <view class="hero-copy">
          <view class="hero-tag">仓储经营分析中心</view>
          <view class="hero-title">收入与计费</view>
   <!--       <view class="hero-desc">这里专门回答“今天干的活有没有变成钱”，继续保留货主排行、账单、应计和明细追溯。</view> -->
        </view>
        <button class="btn-ghost hero-btn" @click="logout">退出</button>
      </view>
      <view class="hero-meta">
        <text>{{ operatorName }}</text>
        <text>{{ scopeWarehouseName }}</text>
        <text>{{ scopeDateText }}</text>
      </view>
    </view>

    <view class="filter-card">
      <BossScopeFilter @change="refreshAll" />
      <view class="filter-grid">
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

        <view class="picker-box info-box">
          <text class="picker-label">账单概况</text>
          <text class="picker-value">{{ summary.issuedBillCount }} 张已出账</text>
          <text class="picker-sub">{{ moneyGroupText(summary.issuedBills) }}</text>
        </view>
      </view>

      <view class="filter-actions">
        <button class="btn-primary" @click="refreshAll">刷新</button>
        <button class="btn-ghost" @click="clearFilters">清空筛选</button>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步仓库计费数据...</view>
    <BossDataStatus :meta="payload?.meta" :error="dataError" :stale="stale" />

    <view v-else-if="!hasAnyData" class="empty-card">
      <view class="empty-title">当前范围没有计费数据</view>
      <view class="empty-desc">可以切换货主、日期、收费类型或状态后继续查看。</view>
    </view>

    <template v-else>
      <view class="kpi-grid">
        <view class="kpi-card blue">
          <view class="kpi-label">货主数</view>
          <view class="kpi-value">{{ summary.ownerCount }}</view>
          <view class="kpi-sub">当前筛选范围内有收费的货主</view>
        </view>
        <view class="kpi-card gold">
          <view class="kpi-label">应计条数</view>
          <view class="kpi-value">{{ summary.accrualCount }}</view>
          <view class="kpi-sub">已排除作废和冲销行</view>
        </view>
        <view class="kpi-card pink">
          <view class="kpi-label">应计金额（分币种）</view>
          <view class="kpi-value">{{ moneyGroupText(summary.accruals) }}</view>
          <view class="kpi-sub">不跨币种合计</view>
        </view>
        <view class="kpi-card green">
          <view class="kpi-label">草稿账单</view>
          <view class="kpi-value">{{ moneyGroupText(summary.draftBills) }}</view>
          <view class="kpi-sub">草稿不计入已出账</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">货主排名</view>
          <view class="section-desc">点击货主进入单货主计费明细。</view>
        </view>
        <view
          v-for="(row, index) in ownerRows"
          :key="`${row.currency}-${row.owner}`"
          class="row-card clickable"
          @click="openOwnerDetail(row.owner)"
        >
          <view class="rank-pill">{{ index + 1 }}</view>
          <view class="row-main">
            <view class="row-title">{{ row.ownerName }}</view>
            <view class="row-sub">{{ row.accrualCount }} 条 · 税额 {{ money(row.taxTotal, row.currency) }}</view>
          </view>
          <view class="row-money">{{ money(row.total, row.currency) }}</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">收费结构</view>
          <view class="section-desc">按收费类型观察本仓主要收费来源。</view>
        </view>
        <view v-for="row in chargeRows" :key="`${row.currency}-${row.chargeType}`" class="row-card">
          <view class="row-main">
            <view class="row-title">{{ row.label }}</view>
            <view class="row-sub">{{ row.accrualCount }} 条 · 税额 {{ money(row.taxTotal, row.currency) }}</view>
          </view>
          <view class="row-money">{{ money(row.total, row.currency) }}</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">状态分布</view>
          <view class="section-desc">快速观察未锁定、已锁定和已开票的收费记录。</view>
        </view>
        <view class="status-grid">
          <view v-for="row in statusRows" :key="`${row.currency}-${row.status}`" class="status-card">
            <view class="status-name">{{ row.label }}</view>
            <view class="status-count">{{ row.accrualCount }} 条</view>
            <view class="status-money">{{ money(row.total, row.currency) }}</view>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">每日趋势</view>
          <view class="section-desc">按服务日期汇总，便于发现高收费日。</view>
        </view>
        <view v-for="row in trendRows" :key="`${row.currency}-${row.serviceDate}`" class="trend-row">
          <view class="trend-date">{{ row.serviceDate }}</view>
          <view class="trend-bar-wrap">
            <view class="trend-bar" :style="{ width: `${row.width}%` }"></view>
          </view>
          <view class="trend-total">{{ money(row.total, row.currency) }}</view>
        </view>
      </view>

      <view class="split-grid">
        <view class="section">
          <view class="section-head">
            <view class="section-title">最近收费记录</view>
            <view class="section-desc">点击查看单条收费记录的完整来源。</view>
          </view>
          <view
            v-for="item in recentAccruals"
            :key="item.id"
            class="row-card clickable"
            @click="openAccrualDetail(item)"
          >
            <view class="row-main">
              <view class="row-title">{{ item.owner_name }} · {{ chargeTypeLabel(item.charge_type) }}</view>
              <view class="row-sub">{{ item.service_date }} · 数量 {{ qty(item.quantity) }}</view>
            </view>
            <view class="row-money">{{ money(item.amount, item.currency) }}</view>
          </view>
        </view>

        <view class="section">
          <view class="section-head">
            <view class="section-title">最近账单</view>
            <view class="section-desc">点击进入账单详情。</view>
          </view>
          <view
            v-for="bill in recentBills"
            :key="bill.id"
            class="row-card clickable"
            @click="openBillDetail(bill)"
          >
            <view class="row-main">
              <view class="row-title">{{ bill.invoice_no }}</view>
              <view class="row-sub">
                {{ bill.owner_name }} · {{ bill.period_label || '未绑定账期' }} · {{ billStatusLabel(bill.status) }}
              </view>
            </view>
            <view class="row-money">{{ money(bill.total, bill.currency) }}</view>
          </view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import BossNav from '@/components/boss-nav.vue'
import BossScopeFilter from '@/components/boss-scope-filter.vue'
import BossDataStatus from '@/components/boss-data-status.vue'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
import { api, buildQuery } from '@/utils/request'
import {
  ACCRUAL_STATUS_LABELS,
  CHARGE_TYPE_LABELS,
  asList,
  chargeTypeLabel,
  billStatusLabel,
  money,
  qty,
  toNumber,
} from '@/utils/billing'

const auth = useAuth()
const bossScope = useBossScope()
const payload = ref(null)
const loading = ref(false)
const dataError = ref(null)
const loadedFingerprint = ref('')
const stale = computed(() => !!dataError.value && loadedFingerprint.value === bossScope.fingerprint && !!payload.value)

const draftChargeType = ref('')
const draftStatus = ref('')

const chargeTypeOptions = [
  { code: '', label: '全部类型' },
  ...Object.entries(CHARGE_TYPE_LABELS).map(([code, label]) => ({ code, label })),
]
const statusOptions = [
  { code: '', label: '全部状态' },
  ...Object.entries(ACCRUAL_STATUS_LABELS).map(([code, label]) => ({ code, label })),
]

const scope = computed(() => payload.value?.scope || {})
const operatorName = computed(() => auth.user?.display_name || auth.user?.username || '老板账号')
const scopeWarehouseName = computed(() => scope.value.warehouse_name || '全部授权仓库')
const scopeDateText = computed(() => `${bossScope.date_from} 至 ${bossScope.date_to}`)

const summary = computed(() => {
  const source = payload.value?.summary || {}
  return {
    ownerCount: Number(source.owner_count || 0),
    accrualCount: Number(source.accrual_count || 0),
    accruals: asList(source.accruals_by_currency),
    issuedBillCount: Number(source.issued_bill_count || 0),
    issuedBills: asList(source.issued_bills_by_currency),
    draftBills: asList(source.draft_bills_by_currency),
  }
})

const chargeTypePickerIndex = computed(() => {
  const index = chargeTypeOptions.findIndex((item) => item.code === draftChargeType.value)
  return index >= 0 ? index : 0
})

const statusPickerIndex = computed(() => {
  const index = statusOptions.findIndex((item) => item.code === draftStatus.value)
  return index >= 0 ? index : 0
})

const ownerRows = computed(() =>
  asList(payload.value?.by_owner)
    .map((item) => ({
      owner: item.owner,
      ownerName: item.owner_name,
      accrualCount: Number(item.accrual_count || 0),
      taxTotal: toNumber(item.tax_total),
      total: toNumber(item.total),
      currency: item.currency,
    }))
)

const chargeRows = computed(() =>
  asList(payload.value?.by_charge_type)
    .map((item) => ({
      chargeType: item.charge_type,
      label: chargeTypeLabel(item.charge_type),
      accrualCount: Number(item.accrual_count || 0),
      taxTotal: toNumber(item.tax_total),
      total: toNumber(item.total),
      currency: item.currency,
    }))
)

const statusRows = computed(() =>
  asList(payload.value?.by_status).map((item) => ({
    status: item.status,
    label: ACCRUAL_STATUS_LABELS[item.status] || item.status || '-',
    accrualCount: Number(item.accrual_count || 0),
    total: toNumber(item.total),
    currency: item.currency,
  }))
)

const trendRows = computed(() => {
  const rows = asList(payload.value?.by_service_date).map((item) => ({
    serviceDate: item.service_date,
    total: toNumber(item.total),
    currency: item.currency,
  }))
  const maxByCurrency = rows.reduce((memo, row) => {
    memo[row.currency] = Math.max(memo[row.currency] || 0, row.total)
    return memo
  }, {})
  return rows.map((row) => ({
    ...row,
    width: maxByCurrency[row.currency] > 0
      ? Math.max((row.total / maxByCurrency[row.currency]) * 100, 8)
      : 0,
  }))
})

const recentAccruals = computed(() => asList(payload.value?.recent_accruals))
const recentBills = computed(() => asList(payload.value?.recent_bills))
const hasAnyData = computed(
  () =>
    summary.value.accrualCount > 0 ||
    ownerRows.value.length > 0 ||
    recentAccruals.value.length > 0 ||
    recentBills.value.length > 0
)

function requestParams() {
  return {
    ...bossScope.params,
    charge_type: draftChargeType.value || undefined,
    status: draftStatus.value || undefined,
    recent_limit: 12,
  }
}

async function refreshAll() {
  const fingerprint = bossScope.fingerprint
  loading.value = true
  try {
    payload.value = await api.billingWarehouseOverview(requestParams())
    loadedFingerprint.value = fingerprint
    dataError.value = null
  } catch (error) {
    dataError.value = error
    if (loadedFingerprint.value !== fingerprint || error.kind === 'FORBIDDEN') payload.value = null
    console.error('load boss billing overview failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function clearFilters() {
  draftChargeType.value = ''
  draftStatus.value = ''
  refreshAll()
}

function moneyGroupText(groups) {
  const rows = asList(groups)
  return rows.length ? rows.map((row) => money(row.total, row.currency)).join(' / ') : '-'
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

function openOwnerDetail(ownerId) {
  const query = buildQuery({
    owner: ownerId,
    warehouse: scope.value.warehouse,
    date_from: bossScope.date_from,
    date_to: bossScope.date_to,
    charge_type: draftChargeType.value,
    status: draftStatus.value,
  })
  uni.navigateTo({
    url: `/pages/billing/owner_detail?${query}`,
  })
}

function openAccrualDetail(item) {
  const query = buildQuery({
    id: item.id,
    owner: item.owner,
    warehouse: item.warehouse,
    date_from: bossScope.date_from,
    date_to: bossScope.date_to,
  })
  uni.navigateTo({
    url: `/pages/billing/accrual_detail?${query}`,
  })
}

function openBillDetail(bill) {
  const query = buildQuery({
    id: bill.id,
    owner: bill.owner,
    warehouse: bill.warehouse,
    period: bill.period,
  })
  uni.navigateTo({
    url: `/pages/billing/bill_detail?${query}`,
  })
}

async function logout() {
  await auth.logout()
  bossScope.clear()
  uni.reLaunch({
    url: '/pages/login',
  })
}

onLoad((query) => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  bossScope.restore(auth.user)
  if (query?.owner) bossScope.owner = String(query.owner)
  if (query?.date_from && query?.date_to) bossScope.setDates(String(query.date_from), String(query.date_to))
  draftChargeType.value = query?.charge_type ? String(query.charge_type) : ''
  draftStatus.value = query?.status ? String(query.status) : ''
  bossScope.loadContext(auth.user).then(refreshAll).catch((error) => {
    dataError.value = error
    payload.value = null
  })
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
.section,
.empty-card {
  background: #fff;
  border-radius: 24rpx;
  padding: 22rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, 0.05);
}

.hero {
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.15), transparent 36%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 55%, #fff8ef 100%);
}

.hero-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18rpx;
}

.hero-copy {
  flex: 1;
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
  line-height: 1.6;
  color: #5f6d87;
}

.hero-btn {
  margin: 0;
  flex-shrink: 0;
}

.hero-meta {
  margin-top: 16rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  font-size: 22rpx;
  color: #6b7b96;
}

.filter-grid,
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
}

.picker-box,
.status-card,
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

.date-range-card {
  grid-column: 1 / -1;
}

.date-range-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
}

.info-box {
  justify-content: center;
}

.picker-label,
.picker-sub,
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

.kpi-card.blue {
  background: linear-gradient(160deg, #eef5ff, #f8fbff);
}

.kpi-card.gold {
  background: linear-gradient(160deg, #fff8e9, #fffcf4);
}

.kpi-card.pink {
  background: linear-gradient(160deg, #fff0f6, #fff8fb);
}

.kpi-card.green {
  background: linear-gradient(160deg, #eefdf3, #f8fffb);
}

.kpi-value {
  margin-top: 12rpx;
  font-size: 40rpx;
  line-height: 1.1;
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
  line-height: 1.6;
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

.rank-pill {
  width: 52rpx;
  height: 52rpx;
  border-radius: 999rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #eef4ff;
  color: #0b5fff;
  font-size: 24rpx;
  font-weight: 700;
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

.status-grid,
.split-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14rpx;
}

.status-name {
  font-size: 24rpx;
  color: #73809a;
}

.status-count {
  margin-top: 10rpx;
  font-size: 30rpx;
  font-weight: 700;
  color: #162034;
}

.status-money {
  margin-top: 8rpx;
  font-size: 24rpx;
  color: #0b5fff;
}

.trend-row {
  display: flex;
  align-items: center;
  gap: 14rpx;
  padding: 18rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.trend-date {
  width: 170rpx;
  font-size: 24rpx;
  color: #55627c;
}

.trend-bar-wrap {
  flex: 1;
  height: 18rpx;
  border-radius: 999rpx;
  background: #e9eef7;
  overflow: hidden;
}

.trend-bar {
  height: 100%;
  border-radius: 999rpx;
  background: linear-gradient(90deg, #0b5fff, #3ba0ff);
}

.trend-total {
  width: 150rpx;
  text-align: right;
  font-size: 24rpx;
  font-weight: 600;
  color: #172034;
}
</style>
