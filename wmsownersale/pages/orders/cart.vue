<template>
  <view class="page-shell">
    <view class="page-header">
      <view class="context-summary">
        <view><text class="context-label">客户</text>{{ cart.customer?.name || '未选择' }}</view>
        <view><text class="context-label">仓库</text>{{ cart.warehouse_name || '未选择' }}</view>
      </view>
      <view class="context-actions">
        <button class="link-button" @click="changeWarehouse">更换仓库</button>
        <button class="link-button" @click="changeCustomer">更换客户</button>
      </view>
      <view v-if="cart.owner_reject_reason" class="reject-reason">
        最近退回原因：{{ cart.owner_reject_reason }}
      </view>
    </view>

    <scroll-view class="content" scroll-y>
      <view class="receiver-card">
        <view class="section-title">订单与收件信息</view>
        <view class="receiver-grid">
          <label class="receiver-item">
            <text class="field-label">平台单号</text>
            <input v-model="form.src_bill_no" class="field-input" placeholder="可选，平台单号" />
          </label>
          <label v-if="isCashCustomer" class="receiver-item">
            <text class="field-label">收件人</text>
            <input v-model="form.contact" class="field-input" placeholder="请输入收件人" />
          </label>
          <label v-if="isCashCustomer" class="receiver-item">
            <text class="field-label">联系电话</text>
            <input v-model="form.contact_phone" class="field-input" placeholder="请输入联系电话" />
          </label>
          <label v-if="isCashCustomer" class="receiver-item receiver-item-full">
            <text class="field-label">收货地址</text>
            <input v-model="form.ship_to" class="field-input" placeholder="请输入完整收货地址" />
          </label>
        </view>
      </view>

      <view v-if="!cart.items.length" class="empty-state">
        <view>购物车中还没有商品</view>
        <button class="empty-action" @click="backToProducts">去选择商品</button>
      </view>

      <CartItemEditor
        v-for="(item, index) in cart.items"
        :key="item.product_id"
        :item="item"
        :quantity-draft="quantityDraft(item)"
        :quantity-error="quantityErrors[item.product_id] || ''"
        :amount="fmt(Number(item.qty || 0) * Number(item.price || 0))"
        @price-input="setPrice(item, $event)"
        @price-commit="enforceMin(item)"
        @quantity-input="onQuantityInput(item, index, $event)"
        @quantity-commit="commitQuantity(item, index)"
        @remove="removeItem(index)"
      />
    </scroll-view>

    <view class="footer">
      <view class="total-row">
        <text>共 {{ cart.items.length }} 种，{{ cart.totalQty }} 件</text>
        <text class="total-amount">合计：¥ {{ fmt(cart.totalAmount) }}</text>
      </view>
      <view class="button-row">
        <button class="secondary-button" @click="backToProducts">继续选品</button>
        <button
          v-if="cart.editing_order_id"
          class="secondary-button"
          :disabled="!canSubmit"
          :loading="submitting"
          @click="saveDraft(false)"
        >保存草稿</button>
        <button
          class="primary-button"
          :disabled="!canSubmit"
          :loading="submitting"
          @click="submitOrder"
        >{{ submitting ? '提交中' : (cart.editing_order_id ? '保存并重新提交' : '提交订单') }}</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'

import CartItemEditor from '@/components/CartItemEditor.vue'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'
import { isCashCustomer as isCashCustomerRecord } from '@/utils/customer'
import { enforceMinimumPrice, initializePriceGuard, isPriceAllowed } from '@/utils/pricing'
import { validateCartQuantity } from '@/utils/quantity'
import { api } from '@/utils/request'

const cart = useCart()
const auth = useAuth()
const submitting = ref(false)
const quantityDrafts = reactive({})
const quantityErrors = reactive({})
const form = cart.order_header

const isCashCustomer = computed(() => isCashCustomerRecord(cart.customer))
const fmt = value => Number(value || 0).toFixed(2)

