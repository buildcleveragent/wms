<template>
  <view class="page">
    <view class="hero">
      <view class="hero-top">
        <view>
          <view class="hero-tag">Bill Detail</view>
          <view class="hero-title">{{ bill?.invoice_no || '账单详情' }}</view>
        </view>
        <view class="status-pill">{{ billStatusLabel(bill?.status) }}</view>
      </view>
      <view v-if="bill" class="hero-meta">
        <text>{{ bill.owner_name }}</text>
        <text>{{ bill.warehouse_name }}</text>
        <text>{{ bill.period_label }}</text>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步账单和明细...</view>

    <view v-if="bill" class="headline-grid">
      <view class="headline-card">
        <view class="headline-label">不含税小计</view>
        <view class="headline-value">{{ money(bill.subtotal) }}</view>
        <view class="headline-sub">账单整体金额</view>
      </view>
      <view class="headline-card">
        <view class="headline-label">税额合计</view>
        <view class="headline-value">{{ money(bill.tax_total) }}</view>
        <view class="headline-sub">账单整体税额</view>
      </view>
      <view class="headline-card">
        <view class="headline-label">价税合计</view>
        <view class="headline-value">{{ money(bill.total) }}</view>
        <view class="headline-sub">{{ bill.due_date ? `到期 ${bill.due_date}` : '未设置到期日' }}</view>
      </view>
      <view class="headline-card dark">
        <view class="headline-label white-soft">当前筛选结果</view>
        <view class="headline-value white">{{ filteredSummary.lineCount }} 条</view>
        <view class="headline-sub white-soft">{{ money(filteredSummary.total) }}</view>
      </view>
    </view>

    <template v-if="bill">
      <view class="filter-card">
        <view class="filter-grid">
          <picker mode="date" :value="draftDateFrom" @change="onDraftDateFromChange">
            <view class="picker-box">
              <text class="picker-label">日期从</text>
              <text class="picker-value">{{ draftDateFrom || '开始日期' }}</text>
            </view>
          </picker>

          <picker mode="date" :value="draftDateTo" @change="onDraftDateToChange">
            <view class="picker-box">
              <text class="picker-label">日期到</text>
              <text class="picker-value">{{ draftDateTo || '结束日期' }}</text>
            </view>
          </picker>

          <picker :range="chargeTypePickerOptions" range-key="label" :value="chargeTypePickerIndex" @change="onDraftChargeTypeChange">
            <view class="picker-box">
              <text class="picker-label">费用类型</text>
              <text class="picker-value">{{ chargeTypePickerOptions[chargeTypePickerIndex]?.label || '全部类型' }}</text>
            </view>
          </picker>
        </view>

        <input
          v-model="draftKeyword"
          class="keyword-input"
          placeholder="匹配说明或费用类型"
          confirm-type="search"
          @confirm="applyFilters"
        />

        <view class="filter-actions">
          <button class="btn-primary" @click="applyFilters">筛选</button>
          <button class="btn-ghost" @click="clearFilters">清空</button>
          <button class="btn-ghost" @click="goBackOverview">返回总览</button>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">当前筛选汇总</view>
            <view class="section-desc">领导看这块就能快速回答每种收费项各收了多少钱。</view>
          </view>
        </view>

        <view class="group-grid">
          <view v-for="row in groupedRows" :key="row.chargeType" class="group-card">
            <view class="group-label">{{ row.label }}</view>
            <view class="group-total">{{ money(row.total) }}</view>
            <view class="group-sub">{{ row.lineCount }} 条 · 税额 {{ money(row.taxTotal) }}</view>
          </view>
        </view>

        <view v-if="!groupedRows.length" class="empty-inline">当前筛选下没有汇总结果。</view>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">账单明细</view>
            <view class="section-desc">保留服务日期、数量、单价、金额和说明，足够追溯，不走复杂配置页。</view>
          </view>
        </view>

        <view v-for="line in filteredLines" :key="line.id" class="line-card">
          <view class="line-head">
            <view class="line-title">{{ chargeTypeLabel(line.charge_type) }}</view>
            <view class="line-amount">{{ money(line.amount) }}</view>
          </view>
          <view class="line-date">{{ line.service_date }}</view>
          <view class="metric-grid">
            <view class="metric-item">
              <text class="metric-label">数量</text>
              <text class="metric-value">{{ qty(line.quantity) }}</text>
            </view>
            <view class="metric-item">
              <text class="metric-label">单价/费率</text>
              <text class="metric-value">{{ rate(line.unit_price) }}</text>
            </view>
            <view class="metric-item">
              <text class="metric-label">税额</text>
              <text class="metric-value">{{ money(line.tax_amount) }}</text>
            </view>
          </view>
          <view class="line-desc">{{ line.description || '无额外说明' }}</view>
        </view>

        <view v-if="!filteredLines.length" class="empty-inline">当前筛选下没有账单明细。</view>
      </view>
    </template>

    <view v-else-if="!loading" class="empty-card">
      <view class="empty-title">当前账单暂不可用</view>
      <view class="empty-desc">请确认账单已生成，或返回计费总览重新选择账期。</view>
      <button class="btn-ghost return-btn" @click="goBackOverview">返回总览</button>
    </view>
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

