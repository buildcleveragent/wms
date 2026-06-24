<template>
  <view class="page">
    <view class="hero">
      <view class="hero-top">
        <view>
          <view class="hero-tag">Accrual Detail</view>
          <view class="hero-title">{{ chargeTypeLabel(detail?.charge_type) }}</view>
        </view>
        <view class="status-pill">{{ accrualStatusLabel(detail?.status) }}</view>
      </view>
      <view v-if="detail" class="hero-meta">
        <text>{{ detail.owner_name }}</text>
        <text>{{ detail.warehouse_name }}</text>
        <text>{{ detail.service_date }}</text>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步收费记录详情...</view>

    <template v-else-if="detail">
      <view class="headline-grid">
        <view class="headline-card">
          <view class="headline-label">不含税金额</view>
          <view class="headline-value">{{ money(detail.amount) }}</view>
          <view class="headline-sub">税额 {{ money(detail.tax_amount) }}</view>
        </view>
        <view class="headline-card">
          <view class="headline-label">数量</view>
          <view class="headline-value">{{ qty(detail.quantity) }}</view>
          <view class="headline-sub">单价/费率 {{ qty(detail.unit_price) }}</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">基础信息</view>
          <view class="section-desc">这条收费记录的归属、规则和账期信息。</view>
        </view>

        <view class="info-row"><text class="label">应计指纹</text><text class="value mono">{{ detail.acc_fingerprint }}</text></view>
        <view class="info-row"><text class="label">收费类型</text><text class="value">{{ chargeTypeLabel(detail.charge_type) }}</text></view>
        <view class="info-row"><text class="label">规则算法</text><text class="value">{{ detail.rule_calc_method || '-' }}</text></view>
        <view class="info-row"><text class="label">规则备注</text><text class="value">{{ detail.rule_note || '-' }}</text></view>
        <view class="info-row"><text class="label">账期</text><text class="value">{{ detail.period_label || '未锁账期' }}</text></view>
        <view class="info-row"><text class="label">创建人</text><text class="value">{{ detail.created_by_username || '-' }}</text></view>
        <view class="info-row"><text class="label">创建时间</text><text class="value">{{ detail.created_at || '-' }}</text></view>
        <view class="info-row"><text class="label">是否冲销</text><text class="value">{{ detail.is_reversal ? '是' : '否' }}</text></view>
        <view v-if="detail.reversal_of" class="info-row"><text class="label">冲销来源</text><text class="value mono">#{{ detail.reversal_of }}</text></view>
        <view
          v-if="detail.pre_adjustment_amount !== null && detail.pre_adjustment_amount !== undefined"
          class="info-row"
        >
          <text class="label">调整前金额</text>
          <text class="value">{{ money(detail.pre_adjustment_amount) }}</text>
        </view>
      </view>

      <view v-if="detail.event" class="section">
        <view class="section-head">
          <view class="section-title">来源事件</view>
          <view class="section-desc">来自任务过账或扫描日志的计费事实。</view>
        </view>

        <view class="info-row"><text class="label">事件指纹</text><text class="value mono">{{ detail.event_fp || '-' }}</text></view>
        <view class="info-row"><text class="label">事件类型</text><text class="value">{{ chargeTypeLabel(detail.event_charge_type) }}</text></view>
        <view class="info-row"><text class="label">事件日期</text><text class="value">{{ detail.event_service_date || '-' }}</text></view>
        <view class="info-row"><text class="label">任务号</text><text class="value">{{ detail.event_task_no || '-' }}</text></view>
        <view class="info-row"><text class="label">任务行</text><text class="value">{{ detail.event_task_line || '-' }}</text></view>
        <view class="info-row"><text class="label">扫描日志</text><text class="value">{{ detail.event_scan_log || '-' }}</text></view>
        <view class="info-row"><text class="label">过账日志</text><text class="value">{{ detail.event_posting_journal || '-' }}</text></view>
        <view class="info-row"><text class="label">事件数量</text><text class="value">{{ qty(detail.event_quantity) }} {{ detail.event_quantity_uom || '' }}</text></view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">账单关联</view>
          <view class="section-desc">如果这条收费已经开票，会在这里展示账单信息。</view>
        </view>

        <template v-if="detail.bill_id">
          <view class="info-row"><text class="label">账单号</text><text class="value">{{ detail.bill_invoice_no || '-' }}</text></view>
          <view class="info-row"><text class="label">账单状态</text><text class="value">{{ billStatusLabel(detail.bill_status) }}</text></view>
          <view class="info-row"><text class="label">账单说明</text><text class="value">{{ detail.bill_line_description || '-' }}</text></view>
          <button class="btn-primary action-btn" @click="openBillDetail">查看账单详情</button>
        </template>
        <view v-else class="empty-inline">当前收费记录还没有关联账单。</view>
      </view>

      <view class="action-row">
        <button class="btn-ghost" @click="goBackOwnerDetail">返回货主明细</button>
        <button class="btn-ghost" @click="goBackOverview">返回总览</button>
      </view>
    </template>

    <view v-else class="empty-card">
      <view class="empty-title">收费记录不存在</view>
      <view class="empty-desc">请返回上一页重新选择收费记录。</view>
      <button class="btn-ghost action-btn" @click="goBackOverview">返回总览</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { api, buildQuery } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { accrualStatusLabel, billStatusLabel, chargeTypeLabel, money, qty } from '@/utils/billing'

