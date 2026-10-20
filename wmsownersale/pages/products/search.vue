<template>
  <view class="page-shell">
    <view class="page-header">
      <view class="context-line">客户：{{ cart.customer?.name || '未选择' }}</view>
      <view class="search-bar">
        <input
          v-model="q"
          class="search-input"
          placeholder="名称 / 编码 / 条码"
          confirm-type="search"
          @confirm="search"
        />
        <button class="toolbar-button" @click="search">搜索</button>
        <!-- #ifndef H5 -->
        <button class="toolbar-button" @click="scanAdd">扫码</button>
        <!-- #endif -->
      </view>
      <view class="purchase-tabs">
        <button :class="{ selected: mode === 'all' }" @click="changeMode('all')">全部商品</button>
        <button :class="{ selected: mode === 'frequent' }" @click="changeMode('frequent')">客户常购</button>
        <button :disabled="firstLoading" @click="loadProducts({reset:true})">刷新</button>
      </view>
      <view v-if="mode === 'frequent'" class="purchase-hint">近90天本人可见的已审核订购记录，不代表实际发货。</view>
      <view v-if="lastLoadedAt" class="purchase-hint">最近成功加载：{{ lastLoadedAt }}</view>
      <view v-if="loadError && rows.length" class="purchase-hint">{{ loadError }}</view>
    </view>

    <scroll-view
      class="content"
      scroll-y
      :lower-threshold="120"
      @scrolltolower="loadProducts"
    >
      <AsyncState
        v-if="firstLoading"
        kind="loading"
        message="正在加载商品…"
      />
      <AsyncState
        v-else-if="loadError && !rows.length"
        kind="error"
        :message="loadError"
        @retry="search"
      />
      <AsyncState
        v-else-if="!rows.length"
        kind="empty"
        message="没有符合条件的商品"
      />

      <ProductCard
        v-for="product in rows"
        :key="product.id"
        :product="product"
        :quantity="qtyMap[product.id] ?? ''"
        :base-quantity="baseQtyPreview(product)"
        :selected-unit-index="getUnitIndex(product)"
        :amount="lineAmount(baseQtyPreview(product), product.price)"
        @quantity-input="setQty(product.id, $event)"
        @unit-change="onUnitChange(product, $event)"
        @price-input="setPrice(product, $event)"
        @price-commit="enforceMin(product)"
        @add="add(product)"
      />

      <AsyncState
        v-if="loadingMore"
        kind="loading"
        message="正在加载更多商品…"
      />
      <AsyncState
        v-else-if="loadMoreError"
        kind="error"
        :message="loadMoreError"
        @retry="loadProducts"
      />
      <AsyncState
        v-else-if="rows.length && !list.next"
        kind="done"
        message="已加载全部商品"
      />
    </scroll-view>

    <view class="footer">
      <button class="cart-button" @click="goCart">
        查看订单 · {{ cart.items.length }} 行 · ¥{{ cart.totalAmount }}
      </button>
    </view>
  </view>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { onLoad, onShow, onHide } from '@dcloudio/uni-app'

import AsyncState from '@/components/AsyncState.vue'
import ProductCard from '@/components/ProductCard.vue'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'
import { enforceMinimumPrice, initializePriceGuard } from '@/utils/pricing'
import { previewBaseQuantity, validateDesiredQuantity } from '@/utils/quantity'
import { lineAmount } from '@/utils/orderAmount'
import { api } from '@/utils/request'
import { mergeUniqueById } from '@/utils/pagination'
// #ifndef H5
import { scanOne } from '@/utils/scan'
// #endif

type ProductList = {
  count: number
  next: string | null
  previous: string | null
  results: any[]
}

const emptyList = (): ProductList => ({ count: 0, next: null, previous: null, results: [] })

const auth = useAuth()
const cart = useCart()
const q = ref('')
const appliedSearch = ref('')
const mode = ref('all')
const list = ref<ProductList>(emptyList())
const rows = computed(() => list.value.results || [])
const qtyMap = ref<Record<number, string>>({})
const firstLoading = ref(false)
const loadingMore = ref(false)
const loadError = ref('')
const loadMoreError = ref('')
const currentPage = ref(0)
const lastLoadedAt = ref('')
let searchGeneration = 0
let alive = true
let active = true, inflight = false, pendingReset = false, firstShow = true, denied = false
const identity = () => `${auth.user?.id}:${auth.user?.owner_id}`


function normalizeList(response: any): ProductList {
  if (Array.isArray(response)) {
    return { count: response.length, next: null, previous: null, results: response }
  }
  return response?.results ? response : emptyList()
}