const BILL_STATUS_LABELS = {
  DRAFT: '草稿',
  ISSUED: '已开票',
  PAID: '已收款',
  VOID: '作废',
}

const billId = ref('')
const ownerId = ref('')
const warehouseId = ref('')
const periodId = ref('')

const bill = ref(null)
const lines = ref([])
const loading = ref(false)

const draftDateFrom = ref('')
const draftDateTo = ref('')
const draftChargeType = ref('')
const draftKeyword = ref('')

const appliedDateFrom = ref('')
const appliedDateTo = ref('')
const appliedChargeType = ref('')
const appliedKeyword = ref('')

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

function billStatusLabel(code) {
  return BILL_STATUS_LABELS[code] || code || '-'
}

function buildQuery(params = {}) {
  return Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
}

const chargeTypePickerOptions = computed(() => {
  const seen = new Map()
  lines.value.forEach((line) => {
    if (!seen.has(line.charge_type)) {
      seen.set(line.charge_type, {
        code: line.charge_type,
        label: chargeTypeLabel(line.charge_type),
      })
    }
  })
  return [{ code: '', label: '全部类型' }, ...Array.from(seen.values())]
})

const chargeTypePickerIndex = computed(() => {
  const index = chargeTypePickerOptions.value.findIndex((item) => String(item.code) === String(draftChargeType.value))
  return index >= 0 ? index : 0
})

const filteredLines = computed(() => {
  return lines.value.filter((line) => {
    if (appliedChargeType.value && line.charge_type !== appliedChargeType.value) return false
    if (appliedDateFrom.value && String(line.service_date) < appliedDateFrom.value) return false
    if (appliedDateTo.value && String(line.service_date) > appliedDateTo.value) return false

    if (appliedKeyword.value) {
      const keyword = appliedKeyword.value.toLowerCase()
      const haystack = [
        line.description || '',
        line.service_date || '',
        chargeTypeLabel(line.charge_type),
      ]
        .join(' ')
        .toLowerCase()

      if (!haystack.includes(keyword)) return false
    }

    return true
  })
})

const groupedRows = computed(() => {
  const map = new Map()
  filteredLines.value.forEach((line) => {
    const key = line.charge_type || 'UNKNOWN'
    if (!map.has(key)) {
      map.set(key, {
        chargeType: key,
        label: chargeTypeLabel(key),
        lineCount: 0,
        subtotal: 0,
        taxTotal: 0,
        total: 0,
      })
    }

    const item = map.get(key)
    item.lineCount += 1
    item.subtotal += toNumber(line.amount)
    item.taxTotal += toNumber(line.tax_amount)
    item.total = item.subtotal + item.taxTotal
  })

  return Array.from(map.values()).sort((a, b) => b.total - a.total)
})

const filteredSummary = computed(() => {
  const summary = filteredLines.value.reduce(
    (acc, line) => {
      acc.lineCount += 1
      acc.subtotal += toNumber(line.amount)
      acc.taxTotal += toNumber(line.tax_amount)
      acc.total = acc.subtotal + acc.taxTotal
      return acc
    },
    { lineCount: 0, subtotal: 0, taxTotal: 0, total: 0 }
  )
  return summary
})

function applyFilters() {
  appliedDateFrom.value = draftDateFrom.value
  appliedDateTo.value = draftDateTo.value
  appliedChargeType.value = draftChargeType.value
  appliedKeyword.value = (draftKeyword.value || '').trim()
}

function clearFilters() {
  draftDateFrom.value = ''
  draftDateTo.value = ''
  draftChargeType.value = ''
  draftKeyword.value = ''
  applyFilters()
}

function onDraftDateFromChange(event) {
  draftDateFrom.value = event.detail.value || ''
}

function onDraftDateToChange(event) {
  draftDateTo.value = event.detail.value || ''
}

function onDraftChargeTypeChange(event) {
  const next = chargeTypePickerOptions.value[Number(event.detail.value)]
  draftChargeType.value = next?.code || ''
}

