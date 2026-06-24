<template>
  <view class="page">
    <BossNav active="home" />

    <view class="hero">
      <view class="hero-head">
        <view class="hero-copy">
          <view class="hero-tag">仓储经营分析中心</view>
          <view class="hero-title">首页总览</view>
      <!--    <view class="hero-desc">先看今天仓库忙不忙、顺不顺、钱算得对不对，再决定要不要深挖到收入页或预警中心。</view> -->
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
          <text class="picker-label">今日关注</text>
          <text class="picker-value">{{ summary.openAlertCount }} 项预警</text>
          <text class="picker-sub">逾期应收 {{ money(summary.overdueReceivableTotal) }}</text>
        </view>
      </view>

      <view class="filter-actions">
        <button class="btn-primary" @click="refreshAll">刷新</button>
        <button class="btn-ghost" @click="openRevenue">看收入页</button>
        <button class="btn-ghost" @click="openAlerts">看预警</button>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步仓储经营分析中心数据...</view>

    <template v-else>
      <view class="kpi-grid">
        <view class="kpi-card blue">
          <view class="kpi-label">今日入库单</view>
          <view class="kpi-value">{{ summary.todayInboundOrders }}</view>
          <view class="kpi-sub">今日出库 {{ summary.todayOutboundOrders }} 单</view>
        </view>
        <view class="kpi-card gold">
          <view class="kpi-label">当前在库量</view>
          <view class="kpi-value">{{ qty(summary.currentOnhandQty) }}</view>
          <view class="kpi-sub">可用 {{ qty(summary.currentAvailableQty) }}</view>
        </view>
        <view class="kpi-card pink">
          <view class="kpi-label">库位占用率</view>
          <view class="kpi-value">{{ percent(summary.locationOccupancyRate) }}</view>
          <view class="kpi-sub">{{ summary.occupiedLocationCount }}/{{ summary.activeLocationCount }} 库位占用</view>
        </view>
        <view class="kpi-card green">
          <view class="kpi-label">体积利用率</view>
          <view class="kpi-value">{{ percent(summary.volumeUtilizationRate) }}</view>
          <view class="kpi-sub">已用 {{ summary.usedVolumeText }} / 容量 {{ summary.capacityVolumeText }}</view>
        </view>
        <view class="kpi-card navy">
          <view class="kpi-label">今日计费额</view>
          <view class="kpi-value">{{ money(summary.todayAccrualTotal) }}</view>
          <view class="kpi-sub">本月已出账 {{ money(summary.monthBilledTotal) }}</view>
        </view>
        <view class="kpi-card orange">
          <view class="kpi-label">逾期应收</view>
          <view class="kpi-value">{{ money(summary.overdueReceivableTotal) }}</view>
          <view class="kpi-sub">锁定 {{ qty(summary.currentLockedQty) }} / 损坏 {{ qty(summary.currentDamagedQty) }}</view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">今日作业节奏</view>
          <view class="section-desc">用“今日新任务完成率 + 当前积压”判断仓库有没有卡在某个环节。</view>
        </view>
        <view class="task-grid">
          <view v-for="row in taskRows" :key="row.taskType" class="task-card">
            <view class="task-title">{{ row.label }}</view>
            <view class="task-main">{{ row.todayCompleted }}/{{ row.todayTotal }}</view>
            <view class="task-sub">完成率 {{ percent(row.completionRate) }}</view>
            <view class="task-backlog">当前积压 {{ row.backlog }}</view>
          </view>
        </view>
      </view>

      <view class="split-grid">
        <view class="section">
          <view class="section-head">
            <view class="section-title">收入 Top 货主</view>
            <view class="section-desc">按本月应计排序，优先回答“谁在赚钱”。</view>
          </view>
          <view v-if="!revenueRows.length" class="empty-inline">当前范围暂无收入排行。</view>
          <view
            v-for="(row, index) in revenueRows"
            :key="row.owner"
            class="row-card clickable"
            @click="openOwnerRevenue(row.owner)"
          >
            <view class="rank-pill">{{ index + 1 }}</view>
            <view class="row-main">
              <view class="row-title">{{ row.ownerName }}</view>
              <view class="row-sub">{{ row.accrualCount }} 条应计</view>
            </view>
            <view class="row-money">{{ money(row.total) }}</view>
          </view>
        </view>

        <view class="section">
          <view class="section-head">
            <view class="section-title">占仓 Top 货主</view>
            <view class="section-desc">先看谁最占资源，再决定要不要去收入页对比计费。</view>
          </view>
          <view v-if="!inventoryRows.length" class="empty-inline">当前范围暂无占仓排行。</view>
          <view v-for="(row, index) in inventoryRows" :key="row.owner" class="row-card">
            <view class="rank-pill soft">{{ index + 1 }}</view>
            <view class="row-main">
              <view class="row-title">{{ row.ownerName }}</view>
              <view class="row-sub">可用 {{ qty(row.availableQty) }} / 锁定 {{ qty(row.lockedQty) }}</view>
            </view>
            <view class="row-money">{{ qty(row.onhandQty) }}</view>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">近 7 天趋势</view>
          <view class="section-desc">看订单和计费有没有突然抬头或掉下去。</view>
        </view>
        <view v-for="row in trendRows" :key="row.date" class="trend-row">
          <view class="trend-date">{{ row.date }}</view>
          <view class="trend-bars">
            <view class="trend-line">
              <view class="trend-bar inbound" :style="{ width: `${row.inboundWidth}%` }"></view>
            </view>
            <view class="trend-line">
              <view class="trend-bar outbound" :style="{ width: `${row.outboundWidth}%` }"></view>
            </view>
          </view>
          <view class="trend-side">
            <text>{{ row.inboundOrders }}/{{ row.outboundOrders }}</text>
            <text>{{ money(row.accrualTotal) }}</text>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view class="section-title">待关注</view>
          <view class="section-desc"> 最值得追问的几件事，点进去看完整预警清单。</view>
        </view>
        <view v-if="!attentionRows.length" class="empty-inline">当前没有需要优先关注的异常。</view>
        <view
          v-for="item in attentionRows"
          :key="item.key"
          class="row-card clickable"
          @click="openAlerts"
        >
          <view class="alert-dot" :class="item.severity"></view>
          <view class="row-main">
            <view class="row-title">{{ item.label }}</view>
            <view class="row-sub">{{ item.severity === 'high' ? '高优先级' : '中优先级' }}</view>
          </view>
          <view class="row-money">{{ item.count }}</view>
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
import { asList, money, percent, qty, toNumber } from '@/utils/billing'

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
    todayInboundOrders: Number(source.today_inbound_orders || 0),
    todayOutboundOrders: Number(source.today_outbound_orders || 0),
    currentOnhandQty: toNumber(source.current_onhand_qty),
    currentAvailableQty: toNumber(source.current_available_qty),
    currentLockedQty: toNumber(source.current_locked_qty),
    currentDamagedQty: toNumber(source.current_damaged_qty),
    occupiedLocationCount: Number(source.occupied_location_count || 0),
    activeLocationCount: Number(source.active_location_count || 0),
    locationOccupancyRate: source.location_occupancy_rate,
    volumeUtilizationRate: source.volume_utilization_rate,
    usedVolumeText: toNumber(source.used_volume_m3).toFixed(3),
    capacityVolumeText: toNumber(source.capacity_volume_m3).toFixed(3),
    todayAccrualTotal: toNumber(source.today_accrual_total),
    monthBilledTotal: toNumber(source.month_billed_total),
    overdueReceivableTotal: toNumber(source.overdue_receivable_total),
    openAlertCount: Number(source.open_alert_count || 0),
  }
})

