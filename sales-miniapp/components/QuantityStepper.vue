<template>
  <view class="stepper">
    <button class="step" @click="emitChange(Number(modelValue || 0) - Number(step || 1))">-</button>
    <input class="qty" type="number" :value="modelValue" @input="emitChange($event.detail.value)" />
    <button class="step" @click="emitChange(Number(modelValue || 0) + Number(step || 1))">+</button>
  </view>
</template>

<script setup>
const props = defineProps({
  modelValue: {
    type: [String, Number],
    default: 1,
  },
  min: {
    type: [String, Number],
    default: 1,
  },
  step: {
    type: [String, Number],
    default: 1,
  },
})

const emit = defineEmits(['update:modelValue', 'change'])

function emitChange(value) {
  const min = Number(props.min || 0)
  const next = Math.max(Number(value) || min, min)
  emit('update:modelValue', next)
  emit('change', next)
}
</script>

<style scoped>
.stepper {
  width: 214rpx;
  height: 64rpx;
  display: flex;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  overflow: hidden;
  background: #fff;
}

.step {
  width: 64rpx;
  height: 64rpx;
  line-height: 64rpx;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: #f8fafc;
  color: #17202a;
  font-size: 32rpx;
}

.step::after {
  border: 0;
}

.qty {
  width: 86rpx;
  height: 64rpx;
  text-align: center;
  border-left: 1rpx solid #d7dde8;
  border-right: 1rpx solid #d7dde8;
  font-size: 26rpx;
}
</style>
