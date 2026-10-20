<template>
  <view class="inventory-detail">
    <view v-if="header" class="card product">
      <text class="title">{{ header.product.name }}</text>
      <text class="muted">编码：{{ header.product.code || '未记录' }} · 规格：{{ header.product.spec || '未记录' }}</text>
      <text>单位：{{ header.product.base_unit || '未记录' }}</text>
    </view>
    <view class="tabs">
      <button :class="{ selected: tab === 'detail' }" @click="select('detail')">库存明细</button>
      <button :class="{ selected: tab === 'transactions' }" @click="select('transactions')">库存流水</button>
    </view>
    <view v-if="!denied" class="card filters">
      <picker :range="warehouseOptions" range-key="name" :value="warehouseIndex" @change="form.warehouse_id = warehouseOptions[Number($event.detail.value)].id">
        <view class="picker-control">仓库：{{ warehouseOptions[warehouseIndex]?.name }} ▾</view>
      </picker>
      <input v-model="form.batch" class="input" placeholder="搜索批次号" @confirm="search" />
      <picker v-if="tab === 'detail'" :range="expiryOptions" range-key="label" :value="expiryOptions.findIndex(v => v.value === form.expiry)" @change="form.expiry = expiryOptions[Number($event.detail.value)].value">
        <view class="picker-control">效期：{{ expiryOptions.find(v => v.value === form.expiry)?.label }} ▾</view>
      </picker>
      <template v-else>
        <picker :range="typeOptions" range-key="label" :value="typeOptions.findIndex(v => v.value === form.tx_type)" @change="form.tx_type = typeOptions[Number($event.detail.value)].value">
          <view class="picker-control">类型：{{ typeOptions.find(v => v.value === form.tx_type)?.label }} ▾</view>
        </picker>
        <view class="date-range">
          <picker mode="date" :value="form.date_from" @change="form.date_from = $event.detail.value"><view class="picker-control">{{ form.date_from || '开始日期' }}</view></picker>
          <text>至</text>
          <picker mode="date" :value="form.date_to" @change="form.date_to = $event.detail.value"><view class="picker-control">{{ form.date_to || '结束日期' }}</view></picker>
        </view>
      </template>
      <view class="actions">
        <button class="search" size="mini" @click="search">筛选</button>
        <button size="mini" @click="reset">重置</button>
        <button class="refresh" size="mini" :disabled="state.loading" @click="load()">刷新</button>
        <button v-if="tab === 'detail' && canExport" size="mini" :disabled="exporting || state.loading || !header" @click="exportExcel(`/api/inventory/summary/${id}/detail/export/`, sections.detail.filters, '库存批次明细')">{{ exporting ? '正在导出…' : '导出 Excel' }}</button>
      </view>
      <text v-if="validationError" class="error">{{ validationError }}</text>
      <text v-if="exportError" class="error">{{ exportError }}</text>
    </view>
    <text v-if="state.data" class="updated">最近更新：{{ formatTime(state.data.as_of) }}</text>
    <view v-if="tab === 'detail' && state.data" class="card">
      <text class="section-title">筛选范围库存</text>
      <text class="muted formula">可用＝现有－分配－锁定－残次</text>
      <view v-for="total in state.data.totals" :key="total.base_unit" class="totals">
        <text>单位：{{ total.base_unit || '未记录' }}</text>
        <view class="quantity-grid"><view v-for="field in qtyFields" :key="field.key"><text class="muted">{{ field.label }}</text><text class="quantity">{{ qty(total[field.key]) }}</text></view></view>
      </view>
      <text v-if="state.data.unit_mismatch" class="warning">存在单位差异，已按单位分别展示，请核对。</text>
    </view>
    <view v-if="tab === 'transactions'" class="notice">
      流水记录实际库存增减；出库扣减不代表已发运。占用、锁定变化不在本流水内。
      <text v-if="state.filters.date_from || state.filters.date_to">日期筛选不包含过账时间未记录的流水。</text>
    </view>
    <view v-if="state.error" class="card error-state">
      <text class="error">{{ state.error }}</text>
      <button v-if="!denied" class="retry" size="mini" @click="load(tab, !!state.next && state.error.startsWith('加载失败'))">重试</button>
    </view>
    <view v-if="state.loading && !state.data" class="empty">加载中…</view>
    <view v-else-if="state.data && !state.rows.length" class="empty">{{ tab === 'detail' ? '当前筛选下暂无库存' : '当前筛选下暂无流水' }}</view>
    <template v-if="tab === 'detail'">
      <view v-for="(row, index) in state.rows" :key="index" class="card stock-row">
        <text class="section-title">{{ row.warehouse_name }}</text>
        <text>批次：{{ row.batch_no || '未记录' }}</text>
        <view class="dates"><text>生产日期：{{ row.production_date || '未记录' }}</text><text>有效期至：{{ row.expiry_date || '未记录' }}</text></view>
        <text class="muted">单位：{{ row.base_unit || '未记录' }}</text>
        <view class="quantity-grid"><view v-for="field in qtyFields" :key="field.key"><text class="muted">{{ field.label }}</text><text class="quantity">{{ qty(row[field.key]) }}</text></view></view>
      </view>
    </template>
    <template v-else>
      <view v-for="row in state.rows" :key="row.id" class="card transaction-row">
        <view class="transaction-heading"><text class="section-title">{{ row.type_label }}</text><text :class="['delta', row.qty_delta.startsWith('-') ? 'negative' : 'positive']">{{ signed(row.qty_delta) }} {{ row.base_unit }}</text></view>
        <text class="muted">{{ row.posted_at ? formatTime(row.posted_at) : '过账时间未记录' }}</text>
        <text>{{ row.warehouse_name }} · 批次：{{ row.batch_no || '未记录' }}</text>
        <text class="muted">生产日期：{{ row.production_date || '未记录' }} · 有效期至：{{ row.expiry_date || '未记录' }}</text>
        <text :class="{ 'document-link': row.document.order_id || row.document.return_id }" @click="openDocument(row.document)">{{ row.document.label }}</text>
        <text class="reason">说明：{{ row.reason }}</text>
        <text v-if="row.movement_note" class="notice">{{ row.movement_note }}</text>
        <text v-if="row.unit_mismatch" class="warning">与当前商品单位不同，请核对。</text>
      </view>
    </template>
    <button v-if="state.next && !denied" class="load-more" :disabled="state.loading" @click="load(tab, true)">{{ state.loading ? '加载中…' : '加载更多' }}</button>
    <text v-else-if="state.data && state.rows.length" class="empty">已加载全部{{ tab === 'detail' ? '库存' : '流水' }}</text>
  </view>