const taskRows = computed(() =>
  asList(payload.value?.task_progress).map((row) => ({
    taskType: row.task_type,
    label: row.label || row.task_type,
    todayTotal: Number(row.today_total || 0),
    todayCompleted: Number(row.today_completed || 0),
    completionRate: row.completion_rate,
    backlog: Number(row.backlog || 0),
  }))
)

const revenueRows = computed(() =>
  asList(payload.value?.rankings?.revenue_top_owners).map((row) => ({
    owner: row.owner,
    ownerName: row.owner_name,
    accrualCount: Number(row.accrual_count || 0),
    total: toNumber(row.total),
  }))
)

const inventoryRows = computed(() =>
  asList(payload.value?.rankings?.inventory_top_owners).map((row) => ({
    owner: row.owner,
    ownerName: row.owner_name,
    onhandQty: toNumber(row.onhand_qty),
    availableQty: toNumber(row.available_qty),
    lockedQty: toNumber(row.locked_qty),
  }))
)

const attentionRows = computed(() =>
  asList(payload.value?.attention_items).map((item) => ({
    key: item.key,
    label: item.label,
    count: Number(item.count || 0),
    severity: item.severity || 'medium',
  }))
)

const trendRows = computed(() => {
  const rows = asList(payload.value?.trend_7d).map((row) => ({
    date: row.date,
    inboundOrders: Number(row.inbound_orders || 0),
    outboundOrders: Number(row.outbound_orders || 0),
    accrualTotal: toNumber(row.accrual_total),
  }))
  const inboundMax = Math.max(1, ...rows.map((row) => row.inboundOrders))
  const outboundMax = Math.max(1, ...rows.map((row) => row.outboundOrders))
  return rows.map((row) => ({
    ...row,
    inboundWidth: Math.max(8, (row.inboundOrders / inboundMax) * 100),
    outboundWidth: Math.max(8, (row.outboundOrders / outboundMax) * 100),
  }))
})

