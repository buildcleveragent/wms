<template>
  <view class="page">
    <view class="search-bar">
      <input
        v-model="q"
        class="search-input"
        placeholder="搜索商品名称 / 编码 / SKU"
        confirm-type="search"
        @confirm="onSearch"
      />
      <button class="search-btn" size="mini" @click="onSearch">搜索</button>
    </view>

    <view v-if="(loading || refreshing) && rows.length === 0" class="state-wrap">
      <text class="state-text">加载中...</text>
    </view>

    <view v-else-if="error && rows.length === 0" class="state-wrap state-column">
      <text class="state-text">{{ error }}</text>
      <button size="mini" @click="reload">重试</button>
    </view>

    <view v-else-if="!loading && rows.length === 0" class="state-wrap">
      <text class="state-text">暂无库存数据</text>
    </view>

    <template v-else>
      <scroll-view class="table-scroll" scroll-x>
        <view class="table-content">
          <view class="table-header">
            <view class="cell col-name">商品名</view>
            <view class="cell col-code">编码</view>
            <view class="cell col-sku">SKU</view>
            <view class="cell col-spec">规格</view>
            <view class="cell col-unit">单位</view>
            <view class="cell col-num">现有</view>
            <view class="cell col-num">可用</view>
            <view class="cell col-num">分配</view>
            <view class="cell col-num">锁定</view>
            <view class="cell col-num">残次</view>
          </view>
        <view
          v-for="(item, index) in rows"
          :key="item.id"
          :class="['table-row', { odd: index % 2 === 1 }]"
        >
          <view class="cell col-name strong">{{ item.product_name || '-' }}</view>
          <view class="cell col-code">{{ item.product_code || '-' }}</view>
          <view class="cell col-sku">{{ item.product_sku || '-' }}</view>
          <view class="cell col-spec">{{ item.product_spec || '-' }}</view>
          <view class="cell col-unit">{{ item.base_unit || '-' }}</view>
<!--          <view class="cell col-num">{{ item.onhand_qty }}</view>
          <view class="cell col-num primary">{{ item.available_qty }}</view>
          <view class="cell col-num">{{ item.allocated_qty }}</view>
          <view class="cell col-num">{{ item.locked_qty }}</view>
          <view class="cell col-num">{{ item.damaged_qty }}</view> -->
		  <view class="cell col-num">
		    <text class="num-text">{{ fmtQty(item.onhand_qty_display || item.onhand_qty) }}</text>
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

        </view>
      </scroll-view>

      <view class="bottom-state">
        <text class="loaded-count">已加载 {{ rows.length }} / 总计 {{ total }}</text>
        <text v-if="loadingMore">加载更多中...</text>
        <button v-else-if="error" class="load-more-button" size="mini" @click="loadMore">
          重试加载
        </button>
        <button v-else-if="hasNext" class="load-more-button" size="mini" @click="loadMore">
          加载更多
        </button>
        <text v-else>已加载全部库存</text>
      </view>
    </template>
  </view>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { onPullDownRefresh, onReachBottom, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { mergeUniqueById } from '@/utils/pagination'

const PAGE_SIZE = 50

const q = ref('')
const rows = ref([])
const page = ref(1)
const loading = ref(false)
const loadingMore = ref(false)
const refreshing = ref(false)
const hasNext = ref(true)
const total = ref(0)
const error = ref('')
let requestGeneration = 0
let active = true

function fmtQty(value) {
  if (value === null || value === undefined || value === '') return '-'

  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)

  return String(Number(n.toFixed(4)))
}

function isBusy() {
  return loading.value || loadingMore.value || refreshing.value
}

async function loadFirst({ refresh = false } = {}) {
  const generation = ++requestGeneration
  const search = q.value

  page.value = 1
  hasNext.value = true
  total.value = 0
  error.value = ''
  rows.value = []
  loading.value = !refresh
  loadingMore.value = false
  refreshing.value = refresh

  try {
    const res = await api.inventorySummary({
      search,
      page: 1,
      page_size: PAGE_SIZE,
    })
    if (!active || generation !== requestGeneration) return

    const list = Array.isArray(res?.results) ? res.results : []
    rows.value = mergeUniqueById([], list, { replace: false })
    total.value = Number.isFinite(Number(res?.count)) ? Number(res.count) : rows.value.length
    hasNext.value = Boolean(res?.next)
    page.value = 2
    error.value = ''
  } catch (e) {
    if (active && generation === requestGeneration) {
      error.value = e?.message || '库存加载失败，请稍后重试'
    }
  } finally {
    if (active && generation === requestGeneration) {
      loading.value = false
      refreshing.value = false
    }
  }
}

