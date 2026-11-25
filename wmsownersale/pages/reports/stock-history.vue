<template>
  <view class="history-page">
    <view class="heading">
      <view><text class="title">{{ eventId ? '收发明细' : '出入库记录' }}</text><text class="muted">{{ eventId ? '实际收发数量与原始凭据' : '出库按实物复核通过，入库按实际入账统计' }}</text></view>
      <button size="mini" :disabled="loading || !allowed" @click="refresh">刷新</button>
    </view>
    <template v-if="allowed && !eventId">
      <view class="card filters">
        <view class="segments" role="group" aria-label="统计方式">
          <button v-for="grain in grains" :key="grain.value" :class="{selected: applied.grain === grain.value}" @click="apply({grain: grain.value})">{{ grain.label }}</button>
        </view>
        <text class="range-label">{{ rangeLabel }}</text>
        <text v-if="summary?.access_scope === 'personal'" class="muted">本人经手：本人创建的单据及本人收银的历史 POS，退货跟随原单；当前库存为货主共享库存。</text>
        <view class="date-range">
          <picker mode="date" :value="form.date_from" @change="form.date_from = $event.detail.value"><view class="picker-control">{{ form.date_from || '开始日期' }}</view></picker>
          <text>至</text>
          <picker mode="date" :value="form.date_to" @change="form.date_to = $event.detail.value"><view class="picker-control">{{ form.date_to || '结束日期' }}</view></picker>
          <button size="mini" @click="allDates">全部历史</button>
        </view>
        <view class="filter-grid">
          <picker :range="warehouses" range-key="name" :value="warehouseIndex" @change="form.warehouse_id = warehouses[Number($event.detail.value)].id"><view class="picker-control">{{ warehouses[warehouseIndex].name }} ▾</view></picker>
          <picker :range="categories" range-key="label" :value="categoryIndex" @change="form.category = categories[Number($event.detail.value)].value"><view class="picker-control">{{ categories[categoryIndex].label }} ▾</view></picker>
          <input v-model="form.search" placeholder="商品名称 / 编码 / SKU" @confirm="apply()" />
          <input v-model="form.document_no" placeholder="搜索单据号" @confirm="apply()" />
          <input v-model="form.batch" placeholder="搜索商品批次" @confirm="apply()" />
          <button class="primary" @click="apply()">查询</button>
        </view>
        <view v-if="applied.product_id || applied.document_key" class="context-note">
          <text>{{ applied.product_id ? '当前商品的收发历史' : '当前单据的分次收发' }}</text>
          <button size="mini" @click="apply({product_id: '', document_key: ''})">查看全部</button>
        </view>
      </view>

      <view v-if="summary" class="summary-grid">
        <button v-for="kind in ['inbound', 'outbound']" :key="kind" :class="['summary-card', kind]" @click="showCategory(kind)">
          <text class="summary-label">{{ summary.scope_label }}{{ kind === 'inbound' ? '入库' : '出库' }}</text>
          <view v-for="total in totals(kind)" :key="`${total.base_unit}-${total.currency}`" class="summary-total"><text class="big-number">{{ qty(total.qty) }}</text><text>{{ historyUnit(total) }}</text></view>
          <HistoryAmounts :amounts="(summary.amounts || []).filter(a => a.category === kind)" />
          <text v-if="totals(kind).some(t => t.posting_pending_count)" class="warning">含库存扣减异常／待入账记录</text>
          <text v-if="!totals(kind).length" class="muted">当前条件暂无记录</text>
          <text class="summary-hint">查看{{ kind === 'inbound' ? '入库' : '出库' }}商品明细 ›</text>
        </button>
      </view>
      <HistoryCharts v-if="!fromChartDetail" :key="identity" :filters="applied" :snapshot="snapshot" :as-of="summary?.as_of"
        @records="openChartRecords" @denied="revoke" />
      <view v-if="otherTotals.length" class="card">
        <text class="section-title">其他库存变化</text>
        <button v-for="total in otherTotals" :key="`${total.category}-${total.base_unit}`" class="other-row" @click="showCategory(total.category)"><text>{{ total.category_label }}</text><text>{{ qty(total.qty) }} {{ historyUnit(total) }} ›</text></button>
      </view>
      <text v-if="hasUnverified" class="warning">部分历史记录的来源、时间或批次待核对，已保留数量。</text>
      <view class="list-heading">
        <view class="view-tabs" role="group" aria-label="查看方式">
          <button v-for="tab in views" :key="tab.value" :class="{active: activeView === tab.value}" :aria-pressed="activeView === tab.value" @click="selectView(tab.value)">{{ tab.label }}</button>
        </view>
        <text v-if="applied.grain !== 'all' && applied.grain !== 'day'" class="section-title">{{ grains.find(g => g.value === applied.grain)?.label }}统计</text>
        <button size="mini" @click="apply({order: applied.order === 'desc' ? 'asc' : 'desc'})">{{ applied.order === 'desc' ? '最新在前' : '最早在前' }}</button>
      </view>
      <view class="toolbar">
        <text class="muted">共 {{ count }} 组 · 不同单位分别统计</text>
        <button v-if="canExport" size="mini" :disabled="loading || exporting || !summary" @click="download">{{ exporting ? '导出中…' : `导出${exportLabel}` }}</button>
      </view>
      <text v-if="summary" class="muted updated">数据更新：{{ historyTime(summary.as_of) }}（北京时间）</text>
      <text v-if="applied.base_unit || applied.missing_unit" class="muted">数量单位：{{ historyUnit(summary?.totals?.[0]) }}</text>
      <text v-if="applied.currency" class="muted">币种：{{ applied.currency === 'CNY' ? '人民币' : applied.currency }}</text>
      <text v-if="applied.grain === 'week'" class="muted">每周按周一至周日统计。</text>
    </template>

    <view v-if="error || exportError || photoError" class="card error-state" role="status"><text>{{ error || exportError || photoError }}</text><button v-if="allowed" size="mini" @click="retry">重试</button></view>
    <view v-if="loading && !rows.length" class="empty">正在读取库存历史…</view>
    <view v-else-if="allowed && !rows.length && !error" class="empty">当前条件下没有收发记录</view>

    <template v-if="allowed && !eventId">
      <template v-if="applied.grain !== 'all'">
        <view v-for="row in rows" :key="row.period_start || 'undated'" class="card record">
          <view class="record-head"><text class="section-title">{{ periodLabel(row) }}</text></view>
          <text v-if="row.partial_period" class="muted">实际统计：{{ row.range_start }} 至 {{ row.range_end }}</text>
          <HistoryPeriodBreakdown :row="row" :grain="applied.grain" @select="openPeriod(row, $event)" />
        </view>
      </template>
      <template v-else-if="view === 'products'">
        <button v-for="row in rows" :key="row.product_id" class="card record" @click="openProduct(row)">
          <view class="record-head"><text class="section-title">{{ row.product_name }}</text><text class="arrow">›</text></view>
          <text class="muted">{{ row.product_code }} · {{ row.product_spec || '规格未记录' }}</text>
          <HistoryQuantities :row="row" />
          <view v-for="stock in row.current_stock || []" :key="stock.base_unit" class="current-stock"><text>当前现有 {{ qty(stock.current_onhand) }} {{ historyUnit(stock) }}</text><text>当前可用 {{ qty(stock.current_available) }} {{ historyUnit(stock) }}</text></view>
          <text class="muted">最近收发：{{ historyTime(row.last_at) }}</text>
        </button>
      </template>
      <template v-else-if="view === 'documents'">
        <button v-for="row in rows" :key="`${row.document_key}-${row.warehouse_id}-${row.base_unit}`" class="card record" @click="openDocumentHistory(row)">
          <view class="record-head"><text class="section-title">{{ row.document_no || '关联单据待核对' }}</text><text class="arrow">›</text></view>
          <text class="muted">{{ row.warehouse_name }}<text v-if="row.counterparty"> · {{ row.counterparty }}</text></text>
          <HistoryQuantities :row="row" /><text class="muted record-hint">最近收发：{{ historyTime(row.last_at) }}</text>
        </button>
      </template>
      <template v-else>
        <button v-for="row in rows" :key="`${row.event_id}-${row.base_unit}`" class="card record" @click="openEvent(row)">
          <view class="record-head"><text class="section-title">{{ row.category_label }}</text><text class="arrow">›</text></view>
          <text>{{ row.document_no || '关联单据待核对' }}</text>
          <text class="muted">{{ historyTime(row.event_at) }} · {{ row.warehouse_name }}</text>
          <text class="muted">{{ row.product_count === 1 ? row.product_name : `${row.product_count} 种商品` }}</text>
          <HistoryQuantities :row="row" />
        </button>
      </template>
    </template>

    <template v-if="allowed && eventId">
      <text class="muted">以下为{{ rangeLabel }}内符合筛选条件的明细</text>
      <view v-for="row in rows" :key="row.id" class="card detail-row">
        <view class="record-head"><text class="section-title">{{ row.product_name }}</text><text class="event-qty">{{ signed(row.qty_delta) }} {{ historyUnit(row) }}</text></view>
        <text>{{ row.category_label }} · {{ row.document_no || '关联单据待核对' }}</text>
        <text class="muted">{{ row.product_code }} · {{ row.product_spec || '规格未记录' }}</text>
        <text class="muted">{{ historyTime(row.posted_at) }} · {{ row.warehouse_name }}</text>
        <text>批次：{{ row.batch_no || '未记录' }}</text>
        <text class="muted">生产：{{ row.production_date || '未记录' }} · 到期：{{ row.expiry_date || '未记录' }}</text>
        <text v-if="['inbound','outbound'].includes(row.category)">{{ row.category === 'inbound' ? '采购金额' : '销售金额' }}：{{ row.amount == null ? '金额待核对' : `${historyMoney(row.amount)} ${row.currency === 'CNY' ? '元' : row.currency}` }}</text>
        <text v-if="row.posting_status !== 'POSTED'" class="warning">库存扣减异常／待入账</text>
        <text v-if="row.dimensions_unverified" class="warning">批次明细待核对</text>
        <button v-if="row.inbound_id || row.outbound_id || row.customer_return_id" size="mini" @click="openSource(row)">查看原始单据</button>
      </view>
      <view v-for="receipt in detail?.receipts || []" :key="receipt.id" class="card">
        <text class="section-title">验收凭据 {{ receipt.number }}</text>
        <view v-for="line in receipt.lines" :key="line.id" class="evidence-line">
          <text>{{ line.product_name }} · {{ historyUnit(line) }} · 批次 {{ line.batch_no || '未记录' }}</text>
          <text v-if="line.quality_recorded !== false">良品 {{ qty(line.qty_ok) }} · 破损 {{ qty(line.qty_damage) }} · 拒收 {{ qty(line.qty_reject) }}</text>
          <text v-else class="muted">旧凭据未记录良品、破损及拒收分类</text>
          <text v-if="line.damage_reason">破损原因：{{ line.damage_reason }}</text><text v-if="line.reject_reason">拒收原因：{{ line.reject_reason }}</text>
          <view class="photos"><button v-for="photo in line.photos" :key="photo.id" size="mini" @click="preview(photo)">查看照片</button></view>
        </view>
      </view>
    </template>
    <button v-if="nextPage && allowed" class="load-more" :disabled="loading" @click="load(true)">{{ loading ? '加载中…' : '加载更多' }}</button>
    <text v-else-if="rows.length" class="empty">已加载全部记录</text>
  </view>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { onLoad, onHide } from '@dcloudio/uni-app'
