<template>
  <view class="page">
    <view class="hero">
      <view class="hero-tag">Billing Overview</view>
      <view class="hero-title">计费总览</view>
      <view class="hero-desc">面向管理层的账期结果页，重点看总额、构成、趋势和账单状态。</view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步账期和账单数据...</view>

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

        <picker
          v-if="periodPickerOptions.length"
          :range="periodPickerOptions"
          range-key="label"
          :value="periodPickerIndex"
          @change="onPeriodChange"
        >
          <view class="picker-box">
            <text class="picker-label">账期</text>
            <text class="picker-value">{{ periodPickerOptions[periodPickerIndex]?.label || '选择账期' }}</text>
          </view>
        </picker>
        <view v-else class="picker-box disabled">
          <text class="picker-label">账期</text>
          <text class="picker-value">暂无账期</text>
        </view>
      </view>

      <button class="refresh-btn" @click="refreshAll">刷新</button>
    </view>

    <view v-if="!periods.length && !loading" class="empty-card">
      <view class="empty-title">还没有可展示的账期</view>
      <view class="empty-desc">先创建 BillingPeriod 并生成应计或账单，这里才会展示管理层可看的结果。</view>
    </view>

    <template v-else-if="selectedPeriod">
      <view class="period-card">
        <view class="period-head">
          <view>
            <view class="period-title">{{ selectedPeriod.label }}</view>
            <view class="period-sub">{{ selectedPeriod.owner_name }} / {{ selectedPeriod.warehouse_name }}</view>
          </view>
          <view class="status-pill">{{ periodStatusLabel(selectedPeriod.status) }}</view>
        </view>
        <view class="period-meta">
          <text>{{ selectedPeriod.start_date }} 至 {{ selectedPeriod.end_date }}</text>
          <text>{{ previewScopeText }}</text>
        </view>
      </view>

      <view class="kpi-grid">
        <view class="kpi-card blue">
          <view class="kpi-label">应计条数</view>
          <view class="kpi-value">{{ summary.accrualCount }}</view>
          <view class="kpi-sub">当前账期口径</view>
        </view>
        <view class="kpi-card gold">
          <view class="kpi-label">不含税小计</view>
          <view class="kpi-value">{{ money(summary.subtotal) }}</view>
          <view class="kpi-sub">应计汇总</view>
        </view>
        <view class="kpi-card pink">
          <view class="kpi-label">税额合计</view>
          <view class="kpi-value">{{ money(summary.taxTotal) }}</view>
          <view class="kpi-sub">税额汇总</view>
        </view>
        <view class="kpi-card green">
          <view class="kpi-label">价税合计</view>
          <view class="kpi-value">{{ money(summary.total) }}</view>
          <view class="kpi-sub">{{ currentBill ? '已出账金额' : '未出账估算' }}</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">按收费类型汇总</view>
            <view class="section-desc">金额越高的费用项排越前，适合快速看构成。</view>
          </view>
        </view>

        <view
          v-for="row in chargeRows"
          :key="row.chargeType"
          class="summary-row clickable"
          @click="openBillDetail({ charge_type: row.chargeType })"
        >
          <view class="summary-main">
            <view class="summary-title">{{ row.label }}</view>
            <view class="summary-sub">{{ row.accrualCount }} 条 · 税额 {{ money(row.taxTotal) }}</view>
          </view>
          <view class="summary-money">{{ money(row.total) }}</view>
        </view>

        <view v-if="!chargeRows.length" class="empty-inline">当前账期还没有收费类型汇总。</view>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">按状态汇总</view>
            <view class="section-desc">快速判断还有多少费用未锁、已锁或已开票。</view>
          </view>
        </view>

        <view class="status-grid">
          <view v-for="row in statusRows" :key="row.status" class="status-card">
            <view class="status-name">{{ row.label }}</view>
            <view class="status-count">{{ row.accrualCount }} 条</view>
            <view class="status-money">{{ money(row.total) }}</view>
          </view>
        </view>

        <view v-if="!statusRows.length" class="empty-inline">当前账期还没有状态分布。</view>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">每日费用趋势</view>
            <view class="section-desc">用简化条形展示每天费用高低，点某天可跳到账单明细。</view>
          </view>
        </view>

        <view
          v-for="row in trendRows"
          :key="row.serviceDate"
          class="trend-row"
          @click="openBillDetail({ date_from: row.serviceDate, date_to: row.serviceDate })"
        >
          <view class="trend-date">{{ row.serviceDate }}</view>
          <view class="trend-bar-wrap">
            <view class="trend-bar" :style="{ width: `${row.width}%` }"></view>
          </view>
          <view class="trend-total">{{ money(row.total) }}</view>
        </view>

        <view v-if="!trendRows.length" class="empty-inline">当前账期还没有每日趋势。</view>
      </view>

      <view class="bill-card">
        <view class="bill-head">
          <view>
            <view class="section-title white">当前账单</view>
            <view class="section-desc white-soft">{{ currentBill ? '该账期已生成账单，可继续下钻到明细。' : '该账期暂未生成账单。' }}</view>
          </view>
          <view v-if="currentBill" class="bill-total">{{ money(toNumber(currentBill.total)) }}</view>
        </view>

        <template v-if="currentBill">
          <view class="bill-number">{{ currentBill.invoice_no }}</view>
          <view class="bill-meta">
            <text>{{ billStatusLabel(currentBill.status) }}</text>
            <text>{{ currentBill.issue_date }}</text>
            <text v-if="currentBill.due_date">到期 {{ currentBill.due_date }}</text>
          </view>
        </template>
        <view v-else class="bill-empty">账期可以先看应计和趋势，出账后这里会出现结算单。</view>

        <button class="bill-btn" :disabled="!currentBill" @click="openBillDetail()">查看账单详情</button>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">最近应计预览</view>
            <view class="section-desc">只放最近 10 条，让页面不是黑盒，但不堆技术细节。</view>
          </view>
        </view>

        <view v-for="item in recentAccruals" :key="item.id" class="accrual-row">
          <view class="accrual-main">
            <view class="accrual-title">{{ chargeTypeLabel(item.charge_type) }}</view>
            <view class="accrual-sub">{{ item.service_date }} · 数量 {{ qty(item.quantity) }} · 单价 {{ rate(item.unit_price) }}</view>
          </view>
          <view class="accrual-money">{{ money(item.amount) }}</view>
        </view>

        <view v-if="!recentAccruals.length" class="empty-inline">当前账期没有最近应计预览。</view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const CHARGE_TYPE_LABELS = {
  RECEIVE: '收货',
  PUTAWAY: '上架/移库入',
  RELOC: '移库',
  PICK: '拣货',
  REVIEW: '复核',
  PACK: '打包',
  LOAD: '装车',
  DISPATCH: '发运/订单处理',
  COUNT: '盘点',
  ADJUST: '调整',
  STORAGE: '仓储/保管',
}

