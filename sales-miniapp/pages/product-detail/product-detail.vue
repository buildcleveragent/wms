<template>
  <view class="detail-page">
    <image v-if="product.image_url" class="hero-img" :src="product.image_url" mode="aspectFill" />
    <view v-else class="hero-img placeholder">货</view>

    <view class="info">
      <view class="title-row">
        <view class="name">{{ product.name }}</view>
        <button class="favorite" @click="toggleFavorite">{{ favoriteText }}</button>
      </view>
      <view v-if="productSubtitle" class="meta">{{ productSubtitle }}</view>
      <view class="price-row">
        <PriceText :value="product.price" />
        <text v-if="product.market_price" class="market">¥{{ money(product.market_price) }}</text>
      </view>

      <view class="cells">
        <view class="cell">
          <text>库存</text>
          <text>{{ productStockLabel }}</text>
        </view>
        <view class="cell" @click="openUomPicker">
          <text>单位</text>
          <picker v-if="hasUomOptions" :range="product.uom_options" range-key="name" :value="uomIndex" @change="changeUom">
            <view>{{ product.order_uom }}</view>
          </picker>
          <text v-else>{{ product.order_uom }}</text>
        </view>
        <view class="cell">
          <text>购买</text>
          <text>起购 {{ rules.min_order_qty }}，按 {{ rules.multiple_qty }} 递增</text>
        </view>
      </view>

      <view class="desc" v-if="product.description">
        <view class="section-title">商品详情</view>
        <view class="desc-text">{{ product.description }}</view>
      </view>
      <view class="desc">
        <view class="section-title">规格与服务</view>
        <view class="desc-text">{{ serviceText }}</view>
      </view>

      <view v-if="relatedProducts.length" class="recommend">
        <view class="section-head">
          <text>相关推荐</text>
          <text class="more" @click="goRelatedList">查看更多</text>
        </view>
        <view class="recommend-list">
          <ProductCard
            v-for="item in relatedProducts"
            :key="`related-${item.id}`"
            :product="item"
            @open="openRelatedProduct"
            @add="addRelatedProduct"
          />
        </view>
      </view>
    </view>

    <view class="bottom">
      <QuantityStepper v-model="qty" :min="Number(rules.min_order_qty || 1)" :step="Number(rules.multiple_qty || 1)" />
      <button class="ghost" :disabled="actionDisabled" @click="addToCart">加入购物车</button>
      <button class="buy" :disabled="actionDisabled" @click="buyNow">立即购买</button>
    </view>
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import PriceText from '../../components/PriceText.vue'
import ProductCard from '../../components/ProductCard.vue'
import QuantityStepper from '../../components/QuantityStepper.vue'
import { productService } from '../../services/product'
import { useBrowseStore } from '../../stores/browse'
import { useCartStore } from '../../stores/cart'
import { money } from '../../utils/money'
import { getToken } from '../../utils/request'

const browse = useBrowseStore()
const cart = useCartStore()
const product = ref({})
const relatedProducts = ref([])
const qty = ref(1)
const uomIndex = ref(0)

const rules = computed(() => product.value.rules || {})
const productSubtitle = computed(() => {
  const current = product.value || {}
  const parts = [current.spec, current.brand_name || current.brand].filter(Boolean)
  const uom = current.order_uom || current.base_uom
  if (!parts.length && uom) parts.push(uom)
  return parts.join(' · ')
})
const productStockLabel = computed(() => {
  const stock = product.value.stock || {}
  return stock.display || stock.text || '-'
})
const serviceText = computed(() => {
  const parts = [
    product.value.pack_requirement,
    product.value.pack_note,
  ].filter(Boolean)
  return parts.length ? parts.join(' ') : '支持配送或自提，具体以结算页为准。'
})
const hasUomOptions = computed(() => Boolean(product.value.uom_options && product.value.uom_options.length))
const favoriteText = computed(() => (browse.isFavorite(product.value) ? '已收藏' : '收藏'))
const isOutOfStock = computed(() => product.value.stock && product.value.stock.status === 'OUT')
const actionDisabled = computed(() => isOutOfStock.value)

async function load(id, params = {}) {
  const data = await productService.detail(id, params)
  product.value = normalize(data)
  browse.addRecent(product.value)
  qty.value = Number((product.value.rules && product.value.rules.min_order_qty) || 1)
  await loadRelatedProducts(product.value.id)
}

async function loadRelatedProducts(currentId) {
  relatedProducts.value = []
  try {
    const params = {
      ordering: 'hot',
      page_size: 8,
    }
    if (product.value.category_id) params.category_id = product.value.category_id
    const data = await productService.list(params)
    const rows = data.results || data || []
    relatedProducts.value = rows
      .filter((item) => {
        if (String(item.config_id || '') === String(product.value.config_id || '')) return false
        return String(item.id) !== String(currentId)
      })
      .slice(0, 4)
  } catch (err) {
    relatedProducts.value = []
  }
}

