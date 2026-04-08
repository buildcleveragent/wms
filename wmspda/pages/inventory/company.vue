<template>
  <view class="page">
    <view class="toolbar">
      <view class="mode-switch">
        <view
          :class="['mode-btn', { active: mode === 'warehouse' }]"
          @click="changeMode('warehouse')"
        >
          按仓统计
        </view>
        <view
          :class="['mode-btn', { active: mode === 'all' }]"
          @click="changeMode('all')"
        >
          全仓汇总
        </view>
      </view>

      <view class="filters">
        <input
          v-model="search"
          class="input"
          placeholder="搜索仓库 / 货主 / 商品 / 编码 / SKU"
          confirm-type="search"
          @confirm="onSearch"
        />

        <input
          v-model="warehouseId"
          class="input small"
          placeholder="仓库ID"
          type="number"
          v-if="mode === 'warehouse'"
        />

        <input
          v-model="ownerId"
          class="input small"
          placeholder="货主ID"
          type="number"
        />

        <button class="btn" size="mini" @click="onSearch">查询</button>
      </view>
    </view>

    <view v-if="loading && rows.length === 0" class="state-wrap">
      <text class="state-text">加载中...</text>
    </view>

    <view v-else-if="!loading && rows.length === 0" class="state-wrap">
      <text class="state-text">暂无数据</text>
    </view>

    <view v-else class="table-wrap">
      <!-- 表头 -->
      <view class="table-header">
        <view v-if="mode === 'warehouse'" class="cell col-warehouse">仓库</view>
        <view class="cell col-owner">货主</view>
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

      <!-- 数据 -->
      <scroll-view
        class="list"
        scroll-y
        @scrolltolower="loadMore"
        refresher-enabled
        :refresher-triggered="refreshing"
        @refresherrefresh="onRefresh"
      >
        <view
          v-for="(item, index) in rows"
          :key="rowKey(item, index)"
          :class="['table-row', { odd: index % 2 === 1 }]"
        >
          <view v-if="mode === 'warehouse'" class="cell col-warehouse">
            {{ item.warehouse_name || '-' }}
          </view>

          <view class="cell col-owner">{{ item.owner_name || '-' }}</view>
          <view class="cell col-name strong">{{ item.product_name || '-' }}</view>
          <view class="cell col-code">{{ item.product_code || '-' }}</view>
          <view class="cell col-sku">{{ item.product_sku || '-' }}</view>
          <view class="cell col-spec">{{ item.product_spec || '-' }}</view>
          <view class="cell col-unit">{{ item.base_unit || '-' }}</view>
          <view class="cell col-num">{{ item.onhand_qty }}</view>
          <view class="cell col-num primary">{{ item.available_qty }}</view>
          <view class="cell col-num">{{ item.allocated_qty }}</view>
          <view class="cell col-num">{{ item.locked_qty }}</view>
          <view class="cell col-num">{{ item.damaged_qty }}</view>
        </view>

        <view class="bottom-state">
          <text v-if="loadingMore">加载更多中...</text>
          <text v-else-if="finished">没有更多了</text>
        </view>
      </scroll-view>
    </view>
  </view>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '@/utils/request'

const mode = ref('warehouse')
const search = ref('')
const warehouseId = ref('')
const ownerId = ref('')

const rows = ref([])
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
const loadingMore = ref(false)
const refreshing = ref(false)
const finished = ref(false)

function rowKey(item, index) {
  if (mode.value === 'warehouse') {
    return `${item.warehouse_id || ''}_${item.owner_id || ''}_${item.product_id || ''}_${index}`
  }
  return `${item.owner_id || ''}_${item.product_id || ''}_${index}`
}

async function load(reset = false) {
  if (loading.value || loadingMore.value) return

  if (reset) {
    page.value = 1
    finished.value = false
  }

  if (finished.value && !reset) return

  if (reset) {
    loading.value = true
  } else {
    loadingMore.value = true
  }

  try {
    const res = await api.companyInventorySummary({
      mode: mode.value,
      warehouse_id: mode.value === 'warehouse' ? warehouseId.value : '',
      owner_id: ownerId.value,
      search: search.value,
      page: page.value,
      page_size: pageSize.value,
    })

    const list = Array.isArray(res?.results) ? res.results : []

    if (reset) {
      rows.value = list
    } else {
      rows.value = rows.value.concat(list)
    }

    const count = Number(res?.count || 0)
    if (rows.value.length >= count || list.length < pageSize.value) {
      finished.value = true
    } else {
      page.value += 1
    }
  } catch (e) {
    console.error('load company inventory summary failed:', e)
  } finally {
    loading.value = false
    loadingMore.value = false
    refreshing.value = false
  }
}

function changeMode(m) {
  if (mode.value === m) return
  mode.value = m
  if (m === 'all') {
    warehouseId.value = ''
  }
  load(true)
}

function onSearch() {
  load(true)
}

function loadMore() {
  load(false)
}

function onRefresh() {
  refreshing.value = true
  load(true)
}

onMounted(() => {
  load(true)
})
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: #fff;
  box-sizing: border-box;
}

.toolbar {
  border-bottom: 1rpx solid #dcdcdc;
  background: #fff;
}

.mode-switch {
  display: flex;
  padding: 12rpx;
  gap: 12rpx;
}

.mode-btn {
  padding: 12rpx 24rpx;
  border: 1rpx solid #cfcfcf;
  background: #fff;
  color: #333;
  font-size: 24rpx;
}

.mode-btn.active {
  background: #1677ff;
  color: #fff;
  border-color: #1677ff;
}

.filters {
  display: flex;
  gap: 12rpx;
  padding: 0 12rpx 12rpx;
  align-items: center;
}

.input {
  flex: 1;
  height: 64rpx;
  padding: 0 16rpx;
  border: 1rpx solid #cfcfcf;
  background: #fff;
  font-size: 24rpx;
  box-sizing: border-box;
}

.input.small {
  flex: 0 0 140rpx;
}

.btn {
  height: 64rpx;
  line-height: 64rpx;
  padding: 0 24rpx;
  background: #1677ff;
  color: #fff;
  border-radius: 0;
}

.table-wrap {
  background: #fff;
}

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

.col-warehouse {
  flex: 1.1;
  min-width: 140rpx;
}

.col-owner {
  flex: 1;
  min-width: 120rpx;
}

.col-name {
  flex: 1.4;
  min-width: 160rpx;
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

.list {
  height: calc(100vh - 160rpx);
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

.bottom-state {
  text-align: center;
  color: #888;
  font-size: 22rpx;
  padding: 20rpx 0 30rpx;
  background: #fff;
}
</style>