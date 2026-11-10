<template>
  <view class="page drill-page">
    <view class="head">
      <view class="title">{{ title }}</view>
      <view class="muted">{{ scopeLabel }} · 约 30 秒检查更新</view>
      <view v-if="route.type === 'tasks'" class="toolbar">
        <button v-for="item in taskModes" :key="item.key" size="mini" @click="setMode(item.key)">{{ item.label }}</button>
      </view>
      <view v-if="route.type === 'collection'" class="toolbar">
        <button size="mini" @click="setMode('allocations')">收款核销</button><button size="mini" @click="setMode('activities')">催收历史</button>
        <button size="mini" @click="openActivity({ kind: 'bill', id: route.id }, params)">账单明细</button>
      </view>
      <view class="toolbar">
        <input v-if="['operations', 'tasks'].includes(route.type)" v-model="searchDraft" placeholder="单号查询" @confirm="searchNow" />
        <button v-if="['operations', 'tasks'].includes(route.type)" size="mini" @click="searchNow">查询</button>
        <button size="mini" @click="refresh">刷新</button>
      </view>
      <view class="muted">最后成功同步 {{ updatedAt || '等待同步' }} · {{ data?.count || 0 }} 条</view>
    </view>
    <view v-if="pending" class="notice" @click="apply">有更新，点击刷新当前内容</view>
    <view v-if="error" class="notice">{{ error.message || '同步失败，保留上次结果。' }}</view>
    <view v-for="notice in data?.notices || []" :key="notice" class="notice">{{ notice }}</view>
    <view v-if="data?.meta?.data_status === 'UNAVAILABLE'" class="notice">所选历史日期没有可用明细。</view>
    <button v-if="data?.sourceTarget" @click="openActivity(data.sourceTarget, params)">查看来源单据与作业进度</button>
    <button v-if="data?.related" @click="drill(data.related)">{{ data.related.label }}</button>
    <view v-if="data?.sources" class="toolbar"><button v-for="source in data.sources" :key="source.value" size="mini" @click="setSource(source.value)">{{ source.label }}</button></view>
    <view v-if="loading" class="muted">正在读取明细…</view>
    <view v-else-if="!data?.results?.length && !error" class="empty">当前范围暂无记录</view>
    <view v-for="row in data?.results || []" :key="row.id" class="card" @click="row.drill ? drill(row.drill) : openRow(row)">
      <view class="row-title">{{ row.title }} <text v-if="row.target || row.drill">›</text></view>
      <view v-for="field in row.fields" :key="field.label" class="field"><text>{{ field.label }}</text><text>{{ fieldText(field.value) }}</text></view>
    </view>
    <view class="toolbar pager">
      <button :disabled="page <= 1" size="mini" @click="page--">上一页</button>
      <text>第 {{ page }} 页</text>
      <button :disabled="page * 20 >= (data?.count || 0)" size="mini" @click="page++">下一页</button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api, buildQuery } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
import { useLiveResource } from '@/utils/use-live-resource'
import { openActivity, fieldText } from '@/utils/activity'

const route = ref({})
const ready = ref(false)
const page = ref(1)
const mode = ref('created')
const source = ref('events')
const search = ref('')
const searchDraft = ref('')
const auth = useAuth()
const scope = useBossScope()
const taskModes = [{ key: 'created', label: '期间新增' }, { key: 'completed', label: '期间完成' }, { key: 'backlog', label: '当前积压' }]
const titles = { today: '今日业务明细', sla: 'SLA 指标明细', attention: '需要立即关注', periods: '周期统计明细', tasks: '作业任务明细', operations: '运营流水明细', receivables: '应收账单', collection: '回款与催收历史', risk: '库存风险明细', case: '异常处理历史', assurance: '收入保障明细' }
const title = computed(() => titles[route.value.type] || '业务明细')
const params = computed(() => ({ ...scope.params, ...route.value, page: page.value, page_size: 20,
  mode: mode.value, source: source.value, search: search.value, order_no: search.value, details: 1 }))
