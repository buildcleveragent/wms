<template>
  <view class="page-shell">
    <view class="page-header">
      <view class="context-summary">
        <view><text class="context-label">客户</text>{{ cart.customer?.name || '未选择' }}</view>
        <view><text class="context-label">仓库</text>{{ cart.warehouse_name || '未选择' }}</view>
      </view>
      <view class="context-actions">
        <button class="link-button" :disabled="submitting || !!cart.pending_submission" @click="changeWarehouse">更换仓库</button>
        <button class="link-button" :disabled="submitting || !!cart.pending_submission" @click="changeCustomer">更换客户</button>
      </view>
      <view v-if="cart.owner_reject_reason" class="reject-reason">
        最近退回原因：{{ cart.owner_reject_reason }}
      </view>
    </view>

    <scroll-view class="content" scroll-y>
      <view class="receiver-card">
        <view class="section-title">订单与收件信息</view>
        <view v-if="cart.historical_address" class="address-hint">来自历史订单，请确认收件信息。</view>
        <button class="link-button address-button" :disabled="submitting || !!cart.pending_submission" @click="openAddresses">选择常用地址 / 管理地址</button>
        <view v-if="addressError" class="validation-error">{{ addressError }}</view>
        <view class="receiver-grid">
          <label class="receiver-item">
            <text class="field-label">平台单号</text>
            <input v-model="form.src_bill_no" :disabled="submitting || !!cart.pending_submission" class="field-input" placeholder="可选，平台单号" />
          </label>
          <label class="receiver-item">
            <text class="field-label">收件人</text>
            <input v-model="form.contact" :disabled="submitting || !!cart.pending_submission" class="field-input" placeholder="请输入收件人" maxlength="80" />
          </label>
          <label class="receiver-item">
            <text class="field-label">联系电话</text>
            <input v-model="form.contact_phone" :disabled="submitting || !!cart.pending_submission" class="field-input" placeholder="请输入联系电话" maxlength="40" />
          </label>
          <label class="receiver-item receiver-item-full">
            <text class="field-label">收货地址</text>
            <input v-model="form.ship_to" :disabled="submitting || !!cart.pending_submission" class="field-input" placeholder="请输入完整收货地址" maxlength="200" />
          </label>
          <label class="receiver-item receiver-item-full">
            <text class="field-label">订单备注（选填）</text>
            <textarea v-model="form.remark" :disabled="submitting || !!cart.pending_submission" class="field-input remark-input" placeholder="可填写原出库日期、原单号或其他说明" maxlength="100" />
            <view class="remark-count">{{ form.remark.length }}/100</view>
          </label>
        </view>
      </view>
      <view class="receiver-card">
        <view>价格与库存复核</view>
        <view class="address-hint">{{ checkedAt ? '最近成功检查：' + checkedAt : '尚未完成当前条件检查' }}</view>
        <view class="address-hint">提交不锁定库存，审核时重新分配。</view>
        <view v-if="checkError" class="validation-error">{{ checkError }}</view>
        <view v-if="cart.pending_submission" class="validation-error">提交结果待确认，请重试确认结果。重试不会重复下单。</view>
        <button :disabled="checking || submitting || !!cart.pending_submission || denied" @click="checkCurrent">{{ checking ? '检查中…' : '确认当前信息并重新校验' }}</button>
      </view>

      <view v-if="!cart.items.length" class="empty-state">
        <view>购物车中还没有商品</view>
        <button class="empty-action" @click="backToProducts">去选择商品</button>
      </view>

      <CartItemEditor
        v-for="(item, index) in cart.items"
        :key="lineKey(item)"
        :item="item"
        :quantity-draft="quantityDraft(item)"
        :quantity-error="quantityErrors[lineKey(item)] || ''"
        :disabled="submitting || !!cart.pending_submission"
        :amount="item.is_priced === false ? '' : lineAmount(item.qty, item.price)"
        @price-input="setPrice(item, $event)"
        @price-commit="enforceMin(item)"
        @quantity-input="onQuantityInput(item, index, $event)"
        @quantity-commit="commitQuantity(item, index)"
        @remove="removeItem(index)"
      />
    </scroll-view>

    <view class="footer">
      <view class="total-row">
        <text>共 {{ cart.items.length }} 行</text>
        <text class="total-amount">{{ amountSummaryLabel({items: cart.items, total_amount: cart.totalAmount}) }}</text>
      </view>
      <view class="button-row">
        <button class="secondary-button" :disabled="submitting || !!cart.pending_submission" @click="backToProducts">继续选品</button>
        <button
          v-if="cart.editing_order_id"
          class="secondary-button"
          :disabled="submitting || !cart.items.length || !!cart.pending_submission"
          :loading="submitting"
          @click="saveDraft(false)"
        >保存草稿</button>
        <button
          class="primary-button"
          :disabled="!canSubmit"
          :loading="submitting"
          @click="submitOrder"
        >{{ submitting ? '提交中' : (cart.pending_submission ? '重试确认提交结果' : cart.editing_order_id ? '保存并重新提交' : '提交订单') }}</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'

