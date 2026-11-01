<template>
  <view class="page">
    <view v-if="checkingPermission" class="state-card">正在读取代办出库权限…</view>
    <view v-else-if="!authorized" class="state-card">
      <view>{{ errorMessage || "当前账号无代货主出库权限。" }}</view>
      <button class="retry-button" @click="initialize">重新读取权限</button>
    </view>

    <template v-else>
      <view class="filter-card">
        <view class="filter-title">代货主出库历史 · 按代办时间</view>
        <view class="filter-grid">
          <input
            v-model.trim="filters.search"
            class="input search-input"
            placeholder="订单号、货主、收件人、商品新旧名称或条码"
            confirm-type="search"
            @confirm="reload"
          />
          <picker mode="date" :value="filters.start_date" @change="setStartDate">
            <view class="picker-field">开始：{{ filters.start_date }}</view>
          </picker>
          <picker mode="date" :value="filters.end_date" @change="setEndDate">
            <view class="picker-field">结束：{{ filters.end_date }}</view>
          </picker>
          <picker :range="ownerLabels" :value="ownerIndex" @change="setOwner">
            <view class="picker-field">{{ ownerLabels[ownerIndex] || '全部货主' }}</view>
          </picker>
          <picker :range="operatorLabels" :value="operatorIndex" @change="setOperator">
            <view class="picker-field">{{ operatorLabels[operatorIndex] || '全部操作员' }}</view>
          </picker>
          <picker :range="statusLabels" :value="statusIndex" @change="setStatus">
            <view class="picker-field">{{ statusLabels[statusIndex] || '全部状态' }}</view>
          </picker>
        </view>
        <view v-if="dirty" class="history-meta">筛选条件已修改，点击查询后生效；当前结果仍按上次查询条件。</view>
        <view class="filter-actions">
          <button class="reset-button" :disabled="loading" @click="resetFilters">重置</button>
          <button class="query-button" :disabled="loading" @click="reload">
            {{ loading ? '查询中…' : '查询' }}
          </button>
        </view>
      </view>

      <view class="result-card">
        <view class="result-head">
          <view class="filter-title">查询结果</view>
          <text class="result-count">共 {{ totalCount }} 张</text>
        </view>

        <view v-if="errorMessage" class="error-card">
          <text>{{ errorMessage }}</text>
          <button class="retry-button" @click="retry">重试</button>
        </view>
        <view v-if="loading && !rows.length" class="empty">正在查询历史出库单…</view>
        <view v-else-if="!rows.length && !errorMessage" class="empty">没有符合条件的历史出库单</view>
        <view v-if="rows.length" class="history-list">
          <view v-for="row in rows" :key="row.id" class="history-row">
            <view class="history-main">
              <view class="history-title-line">
                <text class="order-no">{{ row.order_no }}</text>
                <text :class="['status-tag', statusClass(row.business_status)]">
                  {{ row.business_status_label }}
                </text>
              </view>
              <view class="history-meta">
                {{ dateTimeText(row.assisted_at) }} · 货主 {{ row.owner?.name || '-' }} ·
                客户 {{ row.customer?.name || '-' }}
              </view>
              <view class="history-meta">
                收件人 {{ row.receiver_name || '-' }} · 商品行 {{ row.line_count || 0 }} ·
                订单基本数量 {{ qtyText(row.total_base_qty) }}
              </view>
              <view class="history-meta">
                <template v-if="row.transfer_no">
                  转货出库：{{ row.source_owner?.name }} → {{ row.owner?.name }} → {{ row.customer?.name }}
                  · 转货单 {{ row.transfer_no }} · {{ transferStatus(row.transfer_status) }}
                </template>
                <template v-else>直接出库</template>
              </view>
              <view class="history-meta">
                操作员 {{ row.assisted_by?.name || '未记录' }}
                <template v-if="row.src_bill_no"> · 源单号 {{ row.src_bill_no }}</template>
              </view>
            </view>
            <view class="history-actions">
              <button class="action-button" :disabled="!row.can_print_pick" @click="openOutboundPrintPage(row.task.id, null, 'pick')">打印拣货单</button>
              <button class="action-button" :disabled="!row.task?.id" @click="openTask(row)">
                查看任务
              </button>
              <button
                class="action-button print-button"
                :disabled="!row.can_reprint"
                @click="reprint(row)"
              >
                补打送货单
              </button>
            </view>
            <view v-if="!row.can_reprint && row.reprint_unavailable_reason" class="disabled-reason">
              {{ row.reprint_unavailable_reason }}
            </view>
          </view>
        </view>

        <button v-if="hasMore" class="more-button" :disabled="loading" @click="loadMore">
          {{ loading ? '加载中…' : '加载更多' }}
        </button>
      </view>
    </template>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { openOutboundPrintPage } from '@/utils/outboundPrint'
