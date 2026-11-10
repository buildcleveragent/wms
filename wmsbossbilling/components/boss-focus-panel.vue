<template>
  <view class="focus-panel">
    <view v-if="error" class="warning">重点事项同步失败，保留上次结果。<text @click="refresh">重试 ›</text></view>
    <view v-if="pending" class="update" @click="apply">重点事项有更新，点击查看</view>
    <view class="heading"><text>需要立即关注</text><text class="link" @click="openAttention">全部 {{ data?.attention?.count ?? '—' }} ›</text></view>
    <view v-if="automation" :class="['failed', 'delayed', 'worker_unavailable'].includes(automation.state) ? 'warning' : 'sync'">{{ automationLabel(automation.state) }}<text v-if="automation.state === 'disabled'"> · 最近成功检查：{{ lastCheck }}</text></view>
    <view v-else-if="health.some(row => row.stale)" class="warning">超时检查尚未完成或已延迟，当前列表可能不完整。</view>
    <view v-for="row in data?.attention?.results || []" :key="row.id" class="item" @click="openCase(row)">
      <view class="item-title"><text class="issue-copy"><text :class="{ critical: row.severity === 'CRITICAL' }">{{ row.severity === 'CRITICAL' ? '严重 · ' : '' }}</text>{{ row.record_no }} {{ row.title }}</text><text class="issue-badge">{{ row.overdue_seconds != null ? elapsed(row.overdue_seconds) : row.handling_overdue ? '处理超期' : '关注' }} ›</text></view>
    </view>
    <view v-if="data && !data.attention?.count" class="sync">{{ unreliable ? '暂无已发现的重点事项，检查结果待更新。' : '当前没有需要置顶的事项。' }}</view>
    <view class="digest-toggle" @click="expanded = !expanded"><text>{{ data?.digest?.first_visit ? '今日摘要' : '离开期间' }}：{{ digestSummary }}</text><text>{{ expanded ? '收起' : '展开' }} ›</text></view>
    <view v-if="expanded" class="digest-detail">
      <view class="sync">{{ fieldText(data?.digest?.from) }} 至 {{ fieldText(data?.digest?.to) }}</view>
      <view class="digest-grid"><view v-for="row in data?.digest?.results || []" :key="row.key" class="digest-card" @click="openDigest(row)"><text>{{ row.label }}</text><text class="count">{{ row.count }} ›</text><text v-for="amount in row.money || []" :key="amount.currency">{{ amount.currency === 'UNKNOWN' ? '币种未记录' : amount.currency }} {{ amount.amount }}</text></view></view>
      <view class="sync">{{ data?.digest?.notice }}</view>
      <button size="mini" :disabled="confirming || !data?.digest?.token" @click="confirmDigest">{{ confirming ? '正在保存…' : '已查看本次摘要' }}</button>
      <view class="sync">查看摘要不会关闭异常。</view>
    </view>
    <view v-if="showSync" class="sync-details"><view class="sync">重点事项最近成功同步 {{ updatedAt || '等待同步' }}</view><view v-for="check in health" :key="check.rule" class="sync">{{ checkLabels[check.rule] }}检查：{{ fieldText(check.checked_at) }}</view></view>
  </view>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { api, buildQuery } from '@/utils/request'
import { useBossScope } from '@/store/bossScope'
import { useAuth } from '@/store/auth'
import { useLiveResource } from '@/utils/use-live-resource'
import { fieldText } from '@/utils/activity'
import { automationLabel } from '@/utils/automation'
const props = defineProps({ ready: Boolean, reading: Boolean, showSync: Boolean })
const scope = useBossScope()
const auth = useAuth()
const confirming = ref(false)
const expanded = ref(false)
const health = ref([])
const automation = ref(null)
const unreliable = computed(() => automation.value ? automation.value.state !== 'healthy' : health.value.some(row => row.stale))
const lastCheck = computed(() => { const times = health.value.map(row => row.checked_at).filter(Boolean).sort(); return times.length ? fieldText(times[0]) : '尚未完成' })
const statusText = { OPEN: '待处理', ACKNOWLEDGED: '已确认', IN_PROGRESS: '处理中' }
const checkLabels = { DEADLINE_INBOUND: '入库', DEADLINE_OUTBOUND: '出库', DEADLINE_TASK: '任务', BILLING_HEALTH: '计费异常' }
const params = () => ({ warehouse: scope.warehouse, owner: scope.owner })
const key = () => JSON.stringify([auth.user?.id, params()])
const { data, pending, error, updatedAt, refresh, apply } = useLiveResource(async () => {
  const p = params(), fingerprint = key()
  const [attention, digest] = await Promise.all([api.bossAttention({ ...p, page_size: 3 }), api.bossDigest(p)])
  if (fingerprint === key() && props.ready && auth.access) {
    health.value = attention.checks || []
    automation.value = attention.automation || null
    if (attention.scope?.date_to) scope.businessToday = attention.scope.date_to
  }
  return { attention, digest }
}, { key, enabled: () => props.ready && !!auth.access, hold: () => props.reading || confirming.value || expanded.value,
  compare: value => JSON.stringify({
    attention: { count: value?.attention?.count, results: value?.attention?.results?.map(({ overdue_seconds, waiting_seconds, ...row }) => row), checks: value?.attention?.checks?.map(({ rule, stale }) => ({ rule, stale })) },
    digest: { from: value?.digest?.from, first_visit: value?.digest?.first_visit, results: value?.digest?.results },
  }) })
