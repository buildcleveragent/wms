<template>
  <view class="page list-page">
    <view class="search-row">
      <input class="input search-input" v-model="search" placeholder="商品、品牌、关键词" confirm-type="search" @confirm="doSearch" />
      <button v-if="search" class="clear-btn" @click="clearSearch">清</button>
      <button class="search-btn" @click="doSearch">搜</button>
    </view>

    <view v-if="!search && (searchHistory.length || hotKeywords.length)" class="search-panel">
      <view v-if="searchHistory.length" class="search-section">
        <view class="panel-head">
          <text>搜索历史</text>
          <text class="panel-action" @click="clearHistory">清空</text>
        </view>
        <view class="chip-row">
          <view v-for="word in searchHistory" :key="`history-${word}`" class="search-chip" @click="applyKeyword(word)">
            {{ word }}
          </view>
        </view>
      </view>
      <view class="search-section">
        <view class="panel-head">
          <text>热门搜索</text>
        </view>
        <view class="chip-row">
          <view v-for="word in hotKeywords" :key="`hot-${word}`" class="search-chip hot" @click="applyKeyword(word)">
            {{ word }}
          </view>
        </view>
      </view>
    </view>

    <view class="filters">
      <button :class="['filter', ordering === 'sort' && 'active']" @click="setOrdering('sort')">综合</button>
      <button :class="['filter', ordering === 'hot' && 'active']" @click="setOrdering('hot')">热卖</button>
      <button :class="['filter', isPriceOrdering && 'active']" @click="setPriceOrdering">{{ priceLabel }}</button>
      <button :class="['filter', onlyStock && 'active']" @click="toggleStock">有货</button>
    </view>

    <scroll-view v-if="brands.length" class="brand-scroll" scroll-x>
      <view class="brand-row">
        <view
          v-for="brand in brands"
          :key="brand.id || 'all-brand'"
          :class="['brand-chip', String(brandId) === String(brand.id) && 'active']"
          @click="selectBrand(brand.id)"
        >
          <text>{{ brand.name }}</text>
          <text class="brand-count">{{ brand.product_count }} 件</text>
        </view>
      </view>
    </scroll-view>

    <view v-if="rows.length" class="products">
      <ProductCard
        v-for="product in rows"
        :key="product.id"
        :product="product"
        @open="openProduct"
        @add="addProduct"
      />
      <view class="load-more">{{ loading ? '加载中' : hasMore ? '继续上拉' : '没有更多了' }}</view>
    </view>
    <EmptyState v-else :text="loading ? '加载中' : '没有找到商品'" />

    <CartBar :count="cart.items.length" :amount="cart.totalAmount" @checkout="goCart" />
  </view>
</template>