import { useStockHistory } from '@/composables/useStockHistory'
import { useOwnerExport } from '@/composables/useOwnerExport'
import { historyCategories, historyMoney, historyQuery, historyTime, historyUnit, historyURL, periodFilters } from '@/utils/stockHistory'
import { formatQuantity } from '@/utils/quantity'
import { returnPhotoURL } from '@/utils/request'
import HistoryCharts from '@/components/HistoryCharts.vue'
import HistoryPeriodBreakdown from '@/components/HistoryPeriodBreakdown.vue'
import HistoryQuantities from '@/components/HistoryQuantities.vue'
import HistoryAmounts from '@/components/HistoryAmounts.vue'

const {form, applied, view, eventId, summary, detail, rows, error, loading, count, nextPage,
  snapshot, identity, allowed, canExport, revoke, configure, apply, selectView, load, refresh} = useStockHistory()
const fromChartDetail = ref(false)
onLoad((options = {}) => {
  fromChartDetail.value = !!options.snapshot && options.view === 'events'
  configure(options)
})
function openChartRecords(filters) {
  uni.navigateTo({url:historyURL(filters, 'events')})
}
const grains = [{value:'day',label:'按天'}, {value:'week',label:'按周'}, {value:'month',label:'按月'}, {value:'year',label:'按年'}]
const activeView = computed(() => applied.value.grain === 'all' ? view.value : applied.value.grain)
const views = [{value:'day',label:'按天统计'}, {value:'products',label:'按商品'}, {value:'documents',label:'按单据'}, {value:'events',label:'原始收发'}]
const categories = [{value:'',label:'全部业务类型'}, ...Object.entries(historyCategories).map(([value,label]) => ({value,label}))]
const warehouses = computed(() => [{id:'',name:'全部仓库'}, ...(summary.value?.warehouses || [])])
const warehouseIndex = computed(() => Math.max(0, warehouses.value.findIndex(w => String(w.id) === String(form.warehouse_id))))
const categoryIndex = computed(() => Math.max(0, categories.findIndex(c => c.value === form.category)))
const rangeLabel = computed(() => applied.value.undated ? '时间未记录' : applied.value.date_from || applied.value.date_to
  ? `${applied.value.date_from || '最早记录'} 至 ${applied.value.date_to || '最新记录'}` : '全部历史')
