<template>
  <view class="p-4">
    <view class="tabs row">
      <button class="tab" :class="{ active: tab === 'PENDING' }" @click="switchTab('PENDING')">待审核</button>
      <button class="tab" :class="{ active: tab === 'APPROVED' }" @click="switchTab('APPROVED')">已通过</button>
      <button class="tab" :class="{ active: tab === 'REJECTED' }" @click="switchTab('REJECTED')">已退回修改</button>
      <button class="tab" :class="{ active: tab === 'CANCELLED' }" @click="switchTab('CANCELLED')">已取消</button>
    </view>

    <view class="bar">
      <input class="input" v-model="q" placeholder="单号 / 客户名 / 客户编码" @confirm="search" />
      <button class="btn" @click="search">搜索</button>
    </view>

    <view v-if="loading && !rows.length" class="state-card">正在加载订单…</view>
    <view v-else-if="error && !rows.length" class="state-card error-state">
      <view>{{ error }}</view>
      <button class="btn retry-btn" @click="reload">重试</button>
    </view>

    <view
      v-for="(o, i) in rows"
      :key="o?.id ?? i"
      class="card"
      @click="goApproveDetail(o)"
    >
      <view class="row">
        <view class="font-bold">{{ o.order_no || ('#' + o.id) }}</view>
        <view class="badge">{{ statusText(o) }}</view>
      </view>
      <view class="text-gray">客户：{{ o.customer_name || '—' }}</view>
      <view class="text-gray">数量：{{ o.total_qty ?? '—' }} · 金额：¥ {{ o.total_amount ?? 0 }}</view>
      <view class="text-gray">业务日期：{{ o.biz_date }}</view>

      <view class="row mt-2">
        <template v-if="canReviewRow(o)">
          <button class="btn btn-sm" :disabled="reviewing" @click.stop="approve(o.id)">审核通过</button>
          <button class="btn btn-sm" :disabled="reviewing" @click.stop="goApproveDetail(o)">填写原因并退回</button>
          <button class="btn btn-sm" :disabled="reviewing" @click.stop="cancel(o.id)">取消订单</button>
        </template>
        <template v-else>
          <!-- 只查看，不直接通过；应由业务员修改后重新提交 -->
        </template>
      </view>
    </view>

    <view v-if="error && rows.length" class="state-card error-state">
      <view>{{ error }}</view>
      <button class="btn retry-btn" @click="retryCurrentPage">重试加载</button>
    </view>

    <view class="row" v-if="list.next && !error" style="margin-top:16rpx">
      <button class="btn" :disabled="loading" @click="loadMore">
        {{ loading ? '正在加载…' : '加载更多' }}
      </button>
    </view>

    <view v-if="!rows.length && !loading && !error" class="text-gray mt-4">暂无数据</view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onPullDownRefresh, onReachBottom, onShow, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useOrderReviewActions } from '@/utils/useOrderReviewActions.js'

const TAB = {
  PENDING: 'PENDING',
  APPROVED: 'APPROVED',
  REJECTED: 'REJECTED',
  CANCELLED: 'CANCELLED',
}

const q = ref('')
const tab = ref(TAB.PENDING)
const list = ref({ count: 0, next: null, previous: null, results: [] })
const rows = computed(() => list.value.results || [])
const page = ref(1)
const loading = ref(false)
const error = ref('')
const failedPage = ref(null)
const firstShow = ref(true)

let alive = true
let reqSeq = 0

onUnload(() => {
  alive = false
  reqSeq += 1
})

const { submitting: reviewing, approveOrder, rejectOrder, cancelOrder } = useOrderReviewActions({
  afterApprove: async () => { await reload() },
  afterReject: async () => { await reload() },
  afterCancel: async () => { await reload() },
})

function normalize(res) {
  return Array.isArray(res)
    ? { count: res.length, next: null, previous: null, results: res }
    : (res?.results ? res : { count: 0, next: null, previous: null, results: [] })
}

