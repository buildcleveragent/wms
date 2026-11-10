<template><view><button size="mini" @click="refreshAll">刷新任务和排行</button><BossDataStatus :meta="payload?.meta" :error="dataError" :stale="stale" /><view v-if="loading">正在读取任务和排行…</view><template v-if="payload">      <view class="section">
        <view class="section-head">
          <view class="section-title">截止日任务与当前积压</view>
          <view class="section-desc">新增和完成分别按创建、完成日期统计；积压反映当前状态。点击查看任务明细。</view>
        </view>
        <view class="task-grid">
          <view v-for="row in taskRows" :key="row.taskType" class="task-card" @click="openTasks(row.taskType)">
            <view class="task-title">{{ row.label }}</view>
            <view class="task-main"><text @click.stop="openTasks(row.taskType, 'completed')">完成 {{ row.todayCompleted }} ›</text> · <text @click.stop="openTasks(row.taskType, 'created')">新增 {{ row.todayTotal }} ›</text></view>
            <view class="task-sub">当日新增任务当前完成率 {{ percent(row.completionRate) }}</view>
            <view class="task-backlog" @click.stop="openTasks(row.taskType, 'backlog')">当前积压 {{ row.backlog }}</view>
          </view>
        </view>
      </view>

      <view class="split-grid">
        <view class="section">
          <view class="section-head">
            <view class="section-title">收入贡献排行</view>
            <view class="section-desc">按原币应计收入分别排序，当前不含任何成本数据。</view>
          </view>
          <view v-if="!revenueRows.length" class="empty-inline">当前范围暂无收入排行。</view>
          <view
            v-for="(row, index) in revenueRows"
            :key="`${row.currency || ''}-${row.owner}`"
            class="row-card clickable"
            @click="openOwnerRevenue(row.owner)"
          >
            <view class="rank-pill">{{ index + 1 }}</view>
            <view class="row-main">
              <view class="row-title">{{ row.ownerName }}</view>
              <view class="row-sub">{{ row.accrualCount }} 条应计</view>
            </view>
            <view class="row-money">{{ money(row.total, row.currency) }}</view>
          </view>
        </view>

        <view class="section">
          <view class="section-head">
            <view class="section-title">占仓 Top 货主</view>
            <view class="section-desc">先看谁最占资源，再决定要不要去收入页对比计费。</view>
          </view>
          <view v-if="!inventoryRows.length" class="empty-inline">当前范围暂无占仓排行。</view>
          <view v-for="(row, index) in inventoryRows" :key="`${row.currency || ''}-${row.owner}`" class="row-card">
            <view class="rank-pill soft">{{ index + 1 }}</view>
            <view class="row-main">
              <view class="row-title">{{ row.ownerName }}</view>
              <view class="row-sub">{{ row.skuCount }} 个 SKU · {{ row.locationCount }} 个库位</view>
            </view>
            <view class="row-money">{{ row.usedVolumeText }} m³</view>
          </view>
        </view>
      </view>

</template></view></template>
<script setup>
import { computed, ref, watch } from 'vue'
import BossDataStatus from '@/components/boss-data-status.vue'
import { useBossScope } from '@/store/bossScope'
import { useLatestRequest } from '@/utils/use-latest-request'
import { api, buildQuery } from '@/utils/request'
import { asList, qty, decimalText, toNumber, money, percent } from '@/utils/billing'
const props = defineProps({ ready: Boolean, refreshKey: Number })
const bossScope = useBossScope()
const payload = ref(null), loading = ref(false), dataError = ref(null), loadedFingerprint = ref('')
const requestKey = () => bossScope.fingerprint
const requests = useLatestRequest(requestKey)
const stale = computed(() => !!dataError.value && loadedFingerprint.value === requestKey() && !!payload.value)
const taskRows = computed(() =>
  asList(payload.value?.task_progress).map((row) => ({
    taskType: row.task_type,
    label: row.label || row.task_type,
    todayTotal: qty(row.today_total),
    todayCompleted: qty(row.today_completed),
    completionRate: row.completion_rate,
    backlog: qty(row.backlog),
  }))
)

const revenueRows = computed(() =>
  asList(payload.value?.rankings?.revenue_contribution_by_currency).flatMap((group) =>
    asList(group.rows).map((row) => ({
    owner: row.owner,
    ownerName: row.owner_name,
    accrualCount: qty(row.accrual_count),
    total: toNumber(row.total),
    currency: group.currency,
  })))
)

const inventoryRows = computed(() =>
  asList(payload.value?.rankings?.inventory_top_owners).map((row) => ({
    owner: row.owner,
    ownerName: row.owner_name,
    skuCount: qty(row.sku_count),
    locationCount: qty(row.location_count),
    usedVolumeText: decimalText(row.used_volume_m3),
  }))
)

async function refreshAll() {
  const current = requests.begin()
  const fingerprint = requestKey()
  const params = { ...bossScope.params }
  if (loadedFingerprint.value !== fingerprint) payload.value = null
  loading.value = true
  dataError.value = null
  try {
    const result = await api.bossHome(params)
    if (!current()) return
    payload.value = result
    loadedFingerprint.value = fingerprint
    dataError.value = null
  } catch (error) {
    if (!current()) return
    dataError.value = error
    if (loadedFingerprint.value !== fingerprint || error.kind === 'FORBIDDEN') payload.value = null
  } finally {
    if (current()) {
      loading.value = false
      uni.stopPullDownRefresh()
    }
  }
}

function openTasks(taskType, mode = 'created') {
  const query = buildQuery({ ...bossScope.params, date_from: bossScope.date_to, date_to: bossScope.date_to, type: 'tasks', task_type: taskType, mode })
  uni.navigateTo({ url: `/pages/cockpit/details?${query}` })
}

function openOwnerRevenue(ownerId) {
  const query = buildQuery({
    ...bossScope.params, warehouse: bossScope.warehouse, owner: ownerId,
  }, { keepEmpty: ['warehouse'] })
  uni.navigateTo({ url: `/pages/billing/owner_detail?${query}` })
}


watch([() => props.ready, () => props.refreshKey, requestKey], () => { if (props.ready) refreshAll(); else requests.invalidate() }, { immediate: true })
</script>
<style scoped>
.section { background: white; border-radius: 20rpx; padding: 24rpx; margin: 18rpx 0; }
.section-title { font-size: 30rpx; font-weight: 700; }.section-desc, .row-sub { font-size: 23rpx; color: #6a7890; margin: 10rpx 0; }
.task-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16rpx; }.task-card { padding: 18rpx; background: #f4f7fb; border-radius: 12rpx; font-size: 24rpx; line-height: 1.8; }.task-title { font-weight: 700; }.task-sub { color: #6a7890; }.task-backlog { color: #975f23; }
.row-card { display: flex; align-items: center; gap: 16rpx; padding: 16rpx 0; border-bottom: 1px solid #edf0f5; }.row-main { flex: 1; }.row-title { font-size: 26rpx; }.row-money { font-size: 25rpx; color: #1656a4; }.rank-pill { background: #eaf0fa; border-radius: 8rpx; padding: 8rpx; font-size: 24rpx; }.empty-inline { font-size: 24rpx; color: #6a7890; padding: 20rpx; }
@media (min-width: 900px) { .split-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24rpx; }.task-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
</style>