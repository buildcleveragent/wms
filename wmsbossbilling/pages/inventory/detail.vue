<template>
  <view class="page">
    <BossNav active="inventory" />

    <view class="hero">
      <view class="hero-head">
        <view class="hero-copy">
          <view class="hero-tag">仓储经营分析中心</view>
          <view class="hero-title">库存明细</view>
        </view>
        <button class="btn-ghost hero-btn" @click="back">返回</button>
      </view>
      <view class="hero-meta">
        <text>{{ operatorName }}</text>
        <text>共 {{ total }} 条</text>
        <text>已加载 {{ rows.length }} 条</text>
      </view>
    </view>
	
	
    <BossScopeFilter @change="onScopeChange" />
    <BossDataStatus :meta="meta" :error="dataError" />

    <view class="filter-card">
      <view class="filter-row">
        <input
          v-model="search"
          class="search-input"
          placeholder="搜索商品名称、编码、SKU、货主"
          confirm-type="search"
          @confirm="onSearch"
        />

        <picker
          mode="selector"
          :range="pageSizeOptions"
          :value="pageSizeOptions.indexOf(pageSize)"
          @change="onPageSizeChange"
        >
          <view class="picker-box">
            <text class="picker-label">每页条数</text>
            <text class="picker-value">{{ pageSize }} 条</text>
          </view>
        </picker>
      </view>

      <view class="filter-actions">
        <button class="btn-primary" @click="onSearch">查询</button>
        <button class="btn-ghost" @click="resetSearch">重置</button>
      </view>
    </view>

    <view class="table-card">
      <view class="table-head">
        <view class="cell col-product">商品</view>
        <view class="cell col-owner">货主</view>
        <view class="cell col-loc">仓库/库位</view>
        <view class="cell col-num">现有</view>
        <view class="cell col-num">可用</view>
        <view class="cell col-num">分配</view>
        <view class="cell col-num">锁定</view>
        <view class="cell col-num">残次</view>
      </view>

      <scroll-view class="table-body" scroll-y @scrolltolower="loadMore">
        <view v-if="!rows.length && !loading" class="empty-inline">
          暂无库存明细。
        </view>

        <view
          v-for="item in rows"
          :key="rowKey(item)"
          class="table-row"
        >
          <view class="cell col-product">
            <view class="product-name">{{ item.product_name || '-' }}</view>
            <view class="product-sub">
              {{ item.product_code || item.product_sku || '-' }}
              <text v-if="item.product_spec"> · {{ item.product_spec }}</text>
              <text> · 单位 {{ item.base_unit || 'UNKNOWN' }}</text>
            </view>
          </view>

          <view class="cell col-owner">
            {{ item.owner_name || '-' }}
          </view>

          <view class="cell col-loc">
            <view>{{ item.warehouse_name || '-' }}</view>
            <view class="product-sub">{{ item.location_code || item.location_name || '-' }}</view>
          </view>

          <view class="cell col-num">
            <text class="num-text">{{ fmtQty(item.onhand_qty_display || item.onhand_qty) }} {{ item.base_unit }}</text>
          </view>

          <view class="cell col-num primary">
            <text class="num-text">{{ fmtQty(item.available_qty_display || item.available_qty) }}</text>
          </view>

          <view class="cell col-num">
            <text class="num-text">{{ fmtQty(item.allocated_qty_display || item.allocated_qty) }}</text>
          </view>

          <view class="cell col-num">
            <text class="num-text">{{ fmtQty(item.locked_qty_display || item.locked_qty) }}</text>
          </view>

          <view class="cell col-num">
            <text class="num-text">{{ fmtQty(item.damaged_qty_display || item.damaged_qty) }}</text>
          </view>
        </view>

        <view class="load-tip">
          <text v-if="loading">正在加载...</text>
          <text v-else-if="finished">已经到底了</text>
          <text v-else>上拉加载更多</text>
        </view>
      </scroll-view>
    </view>
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
import { api } from '@/utils/request'

const auth = useAuth()
const bossScope = useBossScope()

const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const pageSizeOptions = [10, 20, 50, 100]
const search = ref('')
const loading = ref(false)
const finished = ref(false)
const meta = ref(null)
const dataError = ref(null)

const operatorName = computed(() => auth.user?.display_name || auth.user?.username || '老板账号')


function fmtQty(value) {
  if (value === null || value === undefined || value === '') return '-'

  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)

  return String(Number(n.toFixed(4)))
}

function rowKey(item) {
  return `${item.warehouse_id || ''}-${item.owner_id || ''}-${item.product_id || ''}-${item.location_id || ''}-${item.id || ''}`
}