async function loadBillDetail() {
  if (!billId.value) {
    uni.showToast({
      title: '缺少账单编号',
      icon: 'none',
    })
    return
  }

  loading.value = true
  try {
    const payload = await api.billingBillDetail(billId.value)
    bill.value = payload || null
    lines.value = Array.isArray(payload?.lines) ? payload.lines : []
  } catch (error) {
    bill.value = null
    lines.value = []
    console.error('load billing bill detail failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function goBackOverview() {
  const query = buildQuery({
    owner: ownerId.value,
    warehouse: warehouseId.value,
    period: periodId.value,
  })

  if (getCurrentPages().length > 1) {
    uni.navigateBack()
    return
  }

  uni.redirectTo({
    url: query ? `/pages/billing/overview?${query}` : '/pages/billing/overview',
  })
}

onLoad((query) => {
  billId.value = query?.id ? String(query.id) : ''
  ownerId.value = query?.owner ? String(query.owner) : ''
  warehouseId.value = query?.warehouse ? String(query.warehouse) : ''
  periodId.value = query?.period ? String(query.period) : ''

  draftDateFrom.value = query?.date_from ? String(query.date_from) : ''
  draftDateTo.value = query?.date_to ? String(query.date_to) : ''
  draftChargeType.value = query?.charge_type ? String(query.charge_type) : ''
  draftKeyword.value = query?.q ? String(query.q) : ''
  applyFilters()
  loadBillDetail()
})

onPullDownRefresh(() => {
  loadBillDetail()
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
    linear-gradient(135deg, #fff8ef 0%, #fffdf8 55%, #f5f9ff 100%);
  box-shadow: 0 14rpx 36rpx rgba(17, 24, 39, 0.07);
}

.hero-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14rpx;
}

.hero-tag {
  display: inline-flex;
  align-items: center;
  padding: 8rpx 16rpx;
  margin-bottom: 12rpx;
  border-radius: 999rpx;
  background: #fff;
  color: #b45309;
  font-size: 20rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.hero-title {
  font-size: 42rpx;
  font-weight: 700;
  color: #182033;
}

.hero-meta {
  margin-top: 14rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  font-size: 22rpx;
  color: #66748d;
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

.loading-banner {
  margin-bottom: 16rpx;
  padding: 16rpx 20rpx;
  border-radius: 20rpx;
  background: rgba(11, 95, 255, 0.08);
  color: #0b5fff;
  font-size: 24rpx;
}

.headline-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
  margin-bottom: 18rpx;
}

.headline-card,
.filter-card,
.section,
.empty-card {
  background: #fff;
  border-radius: 24rpx;
  padding: 22rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, 0.05);
}

.headline-card.dark {
  background: linear-gradient(155deg, #0f172a 0%, #16233d 100%);
}

.headline-label {
  font-size: 22rpx;
  color: #7a879f;
}

.headline-label.white-soft,
.headline-sub.white-soft {
  color: rgba(255, 255, 255, 0.75);
}

.headline-value {
  margin-top: 12rpx;
  font-size: 38rpx;
  font-weight: 700;
  line-height: 1.08;
  color: #172033;
}

.headline-value.white {
  color: #fff;
}

.headline-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #8592aa;
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

.group-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12rpx;
}

.group-card {
  padding: 18rpx;
  border-radius: 18rpx;
  background: #f7f9fd;
}

.group-label {
  font-size: 24rpx;
  color: #6f7c94;
}

.group-total {
  margin-top: 10rpx;
  font-size: 34rpx;
  font-weight: 700;
  color: #162034;
}

.group-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #7f8ca3;
  line-height: 1.5;
}

.line-card {
  padding: 22rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.line-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.line-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #182134;
}

.line-amount {
  font-size: 30rpx;
  font-weight: 700;
  color: #0b5fff;
}

.line-date {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #7a879f;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12rpx;
  margin-top: 16rpx;
}

.metric-item {
  padding: 16rpx;
  border-radius: 18rpx;
  background: #f7f9fd;
}

.metric-label {
  display: block;
  font-size: 20rpx;
  color: #8090ab;
}

.metric-value {
  display: block;
  margin-top: 8rpx;
  font-size: 26rpx;
  font-weight: 600;
  color: #1b2438;
}

.line-desc {
  margin-top: 14rpx;
  font-size: 22rpx;
  line-height: 1.6;
  color: #627089;
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

.return-btn {
  margin-top: 18rpx;
}

.empty-inline {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #7d8aa1;
}
</style>