function queryParams(pageNo = 1) {
  const statusMap = {
    PENDING: 'OWNER_PENDING',
    APPROVED: 'OWNER_APPROVED',
    REJECTED: 'OWNER_REJECTED',
    CANCELLED: 'CANCELLED',
  }

  const status = statusMap[String(tab.value || '').trim().toUpperCase()]
  return {
    page: pageNo,
    search: q.value || '',
    ...(status ? { approval_status: status } : {}),
    ...(tab.value === TAB.PENDING ? { submit_status: 'SUBMITTED' } : {}),
  }
}

async function fetch(pageNo = 1, { reset = false } = {}) {
  const tag = ++reqSeq
  const params = queryParams(pageNo)
  if (reset) {
    list.value = { count: 0, next: null, previous: null, results: [] }
    page.value = 1
  }
  error.value = ''
  failedPage.value = null
  loading.value = true
  try {
    const res = await api.orders(params)
    if (!alive || tag !== reqSeq) return

    const normalized = normalize(res)
    if (pageNo === 1) {
      list.value = normalized
    } else {
      list.value = {
        ...normalized,
        results: Array.from(new Map([
          ...(list.value.results || []),
          ...normalized.results,
        ].map((row) => [String(row.id), row])).values()),
      }
    }
    page.value = pageNo
  } catch (e) {
    if (alive && tag === reqSeq) {
      error.value = e?.data?.detail || e?.message || '订单加载失败，请稍后重试'
      failedPage.value = pageNo
    }
  } finally {
    if (alive && tag === reqSeq) {
      loading.value = false
      uni.stopPullDownRefresh && uni.stopPullDownRefresh()
    }
  }
}

async function search() {
  await fetch(1, { reset: true })
}

async function reload() {
  await fetch(1, { reset: true })
}

async function loadMore() {
  if (!list.value.next || loading.value) return
  await fetch(page.value + 1)
}

async function retryCurrentPage() {
  await fetch(failedPage.value || page.value + 1)
}

function switchTab(nextTab) {
  if (tab.value === nextTab) return
  tab.value = nextTab
  reload()
}

function statusText(o) {
  return o.approval_status_name || ({
    OWNER_PENDING: '待审核',
    OWNER_APPROVED: '已通过',
    OWNER_REJECTED: '待修改',
    CANCELLED: '已取消',
  }[o.approval_status] || '—')
}

function canReviewRow(order) {
  return Boolean(
    order?.can_owner_review &&
    String(order?.submit_status || '') === 'SUBMITTED' &&
    String(order?.approval_status || '') === 'OWNER_PENDING'
  )
}

async function approve(orderId) {
  await approveOrder(orderId)
}

async function cancel(orderId) {
  await cancelOrder(orderId)
}

function goApproveDetail(o) {
  const id = Number(o?.id || 0)
  if (!id) return
  uni.navigateTo({ url: `/pages/approval/approvedetail?id=${id}` })
}

onLoad(() => {
  fetch(1)
})

onPullDownRefresh(() => {
  search()
})

onReachBottom(() => {
  loadMore()
})

onShow(() => {
  if (firstShow.value) {
    firstShow.value = false
    return
  }
  reload()
})
</script>

<style scoped>
.tabs{ gap: 12rpx; margin-bottom: 16rpx; }
.tab{ padding: 10rpx 20rpx; border-radius: 10rpx; border: 1px solid #ddd; background: #fff; }
.tab.active{ border-color: #333; }
.mt-2{ margin-top: 12rpx; }
.btn-sm{ padding: 10rpx 20rpx; font-size: 24rpx; border-radius: 8rpx; }
.state-card{ margin: 24rpx 0; padding: 36rpx 24rpx; text-align: center; color: #6b7280; }
.error-state{ color: #b42318; background: #fff7ed; border-radius: 12rpx; }
.retry-btn{ width: auto; margin-top: 16rpx; }
</style>
