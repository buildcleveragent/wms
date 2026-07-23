<template>
  <view class="stars" :aria-label="`${label}${value}星`">
    <button
      v-for="score in 5"
      :key="score"
      class="star"
      :class="{ active: score <= value, readonly }"
      :disabled="readonly"
      :aria-label="readonly ? `${score}星` : `选择${score}星`"
      @click.stop="select(score)"
    >
      ★
    </button>
  </view>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  readonly: { type: Boolean, default: false },
  label: { type: String, default: '评分' },
})

const emit = defineEmits(['update:modelValue'])
const value = computed(() => Math.max(0, Math.min(5, Number(props.modelValue || 0))))

function select(score) {
  if (!props.readonly) emit('update:modelValue', score)
}
</script>

<style scoped>
.stars {
  display: flex;
  align-items: center;
  gap: 4rpx;
}

.star {
  width: 48rpx;
  height: 48rpx;
  min-width: 48rpx;
  padding: 0;
  border: 0;
  background: transparent;
  color: #cbd5e1;
  font-size: 40rpx;
  line-height: 48rpx;
}

.star::after {
  border: 0;
}

.star.active {
  color: #f59e0b;
}

.star.readonly {
  width: 32rpx;
  height: 32rpx;
  min-width: 32rpx;
  font-size: 27rpx;
  line-height: 32rpx;
}
</style>