const PERIOD_STATUS_LABELS = {
  OPEN: '开账',
  CLOSED: '关账',
  INVOICED: '已开票',
}

const BILL_STATUS_LABELS = {
  DRAFT: '草稿',
  ISSUED: '已开票',
  PAID: '已收款',
  VOID: '作废',
}

const ACCRUAL_STATUS_LABELS = {
  OPEN: '未锁定',
  LOCKED: '已锁定',
  INVOICED: '已开票',
  VOID: '作废',
}

const loading = ref(false)
const periods = ref([])
const preview = ref(null)
const currentBill = ref(null)
const recentAccruals = ref([])

const selectedOwnerId = ref('')
const selectedWarehouseId = ref('')
const selectedPeriodId = ref('')

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

function qty(value) {
  return toNumber(value).toFixed(4)
}

function rate(value) {
  return toNumber(value).toFixed(4)
}

function chargeTypeLabel(code) {
  return CHARGE_TYPE_LABELS[code] || code || '-'
}

function periodStatusLabel(code) {
  return PERIOD_STATUS_LABELS[code] || code || '-'
}

function billStatusLabel(code) {
  return BILL_STATUS_LABELS[code] || code || '-'
}

function accrualStatusLabel(code) {
  return ACCRUAL_STATUS_LABELS[code] || code || '-'
}

