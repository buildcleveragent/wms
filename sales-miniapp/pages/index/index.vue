<template>
  <view class="index-page">
    <view class="search-wrap">
      <view class="search-row">
        <input
          class="search-input"
          v-model="searchKeyword"
          confirm-type="search"
          placeholder="搜索商品、品牌、关键词"
          @confirm="doSearch"
        />
        <button class="search-button" @click="doSearch">搜索</button>
      </view>
    </view>

    <view class="category-panel">
      <view class="category-grid">
        <view
          v-for="item in home.categories"
          :key="item.id"
          class="category-item"
          @click="goCategory(item.id)"
        >
          <image
            v-if="item.image_url"
            class="category-image"
            :src="item.image_url"
            mode="aspectFill"
          />
          <view v-else class="category-image category-placeholder">
            {{ item.name.slice(0, 1) }}
          </view>
          <view class="category-name">{{ item.name }}</view>
        </view>
      </view>
    </view>

    <swiper v-if="home.banners.length" class="banner" indicator-dots autoplay circular>
      <swiper-item v-for="banner in home.banners" :key="banner.id">
        <image
          class="banner-image"
          :src="banner.image_url"
          mode="aspectFill"
          @click="openBanner(banner)"
        />
      </swiper-item>
    </swiper>
    <view v-else class="banner banner-fallback">
      <view class="fallback-title">每日精选</view>
      <view class="fallback-subtitle">品质好货，安心选购</view>
    </view>

    <view class="feed-tabs">
      <view
        v-for="tab in feedTabs"
        :key="tab.key"
        :class="['feed-tab', activeFeed === tab.key && 'active']"
        @click="selectFeed(tab.key)"
      >
        {{ tab.label }}
      </view>
    </view>

    <view v-if="products.length" class="product-grid">
      <ProductCard
        v-for="product in products"
        :key="`${activeFeed}-${product.config_id || product.id}`"
        :product="product"
        variant="grid"
        @open="openProduct"
        @add="addProduct"
      />
    </view>
    <EmptyState v-else :text="productLoading ? '商品加载中' : emptyFeedText" />

    <view v-if="products.length" class="load-more">
      {{ productLoading ? '加载中' : hasMore ? '继续上拉加载' : '已经到底了' }}
    </view>
  </view>
</template>

