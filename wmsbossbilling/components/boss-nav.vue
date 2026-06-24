<template>
  <view class="nav-card">
    <view class="nav-brand">仓储经营分析中心</view>
    <view class="nav-track">
      <view
        v-for="tab in tabs"
        :key="tab.key"
        class="nav-pill"
        :class="{ active: active === tab.key }"
        @click="go(tab)"
      >
        {{ tab.label }}
      </view>
    </view>
  </view>
</template>

<script setup>
const props = defineProps({
  active: {
    type: String,
    default: 'home',
  },
})

const tabs = [
  { key: 'home', label: '首页总览', url: '/pages/home/index' },
  { key: 'inventory', label: '库存与库容', url: '/pages/inventory/index' },
  { key: 'revenue', label: '收入与计费', url: '/pages/billing/overview' },
  { key: 'alerts', label: '预警中心', url: '/pages/alerts/index' },
]

function go(tab) {
  if (tab.key === props.active) return
  uni.reLaunch({ url: tab.url })
}
</script>

<style scoped>
.nav-card {
  margin-bottom: 18rpx;
  padding: 10rpx;
  border-radius: 26rpx;
  background: linear-gradient(135deg, rgba(9, 31, 84, 0.08), rgba(11, 95, 255, 0.04));
}

.nav-brand {
  margin: 4rpx 8rpx 14rpx;
  font-size: 24rpx;
  font-weight: 700;
  color: #27427d;
  letter-spacing: 1rpx;
}

.nav-track {
  display: flex;
  gap: 10rpx;
}

.nav-pill {
  flex: 1;
  min-height: 78rpx;
  border-radius: 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 16rpx;
  font-size: 24rpx;
  font-weight: 700;
  color: #5f6f8f;
  background: rgba(255, 255, 255, 0.85);
}

.nav-pill.active {
  color: #0b5fff;
  background: #ffffff;
  box-shadow: 0 10rpx 24rpx rgba(11, 95, 255, 0.12);
}
</style>
