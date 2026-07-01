<template>
  <view class="page index-page">
    <view class="topbar">
      <view>
        <view class="shop-name">{{ profileName }}</view>
        <view class="shop-sub">{{ brandTagline }}</view>
      </view>
      <button class="icon-btn" @click="goUser">我</button>
    </view>

    <view class="search" @click="goSearch">
      <text>搜索商品、品牌、关键词</text>
    </view>

    <view class="fulfillment">
      <view class="fulfillment-main" @click="goAddress">
        <view class="fulfillment-title">配送到</view>
        <view class="fulfillment-text">{{ deliveryText }}</view>
      </view>
      <view class="fulfillment-meta">
        <text>支持配送</text>
        <text>门店自提</text>
      </view>
    </view>

    <swiper v-if="home.banners.length" class="banner" indicator-dots autoplay circular>
      <swiper-item v-for="banner in home.banners" :key="banner.id">
        <image class="banner-img" :src="banner.image_url" mode="aspectFill" @click="openBanner(banner)" />
      </swiper-item>
    </swiper>
    <view v-else class="banner fallback">
      <view>
        <view class="fallback-title">严选好货</view>
        <view class="fallback-sub">统一精选 · 安心下单</view>
      </view>
    </view>

    <view class="channel-grid">
      <view class="channel" @click="goCategory()">
        <view class="channel-icon">类</view>
        <view class="channel-text">全部分类</view>
      </view>
      <view class="channel" @click="goList('sort')">
        <view class="channel-icon selected">优</view>
        <view class="channel-text">品质优选</view>
      </view>
      <view class="channel" @click="goList('hot')">
        <view class="channel-icon hot">爆</view>
        <view class="channel-text">热卖榜</view>
      </view>
      <view class="channel" @click="goStockedList">
        <view class="channel-icon stock">货</view>
        <view class="channel-text">有货商品</view>
      </view>
      <view class="channel" @click="goBenefits">
        <view class="channel-icon coupon">券</view>
        <view class="channel-text">领券中心</view>
      </view>
      <view class="channel" @click="goBenefits">
        <view class="channel-icon point">积</view>
        <view class="channel-text">会员积分</view>
      </view>
      <view class="channel" @click="goOrders">
        <view class="channel-icon order">单</view>
        <view class="channel-text">我的订单</view>
      </view>
      <view class="channel" @click="goAfterSales">
        <view class="channel-icon service">售</view>
        <view class="channel-text">售后服务</view>
      </view>
    </view>

    <scroll-view v-if="home.categories.length" class="category-scroll" scroll-x>
      <view class="category-row">
        <view v-for="item in home.categories" :key="item.id" class="category" @click="goCategory(item.id)">
          <view class="category-mark">{{ item.name.slice(0, 1) }}</view>
          <view class="category-name">{{ item.name }}</view>
        </view>
      </view>
    </scroll-view>

    <view class="section-head">
      <text>热卖商品</text>
      <text class="more" @click="goList('hot')">更多</text>
    </view>
    <view class="product-list">
      <ProductCard
        v-for="product in hotProducts"
        :key="`hot-${product.id}`"
        :product="product"
        @open="openProduct"
        @add="addProduct"
      />
    </view>

    <view class="section-head">
      <text>为你推荐</text>
      <text class="more" @click="goList('sort')">更多</text>
    </view>
    <view v-if="recommendProducts.length" class="product-list">
      <ProductCard
        v-for="product in recommendProducts"
        :key="`rec-${product.id}`"
        :product="product"
        @open="openProduct"
        @add="addProduct"
      />
    </view>
    <EmptyState v-else text="暂无上架商品" />

    <CartBar :count="cart.items.length" :amount="cart.totalAmount" @checkout="goCart" />
  </view>
</template>

<script setup>
import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'
import CartBar from '../../components/CartBar.vue'
import EmptyState from '../../components/EmptyState.vue'
import ProductCard from '../../components/ProductCard.vue'
import { productService } from '../../services/product'
import { useCartStore } from '../../stores/cart'
import { useSessionStore } from '../../stores/session'
import { getToken } from '../../utils/request'

const cart = useCartStore()
const session = useSessionStore()
const loading = ref(false)
const home = reactive({
  banners: [],
  categories: [],
  recommend_products: [],
  hot_products: [],
  new_products: [],
})

const profile = computed(() => session.profile)
const profileName = computed(() => '博悦商城')
const brandTagline = computed(() => {
  return '品质好货 · 在线选购'
})
const deliveryText = computed(() => {
  if (getToken()) return '请选择收货地址'
  return '登录后管理收货地址'
})
const hotProducts = computed(() => home.hot_products.length ? home.hot_products : home.new_products)
const recommendProducts = computed(() => home.recommend_products.length ? home.recommend_products : home.hot_products)

