<template>
  <view class="page result-page">
    <view class="result-head">
      <view :class="['result-icon', resultState.tone]">{{ resultState.icon }}</view>
      <view class="result-title">{{ resultState.title }}</view>
      <view class="result-desc">{{ resultState.desc }}</view>
    </view>

    <view class="section">
      <view class="section-title">订单摘要</view>
      <view class="row">
        <text>{{ isBatch ? '配送包裹' : '订单号' }}</text>
        <text>{{ isBatch ? `${batchCount} 个包裹` : order.order_no || '-' }}</text>
      </view>
      <view class="row">
        <text>支付状态</text>
        <text>{{ order.payment_status_name || resultState.payText }}</text>
      </view>
      <view class="row">
        <text>配送方式</text>
        <text>{{ order.delivery_method_name || '-' }}</text>
      </view>
      <view class="row total">
        <text>应付金额</text>
        <text>¥{{ money(payableAmount) }}</text>
      </view>
    </view>

    <view v-if="waitPay" class="section notice">
      <view class="section-title">待付款提醒</view>
      <view class="notice-text">{{ deadlineText }}</view>
    </view>

    <view class="actions">
      <button v-if="waitPay" class="primary" :loading="payLoading" @click="pay">继续支付</button>
      <button v-else class="primary" @click="goDetail">查看订单</button>
      <button class="plain" @click="goHome">继续逛逛</button>
      <button v-if="waitPay" class="plain" @click="goDetail">查看订单</button>
    </view>
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import { orderService } from '../../services/order'
import { paymentService } from '../../services/payment'
import { money } from '../../utils/money'

const id = ref('')
const result = ref('created')
const order = ref({})
const payLoading = ref(false)
const batchCount = ref(0)
const batchAmount = ref('')

const payableAmount = computed(() => {
  if (isBatch.value && batchAmount.value) return batchAmount.value
  const data = order.value || {}
  return data.payable_amount === undefined || data.payable_amount === null ? data.total_amount : data.payable_amount
})
const isBatch = computed(() => batchCount.value > 1 || result.value === 'batch_offline')
const waitPay = computed(() => !isBatch.value && (result.value === 'wait_pay' || order.value.payment_status === 'UNPAID'))
const resultState = computed(() => {
  if (isBatch.value) {
    return {
      icon: '单',
      tone: 'success',
      title: '订单已提交',
      desc: `已生成 ${batchCount.value || 0} 个配送包裹，平台会按包裹安排备货。`,
      payText: '已提交',
    }
  }
  if (waitPay.value) {
    return {
      icon: '待',
      tone: 'warn',
      title: '订单已提交',
      desc: '请完成支付，平台会在付款确认后处理订单。',
      payText: '待付款',
    }
  }
  if (result.value === 'paid' || order.value.payment_status === 'PAID') {
    return {
      icon: '成',
      tone: 'success',
      title: '支付成功',
      desc: '订单已提交，平台会尽快处理。',
      payText: '已支付',
    }
  }
  if (result.value === 'offline' || order.value.payment_status === 'OFFLINE') {
    return {
      icon: '单',
      tone: 'success',
      title: '订单已提交',
      desc: '请按约定完成线下付款，平台会尽快确认。',
      payText: '线下付款',
    }
  }
  return {
    icon: '单',
    tone: 'success',
    title: '订单已提交',
    desc: '可以在订单详情里查看处理进度。',
    payText: '已提交',
  }
})
const deadlineText = computed(() => {
  const value = order.value.pay_deadline_at
  if (!value) return '请尽快完成支付，超时订单可能会自动关闭。'
  const text = String(value).replace('T', ' ').slice(0, 16)
  return `请在 ${text} 前完成支付，超时订单可能会自动关闭。`
})

async function load() {
  if (!id.value) return
  try {
    order.value = await orderService.detail(id.value)
  } catch (err) {
    uni.showToast({ title: err.message || '订单加载失败', icon: 'none' })
  }
}

async function pay() {
  if (payLoading.value || !id.value) return
  payLoading.value = true
  try {
    const prepay = await paymentService.prepay(id.value)
    if (!prepay.paid) {
      await paymentService.requestPayment(prepay.pay_params)
    }
    result.value = 'paid'
    await load()
    uni.showToast({ title: '支付成功', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '支付未完成', icon: 'none' })
  } finally {
    payLoading.value = false
  }
}

function goDetail() {
  if (isBatch.value) {
    uni.switchTab({ url: '/pages/order-list/order-list' })
    return
  }
  if (!id.value) {
    uni.switchTab({ url: '/pages/order-list/order-list' })
    return
  }
  uni.redirectTo({ url: `/pages/order-detail/order-detail?id=${id.value}` })
}

function goHome() {
  uni.switchTab({ url: '/pages/index/index' })
}

onLoad((query = {}) => {
  id.value = query.id || ''
  result.value = query.result || 'created'
  batchCount.value = Number(query.count || 0)
  batchAmount.value = query.amount || ''
  load()
})
</script>

<style scoped>
.result-page {
  padding-bottom: 40rpx;
}

.result-head {
  padding: 50rpx 28rpx 36rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
  text-align: center;
}

.result-icon {
  width: 96rpx;
  height: 96rpx;
  margin: 0 auto 22rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: #fff;
  font-size: 34rpx;
  font-weight: 900;
}

.result-icon.success {
  background: #0f766e;
}

.result-icon.warn {
  background: #d97706;
}

.result-title {
  color: #17202a;
  font-size: 36rpx;
  font-weight: 900;
}

.result-desc {
  margin-top: 12rpx;
  color: #64748b;
  font-size: 26rpx;
  line-height: 1.45;
}

.section {
  margin-top: 18rpx;
  padding: 22rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.section-title {
  margin-bottom: 12rpx;
  color: #17202a;
  font-size: 28rpx;
  font-weight: 850;
}

.row {
  min-height: 56rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20rpx;
  color: #475569;
  font-size: 26rpx;
}

.row text:last-child {
  max-width: 430rpx;
  text-align: right;
  color: #17202a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row.total {
  margin-top: 8rpx;
  padding-top: 12rpx;
  border-top: 1rpx solid #eef2f7;
  font-weight: 850;
}

.row.total text:last-child {
  color: #b42318;
  font-size: 32rpx;
}

.notice {
  background: #fffbeb;
  border-color: #fde68a;
}

.notice-text {
  color: #92400e;
  font-size: 25rpx;
  line-height: 1.5;
}

.actions {
  margin-top: 24rpx;
  display: grid;
  grid-template-columns: 1fr;
  gap: 14rpx;
}

.primary,
.plain {
  height: 82rpx;
  line-height: 82rpx;
  padding: 0;
  border-radius: 8rpx;
  font-size: 28rpx;
  font-weight: 850;
}

.primary {
  border: 0;
  background: #0f766e;
  color: #fff;
}

.plain {
  border: 1rpx solid #d7dde8;
  background: #fff;
  color: #0f766e;
}

.primary::after,
.plain::after {
  border: 0;
}
</style>
