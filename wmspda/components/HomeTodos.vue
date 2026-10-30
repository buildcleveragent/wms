<template>
  <view id="home-todos" class="todo-panel">
    <view class="todo-heading">
      <text class="todo-title">待办提醒</text>
      <button size="mini" :disabled="loading" @click="refresh">{{ loading ? '更新中…' : '刷新' }}</button>
    </view>
    <view v-if="error" class="todo-error">{{ error }}</view>
    <view v-else-if="!loaded" class="todo-muted">正在获取待办…</view>
    <template v-else>
      <view v-if="!pending.length" class="todo-muted">暂无待办</view>
      <view class="todo-list">
        <button v-for="item in items" :key="item.key" class="todo-item" @click="open(item)">
          <text>{{ item.label }}</text>
          <text><text :class="['todo-count', { positive: item.count > 0 }]">{{ item.count }}</text> {{ item.unit }} ›</text>
        </button>
      </view>
    </template>
    <view v-if="popup" class="todo-overlay" @touchmove.stop.prevent>
      <view class="todo-dialog" role="dialog" aria-label="待办事项提醒" aria-modal="true">
        <text class="todo-title">您有待办事项</text>
        <text class="todo-muted">以下事项需要关注，可前往对应列表处理</text>
        <scroll-view scroll-y class="todo-dialog-list">
          <view v-for="item in pending" :key="item.key" class="todo-summary">
            <text>{{ item.label }}</text><text class="positive">{{ item.count }} {{ item.unit }}</text>
          </view>
        </scroll-view>
        <view class="todo-dialog-actions">
          <button @click="popup = false">稍后处理</button>
          <button type="primary" @click="viewTodos">查看待办</button>
        </view>
      </view>
    </view>
  </view>
</template>
<script setup>
import { watch, onUnmounted } from 'vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useHomeTodos } from '@/utils/homeTodos'
const auth = useAuth()
const props = defineProps({ pageVisible: { type: Boolean, default: false } })
const session = useHomeTodos({ fetchTodos: () => api.homeTodos() })
const { items, pending, loading, loaded, error, popup, refresh } = session
const active = () => props.pageVisible && auth.access && (typeof document === 'undefined' || !document.hidden)
function sync() { if (active()) session.show(); else session.hide() }
function open(item) { uni.navigateTo({ url: item.path }) }
function viewTodos() { popup.value = false; uni.pageScrollTo({ scrollTop: 0, duration: 200 }) }
watch([() => auth.access, () => auth.user?.id], () => { session.hide(); session.reset(); sync() })
let permissionIdentity = ''
watch(() => auth.profileLoaded ? JSON.stringify([auth.perms, auth.profile?.capabilities]) : null,
  value => {
    if (!value) return
    if (permissionIdentity && value !== permissionIdentity) session.reset()
    permissionIdentity = value
  })
watch(() => props.pageVisible, sync, { immediate: true })
if (typeof document !== 'undefined') document.addEventListener('visibilitychange', sync)
onUnmounted(() => {
  session.hide()
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', sync)
})
</script>
<style scoped>
.todo-panel { margin-bottom: 28rpx; padding: 24rpx; border: 1px solid #dbe7fa; border-radius: 20rpx; background: #fff; }
.todo-heading { display: flex; align-items: center; justify-content: space-between; gap: 16rpx; }
.todo-heading button { margin: 0; color: #2563eb; }
.todo-title { display: block; font-size: 32rpx; font-weight: 700; color: #172d50; }
.todo-muted { display: block; margin-top: 16rpx; color: #65758b; font-size: 26rpx; line-height: 1.6; }
.todo-error { margin-top: 16rpx; color: #b45309; font-size: 28rpx; }
.todo-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12rpx; margin-top: 18rpx; }
.todo-item { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8rpx; width: 100%; margin: 0; padding: 18rpx; background: #f5f8fd; font-size: 26rpx; line-height: 1.6; text-align: left; color: #334155; }
.todo-item::after { border: none; }
.todo-count { font-size: 34rpx; font-weight: 700; }
.positive { color: #c45808; font-weight: 700; }
.todo-overlay { position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center; justify-content: center; padding: 32rpx; background: rgba(15,23,42,.45); }
.todo-dialog { box-sizing: border-box; width: 100%; max-width: 480px; max-height: 85vh; padding: 32rpx; border-radius: 24rpx; background: #fff; }
.todo-dialog-list { max-height: 45vh; margin-top: 20rpx; }
.todo-summary { display: flex; justify-content: space-between; gap: 16rpx; padding: 16rpx 0; font-size: 28rpx; border-bottom: 1px solid #eef2f7; }
.todo-dialog-actions { display: flex; gap: 16rpx; margin-top: 28rpx; }
.todo-dialog-actions button { flex: 1; margin: 0; font-size: 28rpx; }
@media (max-width: 380px) { .todo-list { grid-template-columns: 1fr; } }
</style>
