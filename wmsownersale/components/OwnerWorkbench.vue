<template>
  <view class="workbench">
    <view class="workbench-heading"><text class="workbench-title">待办与异常</text><button size="mini" :disabled="loading" @click="refresh">{{ loading ? '更新中…' : '刷新待办' }}</button></view>
    <text v-if="data" class="scope-label">{{ data.scope_label }}</text>
    <text v-if="!data && loading" class="workbench-message">正在加载待办…</text>
    <view v-if="error" class="workbench-error"><text>{{ error }}</text><button v-if="!loading" size="mini" @click="refresh">重试</button></view>
    <view v-if="data" class="reminder-grid">
      <button v-for="item in data.categories" :key="item.code" :class="['reminder-card', item.code]" :disabled="!item.available" @click="openCategory(item)">
        <text class="reminder-count">{{ item.available ? item.count : '—' }}</text>
        <text class="reminder-label">{{ item.label }}</text>
        <text class="reminder-hint">{{ item.available ? '查看订单 ›' : '无审核权限' }}</text>
      </button>
    </view>
    <view v-if="data" class="workbench-notes"><text v-for="note in data.notes" :key="note">{{ note }}</text><text>最近更新：{{ formatTime(data.as_of) }}</text></view>
  </view>
</template>
<script setup>
import { toRef } from 'vue'
import { useOwnerWorkbench } from '@/composables/useOwnerWorkbench'
const props = defineProps({ active: Boolean, identity: { type: String, default: '' } })
const { data, loading, error, refresh } = useOwnerWorkbench(toRef(props, 'active'), toRef(props, 'identity'))
function openCategory(item) { if (item.available) uni.navigateTo({ url: `/pages/workbench/items?category=${item.code}` }) }
function formatTime(value) { return (value || '').replace('T', ' ').replace(/\.\d+/, '').replace(/\+08:00$/, '（北京时间）') }
</script>
<style scoped>
.workbench { margin-bottom: 24px; padding: 16px; background: #fff; border: 1px solid #e8ebef; border-radius: 16px; }
.workbench-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.workbench-title { font-size: 18px; font-weight: 700; color: #101828; }.workbench-heading button { margin: 0; }
.scope-label { display: block; margin: 8px 0 14px; color: #667085; font-size: 13px; }
.reminder-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.reminder-card { width: 100%; margin: 0; padding: 14px 12px; display: flex; flex-direction: column; align-items: flex-start; line-height: 1.5; background: #f8fafc; border: 1px solid #e8ebef; border-radius: 10px; text-align: left; }
.reminder-card::after { border: 0; }.reminder-count { font-size: 28px; font-weight: 700; color: #1d4ed8; overflow-wrap: anywhere; }
.returned .reminder-count, .shortage .reminder-count { color: #b45309; }.overdue .reminder-count { color: #b42318; }
.reminder-label { font-size: 15px; color: #1f2937; }.reminder-hint { font-size: 12px; color: #667085; margin-top: 5px; }
.workbench-notes { display: flex; flex-direction: column; gap: 4px; margin-top: 14px; font-size: 12px; line-height: 1.6; color: #667085; overflow-wrap: anywhere; }
.workbench-message { display: block; padding: 16px 0; color: #667085; }.workbench-error { margin: 12px 0; color: #b42318; font-size: 14px; }.workbench-error button { margin-top: 8px; }
@media (min-width: 700px) { .reminder-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
</style>