const scopeLabel = computed(() => `${params.value.date_from || ''} 至 ${params.value.date_to || ''}`)
const fields = values => Object.entries(values).filter(([, value]) => value !== undefined).map(([label, value]) => ({ label, value }))
async function load() {
  const p = { ...params.value }
  const type = route.value.type
  if (type === 'today') return api.bossTodayDetails(p)
  if (type === 'sla') return api.bossSlaDetails(p)
  if (type === 'periods') return api.bossPeriodDetails(p)
  if (type === 'attention') {
    const value = await api.bossAttention({ warehouse: p.warehouse, owner: p.owner, page: page.value, page_size: 20 })
    return { ...value, notices: value.checks?.some(row => row.stale) ? ['超时检查尚未完成或已延迟，列表可能不完整。'] : [], results: value.results.map(row => ({ id: row.id, title: row.title, drill: { type: 'case', id: row.id },
      fields: fields({ 单号: row.record_no, 仓库: row.warehouse_name, 货主: row.owner_name, 严重程度: row.severity, 责任人: row.assignee_name, 业务期限: row.business_due_at || '未设期限', 处理期限: row.due_at || '未设期限', 处理超期: row.handling_overdue ? '是' : '否', 最新进展: row.latest_note || row.latest_action }) })) }
  }
  if (type === 'tasks') return api.bossTasks(p)
  if (type === 'assurance') return api.bossAssuranceDetails(route.value.section, p)
  if (type === 'operations') {
    const value = await api.bossOperationsDetails(p)
    return { ...value, results: value.results.map(row => ({ id: row.detail_id, title: `${row.order_no || row.task_no} · ${row.product?.display_name || row.product?.name || ''}`,
      fields: fields({ 时间: row.event_at, 仓库: row.warehouse?.name, 货主: row.owner?.name, 单据状态: row.status, 操作人: row.operator,
        商品编码: row.product?.code, 批次: row.lot_no, 实际数量: row.actual_qty, 计划数量: row.planned_qty, 基本单位: row.base_uom || '未记录', 库位: row.location?.code }),
      target: row.task_id ? { kind: 'task_detail', id: row.task_id } : null })) }
  }
  if (type === 'receivables') {
    const value = await api.bossReceivableBills(p)
    return { ...value, results: value.results.map(row => ({ id: row.id, title: row.invoice_no, drill: { type: 'collection', id: row.id },
      fields: fields({ 货主: row.owner_name, 币种: row.currency, 账单金额: row.total, 已收: row.paid_amount, 未收: row.outstanding_amount, 到期日: row.due_date, 账龄: row.aging_band }) })) }
  }
  if (type === 'risk') {
    const value = await api.bossInventoryRiskDetails(p)
    return { ...value, results: value.results.map(row => ({ id: row.id, title: row.product_display_name || row.product_name,
      fields: fields({ 库位: row.location_code, 批次: row.batch_no, 剩余数量: row.remaining_qty, 单位: row.base_unit?.name, 库龄天数: row.age_days,
        有效期: row.expiry_date, 成本币种: row.cost_currency, 单位成本: row.unit_cost, 成本质量: row.cost_quality }) })) }
  }
  if (type === 'collection') {
    const value = await api.bossCollectionHistory(route.value.id, p)
    const collection = mode.value === 'activities'
    const rows = collection ? value.collection_case?.activities || [] : value.allocations || []
    return { ...value, count: collection ? value.activities_count : value.allocations_count, results: rows.map(row => ({ id: row.id,
      title: collection ? row.result : row.receipt_no,
      fields: collection ? fields({ 联系时间: row.contacted_at, 渠道: row.channel, 备注: row.note, 下次跟进: row.next_follow_up_at })
        : fields({ 收款日期: row.receipt_date, 金额: row.amount, 币种: value.currency, 反向核销: row.is_reversal }) })) }
  }
  if (type === 'case') {
    const value = await api.bossAlertCaseDetail(route.value.id, p)
    return { ...value, sourceTarget: value.case.source_target, count: value.history_count, notices: [`${value.case.title} · 当前状态 ${value.case.status}`, `责任人：${value.case.assignee_name || '未分派'} · 处理期限：${value.case.due_at || '未设期限'}`, `业务期限：${value.case.business_due_at || '未设期限'} · ${value.case.handling_overdue ? '处理已超期' : ''}`, `任务执行：${value.case.operators?.map(row => row.name).join('、') || '未分派'} · 最新进展：${value.case.latest_note || value.case.latest_action || '暂无记录'}`],
      results: value.case.history.map(row => ({ id: row.id, title: row.action,
        fields: fields({ 时间: row.created_at, 操作人: row.created_by_name || row.created_by_id, 原状态: row.from_status, 当前状态: row.to_status, 备注: row.note }) })) }
  }
  throw new Error('未知明细类型')
}
const { data, pending, error, loading, updatedAt, refresh, apply } = useLiveResource(load,
  { key: () => JSON.stringify([auth.user?.id, params.value]), enabled: () => ready.value && !!auth.access, hold: () => true })
function searchNow() { page.value = 1; search.value = searchDraft.value.trim() }
function setMode(value) { page.value = 1; mode.value = value }
function setSource(value) { page.value = 1; source.value = value }
function drill(value) {
  const query = buildQuery({ ...params.value, ...value, page: 1 })
  uni.navigateTo({ url: `/pages/cockpit/details?${query}` })
}
function openRow(row) { openActivity(row.target, params.value) }
onLoad(async options => {
  if (!auth.ensureAuth()) { uni.reLaunch({ url: '/pages/login' }); return }
  route.value = options
  mode.value = options.mode || (options.type === 'collection' ? 'allocations' : 'created')
  try { ready.value = !!await scope.loadContext(auth.user) } catch (err) { error.value = err }
})
</script>

<style scoped>
.drill-page { padding: 24rpx; max-width: 1350px; margin: auto; }
.head, .card { background: white; border-radius: 20rpx; padding: 26rpx; margin-bottom: 20rpx; }
.title { font-size: 38rpx; font-weight: 800; }
.row-title { font-size: 29rpx; color: #174a8e; font-weight: 700; overflow-wrap: anywhere; }
.muted { color: #708098; font-size: 23rpx; margin: 12rpx 0; }
.field { display: flex; justify-content: space-between; gap: 16rpx; font-size: 25rpx; line-height: 1.8; }
.field text:first-child { color: #687a91; flex-shrink: 0; }
.field text:last-child { text-align: right; overflow-wrap: anywhere; min-width: 0; }
.toolbar { display: flex; flex-wrap: wrap; gap: 12rpx; margin: 18rpx 0; align-items: center; }
.toolbar input { background: #eef3f9; padding: 16rpx; border-radius: 12rpx; }
.notice { padding: 20rpx; border-radius: 14rpx; background: #fff3d5; color: #7c5d27; font-size: 24rpx; margin-bottom: 18rpx; }
.empty { padding: 30rpx; color: #748297; }
.pager { justify-content: flex-end; }
</style>