function buildParams() {
  return selectedOwnerId.value ? { owner: selectedOwnerId.value } : {}
}

async function refreshAll() {
  loading.value = true
  try {
    payload.value = await api.bossHome(buildParams())
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

function openAlerts() {
  uni.reLaunch({ url: '/pages/alerts/index' })
}

function openOwnerRevenue(ownerId) {
  uni.navigateTo({
    url: `/pages/billing/owner_detail?owner=${ownerId}`,
  })
}

function logout() {
  auth.logout()
  uni.reLaunch({ url: '/pages/login' })
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
    radial-gradient(circle at top right, rgba(255, 196, 0, 0.08), transparent 30%),
    linear-gradient(180deg, #f5f7fb 0%, #eef3fb 100%);
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
    radial-gradient(circle at top left, rgba(11, 95, 255, 0.12), transparent 32%),
    linear-gradient(135deg, #ffffff 0%, #f7fbff 58%, #fff8ef 100%);
}

.hero-head,
.filter-row,
.row-card,
.trend-row {
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
  color: #0b5fff;
  font-size: 22rpx;
  font-weight: 700;
}

.hero-title {
  margin-top: 18rpx;
  font-size: 44rpx;
  font-weight: 700;
  color: #162034;
}

.hero-desc,
.section-desc,
.row-sub,
.picker-sub,
.kpi-sub,
.task-sub,
.task-backlog,
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

.filter-row {
  align-items: stretch;
}

.picker-box,
.kpi-card,
.task-card {
  min-height: 116rpx;
  padding: 18rpx;
  border-radius: 22rpx;
  background: #f7f9fd;
}

.picker-box {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.info-box {
  justify-content: center;
}

.picker-label,
.kpi-label,
.task-title {
  font-size: 22rpx;
  color: #7d8ba5;
}

.picker-value,
.kpi-value,
.task-main {
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
  background: rgba(11, 95, 255, 0.08);
  color: #0b5fff;
  font-size: 24rpx;
}

.kpi-grid,
.task-grid,
.split-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14rpx;
}

.kpi-card.blue {
  background: linear-gradient(160deg, #eef5ff, #f8fbff);
}

.kpi-card.gold {
  background: linear-gradient(160deg, #fff8e9, #fffdf4);
}

.kpi-card.pink {
  background: linear-gradient(160deg, #fff0f6, #fff9fb);
}

.kpi-card.green {
  background: linear-gradient(160deg, #eefdf3, #f7fffb);
}

.kpi-card.navy {
  background: linear-gradient(160deg, #edf4ff, #f7fbff);
}

.kpi-card.orange {
  background: linear-gradient(160deg, #fff4ea, #fffaf5);
}

.section-head {
  margin-bottom: 14rpx;
}

.section-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #182234;
}

.task-card {
  background: linear-gradient(180deg, #f8fbff 0%, #eef4ff 100%);
}

.row-card {
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

.rank-pill.soft {
  background: #fff3db;
  color: #d88b12;
}

.row-title {
  font-size: 28rpx;
  font-weight: 600;
  color: #192337;
}

.row-money {
  font-size: 28rpx;
  font-weight: 700;
  color: #0f172a;
}

.trend-row {
  padding: 18rpx 0;
  border-top: 1rpx solid #edf1f7;
}

.trend-date {
  width: 170rpx;
  font-size: 24rpx;
  color: #596780;
}

.trend-bars {
  flex: 1;
  display: grid;
  gap: 8rpx;
}

.trend-line {
  height: 16rpx;
  border-radius: 999rpx;
  overflow: hidden;
  background: #e8eef8;
}

.trend-bar {
  height: 100%;
  border-radius: 999rpx;
}

.trend-bar.inbound {
  background: linear-gradient(90deg, #0b5fff, #56a3ff);
}

.trend-bar.outbound {
  background: linear-gradient(90deg, #f59e0b, #f97316);
}

.trend-side {
  width: 160rpx;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6rpx;
  font-size: 22rpx;
  color: #52617b;
}

.alert-dot {
  width: 18rpx;
  height: 18rpx;
  border-radius: 999rpx;
  margin-top: 6rpx;
}

.alert-dot.high {
  background: #ef4444;
}

.alert-dot.medium {
  background: #f59e0b;
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
  background: #0b5fff;
  color: #fff;
}

.btn-ghost,
.hero-btn {
  background: rgba(255, 255, 255, 0.92);
  color: #1a2a46;
  border: 1rpx solid #dbe5f3;
}
</style>
