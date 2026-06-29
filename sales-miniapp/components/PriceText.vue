<template>
  <text class="price">¥{{ money(value) }}</text>
</template>

<script setup>
import { money } from '../utils/money'

defineProps({
  value: {
    type: [String, Number],
    default: 0,
  },
})
</script>

<style scoped>
.price {
  color: #b42318;
  font-size: 34rpx;
  font-weight: 850;
}
</style>
