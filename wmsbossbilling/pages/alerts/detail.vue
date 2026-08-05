<template>
  <view class="page">
    <view class="title">预警详情（只读）</view>
    <BossDataStatus :meta="meta" :error="dataError" />
    <view v-if="loading" class="card">加载中...</view>
    <view v-else class="card">
      <view v-for="row in fields" :key="row.key" class="field">
        <text>{{ row.key }}</text><text>{{ row.value }}</text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import BossDataStatus from '@/components/boss-data-status.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'

const auth = useAuth()
const detail = ref({})
const loading = ref(false)
const meta = ref(null)
const dataError = ref(null)
const fields = computed(() => Object.entries(detail.value || {}).map(([key, value]) => ({
  key,
  value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? '-'),
})))

onLoad(async (query) => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  loading.value = true
  try {
    const response = await api.bossAlertDetail(query.section, query.item_type, query.id, {
      warehouse: query.warehouse, owner: query.owner,
      date_from: query.date_from, date_to: query.date_to,
    })
    detail.value = response?.detail || {}
    meta.value = response?.meta || null
    dataError.value = null
  } catch (error) {
    dataError.value = error
    detail.value = {}
  } finally { loading.value = false }
})
</script>

<style scoped>
.page { padding: 24rpx; background: #f5f7fb; min-height: 100vh; }
.title { font-size: 38rpx; font-weight: 700; margin-bottom: 18rpx; }
.card { padding: 24rpx; border-radius: 20rpx; background: #fff; }
.field { display: flex; justify-content: space-between; gap: 20rpx; padding: 16rpx 0; border-bottom: 1rpx solid #edf0f5; font-size: 24rpx; }
.field text:first-child { color: #71809c; }
.field text:last-child { text-align: right; word-break: break-all; }
</style>