function buildQuery(params = {}) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
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
  return visiblePeriods.value.map((period) => ({
    id: String(period.id),
    label: `${period.label} · ${periodStatusLabel(period.status)}`,
  }))
})

const selectedPeriod = computed(() => {
  return visiblePeriods.value.find((period) => String(period.id) === String(selectedPeriodId.value)) || null
})

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

const summary = computed(() => {
  const subtotal = toNumber(preview.value?.subtotal)
  const taxTotal = toNumber(preview.value?.tax_total)
  const total = currentBill.value ? toNumber(currentBill.value.total) : subtotal + taxTotal
  return {
    accrualCount: Number(preview.value?.accrual_count || 0),
    subtotal,
    taxTotal,
    total,
  }
})

const previewScopeText = computed(() => {
  if (!selectedPeriod.value) return ''
  return selectedPeriod.value.status === 'OPEN' ? '开账账期内未锁定应计' : '已锁定账期应计'
})

const chargeRows = computed(() => {
  const rows = asList(preview.value?.by_charge_type).map((item) => {
    const subtotal = toNumber(item.subtotal)
    const taxTotal = toNumber(item.tax_total)
    return {
      chargeType: item.charge_type,
      label: chargeTypeLabel(item.charge_type),
      accrualCount: Number(item.accrual_count || 0),
      subtotal,
      taxTotal,
      total: subtotal + taxTotal,
    }
  })
  return rows.sort((a, b) => b.total - a.total)
})

const statusRows = computed(() => {
  return asList(preview.value?.by_status).map((item) => {
    const subtotal = toNumber(item.subtotal)
    const taxTotal = toNumber(item.tax_total)
    return {
      status: item.status,
      label: accrualStatusLabel(item.status),
      accrualCount: Number(item.accrual_count || 0),
      total: subtotal + taxTotal,
    }
  })
})

const trendRows = computed(() => {
  const baseRows = asList(preview.value?.by_service_date).map((item) => {
    const subtotal = toNumber(item.subtotal)
    const taxTotal = toNumber(item.tax_total)
    return {
      serviceDate: item.service_date,
      accrualCount: Number(item.accrual_count || 0),
      total: subtotal + taxTotal,
    }
  })

  const maxValue = Math.max(...baseRows.map((item) => item.total), 0)
  return baseRows.map((item) => ({
    ...item,
    width: maxValue > 0 ? Math.max(12, Math.round((item.total / maxValue) * 100)) : 0,
  }))
})

function clearPeriodPayload() {
  preview.value = null
  currentBill.value = null
  recentAccruals.value = []
}

function ensureSelectedPeriod() {
  if (!visiblePeriods.value.length) {
    selectedPeriodId.value = ''
    clearPeriodPayload()
    return false
  }

  const exists = visiblePeriods.value.some((period) => String(period.id) === String(selectedPeriodId.value))
  if (!exists) {
    selectedPeriodId.value = String(visiblePeriods.value[0].id)
  }
  return true
}

function buildAccrualParams(period) {
  if (!period) return {}
  const scoped = {
    owner: period.owner,
    warehouse: period.warehouse,
  }
  if (period.status === 'OPEN') {
    return {
      ...scoped,
      period__isnull: true,
      status: 'OPEN',
      service_date__gte: period.start_date,
      service_date__lte: period.end_date,
    }
  }
  return {
    ...scoped,
    period: period.id,
  }
}