<script setup>
import { onLoad, onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import CartBar from '../../components/CartBar.vue'
import EmptyState from '../../components/EmptyState.vue'
import ProductCard from '../../components/ProductCard.vue'
import { productService } from '../../services/product'
import { useCartStore } from '../../stores/cart'
import { getToken } from '../../utils/request'

const cart = useCartStore()
const rows = ref([])
const search = ref('')
const categoryId = ref('')
const brandId = ref('')
const ordering = ref('sort')
const onlyStock = ref(false)
const brands = ref([])
const searchHistory = ref([])
const page = ref(1)
const hasMore = ref(true)
const loading = ref(false)
const SEARCH_HISTORY_KEY = 'sale_mini_search_history'
const hotKeywords = ['生鲜', '粮油', '饮品', '纸品', '日用', '热卖']
const isPriceOrdering = computed(() => ordering.value === 'price_asc' || ordering.value === 'price_desc')
const priceLabel = computed(() => (ordering.value === 'price_desc' ? '价格↓' : ordering.value === 'price_asc' ? '价格↑' : '价格'))

async function load(reset = true) {
  if (loading.value) return
  if (!reset && !hasMore.value) return
  if (reset) {
    page.value = 1
    hasMore.value = true
  }
  loading.value = true
  try {
    const data = await productService.list({
      search: search.value,
      category_id: categoryId.value,
      brand_id: brandId.value,
      ordering: ordering.value,
      only_stock: onlyStock.value ? 1 : '',
      page: page.value,
    })
    const next = data.results || data || []
    rows.value = reset ? next : rows.value.concat(next)
    hasMore.value = Boolean(data.next)
    page.value += 1
  } finally {
    loading.value = false
  }
}

async function loadBrands() {
  const rows = await productService.brands({
    category_id: categoryId.value,
  })
  brands.value = [
    { id: '', name: '全部品牌', product_count: rows.reduce((sum, item) => sum + Number(item.product_count || 0), 0) },
    ...rows,
  ]
  if (brandId.value && !rows.some((item) => String(item.id) === String(brandId.value))) {
    brandId.value = ''
  }
}

function setOrdering(value) {
  ordering.value = value
  load(true)
}

function setPriceOrdering() {
  ordering.value = ordering.value === 'price_asc' ? 'price_desc' : 'price_asc'
  load(true)
}

function loadSearchHistory() {
  const rows = uni.getStorageSync(SEARCH_HISTORY_KEY)
  searchHistory.value = Array.isArray(rows) ? rows.filter(Boolean).slice(0, 10) : []
}

function saveSearchKeyword() {
  const word = search.value.trim()
  if (!word) return
  searchHistory.value = [word, ...searchHistory.value.filter((item) => item !== word)].slice(0, 10)
  uni.setStorageSync(SEARCH_HISTORY_KEY, searchHistory.value)
}

function doSearch() {
  search.value = search.value.trim()
  saveSearchKeyword()
  load(true)
}

function applyKeyword(word) {
  search.value = word
  doSearch()
}

function clearSearch() {
  search.value = ''
  load(true)
}

function clearHistory() {
  searchHistory.value = []
  uni.removeStorageSync(SEARCH_HISTORY_KEY)
}

function selectBrand(id) {
  brandId.value = id || ''
  load(true)
}

function toggleStock() {
  onlyStock.value = !onlyStock.value
  load(true)
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

function goCart() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.switchTab({ url: '/pages/cart/cart' })
}

async function init(query = {}) {
  loadSearchHistory()
  search.value = query.search || ''
  categoryId.value = query.category_id || ''
  brandId.value = query.brand_id || ''
  ordering.value = query.ordering || 'sort'
  onlyStock.value = query.only_stock === '1' || query.only_stock === 'true'
  await loadBrands().catch(() => {
    brands.value = []
  })
  if (search.value) saveSearchKeyword()
  load(true)
}

onLoad((query = {}) => {
  init(query)
})

onReachBottom(() => load(false))

onShow(() => {
  if (getToken()) cart.load().catch(() => {})
})

onPullDownRefresh(async () => {
  try {
    await load(true)
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.list-page {
  padding-bottom: 268rpx;
}

.search-row {
  display: flex;
  gap: 12rpx;
}

.search-input {
  flex: 1;
}

.clear-btn,
.search-btn {
  width: 88rpx;
  height: 80rpx;
  line-height: 80rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 26rpx;
}

.clear-btn {
  border: 1rpx solid #d7dde8;
  background: #fff;
  color: #64748b;
}

.clear-btn::after,
.search-btn::after {
  border: 0;
}

.search-panel {
  margin-top: 16rpx;
  padding: 18rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.search-section + .search-section {
  margin-top: 18rpx;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #17202a;
  font-size: 26rpx;
  font-weight: 800;
}

.panel-action {
  color: #64748b;
  font-size: 23rpx;
  font-weight: 400;
}

.chip-row {
  margin-top: 12rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 10rpx;
}

.search-chip {
  height: 52rpx;
  padding: 0 18rpx;
  display: flex;
  align-items: center;
  border-radius: 8rpx;
  background: #f8fafc;
  color: #334155;
  font-size: 23rpx;
}

.search-chip.hot {
  background: #edf8f5;
  color: #0f766e;
}

.filters {
  margin: 16rpx 0;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10rpx;
}

.brand-scroll {
  margin: 0 0 16rpx;
  white-space: nowrap;
}

.brand-row {
  display: flex;
  gap: 10rpx;
}

.brand-chip {
  min-width: 132rpx;
  height: 62rpx;
  padding: 0 16rpx;
  display: flex;
  align-items: center;
  gap: 8rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #334155;
  font-size: 23rpx;
  flex-shrink: 0;
}

.brand-chip.active {
  border-color: #0f766e;
  background: #edf8f5;
  color: #0f766e;
  font-weight: 750;
}

.brand-count {
  color: #64748b;
  font-size: 20rpx;
}

.filter {
  height: 62rpx;
  line-height: 62rpx;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 24rpx;
}

.filter::after {
  border: 0;
}

.filter.active {
  border-color: #0f766e;
  color: #0f766e;
  background: #edf8f5;
}

.products {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.load-more {
  padding: 22rpx 0 8rpx;
  text-align: center;
  color: #64748b;
  font-size: 24rpx;
}
</style>