function quantityDraft(item) {
  const key = String(item.product_id)
  if (!(key in quantityDrafts)) quantityDrafts[key] = String(item.qty ?? '')
  return quantityDrafts[key]
}

function validateAllQuantities() {
  let valid = true
  cart.items.forEach(item => {
    const key = String(item.product_id)
    const result = validateCartQuantity(quantityDraft(item), item.available)
    quantityErrors[key] = result.error
    if (!result.valid || Number(item.qty) !== result.value) valid = false
  })
  return valid
}

const canSubmit = computed(() => {
  if (
    submitting.value ||
    !cart.warehouse_id ||
    !cart.customer ||
    !cart.items.length
  ) return false
  return cart.items.every(item => {
    const result = validateCartQuantity(quantityDraft(item), item.available)
    return result.valid && Number(item.qty) === result.value && !quantityErrors[item.product_id]
  })
})

function setPrice(item, value) {
  item.price = value == null || value === '' ? '' : String(value)
}

function enforceMin(item) {
  const result = enforceMinimumPrice(item)
  if (!result.valid) uni.showToast({ title: result.error, icon: 'none' })
}

function onQuantityInput(item, index, value) {
  const key = String(item.product_id)
  quantityDrafts[key] = value == null ? '' : String(value)
  const result = validateCartQuantity(quantityDrafts[key], item.available)
  quantityErrors[key] = result.error
  if (result.valid) cart.setQty(index, result.value)
}

function commitQuantity(item, index) {
  const key = String(item.product_id)
  const result = validateCartQuantity(quantityDrafts[key], item.available)
  if (result.overAvailable && Number(result.available) >= 0.001) {
    const adjusted = Number(Number(result.available).toFixed(3))
    quantityDrafts[key] = String(adjusted)
    quantityErrors[key] = ''
    cart.setQty(index, adjusted)
    uni.showToast({ title: `已调整为可用库存 ${adjusted}`, icon: 'none' })
    return
  }
  quantityErrors[key] = result.error
}

function removeItem(index) {
  const item = cart.items[index]
  if (!item) return
  const key = String(item.product_id)
  cart.remove(index)
  delete quantityDrafts[key]
  delete quantityErrors[key]
}

function backToProducts() {
  uni.redirectTo({ url: '/pages/products/search' })
}

function changeWarehouse() {
  uni.showModal({
    title: '更换出库仓库',
    content: '更换仓库会清空当前客户、商品和订单收件信息，是否继续？',
    confirmText: '继续更换',
    success: ({ confirm }) => {
      if (confirm) {
        uni.redirectTo({ url: '/pages/warehouses/select?mode=change&returnTo=products' })
      }
    },
  })
}

function changeCustomer() {
  uni.navigateTo({ url: '/pages/customers/select?mode=change&returnTo=cart' })
}

function buildPayload({ includeVersion = false } = {}) {
  const payload = {
    warehouse_id: cart.warehouse_id,
    customer_id: cart.customer?.id,
    outbound_type: 'SALES',
    delivery_method: form.delivery_method || null,
    etd: form.etd || null,
    remark: String(form.remark || '业务员下单').trim(),
    src_bill_no: String(form.src_bill_no || '').trim(),
    contact: String(form.contact || '').trim(),
    contact_phone: String(form.contact_phone || '').trim(),
    ship_to: String(form.ship_to || '').trim(),
    items: cart.items.map(item => ({
      product_id: item.product_id,
      qty: Number(item.qty),
      price: Number(item.price),
    })),
  }
  if (includeVersion) payload.expected_updated_at = cart.editing_updated_at
  return payload
}