async function load() {
  if (loading.value) return
  loading.value = true
  try {
    if (getToken() && !session.profile) {
      try {
        await session.fetchProfile()
      } catch (err) {
        if (!err || err.statusCode !== 401) throw err
      }
    }
    if (getToken() && session.profile && session.profile.customer) await cart.load()
    const data = await productService.home()
    Object.assign(home, {
      banners: data.banners || [],
      categories: data.categories || [],
      recommend_products: data.recommend_products || [],
      hot_products: data.hot_products || [],
      new_products: data.new_products || [],
    })
  } finally {
    loading.value = false
  }
}

function handleLoadError(err) {
  if (err && err.statusCode === 401) return
  uni.showToast({ title: (err && err.message) || '首页加载失败', icon: 'none' })
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

function goSearch() {
  uni.navigateTo({ url: '/pages/product-list/product-list' })
}

function goList(ordering) {
  uni.navigateTo({ url: `/pages/product-list/product-list?ordering=${ordering}` })
}

function goStockedList() {
  uni.navigateTo({ url: '/pages/product-list/product-list?only_stock=1' })
}

function requireLoginPage(url) {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.navigateTo({ url })
}

function goBenefits() {
  requireLoginPage('/pages/benefits/benefits')
}

function goAfterSales() {
  requireLoginPage('/pages/after-sales/after-sales')
}

function goAddress() {
  requireLoginPage('/pages/address/address')
}

function goOrders() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.switchTab({ url: '/pages/order-list/order-list' })
}

function goCart() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.switchTab({ url: '/pages/cart/cart' })
}

function goUser() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.switchTab({ url: '/pages/user/user' })
}

onShow(() => {
  load().catch(handleLoadError)
})

onPullDownRefresh(async () => {
  try {
    await load()
  } catch (err) {
    handleLoadError(err)
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.index-page {
  padding-bottom: 268rpx;
}

.topbar {
  height: 92rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.shop-name {
  max-width: 560rpx;
  color: #17202a;
  font-size: 34rpx;
  font-weight: 850;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.shop-sub {
  margin-top: 4rpx;
  color: #64748b;
  font-size: 23rpx;
}

.icon-btn {
  width: 64rpx;
  height: 64rpx;
  line-height: 64rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #17202a;
  padding: 0;
  font-size: 24rpx;
}

.icon-btn::after {
  border: 0;
}

.search {
  height: 78rpx;
  padding: 0 24rpx;
  display: flex;
  align-items: center;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #64748b;
  font-size: 26rpx;
}

.fulfillment {
  margin-top: 14rpx;
  padding: 18rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.fulfillment-main {
  min-width: 0;
  flex: 1;
}

.fulfillment-title {
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 800;
}

.fulfillment-text {
  margin-top: 6rpx;
  color: #17202a;
  font-size: 27rpx;
  font-weight: 760;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fulfillment-meta {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  color: #64748b;
  font-size: 22rpx;
  text-align: right;
  flex-shrink: 0;
}

.banner {
  height: 240rpx;
  margin-top: 18rpx;
  border-radius: 8rpx;
  overflow: hidden;
}

.banner-img {
  width: 100%;
  height: 240rpx;
}

.fallback {
  padding: 32rpx;
  display: flex;
  align-items: flex-end;
  background: linear-gradient(135deg, #0f766e 0%, #2563eb 100%);
  color: #fff;
}

.fallback-title {
  font-size: 42rpx;
  font-weight: 900;
}

.fallback-sub {
  margin-top: 10rpx;
  font-size: 25rpx;
  opacity: 0.9;
}

.channel-grid {
  margin-top: 18rpx;
  padding: 18rpx 12rpx;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10rpx;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
  background: #fff;
}

.channel {
  min-width: 0;
  text-align: center;
}

.channel-icon {
  width: 52rpx;
  height: 52rpx;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: #edf8f5;
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 900;
}

.channel-icon.hot {
  background: #fff1f2;
  color: #b42318;
}

.channel-icon.selected {
  background: #ecfeff;
  color: #0e7490;
}

.channel-icon.stock {
  background: #f0fdf4;
  color: #15803d;
}

.channel-icon.coupon {
  background: #fff7ed;
  color: #b45309;
}

.channel-icon.point {
  background: #eff6ff;
  color: #2563eb;
}

.channel-icon.order {
  background: #f5f3ff;
  color: #6d28d9;
}

.channel-icon.service {
  background: #f8fafc;
  color: #334155;
}

.channel-text {
  margin-top: 8rpx;
  color: #334155;
  font-size: 21rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-scroll {
  margin-top: 18rpx;
  white-space: nowrap;
}

.category-row {
  display: flex;
  gap: 14rpx;
}

.category {
  width: 128rpx;
  padding: 14rpx 8rpx;
  background: #fff;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
  text-align: center;
  flex-shrink: 0;
}

.category-mark {
  width: 54rpx;
  height: 54rpx;
  margin: 0 auto;
  line-height: 54rpx;
  border-radius: 8rpx;
  background: #edf7ff;
  color: #2563eb;
  font-weight: 850;
}

.category-name {
  margin-top: 8rpx;
  color: #334155;
  font-size: 23rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-head {
  margin: 26rpx 0 14rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #17202a;
  font-size: 31rpx;
  font-weight: 800;
}

.more {
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 600;
}

.product-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}
</style>
