<template>
  <view class="page list-page">
    <view class="head">
      <view>
        <view class="title">我的收藏</view>
        <view class="muted">{{ items.length }} 件商品</view>
      </view>
      <button v-if="items.length" class="ghost" @click="clearAll">清空</button>
    </view>

    <view v-if="items.length" class="products">
      <ProductCard
        v-for="item in items"
        :key="item.key"
        :product="item"
        @open="openProduct"
        @add="addToCart"
      />
    </view>
    <EmptyState v-else text="暂无收藏商品" />
  </view>
</template>

<script setup>
import { onShow } from '@dcloudio/uni-app'
import { computed } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import ProductCard from '../../components/ProductCard.vue'
import { useBrowseStore } from '../../stores/browse'
import { useCartStore } from '../../stores/cart'
import { getToken } from '../../utils/request'

const browse = useBrowseStore()
const cart = useCartStore()
const items = computed(() => browse.favorites)

function detailUrl(item) {
  return `/pages/product-detail/product-detail?id=${item.id}&config_id=${item.config_id || ''}`
}

function openProduct(item) {
  uni.navigateTo({ url: detailUrl(item) })
}

async function addToCart(item) {
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

function clearAll() {
  browse.favorites = []
  browse.persist()
}

onShow(() => {
  browse.reload()
})
</script>

<style scoped>
.head {
  margin-bottom: 18rpx;
  padding: 22rpx;
  display: flex;
  justify-content: space-between;
  gap: 16rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.title {
  color: #17202a;
  font-size: 32rpx;
  font-weight: 850;
}

.muted {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 24rpx;
}

.ghost {
  width: 128rpx;
  height: 64rpx;
  line-height: 64rpx;
  padding: 0;
  border: 1rpx solid #cbd5e1;
  border-radius: 8rpx;
  background: #fff;
  color: #334155;
  font-size: 24rpx;
}

.ghost::after {
  border: 0;
}

.products {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}
</style>