</template>

<script setup>
import { formatQuantity } from '@/utils/quantity'
import { computed, onUnmounted, reactive, ref } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useInventoryDetail } from '@/composables/useInventoryDetail'
import { useInventoryAccess } from '@/composables/useInventoryAccess'
import { useOwnerExport } from '@/composables/useOwnerExport'

const { id, tab, header, denied, sections, setId, show, stop, select, apply, load } = useInventoryDetail()
const { canExport, identity, revoke } = useInventoryAccess()
const { exporting, exportError, exportExcel } = useOwnerExport(() => JSON.stringify([id.value, tab.value, denied.value, sections.detail.filters, identity.value]), {allowed: () => canExport.value, onDenied: revoke})
const blank = () => ({ warehouse_id: '', batch: '', expiry: '', tx_type: '', date_from: '', date_to: '' })
const forms = reactive({ detail: blank(), transactions: blank() })
const form = computed(() => forms[tab.value])
const state = computed(() => sections[tab.value])
const validationError = ref('')
const warehouseOptions = computed(() => [{ id: '', name: '全部仓库' }, ...(header.value?.warehouses || [])])
const warehouseIndex = computed(() => Math.max(0, warehouseOptions.value.findIndex(w => w.id === form.value.warehouse_id)))
const expiryOptions = [{ value: '', label: '全部' }, { value: 'expired', label: '已过期' }, { value: 'valid', label: '未过期' }, { value: 'unknown', label: '未记录' }]
const typeOptions = Object.entries({ '': '全部', RECEIVE: '入库', ISSUE: '出库（库存扣减）', ADJ_GAIN: '调整增加', ADJ_LOSS: '调整减少', MOVE_IN: '移入', MOVE_OUT: '移出', OWNER_XFER_IN: '货权转入', OWNER_XFER_OUT: '货权转出' }).map(([value, label]) => ({ value, label }))
const qtyFields = [{ key: 'onhand_qty', label: '现有' }, { key: 'available_qty', label: '可用' }, { key: 'allocated_qty', label: '分配' }, { key: 'locked_qty', label: '锁定' }, { key: 'damaged_qty', label: '残次' }]
function qty(value) { return formatQuantity(value ?? '0') }
function signed(value) { const v = qty(value); return v.startsWith('-') ? v : `+${v}` }
function formatTime(value) { return value ? value.replace('T', ' ').replace(/\.\d+/, '').replace(/\+08:00$/, '（北京时间）') : '时间未记录' }
function search() {
  validationError.value = ''
  if (tab.value === 'transactions' && form.value.date_from && form.value.date_to && form.value.date_from > form.value.date_to) {
    validationError.value = '开始日期不能晚于结束日期'; return
  }
  apply(tab.value, { ...form.value })
}
function reset() { forms[tab.value] = blank(); search() }
function openDocument(doc) { if (doc.return_id) { uni.navigateTo({url: `/pages/returns/index?id=${doc.return_id}`}); return } if (doc.order_id) uni.navigateTo({ url: `/pages/orders/detail?id=${doc.order_id}` }) }
onLoad((options) => setId(options.id))
onShow(show)
onHide(stop)
onUnload(stop)
onUnmounted(stop)
</script>

