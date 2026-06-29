<template>
  <view class="address-card" :class="{ active }" @click="$emit('select')">
    <view class="between">
      <view class="person">{{ address.contact }} <text>{{ address.phone }}</text></view>
      <text v-if="address.is_default" class="tag">默认</text>
    </view>
    <view class="addr">{{ address.full_address || address.detail }}</view>
  </view>
</template>

<script setup>
defineProps({
  address: {
    type: Object,
    required: true,
  },
  active: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['select'])
</script>

<style scoped>
.address-card {
  padding: 20rpx;
  background: #fff;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
}

.address-card.active {
  border-color: #0f766e;
  background: #f0faf7;
}

.between {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14rpx;
}

.person {
  font-size: 29rpx;
  font-weight: 750;
  color: #17202a;
}

.person text {
  margin-left: 12rpx;
  font-weight: 500;
  color: #475569;
}

.tag {
  height: 40rpx;
  line-height: 40rpx;
  padding: 0 12rpx;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 22rpx;
}

.addr {
  margin-top: 10rpx;
  color: #475569;
  font-size: 25rpx;
  line-height: 1.45;
}
</style>
