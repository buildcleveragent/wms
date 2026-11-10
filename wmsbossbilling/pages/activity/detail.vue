<template>
  <view class="page detail-page">
    <view class="detail-head">
      <view class="title">{{ data?.header?.title || '业务详情' }}</view>
      <view>{{ data?.header?.record_no }}</view>
      <view class="muted">{{ data?.header?.warehouse?.name }} {{ data?.header?.actor?.name }} · 最近同步 {{ updatedAt || '等待同步' }}</view>
      <view class="muted">事件时间 {{ fieldText(data?.header?.occurred_at) }} · 下方状态为当前状态</view>
      <view v-for="field in data?.header?.fields || []" :key="field.label" class="field"><text>{{ field.label }}</text><text>{{ fieldText(field.value) }}</text></view>
      <button size="mini" @click="refresh">刷新</button>
    </view>
    <view v-if="pending" class="notice" @click="apply">详情有更新，点击查看最新数据</view>
    <view v-if="error" class="notice">{{ error.statusCode === 404 ? '记录不存在或不在当前范围内。' : '同步失败，稍后自动重试。' }}</view>
    <view v-if="loading">正在读取详情…</view>
    <view v-for="notice in data?.notices || []" :key="notice" class="notice">{{ notice }}</view>
    <view v-for="(section, name) in data?.sections || {}" :key="name" class="detail-section">
      <view class="section-title">{{ section.label }} · {{ section.count }} 条</view>
      <view v-if="!section.results.length" class="muted">暂无记录</view>
      <view v-for="row in section.results" :key="row.id" class="detail-row" @click="openActivity(row.target, scopeParams)">
        <view class="row-title">{{ row.title }} <text v-if="row.target">›</text></view>
        <view v-for="field in row.fields" :key="field.label" class="field"><text>{{ field.label }}</text><text>{{ fieldText(field.value) }}</text></view>
      </view>
      <view class="page-actions">
        <button v-if="section.page > 1" size="mini" @click="turnPage(name, section.page - 1)">上一页</button>
        <text class="muted">第 {{ section.page }} 页</text>
        <button v-if="section.page * section.page_size < section.count" size="mini" @click="turnPage(name, section.page + 1)">下一页</button>
      </view>
    </view>
  </view>
</template>
<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { fieldText, openActivity } from '@/utils/activity'
import { useLiveResource } from '@/utils/use-live-resource'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
const auth = useAuth()
const scope = useBossScope()
const route = ref({})
const pages = ref({})
const ready = ref(false)
const scopeParams = computed(() => ({ warehouse: route.value.warehouse, owner: route.value.owner }))
const { data, pending, error, loading, updatedAt, refresh, apply } = useLiveResource(
  () => api.bossActivityDetail(route.value.kind, route.value.id, { ...scopeParams.value, ...pages.value }),
  { key: () => JSON.stringify([auth.user?.id, route.value, scopeParams.value, pages.value]), enabled: () => ready.value && !!auth.access, hold: () => true })
function turnPage(name, page) { pages.value = { ...pages.value, [`${name}_page`]: page } }
onLoad(async options => {
  if (!auth.ensureAuth()) { uni.reLaunch({ url: '/pages/login' }); return }
  try {
    const loaded = await scope.loadContext(auth.user)
    route.value = { kind: options.kind || 'task_detail', id: options.id,
      warehouse: options.warehouse ?? scope.warehouse, owner: options.owner ?? scope.owner }
    ready.value = !!options.id && !!loaded
  } catch (err) { error.value = err }
})
</script>
<style scoped>
.detail-page { padding: 24rpx; max-width: 1250px; margin: auto; }
.detail-head, .detail-section { background: white; padding: 28rpx; border-radius: 22rpx; margin-bottom: 22rpx; }
.title { font-size: 38rpx; font-weight: 800; margin-bottom: 14rpx; }
.muted { color: #738097; font-size: 23rpx; margin: 12rpx 0; }
.section-title, .row-title { font-size: 29rpx; font-weight: 700; color: #193d72; }
.detail-row { padding: 22rpx 0; border-bottom: 1px solid #edf1f6; }
.field { display: flex; justify-content: space-between; gap: 20rpx; font-size: 25rpx; line-height: 1.7; overflow-wrap: anywhere; }
.field text:first-child { color: #64748b; flex-shrink: 0; }
.field text:last-child { text-align: right; min-width: 0; }
.notice { padding: 22rpx; margin-bottom: 18rpx; background: #fff5dd; color: #795b24; font-size: 25rpx; border-radius: 16rpx; }
.page-actions { display: flex; align-items: center; justify-content: flex-end; gap: 15rpx; margin-top: 16rpx; }
</style>
