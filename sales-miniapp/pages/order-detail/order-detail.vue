<template>
  <view class="page detail-page">
    <view v-if="order" class="content">
      <view class="head">
        <view>
          <view class="status">{{ order.status_name }}</view>
          <view class="no">{{ order.order_no }}</view>
        </view>
        <OrderStatusTag :status="order.status" :text="order.payment_status_name" />
      </view>

      <view class="section">
        <view class="section-title">收货信息</view>
        <view class="text">{{ order.contact }} {{ order.contact_phone }}</view>
        <view class="text muted">{{ order.ship_to }}</view>
      </view>

      <view class="section">
        <view class="section-title">配送进度</view>
        <view v-if="order.is_combined" class="text muted">本次订单包含 {{ order.order_count }} 个配送包裹，平台会统一安排备货。</view>
        <view class="timeline">
          <view v-for="step in fulfillmentSteps" :key="step.title" :class="['step', step.state]">
            <view class="dot"></view>
            <view class="step-main">
              <view class="step-title">{{ step.title }}</view>
              <view class="step-desc">{{ step.desc }}</view>
            </view>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-title">商品明细</view>
        <view v-for="line in order.lines" :key="line.id" class="line">
          <image v-if="line.image_url" class="thumb" :src="line.image_url" mode="aspectFill" />
          <view class="line-main">
            <view class="line-name">{{ line.product_name }}</view>
            <view v-if="line.product_spec" class="muted">{{ line.product_spec }}</view>
            <view class="muted">{{ line.qty }} {{ line.order_uom }}</view>
          </view>
          <view class="line-amount">¥{{ money(line.line_amount) }}</view>
        </view>
      </view>

      <view class="section">
        <view class="row"><text>配送方式</text><text>{{ order.delivery_method_name }}</text></view>
        <view class="row"><text>商品金额</text><text>¥{{ money(goodsAmount) }}</text></view>
        <view v-for="item in order.adjustments || []" :key="item.id" class="row discount">
          <text>{{ item.title }}</text>
          <text>{{ Number(item.amount) < 0 ? '-' : '' }}¥{{ money(Math.abs(Number(item.amount || 0))) }}</text>
        </view>
        <view class="row total-row"><text>应付金额</text><text class="amount">¥{{ money(payableAmount) }}</text></view>
        <view class="row"><text>备注</text><text>{{ order.remark || '-' }}</text></view>
      </view>

      <view v-if="order.after_sale" class="section">
        <view class="section-title">售后</view>
        <view class="row"><text>申请单</text><text>{{ order.after_sale.request_no }}</text></view>
        <view class="row"><text>类型</text><text>{{ order.after_sale.request_type_name }}</text></view>
        <view class="row"><text>状态</text><text>{{ order.after_sale.status_name }}</text></view>
        <view class="row"><text>金额</text><text>¥{{ money(order.after_sale.amount) }}</text></view>
      </view>

      <view class="actions">
        <button v-if="canPay" class="pay" :loading="payLoading" @click="pay">继续支付</button>
        <button v-if="canReorder" class="reorder" :loading="reorderLoading" @click="reorder">再来一单</button>
        <button v-if="canRefund" class="refund" :loading="refundLoading" @click="refund">申请退款</button>
        <button v-if="canAfterSale" class="refund" :loading="afterSaleLoading" @click="afterSale">申请售后</button>
        <button v-if="canCancel" class="cancel" :loading="loading" @click="cancel">取消订单</button>
      </view>
    </view>
    <EmptyState v-else text="订单加载中" />
  </view>
</template>

<script setup>
import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import OrderStatusTag from '../../components/OrderStatusTag.vue'
import { orderService } from '../../services/order'
import { paymentService } from '../../services/payment'
import { useCartStore } from '../../stores/cart'
import { money } from '../../utils/money'

