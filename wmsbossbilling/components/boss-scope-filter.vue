<template>
  <view class="scope-filter" :class="{ compact }">
    <picker v-if="!hideScope" :disabled="disabled || changingWarehouse" :range="warehouses" range-key="label" :value="warehouseIndex" @change="changeWarehouse">
      <view class="scope-field"><text>仓库</text><text>{{ warehouses[warehouseIndex]?.label }}</text></view>
    </picker>
    <picker v-if="!hideScope" :disabled="disabled || changingWarehouse" :range="owners" range-key="label" :value="ownerIndex" @change="changeOwner">
      <view class="scope-field"><text>货主</text><text>{{ owners[ownerIndex]?.label }}</text></view>
    </picker>
    <picker v-if="!hideDates" :disabled="disabled || changingWarehouse" mode="date" :value="scope.date_from" :end="scope.date_to" @change="changeFrom">
      <view class="scope-field"><text>开始</text><text>{{ scope.date_from }}</text></view>
    </picker>
    <picker v-if="!hideDates" :disabled="disabled || changingWarehouse" mode="date" :value="scope.date_to" :start="scope.date_from" :end="today" @change="changeTo">
      <view class="scope-field"><text>结束</text><text>{{ scope.date_to }}</text></view>
    </picker>
    <view v-if="!hideDates" class="date-tools">
      <button v-for="item in datePresets" :key="item.key" size="mini" :disabled="disabled || changingWarehouse" :class="{ selected: scope.preset === item.key }" @click="choosePreset(item.key)">{{ item.label }}</button>
      <picker :range="granularities" range-key="label" :value="granularityIndex" @change="changeGranularity"><view class="scope-field">统计粒度：{{ granularities[granularityIndex].label }}</view></picker>
      <text class="range-note">支持跨多年查询 · 自定义日期不限天数</text>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useBossScope } from '@/store/bossScope'
import { datePresets, granularities, presetRange } from '@/utils/date-ranges'

defineProps({ disabled: Boolean, hideDates: Boolean, hideScope: Boolean, compact: Boolean })
const emit = defineEmits(['change'])
const scope = useBossScope()
const changingWarehouse = ref(false)
const today = computed(() => scope.businessToday)
const granularityIndex = computed(() => Math.max(0, granularities.findIndex(item => item.key === scope.granularity)))
async function choosePreset(key) {
  if (changingWarehouse.value) return
  changingWarehouse.value = true
  try {
    await scope.loadContext({ id: scope.userId }, true)
    const range = presetRange(key, scope.businessToday)
    if (scope.setDates(range.date_from, range.date_to)) { scope.preset = key; scope.persist(); emit('change') }
  } catch (error) { uni.showToast({ title: error.message || '业务日期加载失败', icon: 'none' }) }
  finally { changingWarehouse.value = false }
}
function changeGranularity(event) { scope.granularity = granularities[Number(event.detail.value)].key; scope.persist(); emit('change') }
const warehouses = computed(() => [
  { id: '', label: '全部授权仓库' },
  ...scope.warehouseOptions.map((row) => ({ id: String(row.id), label: row.name })),
])
const owners = computed(() => [
  { id: '', label: '全部货主' },
  ...scope.ownerOptions.map((row) => ({ id: String(row.id), label: row.name })),
])
const warehouseIndex = computed(() => Math.max(0, warehouses.value.findIndex((row) => row.id === scope.warehouse)))
const ownerIndex = computed(() => Math.max(0, owners.value.findIndex((row) => row.id === scope.owner)))

async function changeWarehouse(event) {
  if (changingWarehouse.value) return
  const next = warehouses.value[Number(event.detail.value) || 0]
  changingWarehouse.value = true
  try {
    const ownerCleared = await scope.selectWarehouse(next?.id || '')
    if (ownerCleared === null) return
    if (ownerCleared) uni.showToast({ title: '原货主不属于新仓库，已清除', icon: 'none' })
    emit('change')
  } catch (error) {
    uni.showToast({ title: error.message || '切换仓库失败，请重试', icon: 'none' })
    emit('change')
  } finally {
    changingWarehouse.value = false
  }
}
function changeOwner(event) {
  scope.selectOwner(owners.value[Number(event.detail.value) || 0]?.id || '')
  emit('change')
}
function changeFrom(event) {
  if (!scope.setDates(event.detail.value, scope.date_to)) {
    uni.showToast({ title: '开始日期不能晚于结束日期，结束不能晚于今天', icon: 'none' })
    return
  }
  emit('change')
}
function changeTo(event) {
  if (!scope.setDates(scope.date_from, event.detail.value)) {
    uni.showToast({ title: '开始日期不能晚于结束日期，结束不能晚于今天', icon: 'none' })
    return
  }
  emit('change')
}
</script>

<style scoped>
.scope-filter { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14rpx; }
.scope-field { min-height: 84rpx; padding: 14rpx 18rpx; border: 1rpx solid #d9e3f2; border-radius: 16rpx; display: flex; flex-direction: column; gap: 6rpx; color: #162034; font-size: 25rpx; }
.scope-field text:first-child { color: #71809c; font-size: 21rpx; }
.date-tools { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 12rpx; align-items: center; }
.date-tools button { margin: 0; }
.selected { background: #e4efff; color: #1656a4; }
.range-note { color: #71809c; font-size: 22rpx; }
.compact { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12rpx; }
.compact .scope-field { padding: 12rpx 16rpx; gap: 6rpx; }
.compact .scope-field text:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
