<template>
  <view class="history-quantities">
    <view v-for="item in visible" :key="item.key" class="quantity-line">
      <text>{{ item.label }}</text>
      <text class="quantity">{{ formatQuantity(row[`${item.key}_qty`]) }} {{ row.base_unit }}<text class="times"> · {{ times(item.key) }}</text></text>
    </view>
  </view>
</template>
<script setup>
import { computed } from 'vue'
import { formatQuantity } from '@/utils/quantity'
import { historyCategories, historyTimes } from '@/utils/stockHistory'
const props = defineProps({ row: {type: Object, required: true} })
const visible = computed(() => Object.entries(historyCategories)
  .filter(([key]) => props.row[`${key}_qty`] != null && formatQuantity(props.row[`${key}_qty`]) !== '0')
  .map(([key, label]) => ({key, label})))
function times(key) {
  const pending = key === 'unknown' || props.row[`${key}_unverified_count`] > 0
  return historyTimes(props.row[`${key}_count`], pending)
}
</script>
<style scoped>
.history-quantities { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.quantity-line { display: flex; gap: 12px; justify-content: space-between; flex-wrap: wrap; font-size: 14px; color: #475569; }
.quantity { color: #172033; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.times { font-weight: 400; color: #64748b; font-size: 12px; }
</style>
