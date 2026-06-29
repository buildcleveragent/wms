<template>
  <view class="page merchant-detail-page">
    <view v-if="merchant" class="merchant-hero">
      <view class="avatar">{{ merchant.name.slice(0, 1) }}</view>
      <view class="hero-main">
        <view class="merchant-name">{{ merchant.name }}</view>
        <view class="merchant-meta">{{ merchant.product_count }} 件在售 · {{ merchant.hot_count || 0 }} 件热卖</view>
        <view class="tag-row">
          <text class="tag">配送</text>
          <text class="tag">客户自提</text>
          <text v-if="Number(merchant.recommended_count || 0) > 0" class="tag accent">推荐商家</text>
        </view>
      </view>
    </view>
    <view v-else class="merchant-hero skeleton">
      <view class="avatar">商</view>
      <view class="hero-main">
        <view class="merchant-name">商家店铺</view>
        <view class="merchant-meta">加载中</view>
      </view>
    </view>

    <view class="action-row">
      <button class="action-btn primary" @click="goAllProducts">全部商品</button>
      <button class="action-btn" @click="goBenefits">领券/积分</button>
    </view>

    <scroll-view v-if="categories.length" class="category-scroll" scroll-x>
      <view class="category-row">
        <view
          v-for="item in categories"
          :key="item.id"
          :class="['category-chip', String(categoryId) === String(item.id) && 'active']"
          @click="selectCategory(item.id)"
        >
          {{ item.name }}
        </view>
      </view>
    </scroll-view>

    <view class="section-head">
      <text>{{ categoryTitle }}</text>
      <text class="more" @click="goAllProducts">更多</text>
    </view>

    <view v-if="products.length" class="product-list">
      <ProductCard
        v-for="product in products"
        :key="product.config_id || product.id"
        :product="product"
        @open="openProduct"
        @add="addProduct"
      />
      <view class="load-more">{{ loading ? '加载中' : hasMore ? '继续上拉' : '没有更多了' }}</view>
    </view>
    <EmptyState v-else :text="loading ? '加载中' : '该商家暂无商品'" />

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
const ownerId = ref('')
const merchant = ref(null)
const categories = ref([])
const categoryId = ref('all')
const products = ref([])
const page = ref(1)
const hasMore = ref(true)
const loading = ref(false)
const ALL_CATEGORY_ID = 'all'

const categoryTitle = computed(() => {
  const current = categories.value.find((item) => String(item.id) === String(categoryId.value))
  return current && current.id !== ALL_CATEGORY_ID ? current.name : '店铺热卖'
})

async function loadMerchant() {
  const rows = await productService.merchants()
  merchant.value = rows.find((item) => String(item.id) === String(ownerId.value)) || null
}

async function loadCategories() {
  const rows = await productService.categories({ owner_id: ownerId.value })
  categories.value = [{ id: ALL_CATEGORY_ID, name: '全部' }, ...(rows || [])]
  if (!categories.value.some((item) => String(item.id) === String(categoryId.value))) {
    categoryId.value = ALL_CATEGORY_ID
  }
}

async function loadProducts(reset = true) {
  if (!ownerId.value) return
  if (loading.value) return
  if (!reset && !hasMore.value) return
  if (reset) {
    page.value = 1
    hasMore.value = true
  }
  loading.value = true
  try {
    const data = await productService.list({
      owner_id: ownerId.value,
      category_id: categoryId.value === ALL_CATEGORY_ID ? '' : categoryId.value,
      ordering: categoryId.value === ALL_CATEGORY_ID ? 'hot' : 'sort',
      page: page.value,
    })
    const next = data.results || data || []
    products.value = reset ? next : products.value.concat(next)
    hasMore.value = Boolean(data.next)
    page.value += 1
  } finally {
    loading.value = false
  }
}

