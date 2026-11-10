<template>
  <view class="activity-panel" :class="{ compact }">
    <view class="activity-heading">
      <view>
        <view class="activity-title">{{ digestToken ? '摘要对应活动' : compact ? '最新重要动态' : '业务活动' }}</view>
        <view v-if="!compact" class="activity-muted">{{ compact ? '今天 · 按当前仓库和货主筛选' : '按所选日期、仓库和货主筛选' }} · 约 30 秒更新</view>
      </view>
      <button v-if="compact" size="mini" @click="openAll">查看全部</button>
    </view>
    <view v-if="!compact && !digestToken" class="activity-filters">
      <picker :range="activityCategories" range-key="label" :value="categoryIndex" @change="changeCategory">
        <view class="activity-input">{{ activityCategories[categoryIndex].label }} ▾</view>
      </picker>
      <input v-model="searchDraft" class="activity-input" placeholder="单号或商品" @confirm="applySearch" />
      <input v-model="actorDraft" class="activity-input" type="number" placeholder="经办人编号（可从动态点选）" @confirm="applySearch" />
      <button size="mini" @click="applySearch">查询</button>
    </view>
    <view v-if="pending" class="activity-update" @click="acceptNew">有新动态，点击更新</view>
    <view v-if="error" class="activity-error">{{ error.statusCode === 404 ? '活动接口尚未上线，请先更新后端。' : '同步失败，保留上次结果并自动重试。' }}</view>
    <view v-if="!compact || showSync" class="activity-muted">{{ updatedAt ? `最近成功同步 ${updatedAt}` : '等待同步' }}</view>
    <view v-if="loading" class="activity-empty">正在读取业务动态…</view>
    <view v-else-if="!rows.length && !error" class="activity-empty">当前范围暂无动态</view>
    <view v-for="row in rows" :key="row.id" class="activity-row" @click="openActivity(row.target, scopeParams)">
      <view class="activity-row-top"><text class="activity-kind">{{ row.title }}</text><text class="activity-time">{{ fieldText(row.occurred_at) }}</text></view>
      <view class="activity-number">{{ row.record_no }}</view>
      <view class="activity-summary">{{ row.summary }}</view>
      <view v-if="!compact" class="activity-muted">{{ row.warehouse?.name }} · {{ row.owner?.name || '按所选货主展示' }} · <text @click.stop="selectActor(row.actor)">{{ row.actor?.name }}{{ row.actor?.id ? ` #${row.actor.id}` : '' }}</text>　›</view>
    </view>
    <view v-if="!compact" class="activity-actions">
      <button size="mini" @click="manualRefresh">立即刷新</button>
      <button v-if="!compact && nextCursor" size="mini" :disabled="moreLoading" @click="loadMore">{{ moreLoading ? '正在读取…' : '加载更早动态' }}</button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api, buildQuery } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
import { useLiveResource } from '@/utils/use-live-resource'
import { activityCategories, fieldText, openActivity } from '@/utils/activity'

const props = defineProps({ compact: Boolean, showSync: Boolean, ready: Boolean, reading: Boolean, dates: Object, digestToken: String, initialCategory: String, initialAction: String })
const auth = useAuth()
const scope = useBossScope()
const categoryIndex = ref(0)
const searchDraft = ref('')
const actorDraft = ref('')
const search = ref('')
const actor = ref('')
const older = ref([])
const cursor = ref(undefined)
const moreLoading = ref(false)
// Manual pagination and polling share one queue, including slow connections.
let requestQueue = Promise.resolve()
function fetchActivities(query) {
  const result = requestQueue.catch(() => {}).then(() => props.digestToken ? api.bossDigestDetails(query) : api.bossActivities(query))
  requestQueue = result
  return result
}
const scopeParams = computed(() => ({ warehouse: scope.warehouse, owner: scope.owner, ...(props.compact ? {} : props.dates) }))
const params = () => ({ ...scopeParams.value, priority: props.compact ? 'important' : 'all', limit: props.compact ? 5 : 20,
  token: props.digestToken, action: props.initialAction, category: props.initialCategory || activityCategories[categoryIndex.value].value, search: search.value, actor: actor.value })