function normalize(data) {
  const options = data.uom_options || []
  const index = Math.max(options.findIndex((item) => item.code === data.order_uom), 0)
  uomIndex.value = index
  const selected = options[index] || {}
  return {
    ...data,
    order_uom: selected.code || data.order_uom,
    qty_in_base: selected.qty_in_base || data.qty_in_base,
    price: selected.unit_price || data.price,
  }
}

function changeUom(event) {
  const index = Number(event.detail.value)
  const selected = product.value.uom_options && product.value.uom_options[index]
  if (!selected) return
  uomIndex.value = index
  product.value.order_uom = selected.code
  product.value.qty_in_base = selected.qty_in_base
  product.value.price = selected.unit_price || product.value.price
}

function openUomPicker() {}

function toggleFavorite() {
  if (!product.value || !product.value.id) return
  const added = browse.toggleFavorite(product.value)
  uni.showToast({ title: added ? '已收藏' : '已取消收藏', icon: 'none' })
}

async function addToCart() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return false
  }
  if (product.value.stock && product.value.stock.status === 'OUT') {
    uni.showToast({ title: '商品暂时缺货', icon: 'none' })
    return false
  }
  try {
    const data = await cart.addProduct(product.value, qty.value)
    uni.showToast({ title: '已加入购物车', icon: 'none' })
    return data || true
  } catch (err) {
    uni.showToast({ title: err.message || '加入失败', icon: 'none' })
    return false
  }
}

async function addRelatedProduct(item) {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  try {
    await cart.addProduct(item, Number((item.rules && item.rules.min_order_qty) || 1))
    uni.showToast({ title: '已加入购物车', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '加入失败', icon: 'none' })
  }
}

function openRelatedProduct(item) {
  if (!item || !item.id) return
  load(item.id, productDetailParams(item)).catch((err) => {
    uni.showToast({ title: err.message || '商品加载失败', icon: 'none' })
  })
}

function productDetailParams(item) {
  return {
    config_id: item.config_id || '',
  }
}

function goRelatedList() {
  uni.navigateTo({ url: '/pages/product-list/product-list?ordering=hot' })
}

async function buyNow() {
  const data = await addToCart()
  if (data) {
    const cartId = (data && (data.cart_id || data.id)) || ''
    uni.navigateTo({ url: `/pages/order-confirm/order-confirm?cart_id=${cartId}` })
  }
}

onLoad((query) => {
  if (query.id) {
    load(query.id, {
      config_id: query.config_id || '',
    })
  }
})
</script>

<style scoped>
.detail-page {
  min-height: 100vh;
  background: #f4f6f8;
  padding-bottom: 136rpx;
}

.hero-img {
  width: 100%;
  height: 560rpx;
  background: #eef2f7;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0f766e;
  font-size: 88rpx;
  font-weight: 900;
}

.info {
  padding: 24rpx;
}

.name {
  color: #17202a;
  font-size: 36rpx;
  font-weight: 850;
  line-height: 1.35;
}

.title-row {
  display: flex;
  align-items: flex-start;
  gap: 18rpx;
}

.title-row .name {
  flex: 1;
}

.favorite {
  min-width: 116rpx;
  height: 60rpx;
  line-height: 60rpx;
  padding: 0 16rpx;
  border: 1rpx solid #b6d8d2;
  border-radius: 8rpx;
  background: #ecfdf5;
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 750;
}

.favorite::after {
  border: 0;
}

.meta {
  margin-top: 8rpx;
  color: #64748b;
  font-size: 25rpx;
}

.price-row {
  margin-top: 18rpx;
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}

.market {
  color: #94a3b8;
  font-size: 24rpx;
  text-decoration: line-through;
}

.cells {
  margin-top: 22rpx;
  background: #fff;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  overflow: hidden;
}

.cell {
  min-height: 78rpx;
  padding: 0 20rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20rpx;
  border-bottom: 1rpx solid #eef2f7;
  color: #334155;
  font-size: 26rpx;
}

.cell:last-child {
  border-bottom: 0;
}

.cell text:first-child {
  color: #64748b;
}

.desc {
  margin-top: 22rpx;
  padding: 22rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.section-title {
  color: #17202a;
  font-size: 28rpx;
  font-weight: 780;
}

.desc-text {
  margin-top: 12rpx;
  color: #475569;
  font-size: 25rpx;
  line-height: 1.6;
}

.recommend {
  margin-top: 24rpx;
}

.section-head {
  margin-bottom: 14rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #17202a;
  font-size: 29rpx;
  font-weight: 850;
}

.more {
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 500;
}

.recommend-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.bottom {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  min-height: 112rpx;
  padding: 18rpx 24rpx;
  display: flex;
  align-items: center;
  gap: 12rpx;
  border-top: 1rpx solid #dfe6ef;
  background: #fff;
}

.ghost,
.buy {
  flex: 1;
  height: 76rpx;
  line-height: 76rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  font-size: 26rpx;
  font-weight: 750;
}

.ghost {
  background: #edf8f5;
  color: #0f766e;
}

.buy {
  background: #0f766e;
  color: #fff;
}

.ghost::after,
.buy::after {
  border: 0;
}

.ghost[disabled],
.buy[disabled] {
  background: #e2e8f0;
  color: #94a3b8;
}
</style>
