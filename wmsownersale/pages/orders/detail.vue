<template>
  <view class="page p-4">
    <view class="text-lg font-bold mb-2">订单详情</view>

    <view v-if="loading" class="state-card">正在加载订单…</view>
    <view v-else-if="error" class="state-card error-state">
      <view>{{ error }}</view>
      <button class="btn retry-btn" @click="loadOrder">重试</button>
    </view>

    <template v-else-if="order">
      <view class="card summary-card">
        <view class="row">
          <view class="font-bold">{{ order.order_no || ('订单#' + order.id) }}</view>
          <view class="badge">{{ amountSummaryLabel(order) }}</view>
        </view>
        <view class="text-gray">提交状态：{{ orderStatus(order, 'submit_status') }}</view>
        <view class="text-gray">审核状态：{{ orderStatus(order, 'approval_status') }}</view>
        <view v-if="order.owner_reject_reason" class="reject-reason">最近退回原因：{{ order.owner_reject_reason }}</view>
        <view v-if="order.warehouse_reject_reason" class="reject-reason">
          <view>仓库退回原因：{{ order.warehouse_reject_reason }}</view>
          <view v-if="order.approval_status === 'WHS_REJECTED'">请原开单业务员撤回修改，重新提交后由货主管理员审核。</view>
        </view>
        <button v-if="order.can_withdraw" class="btn edit-btn withdraw-btn" :disabled="withdrawing || loadingEdit" @click="withdrawAndEdit">{{ withdrawing ? '正在撤回…' : '撤回并修改' }}</button>
        <button v-if="order.can_edit" class="btn edit-btn" :disabled="loadingEdit" @click="editOrder">
          {{ loadingEdit ? '正在加载…' : '修改订单' }}
        </button>
        <button v-if="order.can_reorder" class="btn edit-btn" @click="reorderOrder">再次下单</button>
      </view>

    </template>
    <view v-if="orderId > 0" class="card"><button @click="openReturns(true)">申请退货</button><button @click="openReturns(false)">关联退货记录</button></view>
    <OrderProgress v-if="orderId > 0" :order-id="orderId" :active="pageVisible" />
    <template v-if="order && !loading && !error">
      <view class="card detail-card">
        <view class="section-title">订单信息</view>
        <view class="field"><text>货主</text><text>{{ order.owner_name || '—' }}</text></view>
        <view class="field"><text>客户</text><text>{{ order.customer_name || '—' }}</text></view>
        <view class="field"><text>仓库</text><text>{{ order.warehouse_name || '—' }}</text></view>
        <view class="field"><text>平台单号</text><text>{{ order.src_bill_no || '—' }}</text></view>
        <view class="field"><text>业务日期</text><text>{{ order.biz_date || '—' }}</text></view>
        <view class="field"><text>配送方式</text><text>{{ deliveryMethod(order.delivery_method) }}</text></view>
        <view class="field"><text>预计发货时间</text><text>{{ order.etd || order.expected_delivery_time || '—' }}</text></view>
      </view>

      <view class="card detail-card">
        <view class="section-title">收件信息</view>
        <view class="field"><text>收件人</text><text>{{ order.contact || order.consignee || '—' }}</text></view>
        <view class="field"><text>联系电话</text><text>{{ order.contact_phone || order.phone || '—' }}</text></view>
        <view class="field field-top"><text>收件地址</text><text>{{ order.ship_to || order.address || '—' }}</text></view>
        <view class="field field-top"><text>备注</text><text>{{ order.memo || order.remark || '—' }}</text></view>
      </view>

      <view class="card line-card">
        <view class="section-title">商品明细</view>
        <view v-if="!(order.lines || []).length" class="state-card">暂无商品明细</view>
        <view v-for="(line, index) in (order.lines || [])" :key="line?.id ?? index" class="line-row">
          <view class="line-name">{{ line?.product_name || line?.product_code || ('商品#' + line?.product) }}</view>
          <view class="line-meta">
            <text v-if="line.is_priced !== false">¥ {{ rate(line?.base_price) }} × {{ qty(line?.base_qty) }}</text>
            <text v-else>数量 {{ qty(line?.base_qty) }}</text>
            <text class="line-amount">{{ line.is_priced === false ? '未计价' : '¥ ' + lineAmount(line) }}</text>
          </view>
        </view>
      </view>
    </template>
  </view>
</template>

<script setup>
import { amountSummaryLabel } from '@/utils/orderAmount'
import { formatQuantity } from '@/utils/quantity'
import { orderStatus } from '@/utils/orderStatus'
import { ref } from 'vue'
import { onHide, onLoad, onShow, onUnload } from '@dcloudio/uni-app'
import OrderProgress from '@/components/OrderProgress.vue'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'

