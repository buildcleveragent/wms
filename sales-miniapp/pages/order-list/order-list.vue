<template>
  <view class="page order-list-page">
    <view class="tabs">
      <button v-for="tab in tabs" :key="tab.value" :class="['tab', status === tab.value && 'active']" @click="switchStatus(tab.value)">
        {{ tab.name }}
      </button>
    </view>
    <view v-if="rows.length" class="orders">
      <view v-for="order in rows" :key="order.id" class="order" @click="open(order)">
        <view class="between">
          <view class="no">{{ order.order_no }}</view>
          <OrderStatusTag :status="order.status" :text="order.status_name" />
        </view>
        <view class="meta">{{ order.created_at }} · {{ order.delivery_method_name }}{{ order.is_combined ? ` · ${order.order_count} 个配送包裹` : '' }}</view>
        <view class="products">
          <text v-for="line in order.lines.slice(0, 2)" :key="line.id">{{ line.product_name }} × {{ line.qty }}</text>
        </view>
        <view class="foot">
          <text>{{ order.line_count }} 件</text>
          <text class="amount">¥{{ money(order.total_amount) }}</text>
        </view>
      </view>
      <view class="load-more">{{ loading ? '加载中' : hasMore ? '继续上拉' : '没有更多了' }}</view>
    </view>
    <EmptyState v-else :text="loading ? '加载中' : '暂无订单'" />
  </view>
</template>

<script setup>
import { onLoad, onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import OrderStatusTag from '../../components/OrderStatusTag.vue'
import { orderService } from '../../services/order'
import { useSessionStore } from '../../stores/session'
import { money } from '../../utils/money'

const session = useSessionStore()
const tabs = [
  { value: '', name: '全部' },
  { value: 'WAIT_PAY', name: '待付款' },
  { value: 'WAIT_SHIP', name: '待发货' },
  { value: 'COMPLETED', name: '已完成' },
  { value: 'CANCELLED', name: '已取消' },
]
const rows = ref([])
const status = ref('')
const page = ref(1)
const hasMore = ref(true)
const loading = ref(false)
const loaded = ref(false)

async function ensureProfile() {
  if (session.profile && Array.isArray(session.profile.bindings)) return
  await session.fetchProfile()
}

async function load(reset = true) {
  if (loading.value) return
  if (!reset && !hasMore.value) return
  if (reset) {
    page.value = 1
    hasMore.value = true
  }
  loading.value = true
  try {
    await ensureProfile()
    const data = await orderService.list({
      status: status.value,
      page: page.value,
    })
    const next = data.results || data || []
    rows.value = reset ? next : rows.value.concat(next)
    hasMore.value = Boolean(data.next)
    page.value += 1
  } finally {
    loading.value = false
  }
}

function switchStatus(value) {
  status.value = value
  load(true)
}

function open(order) {
  uni.navigateTo({ url: `/pages/order-detail/order-detail?id=${order.id}` })
}

function takePendingStatus() {
  const pending = uni.getStorageSync('sale_mini_pending_order_status')
  if (!pending) return false
  uni.removeStorageSync('sale_mini_pending_order_status')
  status.value = pending
  return true
}

onLoad(() => {
  takePendingStatus()
  loaded.value = true
  load(true)
})
onShow(() => {
  if (takePendingStatus() || (!loaded.value && !rows.value.length)) {
    loaded.value = true
    load(true)
  }
})
onReachBottom(() => load(false))
onPullDownRefresh(async () => {
  try {
    await load(true)
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.tabs {
  display: flex;
  gap: 10rpx;
  margin-bottom: 16rpx;
  overflow-x: auto;
}

.tab {
  min-width: 120rpx;
  height: 62rpx;
  line-height: 62rpx;
  padding: 0 16rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 24rpx;
}

.tab::after {
  border: 0;
}

.tab.active {
  border-color: #0f766e;
  color: #0f766e;
  background: #edf8f5;
}

.orders {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.order {
  padding: 20rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.between,
.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.no {
  color: #17202a;
  font-size: 28rpx;
  font-weight: 800;
}

.meta,
.products,
.foot {
  margin-top: 10rpx;
  color: #64748b;
  font-size: 24rpx;
}

.products {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.amount {
  color: #b42318;
  font-size: 30rpx;
  font-weight: 850;
}

.load-more {
  padding: 20rpx 0;
  text-align: center;
  color: #64748b;
  font-size: 24rpx;
}
</style>
