<template>
<view class="app">
<slot />
</view>
</template>
<script setup>
</script>
<style>
.app { min-height: 100vh; background: #f6f7fb; color: #111; }
.page { box-sizing: border-box; width: 100%; }
.btn { padding: 10rpx 20rpx; border-radius: 8rpx; background: #3b82f6; color: #fff; }
.btn[disabled] { opacity: .5; }
.btn-outline { padding: 5rpx 20rpx; border: 1rpx solid #cbd5e1; border-radius: 8rpx; background: #fff; color: #111; }
.input { margin: 16rpx 0; padding: 16rpx; border: 1rpx solid #e5e7eb; border-radius: 8rpx; background: #fff; }
.card { margin: 12rpx 0; padding: 18rpx; border: 1rpx solid #eee; border-radius: 12rpx; background: #fff; }
.bar { display: flex; align-items: center; justify-content: flex-start; gap: 10px; }
.row { display: flex; align-items: center; justify-content: space-between; }
.badge { padding: 6rpx 14rpx; border-radius: 9999rpx; background: #eef2ff; color: #3730a3; font-size: 24rpx; }
.title { margin: 16rpx 0 24rpx; font-size: 36rpx; font-weight: 700; }
.text-gray { color: #6b7280; }
.text-lg { font-size: 32rpx; }
.font-bold { font-weight: 700; }
.p-4 { padding: 32rpx; }
.mb-2 { margin-bottom: 16rpx; }
.mb-3 { margin-bottom: 24rpx; }
.mt-2 { margin-top: 16rpx; }
.mt-4 { margin-top: 32rpx; }
</style>
