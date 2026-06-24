<template>
  <view class="page">
    <BossNav active="inventory" />

    <view class="hero">
      <view class="hero-head">
        <view class="hero-copy">
          <view class="hero-tag">仓储经营分析中心</view>
          <view class="hero-title">库存与库容</view>
 <!--         <view class="hero-desc">先看仓里现在压了多少货、哪批快到期、哪些库位最热或最冷，再判断仓容和客户结构有没有风险。</view> -->
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
          <text class="picker-label">当前仓容</text>
          <text class="picker-value">{{ percent(summary.volumeUtilizationRate) }}</text>
          <text class="picker-sub">{{ summary.usedVolumeText }} / {{ summary.capacityVolumeText }} m³</text>
        </view>
      </view>

      <view class="filter-actions">
        <button class="btn-primary" @click="refreshAll">刷新</button>
        <button class="btn-ghost" @click="openRevenue">看收入页</button>
		<button class="btn-ghost" @click="openInventoryDetail">看库存明细</button>
        <button class="btn-ghost" @click="openAlerts">看预警</button>
      </view>
    </view>

    <view v-if="loading" class="loading-banner">正在同步库存与库容...</view>

    <template v-else>
      <view class="kpi-grid">
        <view class="kpi-card blue">
          <view class="kpi-label">当前在库量</view>
          <view class="kpi-value">{{ qty(summary.currentOnhandQty) }}</view>
          <view class="kpi-sub">可用 {{ qty(summary.currentAvailableQty) }}</view>
        </view>
        <view class="kpi-card orange">
          <view class="kpi-label">7天内临期</view>
          <view class="kpi-value">{{ qty(summary.expiringQty7d) }}</view>
          <view class="kpi-sub">{{ summary.expiringSkuCount7d }} 个 SKU</view>
        </view>
        <view class="kpi-card purple">
          <view class="kpi-label">30天未变化</view>
          <view class="kpi-value">{{ qty(summary.staleQty30d) }}</view>
          <view class="kpi-sub">{{ summary.staleSkuCount30d }} 个 SKU</view>
        </view>
        <view class="kpi-card pink">
          <view class="kpi-label">库位占用率</view>
          <view class="kpi-value">{{ percent(summary.locationOccupancyRate) }}</view>
          <view class="kpi-sub">{{ summary.occupiedLocationCount }}/{{ summary.activeLocationCount }} 库位占用</view>
        </view>
        <view class="kpi-card green">
          <view class="kpi-label">体积利用率</view>
          <view class="kpi-value">{{ percent(summary.volumeUtilizationRate) }}</view>
          <view class="kpi-sub">{{ summary.usedVolumeText }} / {{ summary.capacityVolumeText }} m³</view>
        </view>
        <view class="kpi-card navy">
          <view class="kpi-label">热 / 冷库位</view>
          <view class="kpi-value">{{ summary.hotLocationCount }} / {{ summary.coldLocationCount }}</view>
          <view class="kpi-sub">SKU {{ summary.skuCount }} / 货主 {{ summary.ownerCount }}</view>
        </view>
      </view>

      <view class="split-grid">
        <view class="section">
          <view class="section-head">
            <view>
              <view class="section-title">货主占仓排行</view>
              <view class="section-desc">按占用体积排序，先看谁最占仓，再对照收入页判断资源和收费是否匹配。</view>
            </view>
          </view>
          <view v-if="!ownerRows.length" class="empty-inline">当前范围暂无库存排行。</view>
          <view
            v-for="(row, index) in ownerRows"
            :key="row.owner"
            class="row-card clickable"
            @click="openOwnerRevenue(row.owner)"
          >
            <view class="rank-pill">{{ index + 1 }}</view>
            <view class="row-main">
              <view class="row-title">{{ row.ownerName }}</view>
              <view class="row-sub">{{ row.skuCount }} 个 SKU / {{ row.locationCount }} 个库位</view>
            </view>
            <view class="row-side stack">
              <text>{{ row.usedVolumeText }} m³</text>
              <text>{{ qty(row.onhandQty) }}</text>
            </view>
          </view>
        </view>

        <view class="section">
          <view class="section-head">
            <view>
              <view class="section-title">高占用热区</view>
              <view class="section-desc">按库位体积利用率排序，优先暴露最容易形成拥堵或接近吃满的库位。</view>
            </view>
          </view>
          <view v-if="!highHeatRows.length" class="empty-inline">当前范围暂无高占用热区。</view>
          <view v-for="row in highHeatRows" :key="row.location">
            <view class="row-card">
              <view class="row-main">
                <view class="row-title">{{ row.locationCode }}</view>
                <view class="row-sub">
                  {{ row.subwarehouseName || '未分配子仓' }} · {{ row.skuCount }} 个 SKU / {{ row.ownerCount }} 个货主
                </view>
              </view>
              <view class="row-side stack">
                <text class="hotspot-pill" :class="row.hotspotLevel">{{ row.hotspotLabel }}</text>
                <text>{{ percent(row.volumeUtilizationRate) }}</text>
              </view>
            </view>
            <view class="hotspot-detail">
              <text>{{ row.usedVolumeText }} / {{ row.capacityVolumeText }} m³</text>
              <text>在库 {{ qty(row.onhandQty) }} / 可用 {{ qty(row.availableQty) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="split-grid">
        <view class="section">
          <view class="section-head">
            <view>
              <view class="section-title">低效冷区</view>
              <view class="section-desc">这里按“30 天未变化 + 低利用率”筛冷区，优先找出压仓又不活跃的位置。</view>
            </view>
          </view>
          <view v-if="!coldRows.length" class="empty-inline">当前范围暂无低效冷区。</view>
          <view v-for="row in coldRows" :key="row.location">
            <view class="row-card">
              <view class="row-main">
                <view class="row-title">{{ row.locationCode }}</view>
                <view class="row-sub">
                  {{ row.subwarehouseName || '未分配子仓' }} · 最近变化 {{ row.updatedDate }} · {{ row.utilizationText }}
                </view>
              </view>
              <view class="row-side stack">
                <text>静置 {{ row.staleDays }} 天</text>
                <text>{{ qty(row.onhandQty) }}</text>
              </view>
            </view>
          </view>
        </view>

        <view class="section">
          <view class="section-head">
            <view>
              <view class="section-title">临期库存</view>
              <view class="section-desc">这里先盯 7 天内就会到期的库存，避免仓里堆着要出问题的货。</view>
            </view>
          </view>
          <view v-if="!expiringRows.length" class="empty-inline">当前范围没有 7 天内临期库存。</view>
          <view v-for="row in expiringRows" :key="row.id" class="row-card">
            <view class="row-main">
              <view class="row-title">{{ row.productName }}</view>
              <view class="row-sub">{{ row.ownerName }} · {{ row.locationCode }} · 到期 {{ row.expiryDate }}</view>
            </view>
            <view class="row-side stack">
              <text>{{ row.daysLabel }}</text>
              <text>{{ qty(row.onhandQty) }}</text>
            </view>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-head">
          <view>
            <view class="section-title">呆滞库存明细</view>
            <view class="section-desc">当前按库存明细最近更新时间做代理口径，先把 30 天未变化的货拉出来看。</view>
          </view>
        </view>
        <view v-if="!staleRows.length" class="empty-inline">当前范围没有 30 天未变化库存。</view>
        <view v-for="row in staleRows" :key="row.id" class="row-card">
          <view class="row-main">
            <view class="row-title">{{ row.productName }}</view>
            <view class="row-sub">{{ row.ownerName }} · {{ row.locationCode }} · 最近变化 {{ row.updatedDate }}</view>
          </view>
          <view class="row-side stack">
            <text>静置 {{ row.staleDays }} 天</text>
            <text>{{ qty(row.onhandQty) }}</text>
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
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { asList, percent, qty, toNumber } from '@/utils/billing'

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
    currentOnhandQty: toNumber(source.current_onhand_qty),
    currentAvailableQty: toNumber(source.current_available_qty),
    currentLockedQty: toNumber(source.current_locked_qty),
    currentDamagedQty: toNumber(source.current_damaged_qty),
    skuCount: Number(source.sku_count || 0),
    ownerCount: Number(source.owner_count || 0),
    occupiedLocationCount: Number(source.occupied_location_count || 0),
    activeLocationCount: Number(source.active_location_count || 0),
    locationOccupancyRate: source.location_occupancy_rate,
    volumeUtilizationRate: source.volume_utilization_rate,
    usedVolumeText: toNumber(source.used_volume_m3).toFixed(3),
    capacityVolumeText: toNumber(source.capacity_volume_m3).toFixed(3),
    expiringQty7d: toNumber(source.expiring_qty_7d),
    expiringSkuCount7d: Number(source.expiring_sku_count_7d || 0),
    staleQty30d: toNumber(source.stale_qty_30d),
    staleSkuCount30d: Number(source.stale_sku_count_30d || 0),
    hotLocationCount: Number(source.hot_location_count || 0),
    coldLocationCount: Number(source.cold_location_count || 0),
  }
})