import CartItemEditor from '@/components/CartItemEditor.vue'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'
import { isCashCustomer as isCashCustomerRecord } from '@/utils/customer'
import { enforceMinimumPrice, initializePriceGuard, isPriceAllowed } from '@/utils/pricing'
import { formatQuantity, validateCartQuantity } from '@/utils/quantity'
import { amountSummaryLabel, lineAmount } from '@/utils/orderAmount'
import { api } from '@/utils/request'

const cart = useCart()
const auth = useAuth()
const submitting = ref(false)
const quantityDrafts = reactive({})
const quantityErrors = reactive({})
const form = cart.order_header
const checkedAt = ref(''), checkError = ref(''), checking = ref(false), denied = ref(false), addressError = ref('')
let alive = true, active = true, generation = 0, firstShow = true, validationFlight = null, addressFlight = null
const identity = () => `${auth.user?.id}:${auth.user?.owner_id}`
const lineKey = item => String(item.line_key || item.product_id)
const checkoutSignature = () => JSON.stringify(buildPayload())

const isCashCustomer = computed(() => isCashCustomerRecord(cart.customer))

function quantityDraft(item) {
  const key = lineKey(item)
  if (!(key in quantityDrafts)) quantityDrafts[key] = formatQuantity(item.qty)
  return quantityDrafts[key]
}

function validateAllQuantities() {
  let valid = true
  cart.items.forEach((item, index) => {
    const key = lineKey(item)
    const result = validateCartQuantity(quantityDraft(item), cart.remainingFor(index))
    quantityErrors[key] = result.error
    if (!result.valid || Number(item.qty) !== result.value) valid = false
  })
  return valid
}

const canSubmit = computed(() => {
  if (denied.value || submitting.value) return false
  if (cart.pending_submission) return true
  if (
    submitting.value ||
    !cart.warehouse_id ||
    !cart.customer ||
    !cart.items.length
  ) return false
  return cart.items.every((item, index) => {
    const result = validateCartQuantity(quantityDraft(item), cart.remainingFor(index))
    return result.valid && Number(item.qty) === result.value && !quantityErrors[lineKey(item)]
  })
})

function setPrice(item, value) {
  item.price = value == null || value === '' ? '' : String(value)
}

function enforceMin(item) {
  const result = enforceMinimumPrice(item)
  if (!result.valid) uni.showToast({ title: result.error, icon: 'none' })
  checkCurrent()
}

function onQuantityInput(item, index, value) {
  const key = lineKey(item)
  quantityDrafts[key] = value == null ? '' : String(value)
  const result = validateCartQuantity(quantityDrafts[key])
  // Keep the requested quantity even above stock. Submission is guarded using
  // the sum of every row of this product, so editing another row can resolve it.
  if (result.valid) item.qty = result.value
  validateAllQuantities()
}

function commitQuantity(item, index) {
  const key = lineKey(item)
  const result = validateCartQuantity(quantityDrafts[key], cart.remainingFor(index))
  quantityErrors[key] = result.error
  if (validateCartQuantity(quantityDrafts[key]).valid) checkCurrent()
}