watch([key, () => props.ready, () => !!auth.access], () => { health.value = []; automation.value = null; expanded.value = false })
const digestSummary = computed(() => {
  if (!data.value) return '等待同步'
  return [...(data.value.digest?.results || [])].filter(row => row.count > 0).sort((a, b) => b.count - a.count).slice(0, 3).map(row => `${row.label} ${row.count}`).join('、') || '暂无新活动'
})
defineExpose({ refresh })
function elapsed(seconds) { return seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟` : `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分钟` }
function openAttention() { uni.navigateTo({ url: `/pages/cockpit/details?${buildQuery({ ...params(), type: 'attention' })}` }) }
function openCase(row) { uni.navigateTo({ url: `/pages/cockpit/details?${buildQuery({ ...params(), type: 'case', id: row.id })}` }) }
function openDigest(row) {
  uni.navigateTo({ url: `/pages/activity/index?${buildQuery({ ...params(), token: data.value.digest.token, category: row.category, action: row.action })}` })
}
async function confirmDigest() {
  if (confirming.value || !data.value?.digest?.token) return
  const fingerprint = key()
  const token = data.value.digest.token
  const p = params()
  confirming.value = true
  try { await api.bossDigestConfirm(token, p); if (fingerprint === key()) await refresh() }
  catch (err) {
    if (fingerprint === key()) {
      if ([400, 401, 403].includes(err.statusCode)) await refresh()
      uni.showToast({ title: err.statusCode === 400 ? '摘要已更新，请核对后重新确认' : err.message || '保存失败，请重试', icon: 'none' })
    }
  }
  finally { confirming.value = false }
}
</script>
<style scoped>
.focus-panel { background: #fff; border-radius: 24rpx; padding: 28rpx; margin: 22rpx 0; }
.heading, .item-title { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; font-weight: 700; }
.heading { font-size: 32rpx; margin: 16rpx 0; }.heading button { margin: 0; }
.item { padding: 22rpx 0; border-bottom: 1px solid #edf0f6; color: #42536b; font-size: 25rpx; line-height: 1.8; overflow-wrap: anywhere; }
.item-title { color: #85551a; }.critical { color: #b52a36; }.sync { color: #738198; font-size: 22rpx; margin: 10rpx 0; overflow-wrap: anywhere; }
.warning { background: #fff0d7; color: #895a1b; padding: 18rpx; font-size: 24rpx; }.update { color: #1656a4; background: #edf4ff; padding: 18rpx; }
.digest-heading { margin-top: 28rpx; }.digest-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12rpx; }
.digest-card { background: #f3f6fc; border-radius: 12rpx; padding: 18rpx; font-size: 23rpx; display: flex; flex-direction: column; gap: 8rpx; }.count { color: #17569c; font-size: 32rpx; font-weight: 700; }

.focus-panel { padding: 20rpx 24rpx; margin: 16rpx 0; }
.heading { font-size: 30rpx; margin: 0 0 12rpx; }.link { font-size: 24rpx; color: #1656a4; padding: 12rpx 0; }
.item { padding: 18rpx 0; line-height: 1.45; }.item-title { align-items: center; gap: 12rpx; font-size: 26rpx; }
.issue-copy { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; flex: 1; min-width: 0; }
.issue-badge { flex-shrink: 0; font-size: 23rpx; font-weight: 500; }
.digest-toggle { display: flex; align-items: center; justify-content: space-between; gap: 14rpx; font-size: 24rpx; color: #42536b; padding: 24rpx 0 8rpx; }
.digest-toggle text:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.digest-toggle text:last-child { flex-shrink: 0; color: #1656a4; }
.warning { padding: 12rpx; font-size: 23rpx; }.update { padding: 12rpx; font-size: 24rpx; }
</style>
