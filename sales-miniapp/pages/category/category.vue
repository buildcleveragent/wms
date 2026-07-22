<template>
  <view class="category-page">
    <view class="category-head">
      <view class="search" @click="goSearch">搜索当前分类商品</view>
      <view class="major-bar">
        <scroll-view class="major-scroll" scroll-x :show-scrollbar="false">
          <view class="major-row">
            <view
              v-for="item in majorCategories"
              :key="item.id"
              :class="['major-item', sameId(item.id, activeMajorId) && 'active']"
              @click="selectMajor(item.id)"
            >
              <image v-if="item.image_url" class="major-image" :src="item.image_url" mode="aspectFill" />
              <view v-else class="major-image major-placeholder">{{ item.name.slice(0, 1) }}</view>
              <text class="major-name">{{ item.name }}</text>
            </view>
          </view>
        </scroll-view>
        <view :class="['all-major', activeMajorId === ALL_CATEGORY_ID && 'active']" @click="selectMajor(ALL_CATEGORY_ID)">
          <text>全部</text>
          <text class="menu-icon">☰</text>
        </view>
      </view>
    </view>

    <view class="category-body">
      <scroll-view class="middle-nav" scroll-y :show-scrollbar="false">
        <view
          v-for="item in middleCategories"
          :key="item.id"
          :class="['middle-item', sameId(item.id, activeMiddleId) && 'active']"
          @click="selectMiddle(item.id)"
        >
          {{ item.name }}
        </view>
      </scroll-view>

      <view class="product-panel">
        <scroll-view v-if="smallCategories.length > 1" class="small-scroll" scroll-x :show-scrollbar="false">
          <view class="small-row">
            <view
              v-for="item in smallCategories"
              :key="item.id"
              :class="['small-chip', sameId(item.id, activeSmallId) && 'active']"
              @click="selectSmall(item.id)"
            >
              {{ item.name }}
            </view>
          </view>
        </scroll-view>

        <view class="sort-row">
          <view :class="['sort-item', ordering === 'sort' && 'active']" @click="setOrdering('sort')">综合</view>
          <view :class="['sort-item', ordering === 'hot' && 'active']" @click="setOrdering('hot')">热卖</view>
          <view :class="['sort-item', isPriceOrdering && 'active']" @click="setPriceOrdering">{{ priceLabel }}</view>
          <view :class="['sort-item', onlyStock && 'active']" @click="toggleStock">有货</view>
        </view>

        <scroll-view class="product-scroll" scroll-y @scrolltolower="loadProducts(false)">
          <view class="product-list">
            <ProductCard
              v-for="product in products"
              :key="product.id"
              :product="product"
              @open="openProduct"
              @add="addProduct"
            />
            <EmptyState v-if="!products.length && !loading" text="该分类暂无商品" />
            <view v-else class="load-more">{{ loading ? '加载中' : hasMore ? '继续上拉' : '没有更多了' }}</view>
          </view>
        </scroll-view>
      </view>
    </view>

    <CartBar :count="cart.items.length" :amount="cart.totalAmount" @checkout="goCart" />
  </view>
</template>