function openReturns(apply) { uni.navigateTo({url: '/pages/returns/index?' + (apply ? 'order_id=' : 'filter_order=') + orderId.value}) }
function reorderOrder() { uni.navigateTo({url: '/pages/orders/reorder?id=' + orderId.value}) }
const order = ref(null)
const loading = ref(false)
const loadingEdit = ref(false)
const withdrawing = ref(false)
const error = ref('')
const orderId = ref(0)
const pageVisible = ref(true)
const firstShow = ref(true)
const auth = useAuth()
const cart = useCart()

let alive = true
let requestGeneration = 0

onUnload(() => {
  pageVisible.value = false
  alive = false
  requestGeneration += 1
})

async function loadOrder() {
  if (!orderId.value) {
    error.value = '订单参数不正确'
    return
  }
  const generation = ++requestGeneration
  loading.value = true
  error.value = ''
  try {
    const response = await api.orderDetail(orderId.value)
    if (!alive || generation !== requestGeneration) return
    order.value = response || null
    if (!order.value) error.value = '订单不存在或已删除'
  } catch (requestError) {
    if (alive && generation === requestGeneration) {
      order.value = null
      error.value = requestError?.data?.detail || requestError?.message || '订单加载失败，请稍后重试'
    }
  } finally {
    if (alive && generation === requestGeneration) loading.value = false
  }
}

function number(value) {
  const result = Number(value)
  return Number.isFinite(result) ? result : 0
}

function money(value) {
  return number(value).toFixed(2)
}

function rate(value) {
  return number(value).toFixed(4)
}

function qty(value) {
  if (value === null || value === undefined || value === '') return '—'
  return formatQuantity(value)
}

function lineAmount(line) {
  if (line?.amount !== null && line?.amount !== undefined && line?.amount !== '') {
    return money(line.amount)
  }
  return money(number(line?.base_qty) * number(line?.base_price))
}

function deliveryMethod(value) {
  return ({ PICKUP: '客户自提', OWN_TRUCK: '配送', COURIER: '快递/小包' })[value] || value || '—'
}

async function editOrder() {
  if (!order.value?.id || loadingEdit.value) return
  loadingEdit.value = true
  try {
    auth.ensureAuth()
    const context = await api.orderEditContext(order.value.id)
    const ok = cart.beginEdit({
      user_id: auth.user?.id,
      owner_id: auth.user?.owner_id,
      context,
    })
    if (!ok) throw new Error('订单编辑数据不完整')
    uni.redirectTo({ url: '/pages/orders/cart' })
  } catch (requestError) {
    uni.showToast({
      title: requestError?.data?.detail || requestError?.message || '加载编辑数据失败',
      icon: 'none',
    })
  } finally {
    loadingEdit.value = false
  }
}

async function withdrawAndEdit() {
  if (!order.value?.can_withdraw || withdrawing.value || loadingEdit.value) return
  withdrawing.value = true
  try {
    const accepted = await new Promise(resolve => uni.showModal({
      title: '撤回并修改',
      content: '撤回后可修改订单，重新提交时仍需货主管理员审核。',
      confirmText: '确定', cancelText: '取消',
      success: result => resolve(Boolean(result.confirm)), fail: () => resolve(false),
    }))
    if (!accepted) return
    order.value = await api.withdrawOrder(order.value.id)
    await editOrder()
  } catch (e) {
    uni.showToast({ title: e?.data?.detail || e?.message || '撤回失败，请刷新订单后重试', icon: 'none' })
    await loadOrder()
  } finally {
    withdrawing.value = false
  }
}

onLoad((query) => {
  orderId.value = Number(query?.id || 0)
  loadOrder()
})

onShow(() => {
  pageVisible.value = true
  if (firstShow.value) {
    firstShow.value = false
    return
  }
  loadOrder()
})

onHide(() => { pageVisible.value = false })
</script>

<style scoped>
.page { min-height: 100vh; box-sizing: border-box; }
.state-card { padding: 44rpx 20rpx; text-align: center; color: #6b7280; }
.error-state { color: #b42318; background: #fff7ed; border-radius: 16rpx; }
.retry-btn, .edit-btn { width: auto; min-height: 80rpx; margin-top: 20rpx; }
.reject-reason { margin-top: 16rpx; padding: 16rpx; color: #b42318; background: #fff7ed; border-radius: 10rpx; }
.section-title { margin-bottom: 18rpx; font-size: 30rpx; font-weight: 700; }
.field { display: flex; justify-content: space-between; gap: 24rpx; padding: 12rpx 0; color: #374151; }
.field > text:first-child { flex: 0 0 176rpx; color: #6b7280; }
.field > text:last-child { min-width: 0; text-align: right; overflow-wrap: anywhere; }
.field-top { align-items: flex-start; }
.line-row { padding: 20rpx 0; border-top: 1rpx solid #e5e7eb; }
.line-row:first-of-type { border-top: 0; }
.line-name { font-weight: 600; overflow-wrap: anywhere; }
.line-meta { display: flex; justify-content: space-between; gap: 20rpx; margin-top: 10rpx; color: #6b7280; }
.line-amount { color: #111827; font-weight: 700; }
</style>