const totals = kind => (summary.value?.totals || []).filter(t => t.category === kind)
const otherTotals = computed(() => (summary.value?.totals || []).filter(t => !['inbound','outbound'].includes(t.category)))
const hasUnverified = computed(() => (summary.value?.totals || []).some(t => t.unverified_count > 0))
const qty = value => formatQuantity(value ?? '0')
const signed = value => String(value).startsWith('-') ? qty(value) : `+${qty(value)}`
const exportView = computed(() => applied.value.grain === 'all' ? view.value : 'periods')
const exportLabel = computed(() => ({periods:'周期汇总',products:'商品汇总',documents:'单据汇总',events:'原始明细'})[exportView.value])
const {exporting, exportError, exportExcel} = useOwnerExport(() => JSON.stringify([identity.value, applied.value, view.value, snapshot.value]), {allowed: () => canExport.value, onDenied: revoke})
function download() { exportExcel('/api/inventory/owner-history/statistics/export/', {...historyQuery(applied.value), view: exportView.value, snapshot: snapshot.value}, exportLabel.value) }
function allDates() { delete form.undated; apply({date_from:'',date_to:''}) }
function showCategory(category) { view.value = 'products'; apply({category,grain:'all'}) }
function jump(filters, chosenView = 'events', id) { uni.navigateTo({url: historyURL({...filters,snapshot:snapshot.value}, chosenView, id)}) }
function openProduct(row) { jump({...applied.value,product_id:row.product_id,grain:'all'}) }
function openDocumentHistory(row) { jump({...applied.value,document_key:row.document_key,grain:'all'}) }
function openEvent(row) { jump(applied.value,'events',row.event_id) }
function openPeriod(row, category) { jump({ ...periodFilters(applied.value, row), category }, 'events') }
function periodLabel(row) {
  if (!row.period_start) return '时间未记录'
  if (applied.value.grain === 'year') return `${row.period_start.slice(0,4)} 年`
  if (applied.value.grain === 'month') return row.period_start.slice(0,7)
  return applied.value.grain === 'week' ? `${row.period_start} 至 ${row.period_end}` : row.period_start
}
function openSource(row) {
  const path = row.customer_return_id ? `/pages/returns/index?id=${row.customer_return_id}`
    : row.inbound_id ? `/pages/inbound/index?id=${row.inbound_id}` : `/pages/orders/detail?id=${row.outbound_id}`
  uni.navigateTo({url:path})
}
const photoError = ref('')
let photoGeneration = 0
let lastPhoto = null
const stopPhotos = () => { photoGeneration++ }
onHide(stopPhotos)
onUnmounted(stopPhotos)
watch(identity, () => { photoGeneration++; lastPhoto = null; photoError.value = '' })
function retry() {
  if (error.value) return load(error.value.startsWith('加载更多'))
  if (exportError.value) return download()
  if (photoError.value && lastPhoto) return preview(lastPhoto)
}
async function preview(photo) {
  lastPhoto = photo
  photoError.value = ''; const tag = ++photoGeneration; const user = identity.value
  try {
    const image = await returnPhotoURL(photo.url)
    if (tag !== photoGeneration || user !== identity.value || !allowed.value) { image.revoke?.(); return }
    uni.previewImage({urls:[image.url || image],complete:() => image.revoke?.()})
  } catch { if (tag === photoGeneration && user === identity.value) photoError.value = '照片加载失败，请重试' }
}
</script>

