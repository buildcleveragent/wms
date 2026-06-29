<template>
  <view v-if="count" class="cart-bar">
    <view>
      <view class="count">{{ count }} 件商品</view>
      <view class="amount">¥{{ money(amount) }}</view>
    </view>
    <button class="button" @click="$emit('checkout')">去结算</button>
  </view>
</template>

<script setup>
import { money } from '../utils/money'

defineProps({
  count: {
    type: Number,
    default: 0,
  },
  amount: {
    type: Number,
    default: 0,
  },
})

defineEmits(['checkout'])
</script>

<style scoped>
.cart-bar {
  position: fixed;
  left: 24rpx;
  right: 24rpx;
  bottom: calc(128rpx + env(safe-area-inset-bottom));
  height: 104rpx;
  padding: 14rpx 16rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  box-shadow: 0 10rpx 30rpx rgba(15, 23, 42, 0.08);
}

.count {
  color: #17202a;
  font-size: 26rpx;
  font-weight: 700;
}

.amount {
  margin-top: 4rpx;
  color: #b42318;
  font-size: 30rpx;
  font-weight: 850;
}

.button {
  width: 188rpx;
  height: 76rpx;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 28rpx;
  font-weight: 750;
}

.button::after {
  border: 0;
}
</style>
