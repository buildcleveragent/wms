<template>
  <view class="page">
    <view class="title">{{ payload?.label || '预警明细' }}</view>
    <view class="meta">{{ payload?.date_semantics || '' }}</view>
    <view class="meta">第 {{ page }} 页 · 共 {{ payload?.count || 0 }} 条</view>
    <BossDataStatus :meta="payload?.meta" :error="dataError" />
    <view v-if="loading" class="card">加载中...</view>
    <view v-for="item in rows" :key="`${item.item_type || 'item'}-${item.id}`" class="card" @click="openDetail(item)">
      <view class="row-title">{{ titleOf(item) }}</view>
      <view class="meta">{{ metaOf(item) }}</view>
    </view>
    <view class="pager">
      <button :disabled="!payload?.previous_page" @click="go(page - 1)">上一页</button>
      <button :disabled="!payload?.next_page" @click="go(page + 1)">下一页</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import BossDataStatus from '@/components/boss-data-status.vue'
import { useAuth } from '@/store/auth'
import { api, buildQuery } from '@/utils/request'

const auth = useAuth()
const section = ref('')
const params = ref({})
const payload = ref(null)
const rows = ref([])
const page = ref(1)
const loading = ref(false)
const dataError = ref(null)

function itemType(item) {
  return item.item_type || ({ overdue_tasks: 'task', pending_review_tasks: 'task', overdue_bills: 'bill', bills_missing_due_date: 'bill', failed_billing_jobs: 'billing_job', review_differences: 'review_difference', unpriced_billing_events: 'billing_event' }[section.value] || 'item')
}
function titleOf(item) { return item.invoice_no || item.task_no || item.order_no || item.product_name || item.job_name || `${item.owner_name || ''} #${item.id}` }
function metaOf(item) { return [item.service_date, item.issue_date, item.due_date, item.status, item.reason].filter(Boolean).join(' · ') }
async function load() {
  loading.value = true
  try {
    payload.value = await api.bossAlertSection(section.value, { ...params.value, page: page.value, page_size: 20 })
    rows.value = payload.value?.results || []
    dataError.value = null
  } catch (error) {
    dataError.value = error
    payload.value = null
    rows.value = []
  } finally { loading.value = false }
}
function go(next) { page.value = next; load() }
function openDetail(item) {
  uni.navigateTo({ url: `/pages/alerts/detail?${buildQuery({ section: section.value, item_type: itemType(item), id: item.id, ...params.value })}` })
}
onLoad((query) => {
  if (!auth.ensureAuth()) {
    uni.reLaunch({ url: '/pages/login' })
    return
  }
  section.value = query.section || ''
  params.value = { warehouse: query.warehouse, owner: query.owner, date_from: query.date_from, date_to: query.date_to }
  load()
})
</script>

<style scoped>
.page { padding: 24rpx; background: #f5f7fb; min-height: 100vh; }
.title { font-size: 38rpx; font-weight: 700; margin-bottom: 12rpx; }
.meta { color: #71809c; font-size: 24rpx; margin-top: 8rpx; }
.card { margin-top: 18rpx; padding: 24rpx; border-radius: 20rpx; background: #fff; }
.row-title { font-weight: 650; color: #162034; }
.pager { display: flex; gap: 16rpx; margin-top: 24rpx; }
.pager button { flex: 1; }
</style>