function validateOrder() {
  if (!cart.warehouse_id) {
    uni.showToast({ title: '请先选择出库仓库', icon: 'none' })
    return false
  }
  if (!cart.customer?.id) {
    uni.showToast({ title: '请先选择客户', icon: 'none' })
    return false
  }
  if (!cart.items.length) {
    uni.showToast({ title: '请先添加商品', icon: 'none' })
    return false
  }
  if (!validateAllQuantities()) {
    uni.showToast({ title: '存在数量不正确的商品，请修正后再提交', icon: 'none' })
    return false
  }
  if (cart.items.some(item => !isPriceAllowed(item))) {
    uni.showToast({ title: '存在价格低于系统最低价的商品，请修正后再提交', icon: 'none' })
    return false
  }
  if (isCashCustomer.value) {
    if (!String(form.contact || '').trim()) {
      uni.showToast({ title: '请填写收件人', icon: 'none' })
      return false
    }
    const phone = String(form.contact_phone || '').trim()
    if (!phone) {
      uni.showToast({ title: '请填写联系电话', icon: 'none' })
      return false
    }
    if (phone.length < 6 || !/\d/.test(phone)) {
      uni.showToast({ title: '联系电话格式不正确', icon: 'none' })
      return false
    }
    if (!String(form.ship_to || '').trim()) {
      uni.showToast({ title: '请填写收货地址', icon: 'none' })
      return false
    }
  }
  return true
}

async function saveDraft(resubmit = false) {
  if (submitting.value || !cart.editing_order_id || !validateOrder()) return false
  if (!cart.editing_updated_at) {
    uni.showModal({
      title: '编辑上下文已失效',
      content: '请返回订单详情后重新进入编辑。',
      showCancel: false,
    })
    return false
  }

  submitting.value = true
  try {
    const updated = await api.updateOutboundOrder(
      cart.editing_order_id,
      buildPayload({ includeVersion: true }),
    )
    cart.setEditingUpdatedAt(updated?.updated_at)
    if (!resubmit) {
      uni.showToast({ title: '草稿已保存', icon: 'none' })
      return true
    }

    const result = await api.submitOutboundOrder(cart.editing_order_id)
    const orderId = cart.editing_order_id
    cart.resetOrder()
    uni.showToast({ title: '已重新提交', icon: 'none' })
    setTimeout(() => {
      uni.redirectTo({ url: `/pages/orders/detail?id=${result?.id || orderId}` })
    }, 200)
    return true
  } catch (error) {
    const data = error?.data || {}
    if (
      Number(error?.statusCode || error?.code) === 409 &&
      String(data?.code || '') === 'stale_order_edit'
    ) {
      const orderId = cart.editing_order_id
      uni.showModal({
        title: '订单已被修改',
        content: data?.detail || '订单已被其他会话修改，请重新加载。',
        showCancel: false,
        success: () => {
          cart.resetOrder()
          uni.redirectTo({ url: `/pages/orders/detail?id=${orderId}` })
        },
      })
      return false
    }
    uni.showToast({ title: error?.message || data?.detail || '保存订单失败', icon: 'none' })
    return false
  } finally {
    submitting.value = false
  }
}

async function submitOrder() {
  if (submitting.value) return
  if (cart.editing_order_id) return saveDraft(true)
  if (!validateOrder()) return

  submitting.value = true
  try {
    const response = await api.createOutboundOrder(buildPayload(), cart.ensureIdempotencyKey())
    uni.showToast({ title: `已创建：${response?.order_no || response?.id}`, icon: 'none' })
    cart.resetOrder()
    uni.switchTab({ url: '/pages/features/index' })
  } catch (error) {
    const data = error?.data || {}
    if (Number(error?.statusCode || error?.code) === 409) {
      uni.showModal({
        title: '提交内容冲突',
        content: '本次提交内容已变化，请返回并重新开单。',
        showCancel: false,
      })
      return
    }

    const duplicateMessage = data?.src_bill_no || data?.message || data?.detail || ''
    const existingOrderId = Number(data?.existing_order_id || 0)
    const existingOrderNo = String(data?.existing_order_no || '').trim()
    if (String(duplicateMessage).includes('平台单号重复')) {
      uni.showModal({
        title: '平台单号重复',
        content: existingOrderNo
          ? `该平台单号已存在，对应订单：${existingOrderNo}。是否查看原订单？`
          : '该平台单号已存在。是否查看原订单？',
        confirmText: '查看原单',
        cancelText: '返回修改',
        success: ({ confirm }) => {
          if (confirm && existingOrderId) {
            uni.navigateTo({ url: `/pages/orders/detail?id=${existingOrderId}` })
          }
        },
      })
      return
    }
    uni.showToast({
      title: error?.message || data?.detail || data?.message || '创建订单失败',
      icon: 'none',
    })
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  cart.items.forEach(item => {
    initializePriceGuard(item)
    quantityDrafts[String(item.product_id)] = String(item.qty ?? '')
  })
})