const cart = useCartStore()
const id = ref('')
const order = ref(null)
const loading = ref(false)
const payLoading = ref(false)
const reorderLoading = ref(false)
const refundLoading = ref(false)
const afterSaleLoading = ref(false)
const goodsAmount = computed(() => {
  const data = order.value || {}
  return data.goods_amount === undefined || data.goods_amount === null ? data.total_amount : data.goods_amount
})
const payableAmount = computed(() => {
  const data = order.value || {}
  return data.payable_amount === undefined || data.payable_amount === null ? data.total_amount : data.payable_amount
})
const fulfillmentSteps = computed(() => {
  const data = order.value || {}
  if (!data.id) return []
  if (data.status === 'CANCELLED') {
    return [
      { title: '订单已提交', desc: dateText(data.created_at), state: 'done' },
      { title: '订单已取消', desc: '商品未继续配送', state: 'bad' },
    ]
  }
  if (['REFUNDING', 'REFUNDED'].includes(data.payment_status)) {
    return [
      { title: '订单已提交', desc: dateText(data.created_at), state: 'done' },
      { title: data.payment_status === 'REFUNDED' ? '退款完成' : '退款处理中', desc: '退款进度以审核结果为准', state: 'bad' },
    ]
  }
  const paid = ['PAID', 'OFFLINE'].includes(data.payment_status)
  const completed = data.status === 'COMPLETED'
  return [
    { title: '订单已提交', desc: dateText(data.created_at), state: 'done' },
    {
      title: paid ? '付款已确认' : '等待付款',
      desc: paid ? dateText(data.paid_at) : payDeadlineText(data.pay_deadline_at),
      state: paid ? 'done' : 'active',
    },
    {
      title: '备货中',
      desc: data.delivery_method_name ? `${data.delivery_method_name} · 平台处理中` : '平台处理中',
      state: completed ? 'done' : paid ? 'active' : 'pending',
    },
    {
      title: completed ? '订单已完成' : '等待收货',
      desc: completed ? '感谢购买' : '请留意配送或自提通知',
      state: completed ? 'done' : 'pending',
    },
  ]
})
const beforeWarehouseWork = computed(
  () =>
    Boolean(order.value) &&
    ['OWNER_PENDING', 'OWNER_APPROVED', 'WHS_PENDING'].includes(order.value.approval_status),
)
const afterWarehouseWork = computed(
  () =>
    Boolean(order.value) &&
    (order.value.approval_status === 'WHS_APPROVED' || ['PROCESSING', 'COMPLETED'].includes(order.value.status)),
)
const canCancel = computed(
  () =>
    Boolean(order.value) &&
    beforeWarehouseWork.value &&
    !['PAID', 'REFUNDING', 'REFUNDED'].includes(order.value.payment_status),
)
const canPay = computed(() => Boolean(order.value) && !order.value.is_combined && order.value.payment_status === 'UNPAID' && order.value.status !== 'CANCELLED')
const canReorder = computed(() => Boolean(order.value && order.value.lines && order.value.lines.length))
const canRefund = computed(
  () => Boolean(order.value) && !order.value.is_combined && order.value.payment_status === 'PAID' && beforeWarehouseWork.value,
)
const canAfterSale = computed(
  () =>
    Boolean(order.value) &&
    !order.value.is_combined &&
    afterWarehouseWork.value &&
    ['PAID', 'OFFLINE'].includes(order.value.payment_status) &&
    (!order.value.after_sale || order.value.after_sale.status !== 'PENDING'),
)

function dateText(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 16)
}

function payDeadlineText(value) {
  const deadline = dateText(value)
  return deadline ? `请在 ${deadline} 前完成付款` : '请尽快完成付款'
}

async function load() {
  order.value = await orderService.detail(id.value)
}