const ownerRows = computed(() =>
  asList(payload.value?.owner_rankings).map((row) => ({
    owner: row.owner,
    ownerName: row.owner_name,
    onhandQty: toNumber(row.onhand_qty),
    skuCount: Number(row.sku_count || 0),
    locationCount: Number(row.location_count || 0),
    usedVolumeText: toNumber(row.used_volume_m3).toFixed(3),
  }))
)

const expiringRows = computed(() =>
  asList(payload.value?.expiring_items).map((row) => ({
    id: row.id,
    ownerName: row.owner_name || '-',
    productName: row.product_name || row.product_code || '-',
    locationCode: row.location_code || '-',
    expiryDate: row.expiry_date || '-',
    onhandQty: toNumber(row.onhand_qty),
    daysLabel: Number(row.days_to_expiry || 0) <= 0 ? '今天到期' : `剩 ${Number(row.days_to_expiry || 0)} 天`,
  }))
)

const staleRows = computed(() =>
  asList(payload.value?.stale_items).map((row) => ({
    id: row.id,
    ownerName: row.owner_name || '-',
    productName: row.product_name || row.product_code || '-',
    locationCode: row.location_code || '-',
    updatedDate: formatDate(row.updated_at),
    staleDays: Number(row.stale_days || 0),
    onhandQty: toNumber(row.onhand_qty),
  }))
)