onLoad(() => {
  auth.ensureAuth()
  if (!cart.hasContextForUser(auth.user?.id, auth.user?.owner_id)) {
    cart.resetOrder()
    uni.redirectTo({ url: '/pages/warehouses/select' })
    return
  }
  cart.ensureIdempotencyKey()
})
</script>

<style scoped>
.page-shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; background: #f5f7fa; }
.page-header { flex: 0 0 auto; padding: 20rpx; background: #fff; border-bottom: 1rpx solid #e5e7eb; }
.context-summary { display: flex; flex-wrap: wrap; gap: 12rpx 30rpx; font-size: 28rpx; font-weight: 600; color: #111827; }
.context-label { margin-right: 10rpx; font-size: 24rpx; font-weight: 400; color: #6b7280; }
.context-actions { display: flex; gap: 16rpx; margin-top: 14rpx; }
.link-button { min-height: 88rpx; width: 210rpx; margin: 0; font-size: 26rpx; color: #2563eb; background: #fff; border: 1rpx solid #2563eb; }
.reject-reason { margin-top: 14rpx; padding: 14rpx; color: #9a3412; background: #fff7ed; border-radius: 10rpx; }
.content { flex: 1; min-height: 0; box-sizing: border-box; padding-top: 18rpx; }
.receiver-card { margin: 0 20rpx 18rpx; padding: 22rpx; background: #fff; border-radius: 16rpx; }
.section-title { margin-bottom: 18rpx; font-size: 30rpx; font-weight: 600; color: #111827; }
.receiver-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16rpx; }
.receiver-item { min-width: 0; }
.receiver-item-full { grid-column: 1 / -1; }
.field-label { display: block; margin-bottom: 8rpx; font-size: 24rpx; color: #4b5563; }
.field-input { box-sizing: border-box; width: 100%; min-height: 80rpx; padding: 0 16rpx; font-size: 27rpx; border: 1rpx solid #d1d5db; border-radius: 10rpx; }
.empty-state { padding: 60rpx 24rpx; text-align: center; color: #6b7280; }
.empty-action { min-height: 88rpx; width: 260rpx; margin-top: 20rpx; color: #2563eb; background: #fff; border: 1rpx solid #2563eb; }
.footer { flex: 0 0 auto; padding: 14rpx 20rpx calc(14rpx + env(safe-area-inset-bottom)); background: #fff; border-top: 1rpx solid #d1d5db; }
.total-row { display: flex; justify-content: space-between; gap: 16rpx; margin-bottom: 12rpx; font-size: 26rpx; color: #4b5563; }
.total-amount { font-size: 30rpx; font-weight: 700; color: #b42318; }
.button-row { display: flex; gap: 12rpx; }
.primary-button, .secondary-button { flex: 1; min-height: 88rpx; margin: 0; font-size: 27rpx; }
.primary-button { color: #fff; background: #2563eb; }
.secondary-button { color: #2563eb; background: #fff; border: 1rpx solid #2563eb; }
.primary-button[disabled], .secondary-button[disabled] { color: #9ca3af; background: #e5e7eb; border-color: #e5e7eb; }
@media (max-width: 600px) {
  .receiver-grid { grid-template-columns: 1fr; }
  .receiver-item-full { grid-column: auto; }
  .button-row { flex-wrap: wrap; }
  .primary-button, .secondary-button { min-width: 30%; }
}
</style>