<script setup>
import { onLoad, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import CartBar from '../../components/CartBar.vue'
import EmptyState from '../../components/EmptyState.vue'
import ProductCard from '../../components/ProductCard.vue'
import { productService } from '../../services/product'
import { useCartStore } from '../../stores/cart'
import { getToken } from '../../utils/request'

const cart = useCartStore()
const categories = ref([])
const products = ref([])
const activeMajorId = ref('')
const activeMiddleId = ref('')
const activeSmallId = ref('')
const ordering = ref('sort')
const onlyStock = ref(false)
const page = ref(1)
const hasMore = ref(true)
const loading = ref(false)
const requestSeq = ref(0)
const ALL_CATEGORY_ID = 'all'
const ALL_MIDDLE_ID = 'all-middle'
const ALL_SMALL_ID = 'all-small'

const sameId = (left, right) => String(left) === String(right)
const childrenOf = (parentId) => categories.value.filter((item) => sameId(item.parent_id, parentId))
const majorCategories = computed(() => categories.value.filter((item) => !item.parent_id || Number(item.level) === 1))
const selectedMajor = computed(() => majorCategories.value.find((item) => sameId(item.id, activeMajorId.value)))
const selectedMiddle = computed(() => categories.value.find((item) => sameId(item.id, activeMiddleId.value)))
const middleCategories = computed(() => {
  if (!selectedMajor.value) return [{ id: ALL_MIDDLE_ID, name: '全部商品' }]
  return [
    { id: ALL_MIDDLE_ID, name: `全部${selectedMajor.value.name}` },
    ...childrenOf(selectedMajor.value.id),
  ]
})
const smallCategories = computed(() => {
  if (!selectedMiddle.value) return [{ id: ALL_SMALL_ID, name: '全部' }]
  return [{ id: ALL_SMALL_ID, name: '全部' }, ...childrenOf(selectedMiddle.value.id)]
})
const activeCategoryId = computed(() => {
  if (activeSmallId.value && activeSmallId.value !== ALL_SMALL_ID) return activeSmallId.value
  if (activeMiddleId.value && activeMiddleId.value !== ALL_MIDDLE_ID) return activeMiddleId.value
  if (activeMajorId.value && activeMajorId.value !== ALL_CATEGORY_ID) return activeMajorId.value
  return ''
})
const isPriceOrdering = computed(() => ordering.value === 'price_asc' || ordering.value === 'price_desc')
const priceLabel = computed(() => (ordering.value === 'price_desc' ? '价格↓' : ordering.value === 'price_asc' ? '价格↑' : '价格'))

function categoryChain(categoryId) {
  const byId = new Map(categories.value.map((item) => [String(item.id), item]))
  const chain = []
  let node = byId.get(String(categoryId))
  const seen = new Set()
  while (node && !seen.has(String(node.id))) {
    seen.add(String(node.id))
    chain.unshift(node)
    node = node.parent_id ? byId.get(String(node.parent_id)) : null
  }
  return chain
}

async function loadCategories(initialId = '') {
  const rows = await productService.categories()
  categories.value = rows || []
  const fallback = majorCategories.value[0] && majorCategories.value[0].id
  applyCategorySelection(initialId || fallback || ALL_CATEGORY_ID)
}

function applyCategorySelection(categoryId) {
  if (categoryId === ALL_CATEGORY_ID) {
    activeMajorId.value = ALL_CATEGORY_ID
    activeMiddleId.value = ALL_MIDDLE_ID
    activeSmallId.value = ALL_SMALL_ID
    loadProducts(true)
    return
  }
  const chain = categoryChain(categoryId)
  if (!chain.length) {
    applyCategorySelection(ALL_CATEGORY_ID)
    return
  }
  const major = chain[0]
  const middles = childrenOf(major.id)
  activeMajorId.value = major.id
  activeMiddleId.value = chain[1] ? chain[1].id : (middles[0] ? middles[0].id : ALL_MIDDLE_ID)
  activeSmallId.value = chain[2] ? chain[2].id : ALL_SMALL_ID
  loadProducts(true)
}

function selectMajor(id) {
  if (sameId(id, activeMajorId.value)) return
  applyCategorySelection(id)
}

function selectMiddle(id) {
  if (sameId(id, activeMiddleId.value)) return
  activeMiddleId.value = id
  activeSmallId.value = ALL_SMALL_ID
  loadProducts(true)
}

function selectSmall(id) {
  if (sameId(id, activeSmallId.value)) return
  activeSmallId.value = id
  loadProducts(true)
}

async function loadProducts(reset = true) {
  if (!reset && (!hasMore.value || loading.value)) return
  if (reset) {
    page.value = 1
    hasMore.value = true
  }
  const seq = requestSeq.value + 1
  requestSeq.value = seq
  loading.value = true
  try {
    const data = await productService.list({
      category_id: activeCategoryId.value,
      ordering: ordering.value,
      only_stock: onlyStock.value ? 1 : '',
      page: page.value,
    })
    if (seq !== requestSeq.value) return
    const next = data.results || data || []
    products.value = reset ? next : products.value.concat(next)
    hasMore.value = Boolean(data.next)
    page.value += 1
  } finally {
    if (seq === requestSeq.value) loading.value = false
  }
}

function setOrdering(value) {
  ordering.value = value
  loadProducts(true)
}

function setPriceOrdering() {
  ordering.value = ordering.value === 'price_asc' ? 'price_desc' : 'price_asc'
  loadProducts(true)
}

function toggleStock() {
  onlyStock.value = !onlyStock.value
  loadProducts(true)
}

async function addProduct(product) {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  try {
    await cart.addProduct(product, Number((product.rules && product.rules.min_order_qty) || 1))
    uni.showToast({ title: '已加入购物车', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '加入失败', icon: 'none' })
  }
}

function openProduct(product) {
  uni.navigateTo({ url: productDetailUrl(product) })
}

function productDetailUrl(product) {
  const params = [`id=${product.id}`]
  if (product.config_id) params.push(`config_id=${product.config_id}`)
  return `/pages/product-detail/product-detail?${params.join('&')}`
}

function goSearch() {
  const params = []
  if (activeCategoryId.value) params.push(`category_id=${activeCategoryId.value}`)
  if (ordering.value !== 'sort') params.push(`ordering=${ordering.value}`)
  if (onlyStock.value) params.push('only_stock=1')
  uni.navigateTo({ url: `/pages/product-list/product-list${params.length ? `?${params.join('&')}` : ''}` })
}

function goCart() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.switchTab({ url: '/pages/cart/cart' })
}