async function cancel() {
  if (loading.value) return
  loading.value = true
  try {
    order.value = await orderService.cancel(id.value)
    uni.showToast({ title: '已取消', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '取消失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

async function pay() {
  if (payLoading.value || !order.value) return
  payLoading.value = true
  try {
    const prepay = await paymentService.prepay(order.value.id)
    if (!prepay.paid) {
      await paymentService.requestPayment(prepay.pay_params)
    }
    await load()
    uni.showToast({ title: '支付成功', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '支付未完成', icon: 'none' })
  } finally {
    payLoading.value = false
  }
}

async function reorder() {
  if (reorderLoading.value || !order.value) return
  reorderLoading.value = true
  const failures = []
  let added = 0
  try {
    for (const line of order.value.lines || []) {
      try {
        await cart.addProduct(
          {
            id: line.product_id,
            owner_id: order.value.owner_id || line.owner_id,
            config_id: line.config_id,
            order_uom: line.order_uom,
          },
          Number(line.qty || 1),
        )
        added += 1
      } catch (err) {
        failures.push(err.message || `${line.product_name || '商品'} 不可购买`)
      }
    }
    if (!added) {
      throw new Error(failures[0] || '商品已下架或库存不足')
    }
    uni.showToast({
      title: failures.length ? '部分商品已加入购物车' : '已加入购物车',
      icon: 'none',
    })
    uni.switchTab({ url: '/pages/cart/cart' })
  } catch (err) {
    uni.showToast({ title: err.message || '再来一单失败', icon: 'none' })
  } finally {
    reorderLoading.value = false
  }
}

async function refund() {
  if (refundLoading.value || !order.value) return
  refundLoading.value = true
  try {
    const data = await paymentService.refund(order.value.id, '用户申请退款')
    order.value = data.order || order.value
    uni.showToast({ title: '退款申请已提交', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '退款申请失败', icon: 'none' })
  } finally {
    refundLoading.value = false
  }
}

async function afterSale() {
  if (afterSaleLoading.value || !order.value) return
  afterSaleLoading.value = true
  try {
    await orderService.afterSale(order.value.id, '用户申请售后')
    await load()
    uni.showToast({ title: '售后申请已提交', icon: 'none' })
  } catch (err) {
    uni.showToast({ title: err.message || '售后申请失败', icon: 'none' })
  } finally {
    afterSaleLoading.value = false
  }
}

onLoad((query = {}) => {
  id.value = query.id
  load()
})
</script>

<style scoped>
.content {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.head,
.section {
  padding: 22rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.head {
  display: flex;
  justify-content: space-between;
  gap: 16rpx;
}

.status {
  color: #17202a;
  font-size: 34rpx;
  font-weight: 850;
}

.no {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 24rpx;
}

.section-title {
  margin-bottom: 14rpx;
  color: #17202a;
  font-size: 28rpx;
  font-weight: 800;
}

.timeline {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.step {
  display: flex;
  gap: 14rpx;
  color: #94a3b8;
}

.dot {
  width: 18rpx;
  height: 18rpx;
  margin-top: 9rpx;
  border-radius: 50%;
  background: #cbd5e1;
  flex-shrink: 0;
}

.step-main {
  min-width: 0;
}

.step-title {
  color: #64748b;
  font-size: 26rpx;
  font-weight: 800;
}

.step-desc {
  margin-top: 4rpx;
  color: #94a3b8;
  font-size: 23rpx;
  line-height: 1.45;
}

.step.done .dot,
.step.active .dot {
  background: #0f766e;
}

.step.done .step-title,
.step.active .step-title {
  color: #17202a;
}

.step.active .step-desc {
  color: #0f766e;
}

.step.bad .dot {
  background: #b42318;
}

.step.bad .step-title {
  color: #b42318;
}

.text,
.muted,
.row {
  color: #475569;
  font-size: 25rpx;
  line-height: 1.55;
}

.muted {
  color: #64748b;
}

.line {
  display: flex;
  gap: 14rpx;
  padding: 14rpx 0;
  border-bottom: 1rpx solid #eef2f7;
}

.line:last-child {
  border-bottom: 0;
}

.thumb {
  width: 104rpx;
  height: 104rpx;
  border-radius: 8rpx;
  background: #eef2f7;
  flex-shrink: 0;
}

.line-main {
  flex: 1;
  min-width: 0;
}

.line-name {
  color: #17202a;
  font-size: 26rpx;
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.line-amount,
.amount {
  color: #b42318;
  font-weight: 850;
}

.row {
  min-height: 56rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20rpx;
}

.row.discount {
  color: #0f766e;
}

.total-row {
  margin-top: 8rpx;
  padding-top: 12rpx;
  border-top: 1rpx solid #eef2f7;
  color: #17202a;
  font-weight: 850;
}

.actions {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.cancel,
.pay,
.reorder,
.refund {
  height: 82rpx;
  line-height: 82rpx;
  border-radius: 8rpx;
  font-size: 28rpx;
  font-weight: 750;
}

.pay {
  border: 1rpx solid #0f766e;
  background: #0f766e;
  color: #fff;
}

.reorder {
  border: 1rpx solid #b6d8d2;
  background: #ecfdf5;
  color: #0f766e;
}

.refund {
  border: 1rpx solid #fde68a;
  background: #fffbeb;
  color: #a16207;
}

.cancel {
  border: 1rpx solid #f0c8bf;
  background: #fff7f4;
  color: #b42318;
}

.cancel::after,
.pay::after,
.reorder::after,
.refund::after {
  border: 0;
}
</style>