async function loadSelectedPeriodData() {
  if (!selectedPeriod.value) {
    clearPeriodPayload()
    return
  }

  loading.value = true
  try {
    const period = selectedPeriod.value
    const [previewRes, billsRes, accrualsRes] = await Promise.all([
      api.billingPeriodPreview(period.id),
      api.billingBills({ period: period.id }),
      api.billingAccruals(buildAccrualParams(period)),
    ])

    preview.value = previewRes || null
    const bills = asList(billsRes)
    currentBill.value = bills.length ? bills[0] : null
    recentAccruals.value = asList(accrualsRes).slice(0, 10)
  } catch (error) {
    clearPeriodPayload()
    console.error('load billing overview failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

async function refreshAll() {
  loading.value = true
  try {
    const list = asList(await api.billingPeriods())
    periods.value = list

    if (selectedOwnerId.value && !ownerPickerOptions.value.some((item) => String(item.id) === String(selectedOwnerId.value))) {
      selectedOwnerId.value = ''
    }

    if (selectedWarehouseId.value && !warehousePickerOptions.value.some((item) => String(item.id) === String(selectedWarehouseId.value))) {
      selectedWarehouseId.value = ''
    }

    if (!ensureSelectedPeriod()) return
    await loadSelectedPeriodData()
  } catch (error) {
    periods.value = []
    clearPeriodPayload()
    console.error('refresh billing periods failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

async function onOwnerChange(event) {
  const next = ownerPickerOptions.value[Number(event.detail.value)]
  selectedOwnerId.value = next?.id || ''

  if (selectedWarehouseId.value && !warehousePickerOptions.value.some((item) => String(item.id) === String(selectedWarehouseId.value))) {
    selectedWarehouseId.value = ''
  }

  if (!ensureSelectedPeriod()) return
  await loadSelectedPeriodData()
}

async function onWarehouseChange(event) {
  const next = warehousePickerOptions.value[Number(event.detail.value)]
  selectedWarehouseId.value = next?.id || ''

  if (!ensureSelectedPeriod()) return
  await loadSelectedPeriodData()
}

async function onPeriodChange(event) {
  const next = periodPickerOptions.value[Number(event.detail.value)]
  selectedPeriodId.value = next?.id || ''
  await loadSelectedPeriodData()
}

function openBillDetail(extraQuery = {}) {
  if (!currentBill.value?.id) {
    uni.showToast({
      title: '当前账期还没有账单',
      icon: 'none',
    })
    return
  }

  const query = buildQuery({
    id: currentBill.value.id,
    period: selectedPeriod.value?.id,
    owner: selectedOwnerId.value || selectedPeriod.value?.owner,
    warehouse: selectedWarehouseId.value || selectedPeriod.value?.warehouse,
    ...extraQuery,
  })

  uni.navigateTo({
    url: `/pages/billing/bill_detail?${query}`,
  })
}

onLoad((query) => {
  selectedOwnerId.value = query?.owner ? String(query.owner) : ''
  selectedWarehouseId.value = query?.warehouse ? String(query.warehouse) : ''
  selectedPeriodId.value = query?.period ? String(query.period) : ''
  refreshAll()
})

onPullDownRefresh(() => {
  refreshAll()
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: linear-gradient(180deg, #f6f8fc 0%, #edf3ff 100%);
  padding: 24rpx;
  box-sizing: border-box;
}

.hero {
  padding: 28rpx;
  border-radius: 28rpx;
  margin-bottom: 20rpx;
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.16), transparent 35%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 55%, #fff7ed 100%);
  box-shadow: 0 14rpx 40rpx rgba(30, 64, 175, 0.08);
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
  font-size: 44rpx;
  font-weight: 700;
  color: #162034;
}

.hero-desc {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #5f6d87;
}

.loading-banner {
  margin-bottom: 16rpx;
  padding: 16rpx 20rpx;
  border-radius: 20rpx;
  background: rgba(11, 95, 255, 0.08);
  color: #0b5fff;
  font-size: 24rpx;
}

.filter-card,
.period-card,
.section,
.empty-card {
  background: #fff;
  border-radius: 24rpx;
  padding: 22rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, 0.05);
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14rpx;
}

.picker-box {
  min-height: 122rpx;
  padding: 18rpx;
  border-radius: 20rpx;
  background: #f7f9fd;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.picker-box.disabled {
  opacity: 0.6;
}

.picker-label {
  font-size: 22rpx;
  color: #8290a9;
}

.picker-value {
  font-size: 28rpx;
  font-weight: 600;
  color: #1f2940;
  line-height: 1.4;
}

.refresh-btn {
  margin-top: 16rpx;
  background: linear-gradient(135deg, #0b5fff, #378dff);
  color: #fff;
  border-radius: 18rpx;
}

.period-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
}

.period-title {
  font-size: 34rpx;
  font-weight: 700;
  color: #1d263b;
}

.period-sub,
.period-meta {
  margin-top: 8rpx;
  font-size: 24rpx;
  color: #66748d;
}

.period-meta {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
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

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
  margin-bottom: 18rpx;
}

.kpi-card {
  padding: 22rpx;
  border-radius: 22rpx;
  color: #172134;
}

.kpi-card.blue { background: linear-gradient(160deg, #eef5ff, #f8fbff); }
.kpi-card.gold { background: linear-gradient(160deg, #fff8e9, #fffcf4); }
.kpi-card.pink { background: linear-gradient(160deg, #fff0f6, #fff8fb); }
.kpi-card.green { background: linear-gradient(160deg, #eefdf3, #f8fffb); }

.kpi-label {
  font-size: 22rpx;
  color: #73809a;
}

.kpi-value {
  margin-top: 12rpx;
  font-size: 40rpx;
  font-weight: 700;
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

.section-title.white {
  color: #fff;
}

.section-desc {
  margin-top: 6rpx;
  font-size: 22rpx;
  line-height: 1.6;
  color: #73819c;
}

.section-desc.white-soft {
  color: rgba(255, 255, 255, 0.78);
}

.summary-row,
.accrual-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  padding: 20rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.summary-row.clickable:active {
  opacity: 0.72;
}

.summary-main,
.accrual-main {
  flex: 1;
}

.summary-title,
.accrual-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #1b2438;
}

.summary-sub,
.accrual-sub {
  margin-top: 6rpx;
  font-size: 22rpx;
  color: #7a879f;
  line-height: 1.5;
}

.summary-money,
.accrual-money {
  font-size: 28rpx;
  font-weight: 700;
  color: #0f172a;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12rpx;
}

.status-card {
  padding: 18rpx;
  border-radius: 18rpx;
  background: #f7f9fd;
}

.status-name {
  font-size: 24rpx;
  color: #73809a;
}

.status-count {
  margin-top: 8rpx;
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
  width: 148rpx;
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

.bill-card {
  padding: 24rpx;
  border-radius: 28rpx;
  margin-bottom: 18rpx;
  background: linear-gradient(155deg, #0f172a 0%, #17243e 100%);
  box-shadow: 0 18rpx 44rpx rgba(15, 23, 42, 0.18);
}

.bill-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
}

.bill-total {
  font-size: 38rpx;
  font-weight: 700;
  color: #96f2c1;
}

.bill-number {
  margin-top: 20rpx;
  font-size: 34rpx;
  font-weight: 700;
  color: #fff;
}

.bill-meta {
  margin-top: 12rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  font-size: 22rpx;
  color: rgba(255, 255, 255, 0.8);
}

.bill-empty {
  margin-top: 20rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.76);
}

.bill-btn {
  margin-top: 20rpx;
  background: #fff;
  color: #0f172a;
  border-radius: 18rpx;
}

.bill-btn[disabled] {
  opacity: 0.58;
}

.empty-card {
  text-align: center;
}

.empty-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #1b2438;
}

.empty-desc,
.empty-inline {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #7d8aa1;
}
</style>
