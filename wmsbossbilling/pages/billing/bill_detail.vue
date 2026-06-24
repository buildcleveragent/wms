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

    <template v-else-if="bill">
      <view class="headline-grid">
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
          placeholder="匹配说明、应计指纹或费用类型"
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
          <view class="section-title">当前筛选汇总</view>
          <view class="section-desc">快速看清每种收费项各收了多少钱。</view>
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
          <view class="section-title">账单明细</view>
          <view class="section-desc">点击任一账单行可继续查看对应收费记录。</view>
        </view>
        <view
          v-for="line in filteredLines"
          :key="line.id"
          class="line-card clickable"
          @click="openAccrualDetail(line)"
        >
          <view class="line-head">
            <view class="line-title">{{ chargeTypeLabel(line.charge_type) }}</view>
            <view class="line-amount">{{ money(line.amount) }}</view>
          </view>
          <view class="line-date">{{ line.service_date }}</view>
          <view class="line-meta">应计指纹 {{ line.accrual_fingerprint || '-' }}</view>
          <view class="metric-grid">
            <view class="metric-item">
              <text class="metric-label">数量</text>
              <text class="metric-value">{{ qty(line.quantity) }}</text>
            </view>
            <view class="metric-item">
              <text class="metric-label">单价/费率</text>
              <text class="metric-value">{{ qty(line.unit_price) }}</text>
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

    <view v-else class="empty-card">
      <view class="empty-title">当前账单暂不可用</view>
      <view class="empty-desc">请确认账单已生成，或返回计费总览重新选择账单。</view>
      <button class="btn-ghost return-btn" @click="goBackOverview">返回总览</button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { api, buildQuery } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { asList, billStatusLabel, chargeTypeLabel, money, qty, toNumber } from '@/utils/billing'

const auth = useAuth()
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
  const index = chargeTypePickerOptions.value.findIndex(
    (item) => String(item.code) === String(draftChargeType.value)
  )
  return index >= 0 ? index : 0
})

const filteredLines = computed(() =>
  lines.value.filter((line) => {
    if (appliedChargeType.value && line.charge_type !== appliedChargeType.value) return false
    if (appliedDateFrom.value && String(line.service_date) < appliedDateFrom.value) return false
    if (appliedDateTo.value && String(line.service_date) > appliedDateTo.value) return false

    if (appliedKeyword.value) {
      const keyword = appliedKeyword.value.toLowerCase()
      const haystack = [
        line.description || '',
        line.service_date || '',
        line.accrual_fingerprint || '',
        chargeTypeLabel(line.charge_type),
      ]
        .join(' ')
        .toLowerCase()

      if (!haystack.includes(keyword)) return false
    }

    return true
  })
)

const filteredSummary = computed(() => ({
  lineCount: filteredLines.value.length,
  total: filteredLines.value.reduce((sum, line) => sum + toNumber(line.amount) + toNumber(line.tax_amount), 0),
}))

const groupedRows = computed(() => {
  const groups = new Map()
  filteredLines.value.forEach((line) => {
    const key = line.charge_type || ''
    const row = groups.get(key) || {
      chargeType: key,
      label: chargeTypeLabel(key),
      lineCount: 0,
      taxTotal: 0,
      total: 0,
    }
    row.lineCount += 1
    row.taxTotal += toNumber(line.tax_amount)
    row.total += toNumber(line.amount) + toNumber(line.tax_amount)
    groups.set(key, row)
  })
  return Array.from(groups.values()).sort((a, b) => b.total - a.total)
})

async function loadBill() {
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
    lines.value = asList(payload?.lines)
    ownerId.value = ownerId.value || String(payload?.owner || '')
    warehouseId.value = warehouseId.value || String(payload?.warehouse || '')
    periodId.value = periodId.value || String(payload?.period || '')
    applyFilters()
  } catch (error) {
    bill.value = null
    lines.value = []
    console.error('load billing bill detail failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function applyFilters() {
  appliedDateFrom.value = draftDateFrom.value
  appliedDateTo.value = draftDateTo.value
  appliedChargeType.value = draftChargeType.value
  appliedKeyword.value = draftKeyword.value.trim()
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

function openAccrualDetail(line) {
  if (!line?.accrual) return
  const query = buildQuery({
    id: line.accrual,
    owner: ownerId.value || bill.value?.owner,
    warehouse: warehouseId.value || bill.value?.warehouse,
    date_from: appliedDateFrom.value || draftDateFrom.value,
    date_to: appliedDateTo.value || draftDateTo.value,
  })
  uni.navigateTo({
    url: `/pages/billing/accrual_detail?${query}`,
  })
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
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  billId.value = query?.id ? String(query.id) : ''
  ownerId.value = query?.owner ? String(query.owner) : ''
  warehouseId.value = query?.warehouse ? String(query.warehouse) : ''
  periodId.value = query?.period ? String(query.period) : ''
  loadBill()
})

onPullDownRefresh(() => {
  loadBill()
})
</script>

<style scoped>
.page {
  padding: 24rpx;
}

.hero,
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

.hero {
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.14), transparent 35%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 55%, #fff8ef 100%);
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
  color: #0b5fff;
  font-size: 20rpx;
  font-weight: 700;
}

.hero-title {
  font-size: 40rpx;
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

.headline-grid,
.filter-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
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

.picker-box {
  min-height: 116rpx;
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

.group-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12rpx;
}

.group-card {
  padding: 18rpx;
  border-radius: 18rpx;
  background: #f7f9fd;
}

.group-label {
  font-size: 24rpx;
  color: #73809a;
}

.group-total {
  margin-top: 10rpx;
  font-size: 32rpx;
  font-weight: 700;
  color: #162034;
}

.group-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #0b5fff;
}

.line-card {
  padding: 22rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.clickable:active {
  opacity: 0.72;
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
  color: #0f172a;
}

.line-date,
.line-meta {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #7a879f;
}

.line-meta {
  color: #4f5d77;
  word-break: break-all;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12rpx;
  margin-top: 16rpx;
}

.metric-item {
  padding: 14rpx;
  border-radius: 16rpx;
  background: #f7f9fd;
}

.metric-label {
  font-size: 20rpx;
  color: #7d8aa1;
}

.metric-value {
  margin-top: 8rpx;
  font-size: 26rpx;
  font-weight: 600;
  color: #162034;
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

.empty-desc,
.empty-inline {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #7d8aa1;
}

.return-btn {
  margin-top: 16rpx;
}
</style>
