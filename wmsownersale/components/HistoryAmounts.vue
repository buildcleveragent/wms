<template>
  <view class="history-amounts">
    <view v-for="item in visible" :key="`${item.category}-${item.currency}`" class="amount-line">
      <text>{{ item.category === 'inbound' ? '入库采购金额' : '出库销售金额' }}{{ item.amount_status === 'PARTIAL' ? '（已知）' : '' }}</text>
      <text class="amount-value">{{ item.amount_status === 'UNPRICED' ? '金额待核对' : `${historyMoney(item.amount)} ${item.currency === 'CNY' ? '元' : item.currency}` }}</text>
      <text v-if="item.amount_status === 'PARTIAL'" class="amount-warning">部分金额待核对</text>
    </view>
  </view>
</template>
<script setup>
import { computed } from 'vue'
import { historyMoney } from '@/utils/stockHistory'
const props = defineProps({ amounts: {type:Array, default:() => []} })
const visible = computed(() => props.amounts.filter(a => ['inbound','outbound'].includes(a.category)))
</script>
<style scoped>
.history-amounts { margin-top: 10px; }
.amount-line { display:flex; flex-wrap:wrap; justify-content:space-between; gap:4px 10px; margin:6px 0; font-size:13px; color:#475569; }
.amount-value { font-weight:650; color:#1e3a5f; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.amount-warning { flex-basis:100%; color:#a2530c; }
</style>