async function loadProducts({ reset = false } = {}) {
  if (!alive || !active || denied || (typeof document !== 'undefined' && document.hidden)) return
  if (reset) searchGeneration++
  if (inflight) { if (reset) pendingReset = true; return }
  if (!reset && currentPage.value > 0 && !list.value.next) return

  const generation = searchGeneration, actor = identity()
  inflight = true
  const page = reset ? 1 : currentPage.value + 1
  const searchText = appliedSearch.value
  if (reset) {
    loadError.value = ''
    loadMoreError.value = ''
    firstLoading.value = true
  } else {
    loadMoreError.value = ''
    loadingMore.value = true
  }

  try {
    const response = mode.value === 'frequent'
      ? await api.frequentProducts(cart.customer?.id, { search: searchText, page, warehouse_id: cart.warehouse_id })
      : await api.products(searchText, page, cart.warehouse_id || undefined)
    if (!alive || !active || generation !== searchGeneration || actor !== identity()) return
    const normalized = normalizeList(response)
    normalized.results.forEach(initializePriceGuard)
    const merged = reset
      ? normalized.results
      : [...list.value.results, ...normalized.results]
    list.value = {
      ...normalized,
      results: mergeUniqueById([], merged),
    }
    currentPage.value = page
    lastLoadedAt.value = new Date().toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false})
  } catch (error: any) {
    if (!alive || !active || generation !== searchGeneration || actor !== identity()) return
    const status = Number(error?.statusCode || error?.code)
    if ([401,403].includes(status)) { list.value = emptyList(); lastLoadedAt.value = ''; denied = true }
    const message = rows.value.length ? '更新失败，当前为上次数据' :
      ([404,405].includes(status) ? '快捷下单暂不可用' : error?.message || '商品加载失败，请稍后重试')
    if (reset) loadError.value = message
    else loadMoreError.value = message
  } finally {
    inflight = false
    if (alive) {
      firstLoading.value = false
      loadingMore.value = false
    }
    if (pendingReset && active && alive) { pendingReset = false; loadProducts({reset: true}) }
  }
}

function search() {
  if (appliedSearch.value !== q.value.trim()) { list.value = emptyList(); currentPage.value = 0; lastLoadedAt.value = '' }
  appliedSearch.value = q.value.trim()
  return loadProducts({ reset: true })
}
function changeMode(value) {
  if (mode.value === value) return
  mode.value = value; list.value = emptyList(); currentPage.value = 0; qtyMap.value = {}; lastLoadedAt.value = ''
  search()
}

function setQty(productId: number, value: unknown) {
  qtyMap.value = {
    ...qtyMap.value,
    [productId]: value == null ? '' : String(value),
  }
}

function setPrice(product: any, value: unknown) {
  product.price = value == null || value === '' ? '' : String(value)
}

function getUnitIndex(product: any) {
  const options = product.unitOptions || []
  const index = Number(product.selectedUnitIndex)
  return Number.isInteger(index) && index >= 0 && index < options.length ? index : 0
}

function getSelectedUnit(product: any) {
  const index = getUnitIndex(product)
  const option = (product.unitOptions || [])[index] || {}
  return {
    index,
    label: option.label || product.base_unit_name,
    multiplier: Number(option.multiplier || 1),
  }
}

function onUnitChange(product: any, value: unknown) {
  const index = Number(value)
  if (Number.isInteger(index) && index >= 0 && index < (product.unitOptions || []).length) {
    product.selectedUnitIndex = index
  }
}

function baseQtyPreview(product: any) {
  return previewBaseQuantity(qtyMap.value[product.id], getSelectedUnit(product).multiplier)
}

function enforceMin(product: any) {
  const result = enforceMinimumPrice(product)
  if (!result.valid) uni.showToast({ title: result.error, icon: 'none' })
}

function add(product: any) {
  if (!product?.id || product.can_add === false || cart.pending_submission) return
  const selected = getSelectedUnit(product)
  const quantity = validateDesiredQuantity(qtyMap.value[product.id], selected.multiplier)
  if (!quantity.valid) {
    uni.showToast({ title: quantity.error, icon: 'none' })
    return
  }

  const currentBaseQuantity = cart.items.filter(item => item.product_id === product.id)
    .reduce((sum, item) => sum + Number(item.qty || 0), 0)
  const totalBaseQuantity = Number((currentBaseQuantity + quantity.baseQty).toFixed(3))
  const available = Number(product.available || 0)
  if (totalBaseQuantity > available) {
    uni.showToast({
      title: `累计数量 ${totalBaseQuantity} 超过可用库存 ${available}`,
      icon: 'none',
    })
    return
  }

  const changed = cart.addItem({
      id: product.id,
      sku: product.sku,
      name: product.name,
      spec: product.spec,
      price: Number(product.price || 0),
      orig_price: Number(product.orig_price ?? product.price ?? 0),
      minimum_sale_price: product.minimum_sale_price,
      min_price: product.min_price,
      qty: quantity.baseQty,
      product_image_url: product.product_image_url,
      gtin: product.gtin,
      base_unit_name: product.base_unit_name,
      aux_uom_name: product.aux_uom_name,
      aux_qty_in_base: product.aux_qty_in_base,
      product_min_price: product.product_min_price,
      max_discount: product.max_discount,
      available,
      unitOptions: product.unitOptions,
      selectedUnitIndex: selected.index,
      base_uom_id: product.base_uom_id,
      catalog_version: product.catalog_version,
    })

  if (!changed) {
    uni.showToast({ title: '数量无效，请重新输入', icon: 'none' })
    return
  }
  uni.showToast({
    title: `已加入：${product.name || product.sku} × ${quantity.saleQty}${selected.label}`,
    icon: 'none',
  })
}