const highHeatRows = computed(() =>
  asList(payload.value?.high_heat_locations || payload.value?.location_hotspots).map((row) => ({
    location: row.location,
    locationCode: row.location_code || row.location_name || '-',
    subwarehouseName: row.subwarehouse_name || '',
    onhandQty: toNumber(row.onhand_qty),
    availableQty: toNumber(row.available_qty),
    skuCount: Number(row.sku_count || 0),
    ownerCount: Number(row.owner_count || 0),
    usedVolumeText: toNumber(row.used_volume_m3).toFixed(3),
    capacityVolumeText: toNumber(row.capacity_volume_m3).toFixed(3),
    volumeUtilizationRate: row.volume_utilization_rate,
    hotspotLevel: row.hotspot_level || 'watch',
    hotspotLabel: hotspotLabel(row.hotspot_level),
  }))
)

const coldRows = computed(() =>
  asList(payload.value?.cold_locations).map((row) => ({
    location: row.location,
    locationCode: row.location_code || row.location_name || '-',
    subwarehouseName: row.subwarehouse_name || '',
    onhandQty: toNumber(row.onhand_qty),
    availableQty: toNumber(row.available_qty),
    staleDays: Number(row.stale_days || 0),
    updatedDate: formatDate(row.latest_updated_at),
    utilizationText: coldUtilizationText(row.volume_utilization_rate),
  }))
)

function formatDate(value) {
  if (!value) return '-'
  return String(value).slice(0, 10)
}

function hotspotLabel(level) {
  if (level === 'hot') return '高热'
  if (level === 'warm') return '偏热'
  if (level === 'calm') return '平稳'
  return '关注'
}

function coldUtilizationText(value) {
  if (value === null || value === undefined || value === '') return '容量未建档'
  return `利用率 ${percent(value)}`
}