import { useOutboundHistory, quantity as qtyText, localDate } from '@/utils/outboundHistory'
const session = useOutboundHistory({ api, auth: useAuth(), assisted: true })
const { filters, rows, options, count: totalCount, hasMore, loading, authorized, dirty, error: errorMessage, initialize, retry } = session
const checkingPermission = computed(() => loading.value && !authorized.value)
const ownerChoices = computed(() => [{ id: '', name: '全部货主' }, ...(options.value.owners || [])])
const operatorChoices = computed(() => [{ id: '', name: '全部操作员' }, ...(options.value.operators || [])])
const statusChoices = computed(() => [{ value: '', label: '全部状态' }, ...(options.value.statuses || [])])
const ownerLabels = computed(() => ownerChoices.value.map(x => x.name || x.code))
const operatorLabels = computed(() => operatorChoices.value.map(x => x.name || x.username))
const statusLabels = computed(() => statusChoices.value.map(x => x.label))
const ownerIndex = computed(() => Math.max(0, ownerChoices.value.findIndex(x => String(x.id) === String(filters.owner_id))))
const operatorIndex = computed(() => Math.max(0, operatorChoices.value.findIndex(x => String(x.id) === String(filters.operator_id))))
const statusIndex = computed(() => Math.max(0, statusChoices.value.findIndex(x => x.value === filters.status)))
function setStartDate(e) { filters.start_date = e.detail.value }
function setEndDate(e) { filters.end_date = e.detail.value }
function setOwner(e) { filters.owner_id = ownerChoices.value[Number(e.detail.value)].id }
function setOperator(e) { filters.operator_id = operatorChoices.value[Number(e.detail.value)].id }
function setStatus(e) { filters.status = statusChoices.value[Number(e.detail.value)].value }
function reload() { return session.load() }
function loadMore() { return session.load({ more: true }) }
function resetFilters() { return session.reset() }
function dateTimeText(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : `${localDate(date)} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}
function transferStatus(status) { return { RESERVED: '已预留', POSTED: '已完成', CANCELLED: '已取消' }[status] || status }
function statusClass(value) {
  if (value === 'COMPLETED') return 'completed'
  if (['POSTING_FAILED', 'NEED_RECOUNT', 'INCONSISTENT'].includes(value)) return 'exception'
  return value === 'CANCELLED' ? 'cancelled' : 'pending'
}
function reprint(row) { if (row.can_reprint && row.task?.id) openOutboundPrintPage(row.task.id) }
function openTask(row) { if (row.task?.id) uni.navigateTo({ url: `/pages/picking/task_detail?task_id=${row.task.id}` }) }
onLoad(session.applyRoute); onShow(session.initialize); onHide(session.suspend); onUnload(session.dispose)
</script>

<style scoped>
.page { box-sizing: border-box; min-height: 100vh; padding: 18rpx; background: #f6f7fb; }
.state-card, .filter-card, .result-card { padding: 22rpx; border-radius: 16rpx; background: #fff; }
.result-card { margin-top: 18rpx; }
.filter-title { font-size: 30rpx; font-weight: 700; }
.filter-grid { display: grid; grid-template-columns: minmax(220px, 2fr) repeat(5, minmax(130px, 1fr)); gap: 12rpx; margin-top: 16rpx; }
.input, .picker-field { box-sizing: border-box; width: 100%; height: 68rpx; padding: 0 14rpx; border: 1rpx solid #d9dee7; border-radius: 9rpx; background: #fff; }
.picker-field { overflow: hidden; line-height: 68rpx; color: #475569; text-overflow: ellipsis; white-space: nowrap; }
.filter-actions, .result-head, .history-title-line, .history-actions { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; }
.filter-actions { justify-content: flex-end; margin-top: 16rpx; }
.filter-actions button, .action-button, .more-button, .retry-button { margin: 0; font-size: 23rpx; }
.reset-button { color: #475569; background: #f1f5f9; }
.query-button, .print-button { color: #fff; background: #1677ff; }
.result-count, .history-meta, .disabled-reason { color: #697386; font-size: 22rpx; }
.history-row { padding: 18rpx 0; border-bottom: 1rpx solid #e5e7eb; }
.order-no { font-size: 27rpx; font-weight: 700; }
.status-tag { padding: 5rpx 12rpx; border-radius: 999rpx; font-size: 21rpx; }
.status-tag.completed { color: #237804; background: #f0f9e8; }
.status-tag.pending { color: #ad6800; background: #fff7e6; }
.status-tag.exception, .status-tag.cancelled { color: #b42318; background: #fff1f0; }
.history-meta { margin-top: 7rpx; }
.history-actions { justify-content: flex-end; margin-top: 12rpx; }
.action-button { padding: 0 18rpx; height: 58rpx; line-height: 58rpx; color: #1677ff; background: #eef5ff; }
.action-button[disabled] { opacity: .45; }
.disabled-reason { margin-top: 8rpx; text-align: right; }
.more-button { margin-top: 18rpx; color: #1677ff; background: #eef5ff; }
.empty { padding: 60rpx 0; color: #8a94a6; text-align: center; }
.error-card { margin-top: 18rpx; padding: 18rpx; border-radius: 10rpx; color: #b42318; background: #fff1f0; }
.retry-button { margin-top: 14rpx; color: #1677ff; background: #fff; }
@media (max-width: 1023px) {
  .filter-grid { display: block; }
  .input, .picker-field { margin-top: 12rpx; }
  .history-actions { justify-content: stretch; }
  .action-button { flex: 1; }
}
</style>
