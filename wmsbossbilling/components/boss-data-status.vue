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
const detail = computed(() => {
  if (props.error) return props.error.message || props.error.kind
  const warnings = props.meta?.warnings || []
  return warnings.length ? warnings.map((row) => `${row.code}（${row.count || 0}）`).join('、') : '请谨慎解读当前数据。'
})
</script>

<style scoped>
.status { margin: 16rpx 0; padding: 20rpx; border-radius: 16rpx; }
.warning { background: #fff7df; color: #8a5b00; }
.danger { background: #fff0f0; color: #a11b1b; }
.title { font-weight: 700; }
.detail { margin-top: 8rpx; font-size: 23rpx; }
</style>
