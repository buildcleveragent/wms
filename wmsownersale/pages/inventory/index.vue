<template>
  <view class="page">
    <button v-if="canView" class="inventory-alert-entry" size="mini" @click="openAlerts">库存预警 ›</button>
    <view v-if="canView" class="search-bar">
      <input
        v-model="q"
        class="search-input"
        placeholder="搜索商品名称 / 编码 / SKU"
        confirm-type="search"
        @confirm="onSearch"
      />
      <button class="search-btn" size="mini" @click="onSearch">搜索</button>
    </view>
    <button v-if="canExport" size="mini" :disabled="exporting || loading || refreshing" @click="exportExcel('/api/inventory/summary/export/', {search: appliedSearch}, '库存汇总')">{{ exporting ? '正在导出…' : '导出 Excel' }}</button>
    <view v-if="exportError" class="state-wrap">{{ exportError }}</view>
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
        <view class="table-content" :style="{ width: `${tableWidth}rpx` }">
          <view class="table-header">
            <view
              v-for="column in columns"
              :key="column.key"
              :class="['cell', column.className]"
              :style="{ width: `${column.width}rpx` }"
            >{{ column.label }}</view>
          </view>
          <view
            v-for="(item, index) in rows"
            :key="item.id"
            :class="['table-row', { odd: index % 2 === 1 }]"
          >
            <view
              v-for="column in columns"
              :key="column.key"
              :class="['cell', column.className, {
                strong: column.key === 'product_name',
                primary: column.key === 'available_qty',
              }]"
              :style="{ width: `${column.width}rpx` }"
            >
              <text
                v-if="column.key === 'product_name'"
                class="product-link"
                role="link"
                tabindex="0"
                @click="openInventory(item.id)"
                @keydown.enter="openInventory(item.id)"
              >{{ (item.product_display_name || item.product_name) || '-' }}</text>
              <text v-else-if="column.quantity" class="num-text">{{ quantityText(item, column.key) }}</text>
              <template v-else-if="column.key === 'base_unit'">{{ item.base_unit_name || item.base_unit || '-' }}</template>
              <template v-else>{{ item[column.key] || '-' }}</template>
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
import { formatQuantity } from '@/utils/quantity'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onPullDownRefresh, onReachBottom, onUnload, onShow, onHide } from '@dcloudio/uni-app'
import { useInventoryAccess } from '@/composables/useInventoryAccess'
import { api } from '@/utils/request'
import { useOwnerExport } from '@/composables/useOwnerExport'
import { mergeUniqueById } from '@/utils/pagination'

const { canView, canExport, identity, revoke } = useInventoryAccess()
const PAGE_SIZE = 50

function openInventory(id) { uni.navigateTo({ url: `/pages/inventory/detail?id=${id}` }) }
function openAlerts() { uni.navigateTo({ url: '/pages/inventory/alerts' }) }

const q = ref('')
const appliedSearch = ref('')
const { exporting, exportError, exportExcel } = useOwnerExport(() => JSON.stringify([appliedSearch.value, identity.value]), {allowed: () => canExport.value, onDenied: revoke})
const rows = ref([])
const columnDefinitions = [
  { key: 'onhand_qty', label: '现存', className: 'col-num', width: 96, quantity: true },
  { key: 'available_qty', label: '可用', className: 'col-num', width: 96, quantity: true },
  { key: 'allocated_qty', label: '分配', className: 'col-num', width: 96, quantity: true },
  { key: 'product_name', label: '商品名称', className: 'col-name', width: 240 },
  { key: 'product_spec', label: '规格', className: 'col-spec', width: 130 },
  { key: 'base_unit', label: '单位', className: 'col-unit', width: 80 },
  { key: 'product_code', label: '编码', className: 'col-code', width: 160 },
  { key: 'product_sku', label: 'SKU', className: 'col-sku', width: 120 },
  { key: 'locked_qty', label: '锁定', className: 'col-num', width: 100, quantity: true },
  { key: 'damaged_qty', label: '残次', className: 'col-num', width: 100, quantity: true },
]
const columns = computed(() => columnDefinitions.map((column) => {
  if (!column.quantity) return column
  // Reserve 15rpx per monospace character plus 8rpx padding for the whole column.
  const width = rows.value.reduce((current, item) => (
    Math.max(current, quantityText(item, column.key).length * 15 + 8)
  ), column.width)
  return { ...column, width }
}))
const tableWidth = computed(() => columns.value.reduce((width, column) => width + column.width, 0))

function quantityText(item, key) {
  return fmtQty(item[`${key}_display`] || item[key])
}

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

  return formatQuantity(value)
}

function isBusy() {
  return loading.value || loadingMore.value || refreshing.value
}

function clearInventory() {
  requestGeneration++; rows.value=[]; total.value=0; hasNext.value=false
  loading.value=false; loadingMore.value=false; refreshing.value=false
  error.value='登录状态或库存访问权限已失效'
}
watch(identity, () => { clearInventory(); if (active && canView.value) loadFirst() }, {flush:'sync'})
async function loadFirst({ refresh = false } = {}) {
  if (!canView.value) { clearInventory(); return }
  const generation = ++requestGeneration
  const search = appliedSearch.value

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
      if ([401,403].includes(Number(e?.statusCode || e?.status))) { revoke(); clearInventory(); return }
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
  appliedSearch.value = q.value.trim()
  loadFirst()
}

function reload() {
  loadFirst()
}

async function loadMore() {
  if (!canView.value) { clearInventory(); return }
  if (!active || isBusy() || !hasNext.value) return

  const generation = requestGeneration
  const requestedPage = page.value
  const search = appliedSearch.value
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
      if ([401,403].includes(Number(e?.statusCode || e?.status))) { revoke(); clearInventory(); return }
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

onHide(() => { active=false; requestGeneration++; loading.value=false; loadingMore.value=false; refreshing.value=false })
onShow(() => { const returning=!active; active=true; if (returning) loadFirst() })
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

.table-content { background: #fff; }

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

  flex: 0 0 auto;
  padding: 8rpx 10rpx;
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

.col-name,
.col-spec,
.col-unit {
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  text-overflow: clip;
  line-height: 1.4;
}

.col-unit {
  text-align: center;
}

.col-num {
  padding-right: 4rpx;
  padding-left: 4rpx;
  text-align: right;
}

.strong {
  font-weight: 600;
  color: #222;
}

.product-link {
  color: #2563eb;
  cursor: pointer;
}

.product-link:hover,
.product-link:focus-visible {
  text-decoration: underline;
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
  font-family: monospace;
  font-variant-numeric: tabular-nums;
}

</style>
