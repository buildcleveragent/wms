<template>
  <view class="product-card" @click="$emit('open', product)">
    <image v-if="product.image_url" class="thumb" :src="product.image_url" mode="aspectFill" />
    <view v-else class="thumb placeholder">货</view>
    <view class="main">
      <view class="name">{{ product.name }}</view>
      <view class="meta">{{ product.code }} {{ product.spec }}</view>
      <view v-if="product.owner_name" class="merchant">{{ product.owner_name }}</view>
      <view class="badges">
        <text v-if="product.badges && product.badges.hot" class="badge hot">热卖</text>
        <text v-if="product.badges && product.badges.new" class="badge new">新品</text>
        <text v-if="product.stock && product.stock.display" class="badge">{{ product.stock.display }}</text>
      </view>
      <view class="foot">
        <PriceText :value="product.price" />
        <button
          class="add"
          :disabled="product.stock && product.stock.status === 'OUT'"
          @click.stop="$emit('add', product)"
        >
          +
        </button>
      </view>
    </view>
  </view>
</template>

<script setup>
import PriceText from './PriceText.vue'

defineProps({
  product: {
    type: Object,
    required: true,
  },
})

defineEmits(['open', 'add'])
</script>

<style scoped>
.product-card {
  display: flex;
  gap: 18rpx;
  padding: 18rpx;
  background: #fff;
  border: 1rpx solid #e1e7ef;
  border-radius: 8rpx;
}

.thumb {
  width: 152rpx;
  height: 152rpx;
  border-radius: 8rpx;
  background: #eef2f7;
  flex-shrink: 0;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0f766e;
  font-size: 42rpx;
  font-weight: 900;
}

.main {
  flex: 1;
  min-width: 0;
}

.name {
  color: #17202a;
  font-size: 29rpx;
  font-weight: 750;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.merchant {
  margin-top: 8rpx;
  color: #0f766e;
  font-size: 22rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx;
  min-height: 38rpx;
  margin-top: 12rpx;
}

.badge {
  height: 34rpx;
  line-height: 34rpx;
  padding: 0 10rpx;
  border-radius: 8rpx;
  background: #eef2f7;
  color: #475569;
  font-size: 20rpx;
}

.hot {
  background: #fff0ec;
  color: #b42318;
}

.new {
  background: #edf7ff;
  color: #2563eb;
}

.foot {
  margin-top: 12rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14rpx;
}

.add {
  width: 56rpx;
  height: 56rpx;
  line-height: 56rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 36rpx;
  font-weight: 700;
}

.add::after {
  border: 0;
}

.add[disabled] {
  background: #cbd5e1;
  color: #f8fafc;
}
</style>