const key = () => JSON.stringify([auth.user?.id || auth.user?.username, params()])
const { data, pending, error, loading, updatedAt, refresh, apply } = useLiveResource(
  () => fetchActivities(params()), { key, enabled: () => props.ready && !!auth.access,
    hold: () => props.reading || moreLoading.value || older.value.length > 0 })
const rows = computed(() => [...(data.value?.results || []), ...older.value])
const nextCursor = computed(() => cursor.value === undefined ? data.value?.next_cursor : cursor.value)
watch(key, () => { older.value = []; cursor.value = undefined })
watch(error, value => {
  if ([401, 403, 404].includes(value?.statusCode)) {
    data.value = null; pending.value = null; older.value = []; cursor.value = undefined
  }
})
function changeCategory(event) { categoryIndex.value = Number(event.detail.value) }
function applySearch() { search.value = searchDraft.value.trim(); actor.value = actorDraft.value.trim() }
function selectActor(person) {
  if (!props.compact && person?.id) { actorDraft.value = String(person.id); applySearch() }
}
function acceptNew() { older.value = []; cursor.value = undefined; apply() }
function manualRefresh() { acceptNew(); refresh() }
defineExpose({ refresh })
function openAll() { const day = data.value?.scope?.date_to || scope.businessToday; uni.navigateTo({ url: `/pages/activity/index?${buildQuery({ ...scopeParams.value, date_from: day, date_to: day }, { keepEmpty: ['warehouse', 'owner'] })}` }) }
async function loadMore() {
  if (moreLoading.value || !nextCursor.value) return
  const currentKey = key()
  const currentCursor = nextCursor.value
  moreLoading.value = true
  try {
    const result = await fetchActivities({ ...params(), cursor: currentCursor })
    if (currentKey !== key() || !data.value) return
    const ids = new Set(rows.value.map(row => row.id))
    older.value.push(...result.results.filter(row => !ids.has(row.id)))
    cursor.value = result.next_cursor
  } catch (err) { if (currentKey === key()) error.value = err }
  finally { moreLoading.value = false }
}
</script>

<style scoped>
.activity-panel { background: #fff; padding: 28rpx; border-radius: 24rpx; margin: 20rpx 0; }
.activity-heading, .activity-row-top, .activity-actions { display: flex; justify-content: space-between; gap: 16rpx; align-items: center; }
.activity-heading button { flex-shrink: 0; }
.activity-title { font-weight: 800; font-size: 34rpx; }
.activity-muted { font-size: 23rpx; color: #677992; line-height: 1.7; overflow-wrap: anywhere; }
.activity-row { padding: 24rpx 0; border-bottom: 1px solid #edf0f6; }
.activity-kind { color: #164b9c; font-weight: 700; font-size: 28rpx; }
.activity-time { color: #788599; font-size: 21rpx; text-align: right; }
.activity-number { font-size: 26rpx; margin-top: 8rpx; overflow-wrap: anywhere; }
.activity-summary { font-size: 25rpx; margin: 8rpx 0; color: #33465e; overflow-wrap: anywhere; }
.activity-update { background: #e9f2ff; color: #1257aa; padding: 18rpx; margin: 16rpx 0; border-radius: 12rpx; }
.activity-error { color: #b64828; font-size: 25rpx; padding: 14rpx 0; }
.activity-empty { padding: 32rpx 0; color: #788599; }
.activity-actions { margin-top: 20rpx; justify-content: flex-end; }
.activity-filters { display: flex; flex-wrap: wrap; gap: 12rpx; margin: 20rpx 0; }
.activity-input { padding: 16rpx; background: #f2f6fb; border-radius: 12rpx; font-size: 26rpx; min-height: 68rpx; }
@media (max-width: 480px) { .activity-row-top { align-items: flex-start; flex-direction: column; gap: 5rpx; } }
.compact { padding: 20rpx 24rpx; margin: 16rpx 0; }.compact .activity-title { font-size: 30rpx; }.compact .activity-row { padding: 16rpx 0; }.compact .activity-row-top { flex-direction: row; align-items: center; }.compact .activity-time { max-width: 42%; font-size: 21rpx; }.compact .activity-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 24rpx; }.compact .activity-number { font-size: 24rpx; }.compact .activity-kind { font-size: 26rpx; }
</style>
