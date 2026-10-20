<template>
  <view class="p-4">
    <view class="text-lg font-bold mb-2">我的订单</view>
    <view class="search-row">
      <input class="input" v-model="q" placeholder="单号 / 客户" @confirm="search" />
      <button class="btn-outline search-btn" :disabled="firstLoading" @click="search">搜索</button>
    </view>
    <button class="btn-outline export-btn" :disabled="exporting || firstLoading" @click="exportExcel('/api/outbound/orders/owner-export/', {search: appliedSearch}, '订单明细')">{{ exporting ? '正在导出…' : '导出 Excel' }}</button>
    <view v-if="exportError" class="state-card error-state">{{ exportError }}</view>

    <view v-if="firstLoading" class="state-card">正在加载订单…</view>
    <view v-else-if="error && !rows.length" class="state-card error-state">
      <view>{{ error }}</view>
      <button class="btn-outline retry-btn" @click="search">重试</button>
    </view>
    <view v-else-if="!rows.length" class="state-card">没有符合条件的订单</view>

    <view v-for="(order, index) in rows" :key="order?.id ?? index" class="order-card" @click="goDetail(order)">
      <view class="row">
        <view class="font-bold">{{ order?.order_no || ('订单#' + order?.id) }}</view>
        <view class="badge">{{ amountSummaryLabel(order) }}</view>
      </view>
      <view class="text-gray">客户：{{ order?.customer_name || '—' }}</view>
      <view class="text-gray">仓库：{{ order?.warehouse_name || '—' }}</view>
      <view class="text-gray">提交：{{ orderStatus(order, 'submit_status') }}</view>
      <view class="text-gray">审核：{{ orderStatus(order, 'approval_status') }}</view>
      <view v-if="order?.biz_date" class="text-gray">业务日期：{{ order.biz_date }}</view>
      <button v-if="order?.can_reorder" class="btn-outline" @click.stop="reorder(order)">再次下单</button>
    </view>

    <view v-if="loadingMore" class="state-card">正在加载更多…</view>
    <view v-else-if="error && rows.length" class="state-card error-state">
      <view>{{ error }}</view>
      <button class="btn-outline retry-btn" @click="retryLoadMore">重试加载更多</button>
    </view>
    <view v-else-if="rows.length && !list.next" class="state-card">已加载全部订单</view>
  </view>
</template>

<script setup>
import { amountSummaryLabel } from '@/utils/orderAmount'
import { orderStatus } from '@/utils/orderStatus'
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onReachBottom, onShow, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useOwnerExport } from '@/composables/useOwnerExport'

const q = ref('')
const appliedSearch = ref('')
const { exporting, exportError, exportExcel } = useOwnerExport(() => appliedSearch.value)
const list = ref({ count: 0, next: null, previous: null, results: [] })
const rows = computed(() => list.value.results || [])
const firstLoading = ref(false)
const loadingMore = ref(false)
const error = ref('')
const currentPage = ref(0)
const failedPage = ref(null)
const firstShow = ref(true)

let alive = true
let generation = 0

onUnload(() => {
  alive = false
  generation += 1
})

function normalize(response) {
  return Array.isArray(response)
    ? { count: response.length, next: null, previous: null, results: response }
    : (response?.results ? response : { count: 0, next: null, previous: null, results: [] })
}

async function loadOrders({ reset = false, page: requestedPage } = {}) {
  if (!reset && (firstLoading.value || loadingMore.value)) return
  if (!reset && currentPage.value > 0 && !list.value.next && !requestedPage) return

  const requestGeneration = reset ? ++generation : generation
  const page = requestedPage || (reset ? 1 : currentPage.value + 1)
  const searchText = appliedSearch.value

  if (reset) {
    firstLoading.value = true
    list.value = { count: 0, next: null, previous: null, results: [] }
    currentPage.value = 0
  } else {
    loadingMore.value = true
  }
  error.value = ''
  failedPage.value = null

  try {
    const response = await api.orders(searchText, page)
    if (!alive || requestGeneration !== generation) return
    const normalized = normalize(response)
    const merged = reset ? normalized.results : [...list.value.results, ...normalized.results]
    list.value = {
      ...normalized,
      results: Array.from(new Map(merged.map((row) => [String(row.id), row])).values()),
    }
    currentPage.value = page
  } catch (requestError) {
    if (alive && requestGeneration === generation) {
      error.value = requestError?.message || requestError?.data?.detail || '订单加载失败，请稍后重试'
      failedPage.value = page
    }
  } finally {
    if (alive && requestGeneration === generation) {
      firstLoading.value = false
      loadingMore.value = false
      uni.stopPullDownRefresh && uni.stopPullDownRefresh()
    }
  }
}

function search() {
  appliedSearch.value = q.value.trim()
  return loadOrders({ reset: true })
}

function retryLoadMore() {
  return loadOrders({ page: failedPage.value || currentPage.value + 1 })
}

function goDetail(order) {
  if (!order?.id) return
  uni.navigateTo({ url: `/pages/orders/detail?id=${order.id}` })
}
function reorder(order) { uni.navigateTo({ url: `/pages/orders/reorder?id=${order.id}` }) }

function money(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '0.00'
}

onLoad(search)
onReachBottom(() => loadOrders())
onPullDownRefresh(() => loadOrders({reset:true}))
onShow(() => {
  if (firstShow.value) {
    firstShow.value = false
    return
  }
  loadOrders({reset:true})
})
</script>

<style scoped>
.search-row { display: flex; align-items: center; gap: 12rpx; }
.search-row .input { flex: 1; min-width: 0; }
.export-btn { margin: 12rpx 0; }
.search-btn, .retry-btn { width: auto; min-height: 80rpx; }
.state-card { padding: 36rpx 12rpx; color: #6b7280; text-align: center; }
.error-state { margin-top: 16rpx; color: #b42318; background: #fff7ed; border-radius: 12rpx; }
.order-card {
  box-sizing: border-box;
  min-width: 0;
  overflow-wrap: anywhere;
  display: block;
  width: 100%;
  min-height: 96rpx;
  margin: 20rpx 0 0;
  padding: 24rpx;
  text-align: left;
  line-height: 1.5;
  background: #fff;
  border: 1rpx solid #e5e7eb;
  border-radius: 16rpx;
}
.order-card .row { display:flex; flex-wrap:wrap; gap:12rpx; }
.order-card .row > view { min-width:0; max-width:100%; }
.order-card::after { border: 0; }
</style>
