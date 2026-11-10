<template>
  <view class="period-panel">
    <view class="heading">所选区间统计与对比</view>
    <view class="muted">{{ scope.date_from }} 至 {{ scope.date_to }} · {{ granularities.find(row => row.key === trend?.granularity)?.label || '自动粒度' }}</view>
    <button size="mini" :disabled="loading" @click="load">刷新统计</button>
    <view v-if="loading" class="muted">正在计算完整日期范围…</view>
    <view v-if="error" class="warning">{{ error.message || '统计加载失败' }}</view>
    <template v-if="summary">
      <view class="muted">{{ summary.coverage }}</view>
      <view v-for="notice in summary.notices || []" :key="notice" class="muted">{{ notice }}</view>
      <view class="metric-grid"><view v-for="metric in summary.results" :key="metric.key" class="metric" @click="details(metric.key)"><text>{{ metric.label }}</text><text class="value">{{ metric.count }} ›</text><text v-for="amount in metric.money || []" :key="amount.currency" class="money">{{ amount.currency || '币种未记录' }} {{ amount.amount }}</text></view></view>
      <view v-for="(comparison, key) in summary.comparisons || {}" :key="key" class="comparison">
        <view class="heading small">{{ key === 'previous' ? '前一期对比' : '去年同期对比' }}</view>
        <view v-if="!comparison" class="muted">对比日期不可用</view>
        <template v-else>
          <view class="muted">对比区间：{{ comparison.date_from }} 至 {{ comparison.date_to }}</view>
          <view v-for="metric in comparison.metrics" :key="metric.key" class="compare-row" @click="details(metric.key, comparison)"><text>{{ metric.label }}</text><text>本期 {{ currentCount(metric.key) }} / 对比期 {{ metric.count }} ›</text><text v-for="amount in metric.money || []" :key="amount.currency" class="money">对比期 {{ amount.currency || '币种未记录' }} {{ amount.amount }}</text></view>
          <view class="muted">历史完整性未知时不推算增长率；点击查看对比期记录。</view>
        </template>
      </view>
    </template>
    <view v-if="trend" class="trend"><view class="heading small">周期趋势</view>
      <view v-for="row in trend.results" :key="row.date" class="bucket"><view class="bucket-title">{{ row.date_from }} 至 {{ row.date_to }}</view>
        <view class="bucket-metrics"><view v-for="metric in summary?.results || []" :key="metric.key" class="bucket-cell" @click="details(metric.key, row)">{{ metric.label }} {{ row.metrics[metric.key]?.count || 0 }} ›<text v-for="amount in row.metrics[metric.key]?.money || []" :key="amount.currency"> {{ amount.currency || '币种未记录' }} {{ amount.amount }}</text></view></view>
      </view>
      <view class="pager"><button size="mini" :disabled="page === 1 || loading" @click="page--">上一页</button><text>第 {{ page }} 页 · 共 {{ trend.count }} 个周期</text><button size="mini" :disabled="page * 100 >= trend.count || loading" @click="page++">下一页</button></view>
    </view>
  </view>
</template>
<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { api, buildQuery } from '@/utils/request'
import { useBossScope } from '@/store/bossScope'
import { useAuth } from '@/store/auth'
import { granularities } from '@/utils/date-ranges'
const props = defineProps({ ready: Boolean, refreshKey: Number })
const scope = useBossScope()
const auth = useAuth()
const summary = ref(null), trend = ref(null), error = ref(null), loading = ref(false), page = ref(1)
let revision = 0, disposed = false, queue = Promise.resolve()
const key = () => JSON.stringify([auth.user?.id, scope.params])
function load(refreshSummary = true) {
  if (!props.ready || !auth.access || disposed) return
  const requestKey = key(), epoch = ++revision, params = { ...scope.params, page: page.value, page_size: 100 }
  loading.value = true
  queue = queue.catch(() => {}).then(async () => {
    if (disposed || epoch !== revision) return
    try {
      const [nextSummary, nextTrend] = await Promise.all([refreshSummary || !summary.value ? api.bossPeriodSummary(params) : Promise.resolve(summary.value), api.bossPeriods(params)])
      if (disposed || requestKey !== key() || epoch !== revision) return
      summary.value = nextSummary; trend.value = nextTrend; error.value = null
    } catch (err) { if (!disposed && requestKey === key() && epoch === revision) { error.value = err; if ([401,403,404].includes(err.statusCode)) { summary.value = null; trend.value = null } } }
    finally { if (epoch === revision) loading.value = false }
  })
  return queue
}
watch([key, () => props.ready], () => { revision++; summary.value = null; trend.value = null; page.value = 1; load() }, { immediate: true })
watch(page, () => load(false))
watch(() => props.refreshKey, () => load(true))
onBeforeUnmount(() => { disposed = true; revision++ })
function currentCount(key) { return summary.value?.results.find(row => row.key === key)?.count || 0 }
function details(metric, range = {}) { uni.navigateTo({ url: `/pages/cockpit/details?${buildQuery({ ...scope.params, type: 'periods', metric, date_from: range.date_from || scope.date_from, date_to: range.date_to || scope.date_to })}` }) }
</script>
<style scoped>
.period-panel { padding: 28rpx; background: #fff; border-radius: 24rpx; margin: 22rpx 0; }.heading { font-size: 32rpx; font-weight: 800; margin-bottom: 12rpx; }.small { font-size: 27rpx; }.muted { font-size: 22rpx; color: #748297; margin: 12rpx 0; }.warning { color: #a85c24; }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap: 14rpx; margin: 24rpx 0; }.metric { background: #f1f5fc; padding: 20rpx; display: flex; flex-direction: column; gap: 8rpx; font-size: 24rpx; border-radius: 12rpx; }.value { font-size: 34rpx; color: #1656a4; font-weight: 700; }.money { font-size: 22rpx; overflow-wrap: anywhere; }
.comparison { margin: 26rpx 0; }.compare-row { display: flex; flex-wrap: wrap; gap: 10rpx; justify-content: space-between; border-bottom: 1px solid #edf0f6; padding: 14rpx 0; font-size: 23rpx; }.compare-row .money { flex-basis: 100%; text-align: right; }
.bucket { border-top: 1px solid #e6edf6; padding: 18rpx 0; }.bucket-title { font-weight: 700; font-size: 25rpx; }.bucket-metrics { display: grid; grid-template-columns: repeat(auto-fit,minmax(140px,1fr)); gap: 12rpx; margin-top: 12rpx; }.bucket-cell { font-size: 22rpx; color: #305782; }.bucket-cell text { display: block; }.pager { display: flex; flex-wrap: wrap; gap: 12rpx; align-items: center; font-size: 23rpx; }
</style>
