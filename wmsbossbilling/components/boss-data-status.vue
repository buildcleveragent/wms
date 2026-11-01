<template>
  <view v-if="visible" class="status" :class="tone">
    <view class="title">{{ title }}</view>
    <view class="detail">{{ detail }}</view>
  </view>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ meta: Object, error: Object, stale: Boolean })
const visible = computed(() => !!props.error || props.stale || ['WARNING', 'UNAVAILABLE'].includes(props.meta?.data_status))
const tone = computed(() => props.error || props.meta?.data_status === 'UNAVAILABLE' ? 'danger' : 'warning')
const title = computed(() => {
  if (props.error?.kind === 'FORBIDDEN') return '无权查看当前范围'
  if (props.error?.kind === 'NETWORK_ERROR') return props.stale ? '网络失败，以下为非最新数据' : '网络失败'
  if (props.error) return '数据加载失败'
  if (props.meta?.data_status === 'UNAVAILABLE') return '所选范围数据不可用'
  return '数据存在质量提示'
})
const warningLabels = {
  HISTORICAL_INVENTORY_MISSING: '所选日期缺少库存快照',
  HISTORICAL_INVENTORY_APPROXIMATE: '历史库存为近似数据',
  HISTORICAL_INVENTORY_UNIT_UNKNOWN: '历史单位无法确认，未计入可信单位汇总',
  HISTORICAL_INVENTORY_UNIT_INFERRED: '历史单位为推断值，未计入可信单位汇总',
  HISTORICAL_STALE_METRIC_UNAVAILABLE: '历史快照不支持呆滞库存和冷区指标',
  RESOURCE_SNAPSHOT_DAYS_MISSING: '资源收益缺少部分日期的库存快照',
  RESOURCE_SNAPSHOT_APPROXIMATE: '资源快照为近似数据',
  INVENTORY_COST_MISSING: '部分库存缺少成本或币种，估值仅包含已知成本',
  INVENTORY_AGE_UNKNOWN: '部分库存的入库日期未知',
  FIFO_HISTORY_NOT_AVAILABLE: '所选日期缺少 FIFO 价值历史',
  FIFO_DAILY_VALUE_HISTORY_MISSING: '缺少 FIFO 每日价值历史，仅提供当前价值',
  UNKNOWN_CURRENCY: '部分记录币种未知',
  BILLS_MISSING_DUE_DATE: '部分账单缺少到期日',
}
const detail = computed(() => {
  if (props.error) return props.error.message || props.error.kind
  const warnings = props.meta?.warnings || []
  return warnings.length ? warnings.map((row) => `${row.detail || warningLabels[row.code] || row.code}（${row.count || 0}）`).join('、') : '请谨慎解读当前数据。'
})
</script>

<style scoped>
.status { margin: 16rpx 0; padding: 20rpx; border-radius: 16rpx; }
.warning { background: #fff7df; color: #8a5b00; }
.danger { background: #fff0f0; color: #a11b1b; }
.title { font-weight: 700; }
.detail { margin-top: 8rpx; font-size: 23rpx; }
</style>
