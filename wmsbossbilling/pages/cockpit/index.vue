<template>
  <view class="page">
    <boss-nav active="cockpit" />
    <boss-scope-filter @change="refresh" />
    <scroll-view scroll-x class="tabs">
      <view class="tab-track">
        <view v-for="item in tabs" :key="item.key" class="tab" :class="{ active: tab === item.key }" @click="selectTab(item.key)">{{ item.label }}</view>
      </view>
    </scroll-view>
    <boss-data-status :meta="payload?.meta" :error="error" />
    <view class="toolbar">
      <text class="asof">数据截至 {{ payload?.meta?.generated_at || '-' }}</text>
      <button size="mini" class="snapshot" @click="createSnapshot">创建例会快照</button>
      <button v-if="tab === 'operations'" size="mini" class="snapshot" @click="exportOperations">导出运营明细</button>
      <button v-else-if="tab !== 'cases'" size="mini" class="snapshot" @click="exportCurrent">导出当前报表</button>
    </view>

    <view v-if="loading" class="empty">正在加载...</view>
    <template v-else-if="tab === 'assurance'">
      <view class="grid">
        <view v-for="(section, key) in payload?.sections || {}" :key="key" class="card">
          <text class="label">{{ assuranceLabels[key] || key }}</text>
          <text class="value">{{ section.count || 0 }}</text>
        </view>
      </view>
    </template>
    <template v-else-if="tab === 'receivables'">
      <view v-for="row in payload?.outstanding_by_currency || []" :key="row.currency" class="wide-card">
        <text class="label">未收余额 · {{ row.currency }}</text>
        <text class="money">{{ money(row.total, row.currency) }}</text>
        <text class="minor">不含税 {{ money(row.subtotal, row.currency) }} · 税额 {{ money(row.tax_total, row.currency) }}</text>
      </view>
      <view v-for="row in payload?.dso_by_currency || []" :key="`dso-${row.currency}`" class="wide-card">
        <text class="label">滚动90天 DSO · {{ row.currency }}</text>
        <text class="value">{{ row.days == null ? '不可用' : `${Number(row.days).toFixed(1)} 天` }}</text>
        <text class="minor">分子 {{ money(row.outstanding, row.currency) }} / 90天开票 {{ money(row.issued_90_days, row.currency) }}</text>
      </view>
    </template>
    <template v-else-if="tab === 'operations'">
      <view class="grid">
        <view v-for="(row, direction) in payload?.operations?.summary || {}" :key="direction" class="card">
          <text class="label">{{ direction === 'inbound' ? '入库' : '出库' }}实际</text>
          <text class="value">{{ row.orders || 0 }} 单</text>
          <text class="minor">{{ row.lines || 0 }} 行（数量请在按基本单位分组的明细中查看）</text>
        </view>
      </view>
      <view class="wide-card">
        <text class="label">订单级 SLA / OTIF</text>
        <view class="line"><text>准时率</text><text>{{ rate(payload?.sla?.on_time_rate) }}</text></view>
        <view class="line"><text>齐套率</text><text>{{ rate(payload?.sla?.in_full_rate) }}</text></view>
        <view class="line"><text>OTIF</text><text>{{ rate(payload?.sla?.otif_rate) }}</text></view>
        <view class="line"><text>SLA 覆盖率</text><text>{{ rate(payload?.sla?.coverage) }}</text></view>
      </view>
      <view class="wide-card">
        <text class="label">履约周期（平均 / P50 / P90）</text>
        <view v-for="(row, key) in payload?.cycles || {}" :key="key" class="line">
          <text>{{ cycleLabels[key] || key }}</text>
          <text>{{ duration(row.average_seconds) }} / {{ duration(row.p50_seconds) }} / {{ duration(row.p90_seconds) }}</text>
        </view>
      </view>
      <view class="wide-card">
        <text class="label">任务积压</text>
        <text class="value">{{ payload?.backlog?.count || 0 }}</text>
        <text class="minor">最老 {{ duration((payload?.backlog?.oldest_age_minutes || 0) * 60) }} · {{ payload?.backlog?.date_semantics }}</text>
      </view>
    </template>
    <template v-else-if="tab === 'yield'">
      <view v-for="group in payload?.rankings_by_currency || []" :key="group.currency" class="wide-card">
        <text class="label">资源收益 · {{ group.currency }}</text>
        <view v-for="row in group.items" :key="row.owner_id" class="line">
          <text>{{ row.owner_name || `货主 #${row.owner_id}` }}</text>
          <text>{{ money(row.revenue_subtotal, group.currency) }} · 差值 {{ percentPoint(row.contribution_gap) }}</text>
        </view>
      </view>
    </template>
    <template v-else-if="tab === 'performance'">
      <view v-for="row in payload?.selected_period_vs_prior || []" :key="row.currency" class="wide-card">
        <text class="label">所选区间 vs 前一期 · {{ row.currency }}</text>
        <text class="money">{{ money(row.subtotal, row.currency) }}</text>
        <text class="minor">前一期 {{ money(row.prior_subtotal, row.currency) }} · {{ rate(row.change_rate) }}</text>
      </view>
      <view v-for="row in payload?.forecasts || []" :key="`forecast-${row.currency}`" class="wide-card">
        <text class="label">月底收入预测 · {{ row.currency }}</text>
        <text class="money">{{ money(row.forecast, row.currency) }}</text>
        <text class="minor">{{ row.algorithm }} · 样本 {{ row.sample_days }} 天</text>
      </view>
    </template>
    <template v-else-if="tab === 'risk'">
      <view class="wide-card">
        <text class="label">FIFO 库存价值</text>
        <view v-for="row in payload?.value_by_currency || []" :key="row.currency" class="line"><text>{{ row.currency }}</text><text>{{ money(row.total, row.currency) }}</text></view>
      </view>
      <view class="wide-card">
        <text class="label">库龄分层</text>
        <view v-for="row in payload?.age_bands || []" :key="`${row.band}-${row.base_unit?.code}`" class="line"><text>{{ row.band }} · {{ row.base_unit?.name || row.base_unit?.code }}</text><text>{{ row.quantity }} {{ row.base_unit?.code }}</text></view>
      </view>
    </template>
    <template v-else>
      <view v-for="row in payload?.results || []" :key="row.id" class="wide-card">
        <text class="label">{{ row.title }}</text>
        <text class="value">{{ row.severity }} · {{ row.status }}</text>
        <text class="minor">责任人 #{{ row.assignee || '-' }} · 截止 {{ row.due_at || '-' }}</text>
      </view>
      <view class="empty">老板端仅查看，预警处置由授权管理员或财务完成。</view>
    </template>
    <view v-if="!loading && !error && !payload" class="empty">当前范围暂无数据</view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import BossNav from '@/components/boss-nav.vue'