function onSearch() {
  loadFirst()
}

function reload() {
  loadFirst()
}

async function loadMore() {
  if (!active || isBusy() || !hasNext.value) return

  const generation = requestGeneration
  const requestedPage = page.value
  const search = q.value
  loadingMore.value = true

  try {
    const res = await api.inventorySummary({
      search,
      page: requestedPage,
      page_size: PAGE_SIZE,
    })
    if (!active || generation !== requestGeneration) return

    const list = Array.isArray(res?.results) ? res.results : []
    rows.value = mergeUniqueById(rows.value, list, { replace: false })
    total.value = Number.isFinite(Number(res?.count)) ? Number(res.count) : total.value
    hasNext.value = Boolean(res?.next)
    page.value = requestedPage + 1
    error.value = ''
  } catch (e) {
    if (active && generation === requestGeneration) {
      error.value = e?.message || '库存加载失败，请稍后重试'
    }
  } finally {
    if (active && generation === requestGeneration) loadingMore.value = false
  }
}

async function refreshPage() {
  try {
    await loadFirst({ refresh: true })
  } finally {
    uni.stopPullDownRefresh()
  }
}

onReachBottom(loadMore)
onPullDownRefresh(refreshPage)
onUnload(() => {
  active = false
  requestGeneration += 1
})

onMounted(() => {
  active = true
  loadFirst()
})

onUnmounted(() => {
  active = false
  requestGeneration += 1
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: #fff;
  box-sizing: border-box;
}

.search-bar {
  display: flex;
  gap: 12rpx;
  padding: 12rpx;
  background: #fff;
  border-bottom: 1rpx solid #dcdcdc;
}

.search-input {
  flex: 1;
  height: 64rpx;
  padding: 0 16rpx;
  border: 1rpx solid #cfcfcf;
  border-radius: 0;
  background: #fff;
  font-size: 26rpx;
  box-sizing: border-box;
}

.search-btn {
  height: 64rpx;
  line-height: 64rpx;
  padding: 0 24rpx;
  background: #1677ff;
  color: #fff;
  border-radius: 0;
}

.table-content { min-width: 1320rpx; background: #fff; }

.table-header,
.table-row {
  display: flex;
  align-items: center;
  min-height: 64rpx;
  box-sizing: border-box;
}

.table-header {
  background: #f3f3f3;
  border-bottom: 1rpx solid #d9d9d9;
}

.table-row {
  background: #fff;
  border-bottom: 1rpx solid #ededed;
}

.table-row.odd {
  background: #f7f7f7;
}

.cell {
  border: none !important;
  border-radius: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  outline: none !important;

  padding: 0 10rpx;
  box-sizing: border-box;
  font-size: 24rpx;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.table-header .cell {
  font-size: 22rpx;
  color: #666;
  font-weight: 600;
}

.col-name {
  flex: 1.6;
  min-width: 180rpx;
}

.col-code {
  flex: 1;
  min-width: 120rpx;
}

.col-sku {
  flex: 1;
  min-width: 120rpx;
}

.col-spec {
  flex: 1;
  min-width: 120rpx;
}

.col-unit {
  flex: 0.6;
  min-width: 80rpx;
  text-align: center;
}

.col-num {
  flex: 0.8;
  min-width: 100rpx;
  text-align: right;
}

.strong {
  font-weight: 600;
  color: #222;
}

.primary {
  color: #1677ff;
  font-weight: 600;
}

.table-scroll {
  width: 100%;
}

.state-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 50vh;
}

.state-text {
  color: #888;
  font-size: 26rpx;
}
.state-column { flex-direction: column; gap: 20rpx; }

.bottom-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14rpx;
  text-align: center;
  color: #888;
  font-size: 22rpx;
  padding: 20rpx 0 30rpx;
  background: #fff;
}

.loaded-count {
  color: #666;
}

.load-more-button {
  min-width: 180rpx;
}

.col-num {
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

</style>