function buildParams() {
  const params = {}
  if (selectedOwnerId.value) {
    params.owner = selectedOwnerId.value
  }
  return params
}

async function refreshAll() {
  loading.value = true
  try {
    payload.value = await api.bossInventory(buildParams())
  } catch (error) {
    console.error('boss inventory overview failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function onOwnerChange(event) {
  const option = ownerOptions.value[Number(event.detail.value || 0)] || ownerOptions.value[0]
  selectedOwnerId.value = option?.id || ''
  refreshAll()
}

function openRevenue() {
  uni.reLaunch({ url: '/pages/billing/overview' })
}

function openAlerts() {
  uni.reLaunch({ url: '/pages/alerts/index' })
}

function openOwnerRevenue(ownerId) {
  if (!ownerId) return
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


function openInventoryDetail() {
  const query = selectedOwnerId.value
    ? `?owner_id=${selectedOwnerId.value}`
    : ''

  uni.navigateTo({
    url: `/pages/inventory/detail${query}`,
  })
}

</script>

<style scoped>
.page {
  min-height: 100vh;
  padding: 28rpx 24rpx 44rpx;
  background:
    radial-gradient(circle at top right, rgba(37, 84, 255, 0.12), transparent 38%),
    linear-gradient(180deg, #eef3ff 0%, #f6f8fc 45%, #f6f8fc 100%);
  box-sizing: border-box;
}

.hero {
  padding: 32rpx;
  border-radius: 28rpx;
  background: linear-gradient(145deg, #0d1f54, #183b9b 62%, #1c6cff);
  color: #ffffff;
  box-shadow: 0 24rpx 48rpx rgba(14, 37, 99, 0.22);
}

.hero-head {
  display: flex;
  justify-content: space-between;
  gap: 24rpx;
  align-items: flex-start;
}

.hero-copy {
  flex: 1;
}

.hero-tag {
  display: inline-flex;
  margin-bottom: 16rpx;
  padding: 8rpx 18rpx;
  border-radius: 999rpx;
  font-size: 20rpx;
  letter-spacing: 2rpx;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.14);
}

.hero-title {
  font-size: 44rpx;
  font-weight: 700;
}

.hero-desc {
  margin-top: 14rpx;
  font-size: 24rpx;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.82);
}

.hero-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16rpx;
  margin-top: 22rpx;
  font-size: 22rpx;
  color: rgba(255, 255, 255, 0.72);
}

.hero-btn {
  min-width: 120rpx;
}

.filter-card,
.section {
  margin-top: 24rpx;
  padding: 26rpx 24rpx;
  border-radius: 28rpx;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 18rpx 40rpx rgba(17, 36, 86, 0.08);
}

.filter-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16rpx;
}

.picker-box {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 96rpx;
  padding: 20rpx 22rpx;
  border-radius: 22rpx;
  background: #f5f7fd;
}

.picker-label {
  font-size: 22rpx;
  color: #7e89a5;
}

.picker-value {
  margin-top: 8rpx;
  font-size: 28rpx;
  font-weight: 700;
  color: #102047;
}

.picker-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #687796;
}

.info-box {
  background: linear-gradient(145deg, rgba(225, 241, 255, 0.92), rgba(246, 250, 255, 1));
}

.filter-actions {
  display: flex;
  flex-direction: row;
  gap: 12rpx;
  margin-top: 18rpx;
}

.filter-actions .btn-primary,
.filter-actions .btn-ghost {
  flex: 1;
  min-width: 0;
  padding-left: 0;
  padding-right: 0;
  font-size: 24rpx;
}

.loading-banner {
  margin-top: 24rpx;
  padding: 20rpx 22rpx;
  border-radius: 22rpx;
  font-size: 24rpx;
  color: #355095;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 12rpx 30rpx rgba(17, 36, 86, 0.07);
}

.kpi-grid,
.split-grid {
  display: grid;
  gap: 18rpx;
  margin-top: 24rpx;
}

.kpi-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.split-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.kpi-card {
  padding: 26rpx 24rpx;
  border-radius: 26rpx;
  color: #ffffff;
  box-shadow: 0 16rpx 34rpx rgba(18, 39, 92, 0.14);
}