import BossScopeFilter from '@/components/boss-scope-filter.vue'
import BossDataStatus from '@/components/boss-data-status.vue'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
import { api, downloadBinary } from '@/utils/request'
import { money } from '@/utils/billing'

const auth = useAuth()
const scope = useBossScope()
const tab = ref('assurance')
const payload = ref(null)
const error = ref(null)
const loading = ref(false)
const tabs = [
  { key: 'assurance', label: '收入保障' }, { key: 'receivables', label: '应收回款' },
  { key: 'operations', label: '运营履约' }, { key: 'yield', label: '资源收益' },
  { key: 'performance', label: '目标预测' }, { key: 'risk', label: '库存风险' },
  { key: 'cases', label: '预警闭环' },
]
const assuranceLabels = {
  unpriced_events: '未定价事件', missing_accruals: '漏应计', late_arriving_charges: '晚到费用',
  approximate_sources: '近似来源', billing_job_failures: '计费失败', periods_pending_close: '待关账',
  accrual_invoice_variance: '应计/开票差额', close_blockers: '关账阻断',
}
const cycleLabels = { order_to_receive: '订单到收货', receive_to_putaway: '收货到上架', order_to_allocate: '订单到分配', allocate_to_pick: '分配到拣货', pick_to_pack: '拣货到打包', pack_to_ship: '打包到发运' }
const loaders = {
  assurance: api.bossRevenueAssurance, receivables: api.bossReceivables,
  operations: api.bossOperations, yield: api.bossResourceYield,
  performance: api.bossPerformance, risk: api.bossInventoryRisk,
  cases: api.bossAlertCases,
}