function buildParams() {
  const params = {
    ...bossScope.params,
    page: page.value,
    page_size: pageSize.value,
    search: search.value,
  }

  return params
}


async function load(reset = false) {
  if (loading.value) return

  if (reset) {
    page.value = 1
    rows.value = []
    finished.value = false
  }

  if (finished.value && !reset) return

  loading.value = true
  dataError.value = null

  try {
    const res = await api.bossInventoryDetails(buildParams())
    const list = Array.isArray(res?.results) ? res.results : []
    meta.value = res?.meta || null

    total.value = Number(res?.count || 0)

    if (reset) {
      rows.value = list
    } else {
      rows.value = [...rows.value, ...list]
    }

    finished.value = !res?.next_page || rows.value.length >= total.value
  } catch (error) {
    dataError.value = error
    rows.value = []
    total.value = 0
    finished.value = true
    console.error('boss inventory detail failed:', error)
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function loadMore() {
  if (loading.value || finished.value) return
  page.value += 1
  load(false)
}

function onSearch() {
  load(true)
}


function resetSearch() {
  search.value = ''
  load(true)
}

function onPageSizeChange(event) {
  const idx = Number(event.detail.value)
  pageSize.value = pageSizeOptions[idx] || 50
  load(true)
}

function back() {
  uni.navigateBack()
}

onLoad(async (query = {}) => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }

  bossScope.restore(auth.user)
  if (query.warehouse !== undefined) bossScope.warehouse = String(query.warehouse || '')
  if (query.owner !== undefined) bossScope.owner = String(query.owner || '')
  if (query.date_from && query.date_to) bossScope.setDates(query.date_from, query.date_to)
  try {
    await bossScope.loadContext(auth.user, true)
    load(true)
  } catch (error) {
    dataError.value = error
    rows.value = []
    total.value = 0
  }
})


onPullDownRefresh(() => {
  load(true)
})


function onScopeChange() {
  load(true)
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
  background: rgba(255, 255, 255, 0.14);
}

.hero-title {
  font-size: 44rpx;
  font-weight: 700;
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
.table-card {
  margin-top: 24rpx;
  padding: 26rpx 24rpx;
  border-radius: 28rpx;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 18rpx 40rpx rgba(17, 36, 86, 0.08);
}

/* .filter-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220rpx;
  gap: 16rpx;
} */

.search-input {
  min-height: 96rpx;
  padding: 0 22rpx;
  border-radius: 22rpx;
  background: #f5f7fd;
  font-size: 26rpx;
  color: #102047;
}

.picker-box {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 96rpx;
  padding: 16rpx 18rpx;
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

.filter-actions {
  display: flex;
  gap: 16rpx;
  margin-top: 18rpx;
}

.btn-primary,
.btn-ghost {
  flex: 1;
  min-height: 72rpx;
  border-radius: 18rpx;
  font-size: 26rpx;
}

.btn-primary {
  color: #ffffff;
  background: #0b5fff;
}

.btn-ghost {
  color: #2255bb;
  background: #eef4ff;
}

.table-card {
  padding: 0;
  overflow: hidden;
}

.table-head,
.table-row {
  display: flex;
  align-items: center;
}

.table-head {
  padding: 18rpx 20rpx;
  background: #edf3ff;
  font-size: 23rpx;
  font-weight: 700;
  color: #405070;
}

.table-body {
  height: calc(100vh - 430rpx);
}

.table-row {
  padding: 18rpx 20rpx;
  border-bottom: 1rpx solid #edf0f6;
  font-size: 23rpx;
  color: #2b3650;
}

.cell {
  padding: 0 8rpx;
  box-sizing: border-box;
  overflow: hidden;
}

.col-product {
  flex: 1.7;
  min-width: 260rpx;
}

.col-owner {
  flex: 1;
  min-width: 160rpx;
}

.col-loc {
  flex: 1;
  min-width: 160rpx;
}

.col-num {
  flex: 0.72;
  min-width: 104rpx;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  text-align: right;
}

.num-text {
  display: block;
  width: 100%;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.primary {
  color: #0b5fff;
  font-weight: 700;
}

.product-name {
  font-weight: 700;
  color: #102047;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.product-sub {
  margin-top: 6rpx;
  font-size: 21rpx;
  color: #7a879f;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.empty-inline,
.load-tip {
  padding: 28rpx 20rpx;
  text-align: center;
  font-size: 24rpx;
  color: #7c88a2;
}

.filter-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16rpx;
}

.filter-row.second {
  margin-top: 16rpx;
  grid-template-columns: minmax(0, 1fr) 220rpx;
}
</style>
