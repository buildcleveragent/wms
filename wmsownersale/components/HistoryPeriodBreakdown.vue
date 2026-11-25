<template>
  <view>
    <view class="period-columns">
      <button
        v-for="side in directions" :key="side.category"
        :class="['period-side', side.category]" :disabled="!side.totals.length"
        @click="$emit('select', side.category)"
      >
        <text class="period-label">{{ side.label }}数量</text>
        <template v-if="side.totals.length">
          <view v-for="total in side.totals" :key="total.base_unit + '-' + total.currency" class="unit-row">
            <text class="quantity">{{ formatQuantity(total.qty) }} {{ historyUnit(total) }}</text>
            <text v-if="total.unpriced_count" class="note">未计价 {{ formatQuantity(total.unpriced_qty) }} {{ historyUnit(total) }}</text>
          </view>
          <text v-if="side.totals.some(total => total.posting_pending_count)" class="note">库存扣减异常／待入账</text>
          <HistoryAmounts :amounts="side.amounts" />
          <text class="detail-link">查看{{ side.label }}记录 ›</text>
        </template>
        <text v-else class="empty-side">{{ periodName }}无{{ side.label }}</text>
      </button>
    </view>
    <view v-if="otherGroups.length" class="other-changes">
      <text class="period-label">其他库存变化</text>
      <button v-for="group in otherGroups" :key="group.category" class="other-change" @click="$emit('select', group.category)">
        <text>{{ group.label }}</text>
        <view>
          <text v-for="total in group.totals" :key="total.base_unit + '-' + total.currency" class="quantity">
            {{ formatQuantity(total.qty) }} {{ historyUnit(total) }}
          </text>
        </view>
        <text>›</text>
      </button>
    </view>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import HistoryAmounts from './HistoryAmounts.vue'
import { formatQuantity } from '@/utils/quantity'
import { historyCategories, historyUnit } from '@/utils/stockHistory'

const props = defineProps({
  row: { type: Object, required: true },
  grain: { type: String, required: true },
})
defineEmits(['select'])

const periodName = computed(() => props.row.period_start
  ? { day: '本日', week: '本周', month: '本月', year: '本年' }[props.grain]
  : '该组')

const totalsFor = category => (props.row.totals || []).filter(total => total.category === category)
const directions = computed(() => ['inbound', 'outbound'].map(category => ({
  category,
  label: category === 'inbound' ? '入库' : '出库',
  totals: totalsFor(category),
  amounts: (props.row.amounts || []).filter(amount => amount.category === category),
})))
const otherGroups = computed(() => Object.entries(historyCategories)
  .filter(([category]) => category !== 'inbound' && category !== 'outbound')
  .map(([category, label]) => ({ category, label, totals: totalsFor(category) }))
  .filter(group => group.totals.length))
</script>

<style scoped>
.period-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 8px; }
.period-side { display: flex; flex-direction: column; align-items: stretch; box-sizing: border-box; width: 100%; min-width: 0; padding: 12px; margin: 0; text-align: left; white-space: normal; overflow-wrap: anywhere; line-height: 1.5; border-radius: 10px; border: 1px solid #cde8dd; background: #f0faf5; color: #166448; }
.period-side.outbound { background: #eff5ff; border-color: #d3e1fa; color: #2456a9; }
.period-side::after, .other-change::after { border: 0; }
.period-label { font-size: 14px; font-weight: 600; color: #475569; }
.unit-row { margin-top: 4px; }
.quantity { display: block; font-weight: 650; font-variant-numeric: tabular-nums; }
.note, .empty-side, .detail-link { display: block; font-size: 13px; }
.note { color: #a2530c; }
.empty-side { color: #64748b; margin-top: 4px; }
.detail-link { margin-top: auto; padding-top: 8px; }
.other-changes { margin-top: 12px; }
.other-change { display: flex; gap: 8px; align-items: baseline; background: white; padding: 8px 0; margin: 0; font-size: 14px; line-height: 1.5; text-align: left; }
.other-change > view { margin-left: auto; text-align: right; }
</style>
