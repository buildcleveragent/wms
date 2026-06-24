<template>
  <view class="page">
    <BossNav active="alerts" />

    <view class="hero">
      <view class="hero-head">
        <view class="hero-copy">
          <view class="hero-tag">仓储经营分析中心</view>
          <view class="hero-title">预警中心</view>
         <!-- <view class="hero-desc">把最危险的事先抬出来看，避免老板在一堆正常数据里找异常。</view> -->
        </view>
        <button class="btn-ghost hero-btn" @click="logout">退出</button>
      </view>
      <view class="hero-meta">
        <text>{{ operatorName }}</text>
        <text>{{ warehouseName }}</text>
        <text>{{ ownerScopeText }}</text>
      </view>
    </view>

    <view class="filter-card">
      <view class="filter-row">
        <picker :range="ownerOptions" range-key="label" :value="ownerPickerIndex" @change="onOwnerChange">
          <view class="picker-box">
            <text class="picker-label">货主范围</text>
            <text class="picker-value">{{ ownerOptions[ownerPickerIndex]?.label || '全部货主' }}</text>
          </view>
        </picker>

        <view class="picker-box info-box">
          <text class="picker-label">预警总量</text>
          <text class="picker-value">{{ summary.totalItems }} 项</text>
          <text class="picker-sub">高风险 {{ summary.highRiskItems }} 项</text>
        </view>
      </view>

      <view class="filter-actions">
        <button class="btn-primary" @click="refreshAll">刷新</button>
        <button class="btn-ghost" @click="openRevenue">看收入页</button>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步预警中心...</view>

    <template v-else>
      <view class="summary-grid">
        <view class="summary-card danger">
          <view class="summary-label">高风险</view>
          <view class="summary-value">{{ summary.highRiskItems }}</view>
          <view class="summary-sub">优先马上追问</view>
        </view>
        <view class="summary-card calm">
          <view class="summary-label">全部预警</view>
          <view class="summary-value">{{ summary.totalItems }}</view>
          <view class="summary-sub">{{ summary.sectionCount }} 个监控维度</view>
        </view>
      </view>

      <view v-for="section in sectionRows" :key="section.key" class="section">
        <view class="section-head">
          <view>
            <view class="section-title">
              <text class="section-dot" :class="section.severity"></text>
              {{ section.label }}
            </view>
            <view class="section-desc">{{ section.severity === 'high' ? '高风险，建议优先处理。' : '中风险，建议持续关注。' }}</view>
          </view>
          <view class="section-count">{{ section.count }}</view>
        </view>

        <view v-if="!section.items.length" class="empty-inline">当前没有这一类预警。</view>

        <view
          v-for="item in section.items"
          :key="`${section.key}-${item.id || item.order_no || item.invoice_no || item.task_no}`"
          class="row-card"
          :class="{ clickable: canOpenDetail(section.key, item) }"
          @click="openSectionItem(section.key, item)"
        >
          <view class="row-main">
            <view class="row-title">{{ itemTitle(section.key, item) }}</view>
            <view class="row-sub">{{ itemMeta(section.key, item) }}</view>
          </view>
          <view class="row-side">{{ itemSide(section.key, item) }}</view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import BossNav from '@/components/boss-nav.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { asList, billStatusLabel, money, qty } from '@/utils/billing'

const auth = useAuth()
const payload = ref(null)
const loading = ref(false)
const selectedOwnerId = ref('')

const operatorName = computed(() => auth.user?.display_name || auth.user?.username || '老板账号')
const scope = computed(() => payload.value?.scope || {})
const warehouseName = computed(() => scope.value.warehouse_name || '当前仓库')
const ownerScopeText = computed(() => scope.value.owner_name || '全部货主')

const ownerOptions = computed(() => {
  const rows = asList(payload.value?.owner_options).map((item) => ({
    id: String(item.id),
    label: item.name || `货主 #${item.id}`,
  }))
  return [{ id: '', label: '全部货主' }, ...rows]
})