async function loadAll(resetProducts = true) {
  if (!ownerId.value) return
  await Promise.all([loadMerchant(), loadCategories()])
  await loadProducts(resetProducts)
}

function selectCategory(id) {
  categoryId.value = id || ALL_CATEGORY_ID
  loadProducts(true).catch((err) => {
    uni.showToast({ title: err.message || '商品加载失败', icon: 'none' })
  })
}

function productDetailUrl(product) {
  const params = [`id=${product.id}`]
  if (product.owner_id) params.push(`owner_id=${product.owner_id}`)
  if (product.config_id) params.push(`config_id=${product.config_id}`)
  return `/pages/product-detail/product-detail?${params.join('&')}`
}

function openProduct(product) {
  uni.navigateTo({ url: productDetailUrl(product) })
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

function goAllProducts() {
  if (!ownerId.value) return
  uni.navigateTo({ url: `/pages/product-list/product-list?owner_id=${ownerId.value}` })
}

function goBenefits() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.navigateTo({ url: `/pages/benefits/benefits?owner_id=${ownerId.value}` })
}

function goCart() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  uni.switchTab({ url: '/pages/cart/cart' })
}

onLoad((query = {}) => {
  ownerId.value = query.owner_id || ''
  categoryId.value = query.category_id || ALL_CATEGORY_ID
  loadAll(true).catch((err) => {
    uni.showToast({ title: err.message || '店铺加载失败', icon: 'none' })
  })
})

onShow(() => {
  if (getToken()) cart.load({ owner_id: ownerId.value }).catch(() => {})
})

onReachBottom(() => loadProducts(false))

onPullDownRefresh(async () => {
  try {
    await loadAll(true)
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.merchant-detail-page {
  padding-bottom: 268rpx;
}

.merchant-hero {
  min-height: 188rpx;
  padding: 24rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
  border-radius: 8rpx;
  background: linear-gradient(135deg, #0f766e, #2563eb);
  color: #fff;
}

.merchant-hero.skeleton {
  background: #334155;
}

.avatar {
  width: 96rpx;
  height: 96rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: rgba(255, 255, 255, 0.18);
  font-size: 40rpx;
  font-weight: 900;
  flex-shrink: 0;
}

.hero-main {
  min-width: 0;
  flex: 1;
}

.merchant-name {
  font-size: 36rpx;
  font-weight: 900;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.merchant-meta {
  margin-top: 8rpx;
  font-size: 24rpx;
  opacity: 0.86;
}

.tag-row {
  margin-top: 14rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx;
}

.tag {
  height: 40rpx;
  padding: 0 12rpx;
  display: flex;
  align-items: center;
  border-radius: 8rpx;
  background: rgba(255, 255, 255, 0.18);
  font-size: 21rpx;
}

.tag.accent {
  background: rgba(250, 204, 21, 0.22);
}

.action-row {
  margin: 18rpx 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12rpx;
}

.action-btn {
  height: 72rpx;
  line-height: 72rpx;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #334155;
  font-size: 26rpx;
  font-weight: 800;
}

.action-btn.primary {
  border-color: #0f766e;
  background: #0f766e;
  color: #fff;
}

.action-btn::after {
  border: 0;
}

.category-scroll {
  margin-bottom: 18rpx;
  white-space: nowrap;
}

.category-row {
  display: flex;
  gap: 10rpx;
}

.category-chip {
  height: 60rpx;
  padding: 0 20rpx;
  display: flex;
  align-items: center;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 24rpx;
  flex-shrink: 0;
}

.category-chip.active {
  border-color: #0f766e;
  background: #edf8f5;
  color: #0f766e;
  font-weight: 800;
}

.section-head {
  margin: 18rpx 0 14rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #17202a;
  font-size: 30rpx;
  font-weight: 900;
}

.more {
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 750;
}

.product-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.load-more {
  padding: 20rpx 0;
  color: #64748b;
  font-size: 24rpx;
  text-align: center;
}
</style>
