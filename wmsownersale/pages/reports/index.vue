<template>
  <view class="page">
    <view class="section-title">报表与查询</view>

    <view class="card" @click="goInventory">
      <view class="card-title">实时库存</view>
      <view class="card-desc">查看当前货主名下商品库存汇总</view>
    </view>
  </view>
</template>

<script setup>
function goInventory() {
  uni.navigateTo({
    url: '/pages/inventory/index'
  })
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: #f5f6f8;
  padding: 24rpx;
  box-sizing: border-box;
}

.section-title {
  font-size: 30rpx;
  font-weight: 600;
  color: #222;
  margin-bottom: 20rpx;
}

.card {
  background: #fff;
  border-radius: 16rpx;
  padding: 24rpx;
  box-shadow: 0 4rpx 12rpx rgba(0, 0, 0, 0.04);
}

.card-title {
  font-size: 30rpx;
  font-weight: 600;
  color: #222;
  margin-bottom: 8rpx;
}

.card-desc {
  font-size: 24rpx;
  color: #666;
}
</style>