<script setup>
import { onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import ProductCard from '../../components/ProductCard.vue'
import { productService } from '../../services/product'
import { useCartStore } from '../../stores/cart'
import { useSessionStore } from '../../stores/session'
import { getToken } from '../../utils/request'

const cart = useCartStore()
const session = useSessionStore()
const searchKeyword = ref('')
const homeLoading = ref(false)
const productLoading = ref(false)
const initialized = ref(false)
const products = ref([])
const activeFeed = ref('hot')
const page = ref(1)
const hasMore = ref(true)
const productRequestSeq = ref(0)
const feedTabs = [
  { key: 'hot', label: '热卖' },
  { key: 'new', label: '新品' },
  { key: 'recommended', label: '推荐' },
]
const home = reactive({
  banners: [],
  categories: [],
})

const activeFeedLabel = computed(() => {
  const tab = feedTabs.find((item) => item.key === activeFeed.value)
  return tab ? tab.label : '商品'
})
const emptyFeedText = computed(() => `暂无${activeFeedLabel.value}商品`)

async function loadSessionContext() {
  if (!getToken()) return
  if (!session.profile) {
    try {
      await session.fetchProfile()
    } catch (err) {
      if (!err || err.statusCode !== 401) throw err
      return
    }
  }
  if (session.profile && session.profile.customer) await cart.load()
}

async function loadHome() {
  if (homeLoading.value) return
  homeLoading.value = true
  try {
    const data = await productService.home()
    home.banners = data.banners || []
    home.categories = data.categories || []
  } finally {
    homeLoading.value = false
  }
}

async function loadProducts(reset = true) {
  if (!reset && (productLoading.value || !hasMore.value)) return
  if (reset) {
    page.value = 1
    hasMore.value = true
  }
  const requestSeq = productRequestSeq.value + 1
  productRequestSeq.value = requestSeq
  const requestedTag = activeFeed.value
  const requestedPage = page.value
  productLoading.value = true
  try {
    const data = await productService.list({
      tag: requestedTag,
      ordering: requestedTag === 'hot' ? 'hot' : 'sort',
      page: requestedPage,
      page_size: 20,
    })
    if (requestSeq !== productRequestSeq.value || requestedTag !== activeFeed.value) return
    const next = data.results || data || []
    products.value = reset ? next : products.value.concat(next)
    hasMore.value = Boolean(data.next)
    page.value = requestedPage + 1
  } finally {
    if (requestSeq === productRequestSeq.value) productLoading.value = false
  }
}

async function refresh() {
  await loadSessionContext()
  await Promise.all([loadHome(), loadProducts(true)])
}

function handleLoadError(err) {
  if (err && err.statusCode === 401) return
  uni.showToast({ title: (err && err.message) || '首页加载失败', icon: 'none' })
}

function selectFeed(tag) {
  if (tag === activeFeed.value) return
  activeFeed.value = tag
  products.value = []
  loadProducts(true).catch(handleLoadError)
}

function doSearch() {
  const keyword = searchKeyword.value.trim()
  const query = keyword ? `?search=${encodeURIComponent(keyword)}` : ''
  uni.navigateTo({ url: `/pages/product-list/product-list${query}` })
}

async function addProduct(product) {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  try {
    const minQty = Number((product.rules && product.rules.min_order_qty) || 1)
    await cart.addProduct(product, minQty)
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

function openBanner(banner) {
  if (!banner) return
  const type = String(banner.link_type || '').trim().toUpperCase()
  const value = String(banner.link_value || '').trim()
  if (!type && !value) return
  if (['PRODUCT', 'GOODS', 'SKU'].includes(type) && value) {
    const params = [`id=${encodeURIComponent(value)}`]
    if (banner.config_id) params.push(`config_id=${banner.config_id}`)
    uni.navigateTo({ url: `/pages/product-detail/product-detail?${params.join('&')}` })
    return
  }
  if (['CATEGORY', 'CAT'].includes(type) && value) {
    goCategory(value)
    return
  }
  if (type === 'SEARCH' && value) {
    uni.navigateTo({ url: `/pages/product-list/product-list?search=${encodeURIComponent(value)}` })
    return
  }
  if (['LIST', 'PRODUCT_LIST'].includes(type)) {
    const suffix = value
      ? (value.includes('=') ? value : `ordering=${encodeURIComponent(value)}`)
      : 'ordering=sort'
    uni.navigateTo({ url: `/pages/product-list/product-list?${suffix}` })
    return
  }
  if (['PAGE', 'URL'].includes(type) && value.startsWith('/pages/')) {
    openInternalPage(value)
  }
}

function openInternalPage(url) {
  const baseUrl = url.split('?')[0]
  const tabPages = [
    '/pages/index/index',
    '/pages/category/category',
    '/pages/cart/cart',
    '/pages/order-list/order-list',
    '/pages/user/user',
  ]
  if (tabPages.includes(baseUrl)) {
    uni.switchTab({ url: baseUrl })
    return
  }
  uni.navigateTo({ url })
}

function goCategory(id) {
  if (id) uni.setStorageSync('sale_mini_pending_category_id', id)
  uni.switchTab({ url: '/pages/category/category' })
}

onShow(() => {
  if (!initialized.value) {
    refresh()
      .then(() => {
        initialized.value = true
      })
      .catch(handleLoadError)
    return
  }
  loadSessionContext().catch(handleLoadError)
})

onReachBottom(() => {
  loadProducts(false).catch(handleLoadError)
})

onPullDownRefresh(async () => {
  try {
    await refresh()
    initialized.value = true
  } catch (err) {
    handleLoadError(err)
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.index-page {
  min-height: 100vh;
  padding: 18rpx 18rpx 140rpx;
  background: #f4f6f8;
}

.search-wrap {
  padding: 10rpx;
  border: 3rpx solid #2563eb;
  border-radius: 8rpx;
  background: #fff;
}

.search-row {
  height: 74rpx;
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.search-input {
  min-width: 0;
  height: 74rpx;
  padding: 0 24rpx;
  flex: 1;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #f8fafc;
  color: #17202a;
  font-size: 26rpx;
}

.search-button {
  width: 132rpx;
  height: 74rpx;
  line-height: 74rpx;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #2563eb;
  color: #fff;
  font-size: 27rpx;
  font-weight: 800;
}

.search-button::after {
  border: 0;
}

.category-panel {
  margin-top: 18rpx;
  padding: 22rpx 10rpx 10rpx;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
  background: #fff;
}

.category-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  row-gap: 22rpx;
}

.category-item {
  min-width: 0;
  text-align: center;
}

.category-image {
  width: 90rpx;
  height: 90rpx;
  margin: 0 auto;
  border-radius: 8rpx;
  background: #eef2f7;
}

.category-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e6f7f3;
  color: #0f766e;
  font-size: 34rpx;
  font-weight: 900;
}

.category-name {
  margin-top: 9rpx;
  padding: 0 4rpx;
  color: #17202a;
  font-size: 22rpx;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.banner {
  width: 100%;
  height: 286rpx;
  margin-top: 18rpx;
  border-radius: 8rpx;
  overflow: hidden;
}

.banner-image {
  width: 100%;
  height: 286rpx;
}

.banner-fallback {
  padding: 34rpx;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  background: #0f766e;
  color: #fff;
}

.fallback-title {
  font-size: 42rpx;
  font-weight: 900;
}

.fallback-subtitle {
  margin-top: 10rpx;
  font-size: 25rpx;
}

.feed-tabs {
  position: sticky;
  top: 0;
  z-index: 5;
  height: 92rpx;
  margin-top: 18rpx;
  padding: 0 24rpx;
  display: flex;
  align-items: stretch;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx 8rpx 0 0;
  background: #fff;
}

.feed-tab {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #475569;
  font-size: 28rpx;
  font-weight: 700;
}

.feed-tab.active {
  color: #0f766e;
}

.feed-tab.active::after {
  content: '';
  position: absolute;
  left: 28%;
  right: 28%;
  bottom: 8rpx;
  height: 6rpx;
  border-radius: 3rpx;
  background: #0f766e;
}

.product-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12rpx;
  padding-top: 12rpx;
}

.load-more {
  padding: 30rpx 0 10rpx;
  color: #64748b;
  font-size: 23rpx;
  text-align: center;
}
</style>
