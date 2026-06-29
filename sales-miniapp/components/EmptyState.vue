<template>
  <view class="empty-state">
    <view class="mark">{{ mark }}</view>
    <view class="text">{{ text }}</view>
  </view>
</template>

<script setup>
defineProps({
  mark: {
    type: String,
    default: '空',
  },
  text: {
    type: String,
    default: '暂无数据',
  },
})
</script>

<style scoped>
.empty-state {
  min-height: 320rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #64748b;
}

.mark {
  width: 88rpx;
  height: 88rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: #eef2f7;
  color: #475569;
  font-weight: 800;
}

.text {
  margin-top: 18rpx;
  font-size: 28rpx;
}
</style>