function removeItem(index) {
  const item = cart.items[index]
  if (!item) return
  const key = lineKey(item)
  cart.remove(index)
  delete quantityDrafts[key]
  delete quantityErrors[key]
  validateAllQuantities()
  checkCurrent()
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
function openAddresses() {
  uni.navigateTo({url: `/pages/customers/addresses?customer_id=${cart.customer?.id}&select=1`})
}

function buildPayload({ includeVersion = false } = {}) {
  const payload = {
    warehouse_id: cart.warehouse_id,
    customer_id: cart.customer?.id,
    outbound_type: 'SALES',
    delivery_method: form.delivery_method || null,
    etd: form.etd || null,
    remark: String(form.remark || '').trim(),
    src_bill_no: String(form.src_bill_no || '').trim(),
    contact: String(form.contact || '').trim(),
    contact_phone: String(form.contact_phone || '').trim(),
    ship_to: String(form.ship_to || '').trim(),
    ...(cart.editing_order_id ? { editing_order_id: cart.editing_order_id } : {}),
    ...(cart.reorder_source_id ? { reorder_source_id: cart.reorder_source_id } : {}),
    items: cart.items.map(item => ({
      product_id: item.product_id,
      qty: String(quantityDraft(item)),
      price: item.is_priced === false || item.price === '' || item.price == null ? null : String(item.price),
      line_key: lineKey(item),
      base_uom_id: item.base_uom_id || null,
      catalog_version: item.catalog_version || '',
      unit_unverified: Boolean(item.unit_unverified),
    })),
  }
  if (includeVersion) payload.expected_updated_at = cart.editing_updated_at
  return payload
}

function validateOrder({draft = false} = {}) {
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
  if (!draft && !validateAllQuantities()) {
    uni.showToast({ title: '存在数量不正确的商品，请修正后再提交', icon: 'none' })
    return false
  }
  if (!draft && cart.items.some(item => !isPriceAllowed(item))) {
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
  if (submitting.value || !cart.editing_order_id || !validateOrder({draft:!resubmit})) return false
  if (!cart.editing_updated_at) {
    uni.showModal({
      title: '编辑上下文已失效',
      content: '请返回订单详情后重新进入编辑。',
      showCancel: false,
    })
    return false
  }

  submitting.value = true
  const actor = identity(), tag = generation
  try {
    if (resubmit && !await checkCurrent()) return false
    const updated = await api.updateOutboundOrder(
      cart.editing_order_id,
      buildPayload({ includeVersion: true }),
    )
    if (!alive || !active || actor !== identity() || tag !== generation) return false
    cart.setEditingUpdatedAt(updated?.updated_at)
    // Draft saves replace database lines; keep the identities returned by the server.
    if (updated?.lines?.length === cart.items.length) {
      cart.items.forEach((item, index) => {
        const line = updated.lines[index], oldKey = lineKey(item)
        const draft = quantityDraft(item), error = quantityErrors[oldKey] || ''
        delete quantityDrafts[oldKey]
        delete quantityErrors[oldKey]
        item.line_key = `edit-${line.id}`
        item.is_priced = line.is_priced !== false
        if (!item.is_priced) item.price = null
        quantityDrafts[lineKey(item)] = draft
        quantityErrors[lineKey(item)] = error
      })
    }
    if (!resubmit) {
      uni.showToast({ title: '草稿已保存', icon: 'none' })
      return true
    }

    cart.pending_submission = {
      kind: 'resubmit', orderId: cart.editing_order_id,
      payload: JSON.parse(JSON.stringify(buildPayload({includeVersion: true}))),
      key: cart.ensureIdempotencyKey(),
    }
    const pending = cart.pending_submission
    const result = await api.submitOutboundOrder(pending.orderId, pending.payload, pending.key)
    if (!alive || !active || actor !== identity() || tag !== generation) return false
    const orderId = cart.editing_order_id
    cart.resetOrder()
    uni.showToast({ title: '已重新提交', icon: 'none' })
    setTimeout(() => {
      uni.redirectTo({ url: `/pages/orders/detail?id=${result?.id || orderId}` })
    }, 200)
    return true
  } catch (error) {
    if (!alive || !active || actor !== identity() || tag !== generation) return false
    const data = error?.data || {}
    const status = Number(error?.statusCode || error?.code)
    if (status >= 400 && status < 500) cart.pending_submission = null
    if ([401,403].includes(status)) { cart.resetOrder(); denied.value = true; checkError.value = '下单权限已失效'; return false }
    if (data.validation) { applyValidation(data.validation); checkError.value = data.detail; return false }
    if (
      Number(error?.statusCode || error?.code) === 409 &&
      String(data?.code || '') === 'stale_order_edit'
    ) {
      const orderId = cart.editing_order_id
      checkError.value = `订单 ${orderId} 已被修改，当前输入已保留；请返回详情核对并重新进入编辑。`
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
  if (cart.editing_order_id && !cart.pending_submission) return saveDraft(true)
  if (!cart.pending_submission && !validateOrder()) return

  submitting.value = true
  const actor = identity(), tag = generation
  try {
    if (!cart.pending_submission) {
      if (!await checkCurrent()) return
      cart.pending_submission = { payload: JSON.parse(JSON.stringify(buildPayload())), key: cart.ensureIdempotencyKey() }
    }
    const pending = cart.pending_submission
    const response = pending.kind === 'resubmit'
      ? await api.submitOutboundOrder(pending.orderId, pending.payload, pending.key)
      : await api.createOutboundOrder(pending.payload, pending.key)
    if (!alive || !active || actor !== identity() || tag !== generation) return
    uni.showToast({ title: `已创建：${response?.order_no || response?.id}`, icon: 'none' })
    cart.resetOrder()
    uni.redirectTo({ url: `/pages/orders/detail?id=${response.id}` })
  } catch (error) {
    if (!alive || !active || actor !== identity() || tag !== generation) return
    const data = error?.data || {}
    const status = Number(error?.statusCode || error?.code)
    if (status >= 400 && status < 500) cart.pending_submission = null
    if ([401,403].includes(status)) { cart.resetOrder(); denied.value = true; checkError.value = '下单权限已失效'; return }
    if (data.validation) { applyValidation(data.validation); checkError.value = data.detail; return }
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
    quantityDrafts[lineKey(item)] = formatQuantity(item.qty)
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
  loadCart()
})

function applyValidation(report) {
  const facts = new Map((report.items || []).map(row => [String(row.line_key), row]))
  cart.items.forEach(item => {
    const row = facts.get(lineKey(item))
    if (!row) return
    item.available = row.available
    const wasUnpriced = item.is_priced === false
    item.is_priced = row.is_priced !== false
    if (!item.is_priced) item.price = null
    else if (wasUnpriced) item.price = row.price
    item.orig_price = row.price
    item.minimum_sale_price = row.minimum_sale_price
    item.catalog_version = row.catalog_version || ''
    item.current_base_unit_name = row.base_unit_name
    item.unit_mismatch = Boolean(item.base_uom_id && row.base_uom_id && item.base_uom_id !== row.base_uom_id)
    if (!item.base_uom_id && !item.unit_unverified) item.base_uom_id = row.base_uom_id
    item.validation_issues = row.issues || []
    initializePriceGuard(item)
  })
  validateAllQuantities()
  checkedAt.value = new Date(report.checked_at).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false})
}
async function checkCurrent() {
  while (validationFlight) await validationFlight
  if (!alive || !active || denied.value || cart.pending_submission || !cart.items.length ||
      !cart.customer?.id || !cart.warehouse_id) return false
  if (cart.items.some(item => !validateCartQuantity(quantityDraft(item)).valid)) return false
  const payload = buildPayload(), signature = checkoutSignature(), actor = identity(), tag = generation
  checking.value = true; checkError.value = ''
  validationFlight = (async () => {
    try {
      const report = await api.validateOrder(payload)
      if (!alive || !active || tag !== generation || actor !== identity() || signature !== checkoutSignature()) return false
      applyValidation(report)
      if (!report.can_submit) checkError.value = '请处理商品提示，确认当前信息后重新校验。'
      return Boolean(report.can_submit)
    } catch (e) {
      if (!alive || !active || tag !== generation || actor !== identity()) return false
      const status = Number(e?.statusCode || e?.code)
      if ([401,403].includes(status)) { cart.resetOrder(); denied.value = true; checkedAt.value = '' }
      checkError.value = [404,405].includes(status) ? '快捷下单暂不可用' :
        (checkedAt.value ? '更新失败，当前为上次数据；请重试校验后再提交。' : e?.data?.detail || e?.message || '校验失败，请重试。')
      return false
    } finally { checking.value = false }
  })()
  try { return await validationFlight } finally { validationFlight = null }
}
async function loadCart() {
  if (!cart.default_address_loaded && !cart.pending_submission && !addressFlight && cart.customer?.id) {
    const actor = identity(), customer = cart.customer.id, tag = generation
    try {
      addressFlight = api.customerAddresses(customer)
      const result = await addressFlight
      if (!alive || !active || tag !== generation || actor !== identity() || customer !== cart.customer?.id) return
      const address = result.results?.find(row => row.is_default)
      if (address && !form.contact && !form.contact_phone && !form.ship_to) {
        Object.assign(form, {contact: address.contact, contact_phone: address.phone, ship_to: address.address})
      }
      cart.default_address_loaded = true; addressError.value = ''
    } catch (e) {
      if (tag === generation && actor === identity()) addressError.value = '常用地址暂不可用，可手动填写。'
    } finally { addressFlight = null }
  }
  await checkCurrent()
}
onShow(() => { active = true; if (firstShow) { firstShow = false; return }; loadCart() })
onHide(() => { active = false; generation++ })
function dispose() {
  alive = false; active = false; generation++
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibility)
}
function visibility() { generation++; if (!document.hidden && active) loadCart() }
if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibility)
onUnload(dispose); onUnmounted(dispose)
watch(identity, () => { generation++; cart.resetOrder(); denied.value = true; checkedAt.value = ''; checkError.value = '账号已切换，请重新开单' })
</script>

