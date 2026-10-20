<template>
  <view class="progress-card">
    <view class="progress-heading">
      <text class="section-title">履约进度</text>
      <button class="refresh-progress" size="mini" :disabled="loading || stopped" @click="refresh">
        {{ loading ? '更新中…' : '刷新进度' }}
      </button>
    </view>
    <view v-if="error" class="progress-error" role="status">{{ error }}</view>
    <view v-if="!progress && loading" class="progress-hint">正在加载进度…</view>
    <template v-if="progress">
      <view class="current-status">{{ progress.current_status_label }}</view>
      <view class="progress-hint">最近更新：{{ formatTime(progress.as_of) }}（北京时间）</view>
      <view class="timeline">
        <view v-for="stage in progress.stages" :key="stage.code" class="timeline-step" :class="`step-${stage.state}`">
          <view class="stage-heading">
            <text class="stage-name">{{ stage.name }}</text>
            <text class="stage-state">{{ stateLabel(stage.state) }}</text>
          </view>
          <view v-if="stage.started_at" class="progress-hint">开始：{{ formatTime(stage.started_at) }}</view>
          <view v-if="stage.state === 'completed'" class="progress-hint">完成：{{ formatTime(stage.completed_at) }}</view>
          <view v-else-if="['active', 'partial', 'exception'].includes(stage.state) && !stage.started_at" class="progress-hint">时间未记录</view>
          <view v-for="task in stage.tasks" :key="task.id" class="task-summary">
            {{ task.task_no }} · {{ stateLabel(task.state) }}
          </view>
        </view>
      </view>
      <view v-if="progress.exceptions?.length" class="exceptions">
        <view class="section-title">异常与待处理</view>
        <view v-for="(item, index) in progress.exceptions" :key="index" class="exception-item">
          <view>{{ item.message }}</view>
          <view v-if="item.task_no" class="progress-hint">任务：{{ item.task_no }}</view>
          <view v-if="item.product_id" class="progress-hint">商品：{{ productName(item.product_id) }}</view>
          <view v-if="item.occurred_at" class="progress-hint">{{ formatTime(item.occurred_at) }}</view>
        </view>
      </view>
      <view class="section-title quantity-title">商品履约数量</view>
      <view class="progress-hint">按商品及基本单位汇总</view>
      <view v-for="item in progress.quantities" :key="`${item.product_id}-${item.unit_id}`" class="quantity-row">
        <view class="stage-name">{{ item.product_name }}</view>
        <view class="progress-hint">SKU：{{ item.sku || '—' }} · 单位：{{ item.unit }}</view>
        <view class="quantity-grid">
          <view><text>计划</text><text>{{ qty(item.planned_qty) }}</text></view>
          <view><text>已发运</text><text>{{ qty(item.shipped_qty) }}</text></view>
          <view><text>未发运</text><text>{{ qty(item.remaining_qty) }}</text></view>
        </view>
      </view>
      <view v-if="progress.history?.length" class="history">
        <view class="section-title">历史退回记录</view>
        <view v-for="(item, index) in progress.history" :key="index" class="exception-item">
          <view>{{ item.message }}</view>
          <view class="progress-hint">{{ formatTime(item.occurred_at) }}</view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { toRef } from 'vue'
import { useOrderProgress } from '@/composables/useOrderProgress'

const props = defineProps({
  orderId: { type: Number, required: true },
  active: { type: Boolean, default: true },
})
const { progress, loading, error, stopped, refresh } = useOrderProgress(
  toRef(props, 'orderId'), toRef(props, 'active'),
)
const stateLabel = state => ({
  pending: '未开始', active: '处理中', partial: '部分完成', completed: '已完成',
  exception: '异常', not_applicable: '无独立记录',
})[state] || '待核对'
function formatTime(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '时间未记录'
  // Format explicitly in UTC+8 on H5 and mini-program runtimes alike.
  return new Date(date.getTime() + 8 * 3600000).toISOString().slice(0, 19).replace('T', ' ')
}
function qty(value) {
  if (value == null) return '未记录'
  return String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1')
}
function productName(id) {
  return progress.value?.quantities?.find(item => item.product_id === id)?.product_name || `#${id}`
}
</script>

<style scoped>
.progress-card { margin: 12rpx 0; padding: 24rpx; border: 1rpx solid #e5e7eb; border-radius: 16rpx; background: #fff; overflow-wrap: anywhere; }
.progress-heading, .stage-heading { display: flex; align-items: center; justify-content: space-between; gap: 16rpx; }
.section-title { font-size: 30rpx; font-weight: 700; color: #111827; }
.refresh-progress { flex-shrink: 0; margin: 0; color: #2563eb; background: #eff6ff; }
.current-status { margin: 22rpx 0 8rpx; font-size: 34rpx; font-weight: 700; color: #1d4ed8; }
.progress-hint, .task-summary { margin-top: 8rpx; font-size: 24rpx; line-height: 1.6; color: #6b7280; }
.timeline { margin: 28rpx 0 28rpx 10rpx; }
.timeline-step { position: relative; padding: 0 0 28rpx 30rpx; border-left: 3rpx solid #e5e7eb; }
.timeline-step::before { content: ''; position: absolute; left: -9rpx; top: 8rpx; width: 15rpx; height: 15rpx; border-radius: 50%; background: #cbd5e1; }
.timeline-step:last-child { padding-bottom: 0; border-left-color: transparent; }
.step-completed::before { background: #16a34a; }
.step-active::before, .step-partial::before { background: #2563eb; }
.step-exception::before { background: #dc2626; }
.stage-name { font-size: 28rpx; font-weight: 600; color: #374151; }
.stage-state { flex-shrink: 0; font-size: 24rpx; color: #6b7280; }
.step-completed .stage-state { color: #15803d; }
.step-active .stage-state, .step-partial .stage-state { color: #1d4ed8; }
.step-exception .stage-state { color: #b42318; }
.exceptions, .progress-error { margin-top: 16rpx; padding: 18rpx; border-radius: 10rpx; background: #fff7ed; color: #9a3412; }
.exception-item { margin-top: 14rpx; font-size: 26rpx; line-height: 1.6; }
.quantity-title, .history { margin-top: 28rpx; }
.quantity-row { padding: 20rpx 0; border-bottom: 1rpx solid #e5e7eb; }
.quantity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12rpx; margin-top: 16rpx; }
.quantity-grid text { display: block; font-size: 26rpx; color: #111827; }
.quantity-grid text:first-child { margin-bottom: 8rpx; font-size: 24rpx; color: #6b7280; }
</style>
