<template>
  <view class="category-page">
    <view class="side">
      <view
        v-for="item in categories"
        :key="item.id"
        :class="['side-item', String(item.id) === String(activeId) && 'active']"
        @click="selectCategory(item.id)"
      >
        {{ item.name }}
      </view>
    </view>
    <scroll-view class="content" scroll-y @scrolltolower="loadProducts(false)">
      <view class="content-inner">
        <view class="search" @click="goSearch">搜索当前分类商品</view>
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
    <CartBar :count="cart.items.length" :amount="cart.totalAmount" @checkout="goCart" />
  </view>
</template>

<script setup>
import { onLoad, onShow } from '@dcloudio/uni-app'
import { ref } from 'vue'
import CartBar from '../../components/CartBar.vue'
import EmptyState from '../../components/EmptyState.vue'
import ProductCard from '../../components/ProductCard.vue'
import { productService } from '../../services/product'
import { useCartStore } from '../../stores/cart'
import { getToken } from '../../utils/request'

const cart = useCartStore()
const categories = ref([])
const products = ref([])
const activeId = ref('')
const page = ref(1)
const hasMore = ref(true)
const loading = ref(false)
const requestSeq = ref(0)
const ALL_CATEGORY_ID = 'all'

async function loadCategories(initialId = '') {
  const rows = await productService.categories()
  categories.value = [{ id: ALL_CATEGORY_ID, name: '全部' }, ...(rows || [])]
  const hasInitial = categories.value.some((item) => String(item.id) === String(initialId))
  activeId.value = hasInitial ? initialId : ALL_CATEGORY_ID
  loadProducts(true)
}

async function loadProducts(reset = true) {
  if (!activeId.value) return
  if (!reset && !hasMore.value) return
  if (!reset && loading.value) return
  if (reset) {
    page.value = 1
    hasMore.value = true
  }
  const seq = requestSeq.value + 1
  requestSeq.value = seq
  loading.value = true
  try {
    const data = await productService.list({
      category_id: activeId.value === ALL_CATEGORY_ID ? '' : activeId.value,
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

function selectCategory(id) {
  activeId.value = id
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
  if (activeId.value !== ALL_CATEGORY_ID) params.push(`category_id=${activeId.value}`)
  const suffix = params.length ? `?${params.join('&')}` : ''
  uni.navigateTo({ url: `/pages/product-list/product-list${suffix}` })
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
  loadCategories(query.category_id || pendingCategoryId || '')
})

onShow(() => {
  const pendingCategoryId = uni.getStorageSync('sale_mini_pending_category_id')
  if (pendingCategoryId) {
    uni.removeStorageSync('sale_mini_pending_category_id')
    if (String(pendingCategoryId) !== String(activeId.value)) {
      activeId.value = pendingCategoryId
      loadProducts(true)
    }
  }
  if (getToken()) cart.load().catch(() => {})
})
</script>

<style scoped>
.category-page {
  min-height: 100vh;
  display: flex;
  background: #f4f6f8;
  padding-bottom: 268rpx;
}

.side {
  width: 184rpx;
  min-height: 100vh;
  background: #fff;
  border-right: 1rpx solid #dfe6ef;
}

.side-item {
  min-height: 86rpx;
  padding: 20rpx 12rpx;
  display: flex;
  align-items: center;
  color: #475569;
  font-size: 25rpx;
  border-left: 6rpx solid transparent;
}

.side-item.active {
  color: #0f766e;
  background: #edf8f5;
  border-left-color: #0f766e;
  font-weight: 750;
}

.content {
  flex: 1;
  height: 100vh;
}

.content-inner {
  padding: 18rpx 18rpx 280rpx;
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.search {
  height: 72rpx;
  display: flex;
  align-items: center;
  padding: 0 22rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #64748b;
  font-size: 24rpx;
}

.load-more {
  padding: 20rpx 0;
  text-align: center;
  color: #64748b;
  font-size: 24rpx;
}
</style>