function goCart() {
  uni.redirectTo({ url: '/pages/orders/cart' })
}

// #ifndef H5
async function scanAdd() {
  try {
    const code = await scanOne()
    if (!code) return
    q.value = code
    await search()
  } catch (error: any) {
    uni.showToast({ title: error?.errMsg || error?.message || '扫码失败，请检查相机权限', icon: 'none' })
  }
}
// #endif

onLoad(() => {
  auth.ensureAuth()
  if (!cart.hasContextForUser(auth.user?.id, auth.user?.owner_id)) {
    cart.resetOrder()
    uni.redirectTo({ url: '/pages/warehouses/select' })
    return
  }
  if (!cart.customer) {
    uni.redirectTo({ url: '/pages/customers/select?returnTo=products' })
    return
  }
  search()
})
onShow(() => {
  active = true
  if (firstShow) { firstShow = false; return }
  loadProducts({reset:true})
})
onHide(() => { active = false; searchGeneration++; pendingReset = false })
watch(identity, () => {
  searchGeneration++; list.value = emptyList(); currentPage.value = 0; lastLoadedAt.value = ''; denied = false
  cart.resetOrder(); uni.redirectTo({url:'/pages/warehouses/select'})
})
function visibilityChange() {
  searchGeneration++
  if (typeof document !== 'undefined' && !document.hidden && active) loadProducts({reset:true})
}
if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibilityChange)

onUnmounted(() => {
  alive = false
  searchGeneration += 1
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibilityChange)
})
</script>

<style scoped>
.purchase-tabs { display: flex; gap: 12rpx; margin-top: 16rpx; }
.purchase-tabs button { flex: 1; margin: 0; font-size: 26rpx; }
.purchase-tabs .selected { color: #1d4ed8; background: #eff6ff; }
.purchase-hint { color: #64748b; margin-top: 12rpx; font-size: 24rpx; }
.page-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  /* #ifdef H5 */
  /* H5 navigation occupies part of the viewport outside the page content. */
  height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px));
  height: calc(100dvh - var(--window-top, 0px) - var(--window-bottom, 0px));
  /* #endif */
  overflow: hidden;
  background: #f5f7fa;
}
.page-header {
  flex: 0 0 auto;
  padding: 20rpx;
  background: #fff;
  border-bottom: 1rpx solid #e5e7eb;
}
.context-line { margin-bottom: 14rpx; font-size: 28rpx; font-weight: 600; color: #111827; }
.search-bar { display: flex; gap: 12rpx; align-items: center; }
.search-input {
  box-sizing: border-box;
  flex: 1;
  min-width: 0;
  min-height: 88rpx;
  padding: 0 20rpx;
  font-size: 28rpx;
  border: 1rpx solid #d1d5db;
  border-radius: 12rpx;
}
.toolbar-button {
  flex: 0 0 auto;
  min-width: 128rpx;
  min-height: 88rpx;
  margin: 0;
  font-size: 27rpx;
  color: #2563eb;
  background: #fff;
  border: 1rpx solid #2563eb;
}
.content { flex: 1; min-height: 0; padding-top: 20rpx; box-sizing: border-box; }
.footer {
  flex: 0 0 auto;
  padding: 14rpx 20rpx calc(14rpx + env(safe-area-inset-bottom));
  background: #fff;
  border-top: 1rpx solid #d1d5db;
}
.cart-button {
  min-height: 88rpx;
  margin: 0;
  font-size: 29rpx;
  color: #fff;
  background: #2563eb;
}
@media (min-width: 900px) {
  .page-header, .footer { padding-left: max(20rpx, calc((100vw - 1100px) / 2)); padding-right: max(20rpx, calc((100vw - 1100px) / 2)); }
}
</style>
