<template>
  <view :class="['async-state', `is-${kind}`]" role="status">
    <text>{{ message }}</text>
    <button v-if="kind === 'error'" class="retry-button" @click="$emit('retry')">重试</button>
  </view>
</template>

<script setup>
defineProps({
  kind: { type: String, default: 'loading' },
  message: { type: String, required: true },
})

defineEmits(['retry'])
</script>

<style scoped>
.async-state {
  padding: 40rpx 24rpx;
  color: #6b7280;
  text-align: center;
}
.is-error { color: #b42318; }
.retry-button {
  min-height: 88rpx;
  width: 220rpx;
  margin-top: 20rpx;
  color: #2563eb;
  background: #fff;
  border: 1rpx solid #2563eb;
}
</style>
