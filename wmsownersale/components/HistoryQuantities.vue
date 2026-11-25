<template>
  <view class="history-quantities">
    <view v-for="item in visible" :key="item.key" class="quantity-line">
      <text>{{ item.label }}</text>
      <view class="quantities">
        <view v-for="total in item.totals" :key="`${total.base_unit}-${total.currency}`">
          <text class="quantity">{{ formatQuantity(total.qty) }} {{ historyUnit(total) }}</text>
          <text v-if="total.unpriced_count && ['inbound','outbound'].includes(item.key)" class="note">未计价 {{ formatQuantity(total.unpriced_qty) }} {{ historyUnit(total) }}</text>
          <text v-if="total.posting_pending_count" class="warning">库存扣减异常／待入账</text>
        </view>
      </view>
    </view>
    <HistoryAmounts :amounts="row.amounts || []" />
  </view>
</template>
<script setup>
import { computed } from 'vue'
import { formatQuantity } from '@/utils/quantity'
import { historyCategories, historyUnit } from '@/utils/stockHistory'
import HistoryAmounts from '@/components/HistoryAmounts.vue'
const props = defineProps({ row: {type: Object, required: true} })
const visible = computed(() => Object.entries(historyCategories)
  .map(([key,label]) => ({key,label,totals:(props.row.totals || []).filter(t => t.category === key)}))
  .filter(item => item.totals.length))
</script>
<style scoped>
.history-quantities { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.quantity-line { display: flex; gap: 12px; justify-content: space-between; flex-wrap: wrap; font-size: 14px; color: #475569; }
.quantity { color: #172033; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.times { font-weight: 400; color: #64748b; font-size: 12px; }
.quantities { text-align:right; }
.note,.warning { display:block; font-size:12px; color:#a2530c; font-weight:400; }
</style>