const ownerPickerIndex = computed(() => {
  const index = ownerOptions.value.findIndex((item) => item.id === String(selectedOwnerId.value || ''))
  return index >= 0 ? index : 0
})

const summary = computed(() => {
  const source = payload.value?.summary || {}
  return {
    sectionCount: Number(source.section_count || 0),
    totalItems: Number(source.total_items || 0),
    highRiskItems: Number(source.high_risk_items || 0),
  }
})

const sectionOrder = [
  'overdue_tasks',
  'overdue_bills',
  'failed_billing_jobs',
  'pending_review_tasks',
  'expiring_inventory',
  'review_differences',
]

const sectionRows = computed(() =>
  sectionOrder.map((key) => {
    const section = payload.value?.sections?.[key] || {}
    return {
      key,
      label: section.label || key,
      severity: section.severity || 'medium',
      count: Number(section.count || 0),
      items: Array.isArray(section.items) ? section.items : [],
    }
  })
)

function buildParams() {
  return selectedOwnerId.value ? { owner: selectedOwnerId.value } : {}
}

async function refreshAll() {
  loading.value = true
  try {
    payload.value = await api.bossAlerts(buildParams())
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function onOwnerChange(event) {
  const next = ownerOptions.value[Number(event.detail.value) || 0]
  selectedOwnerId.value = next?.id || ''
  refreshAll()
}

function openRevenue() {
  uni.reLaunch({ url: '/pages/billing/overview' })
}

function logout() {
  auth.logout()
  uni.reLaunch({ url: '/pages/login' })
}

function canOpenDetail(sectionKey, item) {
  return sectionKey === 'overdue_bills' && !!item?.id
}

function openSectionItem(sectionKey, item) {
  if (!canOpenDetail(sectionKey, item)) return
  uni.navigateTo({
    url: `/pages/billing/bill_detail?id=${item.id}`,
  })
}

function itemTitle(sectionKey, item) {
  if (sectionKey === 'overdue_tasks') return `${item.task_no} · ${item.owner_name || '未识别货主'}`
  if (sectionKey === 'pending_review_tasks') return `${item.task_no} · ${item.owner_name || '未识别货主'}`
  if (sectionKey === 'expiring_inventory') return `${item.product_name} · ${item.owner_name || '未识别货主'}`
  if (sectionKey === 'overdue_bills') return item.invoice_no
  if (sectionKey === 'failed_billing_jobs') return `${item.owner_name || '未识别货主'} · ${item.job_name}`
  if (sectionKey === 'review_differences') return item.order_no
  return item.id || '-'
}

function itemMeta(sectionKey, item) {
  if (sectionKey === 'overdue_tasks') {
    return `${item.task_type} · 状态 ${item.status} · 计划截至 ${item.planned_end || '-'}`
  }
  if (sectionKey === 'pending_review_tasks') {
    return `状态 ${item.status} · 创建时间 ${item.created_at || '-'}`
  }
  if (sectionKey === 'expiring_inventory') {
    return `库位 ${item.location_code || '-'} · 到期 ${item.expiry_date || '-'}`
  }
  if (sectionKey === 'overdue_bills') {
    return `${item.owner_name || '-'} · ${billStatusLabel(item.status)} · 到期 ${item.due_date || '-'}`
  }
  if (sectionKey === 'failed_billing_jobs') {
    return `${item.service_date || '-'} · ${item.message || '无错误描述'}`
  }
  if (sectionKey === 'review_differences') {
    return `仓库 ${item.warehouse_name || '-'} · 状态 ${item.status}`
  }
  return '-'
}

function itemSide(sectionKey, item) {
  if (sectionKey === 'overdue_tasks') return `${item.overdue_hours || 0}h`
  if (sectionKey === 'expiring_inventory') return qty(item.onhand_qty)
  if (sectionKey === 'overdue_bills') return money(item.total)
  if (sectionKey === 'pending_review_tasks') return item.status || '-'
  if (sectionKey === 'failed_billing_jobs') return '失败'
  if (sectionKey === 'review_differences') return item.status || '-'
  return '-'
}

onLoad(() => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  refreshAll()
})

onPullDownRefresh(() => {
  refreshAll()
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  background:
    radial-gradient(circle at top right, rgba(239, 68, 68, 0.08), transparent 28%),
    linear-gradient(180deg, #f8f5f4 0%, #f3f5fb 100%);
}

.hero,
.filter-card,
.section {
  background: #fff;
  border-radius: 28rpx;
  padding: 24rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 16rpx 40rpx rgba(20, 38, 84, 0.06);
}

.hero {
  background:
    radial-gradient(circle at top left, rgba(239, 68, 68, 0.12), transparent 34%),
    linear-gradient(135deg, #ffffff 0%, #fff7f6 58%, #f7fbff 100%);
}

.hero-head,
.filter-row,
.row-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.hero-copy,
.row-main {
  flex: 1;
}

.hero-tag {
  display: inline-flex;
  align-items: center;
  padding: 10rpx 18rpx;
  border-radius: 999rpx;
  background: #fff;
  color: #ef4444;
  font-size: 22rpx;
  font-weight: 700;
}

.hero-title {
  margin-top: 18rpx;
  font-size: 44rpx;
  font-weight: 700;
  color: #182234;
}

.hero-desc,
.section-desc,
.row-sub,
.picker-sub,
.summary-sub,
.empty-inline {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #71809c;
}

.hero-meta {
  display: flex;
  gap: 14rpx;
  flex-wrap: wrap;
  margin-top: 18rpx;
}

.hero-meta text {
  padding: 8rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(15, 23, 42, 0.05);
  font-size: 22rpx;
  color: #53637f;
}

.picker-box,
.summary-card {
  flex: 1;
  min-height: 116rpx;
  padding: 18rpx;
  border-radius: 22rpx;
  background: #f7f9fd;
}

.picker-box {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.info-box {
  justify-content: center;
}

.picker-label,
.summary-label {
  font-size: 22rpx;
  color: #7d8ba5;
}

.picker-value,
.summary-value {
  margin-top: 8rpx;
  font-size: 30rpx;
  font-weight: 700;
  color: #1d2940;
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
  background: rgba(239, 68, 68, 0.08);
  color: #ef4444;
  font-size: 24rpx;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
  margin-bottom: 18rpx;
}

.summary-card.danger {
  background: linear-gradient(160deg, #fff1f2, #fff8f8);
}

.summary-card.calm {
  background: linear-gradient(160deg, #eef5ff, #f8fbff);
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  margin-bottom: 12rpx;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 10rpx;
  font-size: 30rpx;
  font-weight: 700;
  color: #182234;
}

.section-dot {
  width: 18rpx;
  height: 18rpx;
  border-radius: 999rpx;
}

.section-dot.high {
  background: #ef4444;
}

.section-dot.medium {
  background: #f59e0b;
}

.section-count {
  min-width: 74rpx;
  height: 74rpx;
  border-radius: 22rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fb;
  font-size: 28rpx;
  font-weight: 700;
  color: #182234;
}

.row-card {
  padding: 20rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.clickable:active {
  opacity: 0.72;
}

.row-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #192337;
}

.row-side {
  font-size: 24rpx;
  font-weight: 700;
  color: #0f172a;
}

.btn-primary,
.btn-ghost,
.hero-btn {
  min-height: 84rpx;
  border-radius: 18rpx;
  font-size: 26rpx;
  font-weight: 700;
}

.btn-primary {
  background: #ef4444;
  color: #fff;
}

.btn-ghost,
.hero-btn {
  background: rgba(255, 255, 255, 0.92);
  color: #1a2a46;
  border: 1rpx solid #dbe5f3;
}
</style>