<style scoped>
.inventory-detail { max-width: 960px; margin: 0 auto; padding: 16px 14px 32px; color: #1f2937; background: #f5f7fa; min-height: 100vh; box-sizing: border-box; }
.card { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 8px; overflow-wrap: anywhere; }
.title { font-size: 20px; font-weight: 700; }
.muted, .updated { color: #6b7280; font-size: 13px; }
.tabs { display: flex; gap: 10px; margin: 16px 0; }
.tabs button { flex: 1; font-size: 15px; background: white; color: #4b5563; }
.tabs button.selected, .search { background: #2563eb; color: white; }
.picker-control, .input { border: 1px solid #d1d5db; border-radius: 8px; padding: 10px; font-size: 14px; height: auto; min-height: 20px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.actions button { margin: 0; }
.actions .refresh { margin-left: auto; }
.date-range { display: flex; align-items: center; gap: 8px; }
.date-range picker { flex: 1; }
.updated { display: block; margin: 12px 0; }
.section-title { font-size: 16px; font-weight: 600; }
.quantity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px 8px; margin-top: 8px; }
.quantity-grid view { display: flex; flex-direction: column; gap: 4px; }
.quantity { font-weight: 600; font-size: 17px; overflow-wrap: anywhere; }
.totals + .totals { border-top: 1px solid #e5e7eb; padding-top: 12px; }
.dates { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 13px; color: #6b7280; }
.notice { color: #64748b; font-size: 13px; line-height: 1.7; margin-bottom: 12px; display: block; }
.notice text { display: block; }
.warning { color: #92400e; font-size: 13px; }
.error { color: #b91c1c; font-size: 14px; }
.empty { display: block; padding: 20px 0; text-align: center; color: #6b7280; font-size: 14px; }
.transaction-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; }
.delta { font-size: 18px; font-weight: 700; overflow-wrap: anywhere; }
.negative { color: #b45309; }.positive { color: #047857; }
.document-link { color: #2563eb; text-decoration: underline; }
.reason { line-height: 1.6; font-size: 14px; }.load-more { font-size: 14px; }
@media (min-width: 640px) { .quantity-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); } }
</style>
