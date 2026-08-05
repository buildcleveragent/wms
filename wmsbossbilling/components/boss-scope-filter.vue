<template>
  <view class="scope-filter">
    <picker :range="warehouses" range-key="label" :value="warehouseIndex" @change="changeWarehouse">
      <view class="scope-field"><text>仓库</text><text>{{ warehouses[warehouseIndex]?.label }}</text></view>
    </picker>
    <picker :range="owners" range-key="label" :value="ownerIndex" @change="changeOwner">
      <view class="scope-field"><text>货主</text><text>{{ owners[ownerIndex]?.label }}</text></view>
    </picker>
    <picker mode="date" :value="scope.date_from" :end="scope.date_to" @change="changeFrom">
      <view class="scope-field"><text>开始</text><text>{{ scope.date_from }}</text></view>
    </picker>
    <picker mode="date" :value="scope.date_to" :start="scope.date_from" :end="today" @change="changeTo">
      <view class="scope-field"><text>结束</text><text>{{ scope.date_to }}</text></view>
    </picker>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import { useBossScope } from '@/store/bossScope'
import { formatDate } from '@/utils/billing'

const emit = defineEmits(['change'])
const scope = useBossScope()
const today = formatDate(new Date())
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
  const next = warehouses.value[Number(event.detail.value) || 0]
  const ownerCleared = await scope.selectWarehouse(next?.id || '')
  if (ownerCleared) {
    uni.showToast({ title: '原货主不属于新仓库，已清除', icon: 'none' })
  }
  emit('change')
}
function changeOwner(event) {
  scope.selectOwner(owners.value[Number(event.detail.value) || 0]?.id || '')
  emit('change')
}
function changeFrom(event) {
  if (!scope.setDates(event.detail.value, scope.date_to)) {
    uni.showToast({ title: `日期范围须为 1-${scope.maxRangeDays} 天且不能晚于今天`, icon: 'none' })
    return
  }
  emit('change')
}
function changeTo(event) {
  if (!scope.setDates(scope.date_from, event.detail.value)) {
    uni.showToast({ title: `日期范围须为 1-${scope.maxRangeDays} 天且不能晚于今天`, icon: 'none' })
    return
  }
  emit('change')
}
</script>

<style scoped>
.scope-filter { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14rpx; }
.scope-field { min-height: 84rpx; padding: 14rpx 18rpx; border: 1rpx solid #d9e3f2; border-radius: 16rpx; display: flex; flex-direction: column; gap: 6rpx; color: #162034; font-size: 25rpx; }
.scope-field text:first-child { color: #71809c; font-size: 21rpx; }
</style>