onLoad((query = {}) => {
  const pendingCategoryId = uni.getStorageSync('sale_mini_pending_category_id')
  if (pendingCategoryId) uni.removeStorageSync('sale_mini_pending_category_id')
  loadCategories(query.category_id || pendingCategoryId || '').catch((err) => {
    uni.showToast({ title: err.message || '分类加载失败', icon: 'none' })
  })
})

onShow(() => {
  const pendingCategoryId = uni.getStorageSync('sale_mini_pending_category_id')
  if (pendingCategoryId && categories.value.length) {
    uni.removeStorageSync('sale_mini_pending_category_id')
    applyCategorySelection(pendingCategoryId)
  }
  if (getToken()) cart.load().catch(() => {})
})
</script>

<style scoped>
.category-page {
  height: 100vh;
  overflow: hidden;
  background: #f4f6f8;
}

.category-head {
  height: 230rpx;
  background: #fff;
  border-bottom: 1rpx solid #e2e8f0;
}

.search {
  height: 68rpx;
  margin: 12rpx 18rpx 8rpx;
  padding: 0 26rpx;
  display: flex;
  align-items: center;
  border-radius: 36rpx;
  background: #f1f3f5;
  color: #94a3b8;
  font-size: 25rpx;
}

.major-bar {
  height: 142rpx;
  display: flex;
  border-top: 1rpx solid #f1f5f9;
}

.major-scroll {
  flex: 1;
  width: 0;
  white-space: nowrap;
}

.major-row {
  height: 142rpx;
  padding: 8rpx 4rpx 6rpx 12rpx;
  display: inline-flex;
  align-items: flex-start;
  gap: 6rpx;
}

.major-item {
  width: 116rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  color: #334155;
  font-size: 22rpx;
}

.major-image {
  width: 78rpx;
  height: 78rpx;
  border: 4rpx solid transparent;
  border-radius: 50%;
  background: #edf8f5;
}

.major-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0f766e;
  font-size: 30rpx;
  font-weight: 900;
}

.major-name {
  width: 116rpx;
  margin-top: 5rpx;
  overflow: hidden;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.major-item.active .major-image {
  border-color: #0f766e;
}

.major-item.active .major-name {
  color: #0f766e;
  font-weight: 800;
}

.all-major {
  width: 86rpx;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-left: 1rpx solid #e2e8f0;
  background: #fff;
  color: #334155;
  font-size: 24rpx;
}

.all-major.active {
  color: #0f766e;
  font-weight: 800;
}

.menu-icon {
  margin-top: 6rpx;
  font-size: 23rpx;
}

.category-body {
  height: calc(100vh - 230rpx);
  display: flex;
}

.middle-nav {
  width: 174rpx;
  height: 100%;
  flex-shrink: 0;
  background: #f1f3f5;
  border-right: 1rpx solid #e2e8f0;
}

.middle-item {
  min-height: 86rpx;
  padding: 20rpx 12rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-left: 6rpx solid transparent;
  color: #334155;
  font-size: 23rpx;
  line-height: 1.35;
  text-align: center;
}

.middle-item.active {
  border-left-color: #0f766e;
  background: #fff;
  color: #0f766e;
  font-weight: 800;
}

.product-panel {
  flex: 1;
  width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.small-scroll {
  height: 78rpx;
  flex-shrink: 0;
  white-space: nowrap;
  border-bottom: 1rpx solid #e2e8f0;
}

.small-row {
  height: 78rpx;
  padding: 11rpx 14rpx;
  display: inline-flex;
  align-items: center;
  gap: 12rpx;
}

.small-chip {
  height: 54rpx;
  line-height: 54rpx;
  padding: 0 22rpx;
  border-radius: 28rpx;
  background: #f1f5f9;
  color: #475569;
  font-size: 23rpx;
}

.small-chip.active {
  background: #e6f7f3;
  color: #0f766e;
  font-weight: 800;
}

.sort-row {
  height: 72rpx;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  border-bottom: 1rpx solid #e2e8f0;
  background: #fff;
}

.sort-item {
  flex: 1;
  text-align: center;
  color: #334155;
  font-size: 23rpx;
}

.sort-item.active {
  color: #0f766e;
  font-weight: 800;
}

.product-scroll {
  flex: 1;
  height: 0;
}

.product-list {
  padding: 14rpx 12rpx 300rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  background: #f8fafc;
}

.load-more {
  padding: 20rpx 0;
  text-align: center;
  color: #64748b;
  font-size: 22rpx;
}
</style>