const auth = useAuth()
const accrualId = ref('')
const ownerId = ref('')
const warehouseId = ref('')
const dateFrom = ref('')
const dateTo = ref('')

const detail = ref(null)
const loading = ref(false)

async function loadDetail() {
  if (!accrualId.value) {
    uni.showToast({
      title: '缺少收费记录编号',
      icon: 'none',
    })
    return
  }

  loading.value = true
  try {
    detail.value = await api.billingAccrualDetail(accrualId.value)
  } catch (error) {
    detail.value = null
    console.error('load billing accrual detail failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function openBillDetail() {
  if (!detail.value?.bill_id) return
  const query = buildQuery({
    id: detail.value.bill_id,
    owner: ownerId.value || detail.value.owner,
    warehouse: warehouseId.value || detail.value.warehouse,
    period: detail.value.period,
  })
  uni.navigateTo({
    url: `/pages/billing/bill_detail?${query}`,
  })
}

function goBackOwnerDetail() {
  const query = buildQuery({
    owner: ownerId.value || detail.value?.owner,
    warehouse: warehouseId.value || detail.value?.warehouse,
    date_from: dateFrom.value,
    date_to: dateTo.value,
  })
  if (getCurrentPages().length > 1) {
    uni.navigateBack()
    return
  }
  uni.redirectTo({
    url: query ? `/pages/billing/owner_detail?${query}` : '/pages/billing/owner_detail',
  })
}

function goBackOverview() {
  const query = buildQuery({
    owner: ownerId.value,
    warehouse: warehouseId.value,
    date_from: dateFrom.value,
    date_to: dateTo.value,
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
  accrualId.value = query?.id ? String(query.id) : ''
  ownerId.value = query?.owner ? String(query.owner) : ''
  warehouseId.value = query?.warehouse ? String(query.warehouse) : ''
  dateFrom.value = query?.date_from ? String(query.date_from) : ''
  dateTo.value = query?.date_to ? String(query.date_to) : ''
  loadDetail()
})

onPullDownRefresh(() => {
  loadDetail()
})
</script>

<style scoped>
.page {
  padding: 24rpx;
}

.hero,
.headline-card,
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
    linear-gradient(135deg, #fff8ef 0%, #fffdf8 55%, #f5f9ff 100%);
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

.headline-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
}

.headline-label {
  font-size: 22rpx;
  color: #7a879f;
}

.headline-value {
  margin-top: 12rpx;
  font-size: 38rpx;
  font-weight: 700;
  line-height: 1.08;
  color: #172033;
}

.headline-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #8592aa;
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

.info-row {
  display: flex;
  justify-content: space-between;
  gap: 20rpx;
  padding: 18rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.label {
  width: 180rpx;
  font-size: 24rpx;
  color: #73819c;
}

.value {
  flex: 1;
  font-size: 24rpx;
  color: #1f2940;
  text-align: right;
  word-break: break-all;
}

.mono {
  font-family: monospace;
}

.action-btn {
  margin-top: 16rpx;
}

.action-row {
  display: flex;
  gap: 12rpx;
}

.action-row button {
  flex: 1;
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
