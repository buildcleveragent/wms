<template>
  <view class="history-page">
    <view class="history-card">
      <view class="history-head"><text class="history-title">全部出库订单历史</text><button class="history-button" @click="openStats">统计</button></view>
      <text class="history-muted">按订单业务日期查询 · 包含权限范围内的普通和代货主订单</text>
      <OutboundHistoryFilters :model-value="filters" :options="options" @update:model-value="Object.assign(filters, $event)" @query="load()" />
      <text v-if="dirty" class="history-muted">筛选条件已修改，点击查询后生效；当前结果及加载更多仍按上次查询条件。</text>
      <view class="history-actions"><button class="history-button" @click="reset">重置</button><button class="history-button primary" :disabled="!authorized" @click="load()">查询</button></view>
    </view>
    <view v-if="error" class="history-error">{{ error }}<button class="history-button" @click="retry">重试</button></view>
    <view class="history-card">
      <view class="history-head"><text>查询结果：{{ count }} 张</text><text v-if="loading" class="history-muted">加载中…</text></view>
      <text class="history-muted">{{ periodLabel }}</text>
      <view v-for="row in rows" :key="row.id" class="history-row order-history-row" @click="openDetail(row)">
        <view class="history-head"><text class="history-order-no">{{ row.order_no }}</text><text class="history-badge">{{ row.processing_mode_label }}</text></view>
        <text class="history-meta order-context">{{ row.biz_date }} · {{ row.outbound_type_label }} · {{ row.warehouse_name }}</text>
        <text class="history-meta order-parties">货主 {{ row.owner_name }} · {{ row.supplier_name ? '供应商 ' + row.supplier_name : '客户 ' + (row.customer_name || '-') }}</text>
        <text class="history-meta order-status">{{ row.submit_status_name }} · {{ row.approval_status_name }} · {{ row.is_closed ? '已关闭' : '未关闭' }}</text>
        <text class="history-meta order-quantity">商品行 {{ row.line_count }} · 订单基本数量 {{ quantity(row.total_base_qty) }}</text>
        <text class="history-link">查看详情 ›</text>
      </view>
      <view v-if="!loading && !rows.length && !error" class="history-empty">暂无符合条件的订单</view>
      <button v-if="hasMore" class="history-button more" :disabled="loading" @click="load({ more: true })">加载更多</button>
    </view>
  </view>
</template>
<script setup>
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import OutboundHistoryFilters from '@/components/OutboundHistoryFilters.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useOutboundHistory, queryString, quantity } from '@/utils/outboundHistory'
const session = useOutboundHistory({ api, auth: useAuth() })
const { filters, applied, rows, options, count, hasMore, loading, error, authorized, dirty, periodLabel, load, reset, retry } = session
onLoad(session.applyRoute); onShow(session.initialize); onHide(session.suspend); onUnload(session.dispose)
function openDetail(row) { uni.navigateTo({ url: `/pages/orders/detail?id=${row.id}` }) }
function openStats() { uni.navigateTo({ url: `/pages/orders/stats?${queryString(applied.value || {})}` }) }
</script>
<style src="../../styles/outboundHistory.css"></style>
<style scoped>
@media (min-width: 1024px) {
  .history-page .order-history-row {
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(0, 1.3fr) minmax(0, 1.7fr) minmax(0, 1.25fr) minmax(0, 1.1fr) max-content max-content;
    grid-template-areas: "number context parties status quantity mode detail";
    align-items: center;
    gap: 12px;
    padding: 12px 0;
    line-height: 1.5;
  }
  .order-history-row .history-head { display: contents; }
  .order-history-row .history-order-no { grid-area: number; font-size: 14px; }
  .order-history-row .order-context { grid-area: context; }
  .order-history-row .order-parties { grid-area: parties; }
  .order-history-row .order-status { grid-area: status; }
  .order-history-row .order-quantity { grid-area: quantity; }
  .order-history-row .history-badge { grid-area: mode; font-size: 12px; }
  .order-history-row .history-link { grid-area: detail; font-size: 13px; }
  .history-page .order-history-row .history-meta {
    min-width: 0;
    margin: 0;
    font-size: 13px;
    line-height: inherit;
  }
}
</style>
