<template>
  <view class="history-page">
    <view class="history-card">
      <view class="history-head"><text class="history-title">{{ assisted ? '代货主出库统计' : '全部出库订单统计' }}</text><button class="history-button" :disabled="!applied" @click="openHistory()">历史记录</button></view>
      <text class="history-muted">{{ assisted ? '按代办时间' : '按订单业务日期' }} · {{ periodLabel || '请选择条件查询' }}</text>
      <view class="history-actions periods"><button class="history-button" @click="today">今日</button><picker mode="date" fields="month" :value="filters.start_date.slice(0, 7)" @change="month"><view class="history-button month-picker">选择月份</view></picker><text class="history-muted">或自定义下方日期</text></view>
      <OutboundHistoryFilters :model-value="filters" :options="options" :assisted="assisted" @update:model-value="Object.assign(filters, $event)" @query="load()" />
      <text v-if="dirty" class="history-muted">筛选条件已修改，点击统计后生效；当前结果及历史跳转仍按上次统计条件。</text>
      <view class="history-actions"><button class="history-button" @click="reset">重置</button><button class="history-button primary" :disabled="!authorized" @click="load()">统计</button></view>
    </view>
    <view v-if="error" class="history-error">{{ error }}<button class="history-button" @click="retry">重试</button></view>
    <view v-if="loading" class="history-card history-empty">正在统计…</view>
    <template v-if="stats">
      <view class="history-summary">
        <view class="history-metric" @click="openHistory()">订单数<text class="history-metric-value">{{ summary.order_count || 0 }}</text></view>
        <view class="history-metric">商品行数<text class="history-metric-value">{{ summary.line_count || 0 }}</text></view>
        <view class="history-metric">订单基本数量<text class="history-metric-value">{{ quantity(summary.total_base_qty) }}</text></view>
        <template v-if="assisted">
          <view class="history-metric" @click="openHistory({ status: 'COMPLETED' })">已完成<text class="history-metric-value">{{ summary.completed_count || 0 }}</text></view>
          <view class="history-metric">待处理<text class="history-metric-value">{{ summary.pending_count || 0 }}</text></view>
          <view class="history-metric">异常<text class="history-metric-value">{{ summary.exception_count || 0 }}</text></view>
          <view class="history-metric" @click="openHistory({ status: 'CANCELLED' })">已取消<text class="history-metric-value">{{ summary.cancelled_count || 0 }}</text></view>
        </template>
      </view>
      <text class="history-muted">数量包含所选状态的订单，不代表实际发货量；不同基本单位的合计仅供作业量参考。</text>
      <view class="history-sections">
        <view v-for="group in statusGroups" :key="group.key" class="history-card">
          <text class="history-title">{{ group.title }}</text>
          <view v-for="row in stats[group.key] || []" :key="String(row.value ?? row.status)" class="history-row history-head" @click="openHistory({ [group.filter]: String(row.value ?? row.status) })"><text>{{ row.label }}</text><text class="history-link">{{ row.order_count }} 张 ›</text></view>
        </view>
        <view class="history-card">
          <text class="history-title">每日汇总</text>
          <view v-for="row in stats.daily_rows || []" :key="row.date" class="history-row" @click="openHistory({ start_date: row.date, end_date: row.date })"><view class="history-head"><text>{{ row.date }}</text><text class="history-link">{{ row.order_count }} 张 ›</text></view><text class="history-meta">商品行 {{ row.line_count }} · 数量 {{ quantity(row.total_base_qty) }}</text></view>
          <view v-if="!stats.daily_rows?.length" class="history-empty">暂无每日数据</view>
        </view>
        <view class="history-card">
          <text class="history-title">货主汇总</text>
          <view v-for="row in stats.owner_rows || []" :key="row.owner_id" class="history-row" @click="openHistory({ owner_id: row.owner_id })"><view class="history-head"><text>{{ row.owner_name }}</text><text class="history-link">{{ row.order_count }} 张 ›</text></view><text class="history-meta">商品行 {{ row.line_count }} · 数量 {{ quantity(row.total_base_qty) }}</text></view>
          <view v-if="!stats.owner_rows?.length" class="history-empty">暂无货主数据</view>
        </view>
        <view v-if="assisted" class="history-card">
          <text class="history-title">操作员汇总</text>
          <view v-for="row in stats.operator_rows || []" :key="row.operator_id || 'missing'" class="history-row history-head" @click="row.operator_id && openHistory({ operator_id: row.operator_id })"><text>{{ row.operator_name || '未记录' }}</text><text>{{ row.order_count }} 张</text></view>
          <view v-if="!stats.operator_rows?.length" class="history-empty">暂无操作员数据</view>
        </view>
        <view class="history-card">
          <text class="history-title">商品排行 · 前10名</text><text class="history-muted">当前商品资料 · 按商品和基本单位合并</text>
          <view v-for="row in stats.product_rows || []" :key="`${row.product_id}:${row.base_uom_id}`" class="history-row"><text>{{ row.product_display_name || row.product_name }}</text><text class="history-meta">{{ row.product_sku || row.product_code }} · {{ row.product_spec || '未填写规格' }}</text><view class="history-head"><text>{{ row.order_count }} 张单</text><text>{{ quantity(row.total_base_qty) }} {{ row.base_uom_name }}</text></view></view>
          <view v-if="!stats.product_rows?.length" class="history-empty">暂无商品数据</view>
        </view>
      </view>
    </template>
  </view>
</template>
<script setup>
import { computed } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import OutboundHistoryFilters from '@/components/OutboundHistoryFilters.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useOutboundHistory, queryString, quantity, defaultPeriod, localDate } from '@/utils/outboundHistory'
const assisted = true
const session = useOutboundHistory({ api, auth: useAuth(), assisted, statsMode: true })
const { filters, applied, stats, options, loading, error, authorized, dirty, periodLabel, load, reset, retry } = session
const summary = computed(() => stats.value?.summary || {})
const statusGroups = assisted ? [{ key: 'status_rows', filter: 'status', title: '作业状态分布' }] : [
  { key: 'submit_rows', filter: 'submit_status', title: '提交状态分布' },
  { key: 'approval_rows', filter: 'approval_status', title: '审核状态分布' },
  { key: 'closed_rows', filter: 'is_closed', title: '关闭状态分布' },
]
onLoad(session.applyRoute); onShow(session.initialize); onHide(session.suspend); onUnload(session.dispose)
function today() { Object.assign(filters, defaultPeriod(true)); load() }
function month(event) {
  const value = event.detail.value, [year, m] = value.split('-').map(Number)
  Object.assign(filters, { start_date: `${value}-01`, end_date: localDate(new Date(year, m, 0)) }); load()
}
function openHistory(extra = {}) {
  if (!applied.value) return
  const path = assisted ? '/pages/outbound/assisted_history' : '/pages/orders/index'
  uni.navigateTo({ url: `${path}?${queryString({ ...applied.value, ...extra })}` })
}
</script>
<style src="../../styles/outboundHistory.css"></style>
<style scoped>.periods { justify-content: flex-start; margin-bottom: 18rpx; }.month-picker { line-height: 2.3; border-radius: 5px; }</style>
