<template>
  <view class="settings-page">
    <view class="settings-card">
      <view class="heading">页面自动刷新 <switch :checked="localEnabled" @change="localEnabled = $event.detail.value" /></view>
      <view class="note">仅当前设备、当前账号生效。关闭后仍可手动刷新和切换筛选。</view>
      <view v-if="localEnabled" class="interval">
        <view class="choices"><button v-for="n in presets" :key="n" size="mini" @click="localMinutes = String(n)">{{ n }} 分钟</button></view>
        <view class="input-row"><text>间隔</text><input aria-label="页面刷新分钟" type="number" v-model="localMinutes" /><text>分钟</text></view>
        <view class="note">可自定义 1～1440 的整数分钟。</view>
      </view>
      <view class="note">{{ preferences.automatic ? `已保存：每 ${preferences.minutes} 分钟刷新` : '已保存：自动刷新关闭' }} · 修改后需保存</view>
      <view v-if="localError" class="error">{{ localError }}</view>
      <button class="save-local" size="mini" @click="saveLocal">保存本机设置</button>
    </view>
    <view class="settings-card">
      <view class="heading">后台自动检查 <switch :checked="globalEnabled" :disabled="!server?.can_manage || saving || !!loadError" @change="globalEnabled = $event.detail.value" /></view>
      <view class="note">全系统共享，关闭页面后仍按设置运行。只有超级用户可修改。</view>
      <view v-if="loadError" class="error">{{ loadError }} <text class="link" @click="loadGlobal">重新加载 ›</text></view>
      <view v-else-if="!server" class="note">正在读取设置…</view>
      <template v-if="server && !loadError">
        <view v-if="globalEnabled" class="interval">
          <view v-if="server.can_manage" class="choices"><button v-for="n in presets" :key="n" size="mini" :disabled="saving" @click="globalMinutes = String(n)">{{ n }} 分钟</button></view>
          <view class="input-row"><text>间隔</text><input aria-label="后台检查分钟" type="number" v-model="globalMinutes" :disabled="!server.can_manage || saving" /><text>分钟</text></view>
          <view class="note">可自定义 1～1440 的整数分钟。</view>
        </view>
        <view class="note">已保存：{{ server.enabled ? `每 ${server.interval_minutes} 分钟检查` : '自动检查关闭' }}</view>
        <view class="note">{{ statusLabel(server.state) }}</view>
        <view class="note">最近一轮结束：{{ fieldText(server.last_finished_at) }}；下次检查：{{ fieldText(server.next_check_at) }}</view>
        <view v-if="saveError" class="error">{{ saveError }}</view>
        <button v-if="server.can_manage" class="save-global" size="mini" :disabled="saving" @click="saveGlobal">{{ saving ? '正在保存…' : '保存全系统设置' }}</button>
        <text class="link reload-global" @click="loadGlobal">刷新检查状态 ›</text>
      </template>
    </view>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { onLoad, onUnload } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { useRefreshPreferences, validMinutes } from '@/store/refreshPreferences'
import { api } from '@/utils/request'
import { fieldText } from '@/utils/activity'
import { automationLabel as statusLabel } from '@/utils/automation'
const auth = useAuth(), preferences = useRefreshPreferences()
const presets = [1, 3, 5, 10, 30]
const localEnabled = ref(preferences.automatic), localMinutes = ref(String(preferences.minutes)), localError = ref('')
const server = ref(null), globalEnabled = ref(false), globalMinutes = ref('5')
const loadError = ref(''), saveError = ref(''), saving = ref(false)
let revision = 0
function minutes(value) {
  if (!/^\d+$/.test(String(value)) || !validMinutes(Number(value))) throw new Error('请输入 1～1440 的整数分钟')
  return Number(value)
}
function saveLocal() {
  localError.value = ''
  try { preferences.save(localEnabled.value, localEnabled.value ? minutes(localMinutes.value) : preferences.minutes); localMinutes.value = String(preferences.minutes) }
  catch (error) { localError.value = error.message || '本机设置保存失败'; localEnabled.value = preferences.automatic; localMinutes.value = String(preferences.minutes) }
}
function accept(value) { server.value = value; globalEnabled.value = value.enabled; globalMinutes.value = String(value.interval_minutes) }
async function loadGlobal() {
  if (saving.value) return
  const current = ++revision, user = auth.user?.id
  loadError.value = ''; saveError.value = ''
  try { const value = await api.bossAutomationSettings(); if (current === revision && user === auth.user?.id) accept(value) }
  catch (error) { if (current === revision) loadError.value = error.message || '检查设置读取失败' }
}
async function saveGlobal() {
  if (saving.value || !server.value?.can_manage) return
  let interval
  try { interval = globalEnabled.value ? minutes(globalMinutes.value) : server.value.interval_minutes } catch (error) { saveError.value = error.message; return }
  saving.value = true; saveError.value = ''
  const current = ++revision, user = auth.user?.id
  try {
    const value = await api.saveBossAutomationSettings({ enabled: globalEnabled.value, interval_minutes: interval })
    if (current === revision && user === auth.user?.id) accept(value)
  } catch (error) {
    if (current === revision) { accept(server.value); loadError.value = `${error.message || '保存失败'}；保存结果未确认，请重新加载` }
  } finally { saving.value = false }
}
onLoad(() => {
  if (!auth.ensureAuth()) return uni.reLaunch({ url: '/pages/login' })
  localEnabled.value = preferences.automatic; localMinutes.value = String(preferences.minutes)
  loadGlobal()
})
onUnload(() => { revision++ })
</script>
<style scoped>
.settings-page { max-width: 760px; margin: auto; padding: 24rpx; color: #172b4d; }
.settings-card { padding: 28rpx; margin-bottom: 24rpx; background: white; border: 1px solid #e4eaf3; border-radius: 20rpx; }
.heading { display: flex; align-items: center; justify-content: space-between; font-size: 32rpx; font-weight: 650; gap: 16rpx; }
.note { color: #64748b; font-size: 25rpx; line-height: 1.6; margin: 18rpx 0; }
.choices { display: flex; gap: 10rpx; flex-wrap: wrap; margin: 16rpx 0; }.choices button { margin: 0; }
.input-row { display: flex; align-items: center; gap: 16rpx; font-size: 28rpx; }.input-row input { height: 88rpx; box-sizing: border-box; font-size: 28rpx; border: 1px solid #cbd5e1; border-radius: 8rpx; padding: 12rpx; width: 160rpx; }
.error { color: #a63b28; background: #fff1e8; padding: 14rpx; font-size: 25rpx; margin-bottom: 16rpx; }
.link { color: #1656a4; font-size: 25rpx; padding: 16rpx; display: inline-block; }
</style>