<style scoped>
.history-page { max-width: 960px; margin: 0 auto; padding: 18px 16px 40px; box-sizing: border-box; min-height: 100vh; color: #1f2937; background: #f5f7fb; font-size: 15px; }
.heading,.record-head,.list-heading,.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.heading { margin-bottom: 18px; }.title { display: block; font-size: 24px; font-weight: 700; margin-bottom: 6px; }
.muted { display: block; font-size: 13px; color: #64748b; line-height: 1.6; }.section-title { font-size: 17px; font-weight: 650; overflow-wrap: anywhere; }
button { font-size: 14px; border-radius: 8px; margin: 0; }button::after { border: 0; }.heading button { flex-shrink: 0; }
.card { display: block; background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; margin-bottom: 14px; box-sizing: border-box; text-align: left; line-height: 1.6; width: 100%; overflow-wrap: anywhere; }
.segments { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 8px; margin-bottom: 16px; }.segments button { padding: 0 6px; color: #475569; background: #f1f5f9; }.segments .selected,.primary { color: white; background: #2563eb; }
.range-label { display: block; margin-bottom: 10px; font-size: 14px; color: #334155; }.date-range { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }.date-range picker { flex: 1; min-width: 108px; }
.picker-control,input { box-sizing: border-box; min-height: 40px; border: 1px solid #cbd5e1; border-radius: 7px; padding: 10px; font-size: 14px; background: white; }.filter-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 10px; }.filter-grid input { height: 42px; width: 100%; }
.context-note { margin-top: 12px; display: flex; justify-content: space-between; align-items: center; gap: 10px; color: #2563eb; font-size: 13px; }
.summary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 18px 0; }.summary-card { text-align: left; padding: 18px; border: 1px solid #cde8dd; background: #f0faf5; line-height: 1.5; width: 100%; border-radius: 12px; }.summary-card.outbound { background: #eff5ff; border-color: #d3e1fa; }.summary-label { display: block; color: #475569; font-size: 14px; }.summary-total { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; margin: 8px 0; font-size: 13px; }.big-number { font-size: 28px; font-weight: 750; color: #166448; overflow-wrap: anywhere; max-width: 100%; }.outbound .big-number { color: #2456a9; }.count { color: #64748b; }.summary-hint { display: block; font-size: 12px; color: #52647b; margin-top: 12px; }
.other-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 10px 0; text-align: left; background: white; font-size: 14px; line-height: 1.7; }.other-row text:last-child { font-weight: 600; }
.warning { display: block; color: #a2530c; font-size: 13px; margin: 10px 0; line-height: 1.7; }.list-heading { margin-top: 24px; flex-wrap: wrap; }.view-tabs { display: flex; flex-wrap: wrap; gap: 5px; }.view-tabs button { font-size: 14px; padding: 0 12px; background: transparent; color: #64748b; }.view-tabs .active { color: #2456a9; background: #e8f0fe; font-weight: 650; }
.toolbar { margin: 12px 0; flex-wrap: wrap; }.updated { margin-bottom: 14px; }.arrow { font-size: 22px; color: #8190a3; }.record-head { margin-bottom: 6px; align-items: baseline; }.record-hint { margin-top: 12px; }.record > text { display: block; }.event-qty { color: #2456a9; font-size: 18px; font-weight: 650; overflow-wrap: anywhere; }.current-stock { display: flex; gap: 12px; flex-wrap: wrap; border-top: 1px solid #edf2f7; margin: 14px 0 8px; padding-top: 12px; color: #64748b; font-size: 13px; }.sequence { display: block; color: #52647b; font-size: 13px; margin-top: 8px; }
.detail-row > text,.evidence-line > text { display: block; margin-top: 8px; }.detail-row button { margin-top: 12px; }.evidence-line { margin-top: 14px; padding-top: 12px; border-top: 1px solid #edf2f7; }.photos { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.error-state { color: #a62338; background: #fff1f2; }.error-state button { margin-top: 10px; }.empty { display: block; text-align: center; color: #64748b; padding: 26px 0; font-size: 14px; }.load-more { margin: 18px auto; width: 180px; color: #2456a9; background: white; }
@media(max-width:540px) { .history-page { padding: 14px 12px 28px; }.filter-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }.card { padding: 14px; }.summary-grid { gap: 10px; }.summary-card { padding: 14px; }.big-number { font-size: 25px; }.event-qty { font-size: 16px; }.view-tabs button { padding: 0 10px; } }
</style>
