<template>
  <view class="page after-page">
    <view class="top-actions">
      <button class="action-btn" @click="goOrders">从订单申请</button>
      <button class="action-btn ghost" @click="load">刷新</button>
    </view>

    <scroll-view v-if="merchantOptions.length > 2" class="merchant-scroll" scroll-x>
      <view class="merchant-row">
        <view
          v-for="merchant in merchantOptions"
          :key="merchant.id || 'all'"
          :class="['merchant-chip', String(ownerId) === String(merchant.id) && 'active']"
          @click="switchMerchant(merchant.id)"
        >
          {{ merchant.name }}
        </view>
      </view>
    </scroll-view>

    <view v-if="rows.length" class="after-list">
      <view v-for="item in rows" :key="item.id" class="after-card">
        <view class="card-head">
          <view>
            <view class="request-no">{{ item.request_no }}</view>
            <view class="order-no">订单 {{ item.order_no || item.order_id }}</view>
          </view>
          <view :class="['status', statusClass(item.status)]">{{ item.status_name }}</view>
        </view>
        <view class="info-row">
          <text>类型</text>
          <text>{{ item.request_type_name }}</text>
        </view>
        <view class="info-row">
          <text>金额</text>
          <text>¥{{ money(item.amount) }}</text>
        </view>
        <view class="info-row">
          <text>申请时间</text>
          <text>{{ dateText(item.requested_at) }}</text>
        </view>
        <view v-if="item.reason" class="reason">{{ item.reason }}</view>
        <view v-if="item.review_note" class="review">{{ item.review_note }}</view>
      </view>
    </view>
    <EmptyState v-else :text="loading ? '加载中' : '暂无售后记录'" />
  </view>
</template>

<script setup>
import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import { orderService } from '../../services/order'
import { useSessionStore } from '../../stores/session'
import { money } from '../../utils/money'
import { getToken } from '../../utils/request'

const session = useSessionStore()
const rows = ref([])
const loading = ref(false)
const ownerId = ref('')

const merchantOptions = computed(() => {
  const profile = session.profile || {}
  const bindings = Array.isArray(profile.bindings) ? profile.bindings : []
  const rows = bindings
    .map((item) => item.owner)
    .filter((owner) => owner && owner.id)
  if (!rows.length && profile.owner && profile.owner.id) rows.push(profile.owner)
  const seen = new Set()
  const unique = rows.filter((owner) => {
    const key = String(owner.id)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  return [{ id: '', name: '全部商家' }, ...unique]
})

async function ensureProfile() {
  if (session.profile && Array.isArray(session.profile.bindings)) return
  await session.fetchProfile()
}

function dateText(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 16)
}

function statusClass(status) {
  if (status === 'APPROVED' || status === 'COMPLETED') return 'done'
  if (status === 'REJECTED' || status === 'CANCELLED') return 'bad'
  return 'pending'
}

async function load() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  if (loading.value) return
  loading.value = true
  try {
    await ensureProfile()
    rows.value = await orderService.afterSales({ owner_id: ownerId.value })
  } catch (err) {
    uni.showToast({ title: err.message || '售后加载失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function switchMerchant(id) {
  ownerId.value = id || ''
  load()
}

function goOrders() {
  uni.switchTab({ url: '/pages/order-list/order-list' })
}

onShow(() => {
  load()
})

onPullDownRefresh(async () => {
  try {
    await load()
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.after-page {
  padding-bottom: 48rpx;
}

.top-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12rpx;
  margin-bottom: 18rpx;
}

.merchant-scroll {
  margin-bottom: 18rpx;
  white-space: nowrap;
}

.merchant-row {
  display: flex;
  gap: 10rpx;
}

.merchant-chip {
  min-width: 150rpx;
  max-width: 260rpx;
  height: 62rpx;
  padding: 0 18rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 24rpx;
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}

.merchant-chip.active {
  border-color: #0f766e;
  background: #edf8f5;
  color: #0f766e;
}

.action-btn {
  height: 76rpx;
  line-height: 76rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 26rpx;
  font-weight: 750;
}

.action-btn.ghost {
  border: 1rpx solid #d7dde8;
  background: #fff;
  color: #334155;
}

.action-btn::after {
  border: 0;
}

.after-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.after-card {
  padding: 20rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.card-head,
.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.card-head {
  margin-bottom: 16rpx;
}

.request-no {
  color: #17202a;
  font-size: 28rpx;
  font-weight: 850;
}

.order-no {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
}

.status {
  min-width: 108rpx;
  height: 48rpx;
  padding: 0 14rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  font-size: 23rpx;
}

.status.pending {
  background: #fff7ed;
  color: #b45309;
}

.status.done {
  background: #edf8f5;
  color: #0f766e;
}

.status.bad {
  background: #fef2f2;
  color: #b42318;
}

.info-row {
  min-height: 52rpx;
  color: #334155;
  font-size: 25rpx;
}

.info-row text:first-child {
  color: #64748b;
}

.reason,
.review {
  margin-top: 14rpx;
  padding: 14rpx;
  border-radius: 8rpx;
  background: #f8fafc;
  color: #475569;
  font-size: 24rpx;
  line-height: 1.6;
}

.review {
  background: #edf8f5;
  color: #0f766e;
}
</style>