<style scoped>
.page-shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; background: #f5f7fa; }
/* #ifdef H5 */
.page-shell {
  height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px));
  height: calc(100dvh - var(--window-top, 0px) - var(--window-bottom, 0px));
}
/* #endif */
.page-header { flex: 0 0 auto; padding: 20rpx; background: #fff; border-bottom: 1rpx solid #e5e7eb; }
.context-summary { display: flex; flex-wrap: wrap; gap: 12rpx 30rpx; font-size: 28rpx; font-weight: 600; color: #111827; }
.context-label { margin-right: 10rpx; font-size: 24rpx; font-weight: 400; color: #6b7280; }
.context-actions { display: flex; gap: 16rpx; margin-top: 14rpx; }
.link-button { min-height: 88rpx; width: 210rpx; margin: 0; font-size: 26rpx; color: #2563eb; background: #fff; border: 1rpx solid #2563eb; }
.address-button { width: auto; max-width: 100%; padding: 0 24rpx; margin-bottom: 16rpx; }
.reject-reason { margin-top: 14rpx; padding: 14rpx; color: #9a3412; background: #fff7ed; border-radius: 10rpx; }
.content { flex: 1; min-height: 0; box-sizing: border-box; padding-top: 18rpx; }
.receiver-card { margin: 0 20rpx 18rpx; padding: 22rpx; background: #fff; border-radius: 16rpx; }
.section-title { margin-bottom: 18rpx; font-size: 30rpx; font-weight: 600; color: #111827; }
.receiver-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16rpx; }
.receiver-item { min-width: 0; }
.receiver-item-full { grid-column: 1 / -1; }
.field-label { display: block; margin-bottom: 8rpx; font-size: 24rpx; color: #4b5563; }
.field-input { box-sizing: border-box; width: 100%; min-height: 80rpx; padding: 0 16rpx; font-size: 27rpx; border: 1rpx solid #d1d5db; border-radius: 10rpx; }
.remark-input { height: 160rpx; padding: 16rpx; }
.remark-count { margin-top: 8rpx; text-align: right; font-size: 24rpx; color: #6b7280; }
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