.kpi-card.blue {
  background: linear-gradient(145deg, #0d53ff, #3b89ff);
}

.kpi-card.orange {
  background: linear-gradient(145deg, #ff7d2c, #ffae4d);
}

.kpi-card.purple {
  background: linear-gradient(145deg, #6d43ff, #9471ff);
}

.kpi-card.pink {
  background: linear-gradient(145deg, #ff5784, #ff8aa8);
}

.kpi-card.green {
  background: linear-gradient(145deg, #13a66c, #31cb8a);
}

.kpi-card.navy {
  background: linear-gradient(145deg, #12337a, #2050b1);
}

.kpi-label {
  font-size: 24rpx;
  color: rgba(255, 255, 255, 0.8);
}

.kpi-value {
  margin-top: 16rpx;
  font-size: 42rpx;
  font-weight: 700;
}

.kpi-sub {
  margin-top: 12rpx;
  font-size: 22rpx;
  color: rgba(255, 255, 255, 0.76);
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20rpx;
  margin-bottom: 18rpx;
}

.section-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #102047;
}

.section-desc {
  margin-top: 10rpx;
  font-size: 22rpx;
  line-height: 1.7;
  color: #7283a4;
}

.empty-inline {
  padding: 24rpx 0;
  font-size: 24rpx;
  color: #7c88a2;
}

.row-card {
  display: flex;
  align-items: center;
  gap: 18rpx;
  padding: 20rpx 0;
  border-top: 1rpx solid rgba(15, 31, 73, 0.08);
}

.row-card:first-of-type {
  border-top: none;
  padding-top: 0;
}

.row-card.clickable:active {
  opacity: 0.74;
}

.rank-pill {
  display: inline-flex;
  width: 48rpx;
  height: 48rpx;
  border-radius: 16rpx;
  align-items: center;
  justify-content: center;
  background: rgba(11, 95, 255, 0.12);
  color: #0b5fff;
  font-size: 24rpx;
  font-weight: 700;
}

.row-main {
  flex: 1;
  min-width: 0;
}

.row-title {
  font-size: 28rpx;
  font-weight: 700;
  color: #0f2047;
}

.row-sub {
  margin-top: 8rpx;
  font-size: 22rpx;
  line-height: 1.6;
  color: #7582a0;
}

.row-side {
  text-align: right;
  font-size: 24rpx;
  font-weight: 700;
  color: #0f2047;
}

.row-side.stack {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  align-items: flex-end;
}

.hotspot-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 88rpx;
  height: 42rpx;
  padding: 0 14rpx;
  border-radius: 999rpx;
  font-size: 20rpx;
  font-weight: 700;
}

.hotspot-pill.hot {
  color: #fff2f2;
  background: #ff5f6d;
}

.hotspot-pill.warm {
  color: #fff7ec;
  background: #ff9c33;
}

.hotspot-pill.watch {
  color: #fff;
  background: #5e7dff;
}

.hotspot-pill.calm {
  color: #0f6b4f;
  background: rgba(49, 203, 138, 0.18);
}

.hotspot-detail {
  display: flex;
  justify-content: space-between;
  gap: 16rpx;
  padding: 0 0 18rpx;
  font-size: 22rpx;
  color: #7784a2;
}

.btn-primary,
.btn-ghost {
  flex: 1;
  height: 82rpx;
  line-height: 82rpx;
  border-radius: 22rpx;
  font-size: 26rpx;
  font-weight: 700;
}

.btn-primary {
  color: #ffffff;
  background: linear-gradient(135deg, #0b5fff, #3d86ff);
  box-shadow: 0 12rpx 28rpx rgba(11, 95, 255, 0.24);
}

.btn-ghost {
  color: #20408a;
  background: rgba(11, 95, 255, 0.08);
}

@media (max-width: 720px) {
  .split-grid {
    grid-template-columns: 1fr;
  }

  .filter-row,
  .kpi-grid {
    grid-template-columns: 1fr;
  }


  .hotspot-detail {
    align-items: flex-start;
  }
}
</style>
