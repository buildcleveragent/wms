<template>
  <view class="p-4">
    <view class="card" v-if="loading">
      <view class="text-gray">加载中...</view>
    </view>

    <view class="card" v-else-if="order">
      <view class="row">
        <view class="font-bold">{{ order.order_no || ('#' + order.id) }}</view>
        <view class="badge">¥ {{ money(order.total_amount) }}</view>
      </view>

      <view class="text-gray">客户：{{ order.customer_name || order.customer_id || order.customer || '—' }}</view>
      <view class="text-gray" v-if="order.biz_date">业务日期：{{ order.biz_date }}</view>
      <view class="text-gray">业务员：{{ order.created_by_name || order.salesperson_name || order.created_by || '—' }}</view>
      <view class="text-gray">提交状态：{{ order.submit_status_name || order.submit_status || '—' }}</view>
      <view class="text-gray">审核状态：{{ order.approval_status_name || order.approval_status_display || order.approval_status || '—' }}</view>
      <view class="text-gray" v-if="order.memo">备注：{{ order.memo }}</view>
      <view class="text-gray" style="margin-top:8rpx">
        汇总：数量 {{ order.total_qty ?? '—' }} · 金额 ¥ {{ money(order.total_amount) }}
      </view>
    </view>

    <view class="card" v-if="!loading && order">
      <view class="font-bold" style="margin-bottom:12rpx;">商品明细</view>

      <view v-if="!lines.length" class="text-gray">
        暂无明细。若一直为空，请确认后端订单详情接口返回 lines（/api/outbound/orders/{id}/）。
      </view>

      <view v-else>
        <view class="row font-bold" style="margin-bottom:10rpx;">
          <view style="flex:3;">商品</view>
          <view style="flex:2;">单价</view>
          <view style="flex:2;">数量</view>
          <view style="flex:2; text-align:right;">金额</view>
        </view>

        <view
          v-for="(l, i) in lines"
          :key="l?.id ?? i"
          class="row"
          style="padding:12rpx 0; border-bottom:1rpx solid #e5e7eb;"
        >
          <view style="flex:3;">
            <view class="font-bold" style="line-height:1.2;">
              {{ l.product_name || l.product_sku || l.product_code || l.product || l.product_id || ('#' + (l.id ?? i)) }}
            </view>
            <view class="text-gray" v-if="l.product_sku">SKU：{{ l.product_sku }}</view>
          </view>

          <view style="flex:2;">¥ {{ money(linePrice(l)) }}</view>
          <view style="flex:2;">{{ lineQty(l) }}</view>
          <view style="flex:2; text-align:right;">¥ {{ money(lineAmount(l)) }}</view>
        </view>
      </view>
    </view>

    <view class="card" v-if="!loading && !order">
      <view class="text-gray">加载失败或订单不存在</view>
    </view>
  </view>

  <view class="row" style="gap:12rpx; justify-content:flex-start;">
    <template v-if="canReview">
      <button class="btn" :disabled="submitting" @click="approve">
        {{ submitting ? '处理中...' : '审核通过' }}
      </button>
      <button class="btn" :disabled="submitting" @click="reject">退回修改</button>
      <button class="btn" :disabled="submitting" @click="cancel">取消订单</button>
    </template>

    <button class="btn" @click="goBack">返回</button>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useOrderReviewActions } from '@/utils/useOrderReviewActions'

const id = ref(0)
const order = ref(null)
const loading = ref(false)

const lines = computed(() => {
  const o = order.value || {}
  return o.lines || o.items || o.line_items || []
})

const {
  submitting,
  canReview,
  approveOrder,
  rejectOrder,
  cancelOrder,
} = useOrderReviewActions({
  getOrderId: () => id.value,
  getOrder: () => order.value,
  afterApprove: async () => {
    setTimeout(() => {
      uni.navigateBack()
    }, 200)
  },
  afterReject: async () => {
    setTimeout(() => {
      uni.navigateBack()
    }, 200)
  },
  afterCancel: async () => {
    setTimeout(() => {
      uni.navigateBack()
    }, 200)
  },
})

function money(v) {
  const n = Number(v)
  if (Number.isFinite(n)) return n.toFixed(2)
  return (v ?? 0).toString()
}

function lineQty(l) {
  return l.base_qty ?? l.qty ?? l.qty_plan ?? l.qty_done ?? '—'
}

function linePrice(l) {
  return l.base_price ?? l.price ?? 0
}

function lineAmount(l) {
  if (l.amount !== undefined && l.amount !== null) return l.amount
  const q = Number(lineQty(l)) || 0
  const p = Number(linePrice(l)) || 0
  return q * p
}

function goBack() {
  uni.navigateBack()
}

async function loadDetail() {
  if (!id.value) return
  loading.value = true
  try {
    order.value = await api.orderDetail(id.value)
  } catch (e) {
    uni.showToast({ title: e?.data?.detail || '加载详情失败', icon: 'none' })
    order.value = null
  } finally {
    loading.value = false
  }
}

async function approve() {
  await approveOrder()
}

async function reject() {
  await rejectOrder()
}

async function cancel() {
  await cancelOrder()
}

onLoad(async (query) => {
  id.value = Number(query?.id || 0)
  await loadDetail()
})
</script>