function rate(value) { return value == null ? '无可比基数' : `${(Number(value) * 100).toFixed(1)}%` }
function percentPoint(value) { return value == null ? '-' : `${(Number(value) * 100).toFixed(1)}pp` }
function duration(seconds) {
  if (seconds == null) return '-'
  const hours = Number(seconds) / 3600
  return hours < 24 ? `${hours.toFixed(1)}小时` : `${(hours / 24).toFixed(1)}天`
}
async function refresh() {
  loading.value = true
  error.value = null
  try { payload.value = await loaders[tab.value](scope.params) }
  catch (err) { payload.value = null; error.value = err }
  finally { loading.value = false; uni.stopPullDownRefresh() }
}
function selectTab(value) { tab.value = value; refresh() }
async function createSnapshot() {
  try {
    const result = await api.createBusinessReviewSnapshot({ ...scope.params })
    uni.showModal({ title: '快照已创建', content: `分享码：${result.share_code}`, showCancel: false })
  } catch (err) { uni.showToast({ title: err.message || '创建失败', icon: 'none' }) }
}
async function exportOperations() {
  try {
    await downloadBinary({
      url: '/api/reports/boss/operations/export/', method: 'POST',
      data: { ...scope.params, direction: 'all', metric_basis: 'actual' },
      filename: `operations-${scope.date_from}-${scope.date_to}.xlsx`,
    })
  } catch (err) { uni.showToast({ title: err.message || '导出失败', icon: 'none' }) }
}
async function exportCurrent() {
  const reportTypes = { assurance: 'revenue_assurance', receivables: 'receivables', yield: 'resource_yield', performance: 'performance', risk: 'inventory_risk' }
  const reportType = reportTypes[tab.value]
  if (!reportType) return
  try {
    await downloadBinary({
      url: '/api/reports/boss/exports/', method: 'POST',
      data: { ...scope.params, report_type: reportType },
      filename: `${reportType}-${scope.date_from}-${scope.date_to}.xlsx`,
    })
  } catch (err) { uni.showToast({ title: err.message || '导出失败', icon: 'none' }) }
}
onLoad(() => {
  if (!auth.ensureAuth()) return uni.reLaunch({ url: '/pages/login' })
  scope.loadContext(auth.user).then(refresh)
})
onPullDownRefresh(refresh)
</script>

<style scoped>
.page { min-height: 100vh; padding: 24rpx; background: #f4f7fb; color: #17223a; }
.tabs { margin: 18rpx 0; white-space: nowrap; }
.tab-track { display: inline-flex; gap: 10rpx; }
.tab { padding: 16rpx 24rpx; border-radius: 999rpx; background: white; color: #66738d; font-size: 24rpx; }
.tab.active { background: #0b5fff; color: white; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin: 18rpx 0; }
.asof { color: #7b879f; font-size: 22rpx; }
.snapshot { margin: 0; color: #0b5fff; background: white; }
.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16rpx; }
.card, .wide-card { display: flex; flex-direction: column; gap: 10rpx; padding: 24rpx; margin-bottom: 16rpx; border-radius: 20rpx; background: white; }
.label { color: #697791; font-size: 23rpx; }.value { font-size: 42rpx; font-weight: 800; }.money { font-size: 38rpx; font-weight: 800; color: #0b5fff; }.minor { color: #8792a8; font-size: 22rpx; }
.line { display: flex; justify-content: space-between; gap: 18rpx; padding: 14rpx 0; border-top: 1rpx solid #edf0f5; font-size: 23rpx; }
.empty { padding: 80rpx 20rpx; text-align: center; color: #8b96aa; }
</style>
