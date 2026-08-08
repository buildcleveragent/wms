<template>
  <view :class="['assisted-order-panel', `mode-${mode}`]">
    <view class="header-card">
      <view>
        <text class="label">货主：</text>
        <text>{{ draft.owner?.name || '-' }}</text>
      </view>
      <view>
        <text class="label">客户：</text>
        <text>{{ draft.customer?.name || '-' }}</text>
      </view>
    </view>

    <view v-if="showItems" class="section items-section">
      <view class="section-title">出库商品（{{ draft.itemCount }} 种）</view>
      <view class="cart-table">
        <view v-if="mode === 'embedded'" class="cart-table-header">
          <text>商品</text>
          <text>包装</text>
          <text>基本数量</text>
          <view class="edit-table-header">
            <text>出库数量</text>
            <text>基本单价</text>
            <text>操作</text>
          </view>
        </view>

        <view v-for="(item, index) in draft.items" :key="item.product_id" class="cart-row">
          <view class="cart-product-cell">
            <view class="product-name">{{ item.name }}</view>
            <view class="meta">
              仓库SKU编码：{{ item.sku || '-' }}　可用：{{ formatQty(item.available_qty) }}
              {{ item.base_unit_name }}
            </view>
          </view>

          <view class="package-summary">
            <picker
              :range="itemUnitLabels(item)"
              :value="itemUnitIndex(item)"
              :disabled="submissionLocked"
              @change="changeItemUnit(index, $event)"
            >
              <view class="package-picker">
                {{ item.unit_label }}（×{{ formatQty(item.unit_multiplier) }}）
              </view>
            </picker>
          </view>

          <view class="base-qty-summary">
            <text class="narrow-label">折合：</text>{{ formatQty(item.qty) }}
            {{ item.base_unit_name }}
          </view>

          <view class="edit-row">
            <view class="field-block">
              <text class="field-label">出库数量（{{ item.unit_label }}）</text>
              <input
                class="value-input"
                type="digit"
                :value="item.package_qty"
                :disabled="submissionLocked"
                @input="setItemPackageQty(index, $event)"
                @blur="finalizeItemPackageQty(index)"
              />
            </view>
            <view class="field-block">
              <text class="field-label">基本单价（可不填）</text>
              <input
                class="value-input"
                type="digit"
                :value="item.price"
                placeholder="未提供"
                :disabled="submissionLocked"
                @input="setItemPrice(index, $event)"
                @blur="finalizeItemPrice(index)"
              />
            </view>
            <button
              class="delete-button"
              :disabled="submissionLocked"
              @click="draft.remove(index)"
            >
              删除
            </button>
          </view>
        </view>
        <view v-if="!draft.items.length" class="empty">尚未选择商品</view>
      </view>
    </view>

    <view class="section">
      <view class="section-title">配送与审计信息</view>
      <input
        v-model="draft.form.src_bill_no"
        class="input"
        placeholder="源单号（可选）"
        :disabled="submissionLocked"
      />

      <picker
        :range="deliveryLabels"
        :value="deliveryIndex < 0 ? 0 : deliveryIndex"
        :disabled="submissionLocked"
        @change="changeDeliveryMethod"
      >
        <view class="picker-field">
          {{ deliveryIndex >= 0 ? deliveryLabels[deliveryIndex] : '选择交货方式（可选）' }}
        </view>
      </picker>

      <picker
        mode="date"
        :value="draft.form.etd"
        :disabled="submissionLocked"
        @change="changeEtd"
      >
        <view class="picker-field">
          {{ draft.form.etd || '选择预计发货日期（可选）' }}
        </view>
      </picker>

      <input
        v-model="draft.form.contact"
        class="input recipient-input"
        placeholder="收件人（散客必填）"
        :disabled="submissionLocked"
      />
      <input
        v-model="draft.form.contact_phone"
        class="input recipient-input"
        placeholder="联系电话（散客必填）"
        :disabled="submissionLocked"
      />
      <textarea
        v-model="draft.form.ship_to"
        class="textarea"
        placeholder="收货地址（散客必填）"
        :disabled="submissionLocked"
      />
      <textarea
        v-model="draft.form.remark"
        class="textarea"
        placeholder="订单备注（可选）"
        :disabled="submissionLocked"
      />
      <textarea
        v-model="draft.form.assistance_reason"
        class="textarea"
        placeholder="代办原因（可选，默认按货主授权）"
        :disabled="submissionLocked"
      />
    </view>

    <view class="section print-section">
      <view class="section-title">拣货辅助</view>
      <checkbox-group @change="changePrintOption">
        <label class="print-option">
          <checkbox
            value="print"
            :checked="draft.form.print_after_create"
            :disabled="submissionLocked"
            color="#1677ff"
          />
          <view>
            <view class="print-title">创建后打印出库单</view>
            <view class="print-hint">不勾选则直接进入拣货任务，可稍后在任务列表打印出库单</view>
          </view>
        </label>
      </checkbox-group>
    </view>

    <view v-if="mode === 'page'" class="footer-space" />
    <view class="panel-footer">
      <view class="summary">
        共 {{ draft.itemCount }} 种，基本数量 {{ formatQty(draft.totalQty) }}
      </view>
      <view class="button-row">
        <button
          v-if="mode === 'page'"
          class="outline-button"
          :disabled="submissionLocked"
          @click="emit('continue-selecting')"
        >
          继续选品
        </button>
        <button
          class="submit-button"
          :disabled="submissionLocked || !canSubmit"
          @click="requestSubmit"
        >
          {{ submitButtonText }}
        </button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useAuth } from '@/store/auth'
import { useAssistedOutbound } from '@/store/assistedOutbound'
import { api } from '@/utils/request'
import {
  closePreparedOutboundPrintWindow,
  openOutboundPrintPage,
  prepareOutboundPrintWindow,
} from '@/utils/outboundPrint'

defineProps({
  mode: {
    type: String,
    default: 'page',
    validator: (value) => ['embedded', 'page'].includes(value),
  },
  showItems: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits(['continue-selecting', 'authorization-denied'])
const auth = useAuth()
const draft = useAssistedOutbound()

const deliveryOptions = [
  { value: 'PICKUP', label: '客户自提' },
  { value: 'OWN_TRUCK', label: '配送' },
  { value: 'COURIER', label: '快递/小包' },
]
const deliveryLabels = deliveryOptions.map((option) => option.label)
const deliveryIndex = computed(() => deliveryOptions.findIndex(
  (option) => option.value === draft.form.delivery_method,
))
const submissionLocked = computed(() => draft.submissionLocked === true)
const submitButtonText = computed(() => {
  if (draft.submissionState === 'confirming') return '等待确认…'
  if (draft.submissionState === 'submitting') return '正在创建并分配库存…'
  return '创建代办出库单'
})
const isCashCustomer = computed(
  () => String(draft.customer?.code || '').trim().toUpperCase() === 'CASH',
)
const canSubmit = computed(() => {
  if (!draft.owner?.id || !draft.customer?.id || !draft.items.length) return false
  return draft.items.every((item) => {
    const packageQty = Number(item.package_qty)
    const qty = Number(item.qty)
    const available = Number(item.available_qty || 0)
    const priceIsValid = item.price === '' || item.price === null || item.price === undefined
      || (Number.isFinite(Number(item.price)) && Number(item.price) >= 0)
    return Number.isFinite(packageQty) && packageQty > 0
      && Number.isFinite(qty) && qty > 0 && qty <= available && priceIsValid
  })
})

function eventValue(event) {
  return event?.detail?.value ?? event?.target?.value ?? ''
}

function formatQty(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? String(Number(number.toFixed(3))) : '0'
}

function setItemPackageQty(index, event) {
  if (submissionLocked.value) return
  draft.setPackageQty(index, eventValue(event))
}

function setItemPrice(index, event) {
  if (submissionLocked.value) return
  draft.setPrice(index, eventValue(event))
}

function itemUnitOptions(item) {
  if (Array.isArray(item?.unit_options) && item.unit_options.length) return item.unit_options
  return [
    {
      package_id: item?.package_id ?? null,
      label: item?.unit_label || item?.base_unit_name || item?.base_unit || '基本单位',
      multiplier: Number(item?.unit_multiplier || 1),
    },
  ]
}

function itemUnitLabels(item) {
  return itemUnitOptions(item).map(
    (option) => `${option.label}（×${formatQty(option.multiplier)}）`,
  )
}

function itemUnitIndex(item) {
  const index = itemUnitOptions(item).findIndex(
    (option) => (option.package_id ?? null) === (item.package_id ?? null),
  )
  return index >= 0 ? index : 0
}

function changeItemUnit(index, event) {
  if (submissionLocked.value) return
  const item = draft.items[index]
  if (!item) return
  const option = itemUnitOptions(item)[Number(eventValue(event))]
  if (!option) return
  const result = draft.setItemUnit(index, option)
  if (!result?.ok) {
    uni.showToast({ title: '当前库存或数量不允许切换到该包装', icon: 'none' })
    return
  }
  if (result.clamped) {
    uni.showToast({
      title: `已按库存调整为 ${formatQty(result.package_qty)} ${item.unit_label}`,
      icon: 'none',
    })
  }
}

function finalizeItemPackageQty(index) {
  if (submissionLocked.value) return
  const item = draft.items[index]
  if (!item) return
  const result = draft.finalizePackageQty(index)
  if (!result?.ok) {
    if (['invalid_quantity', 'insufficient_stock'].includes(result?.reason)) {
      const name = item.name
      draft.remove(index)
      uni.showToast({ title: `${name} 数量无效，已从出库单移除`, icon: 'none' })
      return
    }
    uni.showToast({ title: `${item.name} 的包装换算无效`, icon: 'none' })
    return
  }
  if (result.clamped) {
    uni.showToast({
      title: `已按库存调整为 ${formatQty(result.package_qty)} ${item.unit_label}`,
      icon: 'none',
    })
  }
}

function finalizeItemPrice(index) {
  if (submissionLocked.value) return
  const item = draft.items[index]
  if (!item) return
  const result = draft.finalizePrice(index)
  if (!result?.ok) {
    uni.showToast({ title: `${item.name} 的单价已清空，单价不能为负数`, icon: 'none' })
  }
}

function validateItem(index) {
  const item = draft.items[index]
  if (!item) return false
  const packageQty = Number(item.package_qty)
  const qty = Number(item.qty)
  const available = Number(item.available_qty || 0)
  if (!Number.isFinite(packageQty) || packageQty <= 0) {
    uni.showToast({ title: `${item.name} 的${item.unit_label}数量必须大于 0`, icon: 'none' })
    return false
  }
  if (!Number.isFinite(qty) || qty <= 0) {
    uni.showToast({ title: `${item.name} 的数量必须大于 0`, icon: 'none' })
    return false
  }
  if (qty > available) {
    uni.showToast({ title: `${item.name} 最多可出 ${formatQty(available)}`, icon: 'none' })
    return false
  }
  if (item.price !== '' && item.price !== null && item.price !== undefined) {
    const price = Number(item.price)
    if (!Number.isFinite(price) || price < 0) {
      uni.showToast({ title: `${item.name} 的单价必须为空或不小于 0`, icon: 'none' })
      return false
    }
  }
  return true
}

function changeDeliveryMethod(event) {
  if (submissionLocked.value) return
  const index = Number(event?.detail?.value)
  draft.form.delivery_method = deliveryOptions[index]?.value || ''
}

function changeEtd(event) {
  if (submissionLocked.value) return
  draft.form.etd = eventValue(event)
}

function changePrintOption(event) {
  if (submissionLocked.value) return
  const values = Array.isArray(event?.detail?.value) ? event.detail.value : []
  draft.setPrintAfterCreate(
    values.includes('print'),
    auth.user?.id,
    auth.user?.warehouse_id,
  )
}

function validateBeforeSubmit() {
  if (!canSubmit.value) {
    const invalidIndex = draft.items.findIndex((item, index) => !validateItem(index))
    if (invalidIndex < 0) uni.showToast({ title: '请先选择有效商品和数量', icon: 'none' })
    return false
  }

  if (isCashCustomer.value) {
    if (!String(draft.form.contact || '').trim()) {
      uni.showToast({ title: '请填写收件人', icon: 'none' })
      return false
    }
    const phone = String(draft.form.contact_phone || '').trim()
    if (!phone) {
      uni.showToast({ title: '请填写联系电话', icon: 'none' })
      return false
    }
    if (phone.length < 6 || !/\d/.test(phone)) {
      uni.showToast({ title: '联系电话格式不正确', icon: 'none' })
      return false
    }
    if (!String(draft.form.ship_to || '').trim()) {
      uni.showToast({ title: '请填写收货地址', icon: 'none' })
      return false
    }
  }
  return true
}

function makeUuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16)
    const value = char === 'x' ? random : ((random & 0x3) | 0x8)
    return value.toString(16)
  })
}

function buildBusinessPayload() {
  return {
    owner_id: draft.owner.id,
    customer_id: draft.customer.id,
    src_bill_no: String(draft.form.src_bill_no || '').trim(),
    delivery_method: draft.form.delivery_method || null,
    etd: draft.form.etd ? `${draft.form.etd}T00:00:00` : null,
    contact: String(draft.form.contact || '').trim(),
    contact_phone: String(draft.form.contact_phone || '').trim(),
    ship_to: String(draft.form.ship_to || '').trim(),
    remark: String(draft.form.remark || '').trim(),
    assistance_reason: String(draft.form.assistance_reason || '').trim(),
    items: draft.items.map((item) => {
      const line = {
        product_id: item.product_id,
        qty: Number(item.qty),
      }
      if (item.package_id !== null && item.package_id !== undefined) {
        line.package_id = Number(item.package_id)
        line.package_qty = Number(item.package_qty)
      }
      if (item.price !== '' && item.price !== null && item.price !== undefined) {
        line.price = Number(item.price)
      }
      return line
    }),
  }
}

function payloadWithStableRequestId() {
  const businessPayload = buildBusinessPayload()
  const signature = JSON.stringify(businessPayload)
  if (!draft.lastRequestId || signature !== draft.lastRequestSignature) {
    draft.lastRequestId = makeUuid()
    draft.lastRequestSignature = signature
  }
  return { ...businessPayload, request_id: draft.lastRequestId }
}

function redirectToTask(taskId) {
  return new Promise((resolve, reject) => {
    uni.redirectTo({
      url: `/pages/picking/task_detail?task_id=${taskId}`,
      success: resolve,
      fail: reject,
    })
  })
}

function requestSubmit() {
  if (submissionLocked.value || !validateBeforeSubmit()) return
  draft.submissionState = 'confirming'
  uni.showModal({
    title: '确认创建代办出库单',
    content: `共 ${draft.itemCount} 种商品，基本数量 ${formatQty(draft.totalQty)}。创建后将立即分配库存并发布拣货任务，是否继续？`,
    confirmText: '确认创建',
    cancelText: '返回检查',
    success: (result) => {
      if (!result.confirm) {
        draft.submissionState = 'idle'
        return
      }
      draft.submissionState = 'submitting'
      executeSubmit()
    },
    fail: (error) => {
      draft.submissionState = 'idle'
      console.warn('无法显示代办出库确认框', error)
      uni.showToast({ title: '无法显示确认框，请重试', icon: 'none' })
    },
  })
}

async function executeSubmit() {
  const shouldPrint = Boolean(draft.form.print_after_create)
  const preparedPrintWindow = shouldPrint ? prepareOutboundPrintWindow() : null
  let printStarted = false
  try {
    const result = await api.createAssistedOutboundOrder(payloadWithStableRequestId())
    const taskId = Number(result?.task_id)
    if (!taskId) throw new Error('后端未返回拣货任务 ID')
    uni.showToast({
      title: result?.replayed ? '已恢复原代办订单' : '代办订单已创建',
      icon: 'none',
    })
    if (shouldPrint) printStarted = openOutboundPrintPage(taskId, preparedPrintWindow)
    await redirectToTask(taskId)
    draft.resetAll()
  } catch (error) {
    if (!printStarted) closePreparedOutboundPrintWindow(preparedPrintWindow)
    if (Number(error?.statusCode || error?.code) === 403) {
      auth.invalidateAssistedCapability()
      emit('authorization-denied')
    }
    console.error(error)
  } finally {
    draft.submissionState = 'idle'
  }
}

onMounted(() => {
  draft.loadPrintPreference(auth.user?.id, auth.user?.warehouse_id)
})
</script>

<style scoped>
.assisted-order-panel { box-sizing: border-box; min-height: 100%; background: #f6f7fb; }
.header-card { display: flex; justify-content: space-between; gap: 20rpx; margin: 0 0 18rpx; padding: 22rpx; border-radius: 16rpx; background: #fff; }
.label, .meta, .field-label { color: #697386; }
.section { margin-top: 18rpx; padding: 18rpx; border-radius: 16rpx; background: #fff; box-shadow: 0 4rpx 16rpx rgba(0,0,0,.03); }
.section-title { margin-bottom: 12rpx; font-size: 30rpx; font-weight: 700; }
.cart-row { padding: 20rpx 0; border-bottom: 1rpx solid #e1e5eb; }
.cart-product-cell { min-width: 0; }
.product-name { font-size: 28rpx; font-weight: 700; }
.meta { margin-top: 6rpx; font-size: 23rpx; }
.package-summary { margin-top: 10rpx; color: #334155; font-size: 24rpx; }
.package-picker { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.base-qty-summary { margin-top: 10rpx; color: #1677ff; font-size: 23rpx; text-align: right; }
.edit-row { display: flex; align-items: flex-end; gap: 12rpx; margin-top: 14rpx; }
.field-block { min-width: 0; flex: 1; }
.field-label { display: block; margin-bottom: 6rpx; font-size: 22rpx; }
.value-input, .picker-field { box-sizing: border-box; width: 100%; height: 64rpx; padding: 0 14rpx; border: 1rpx solid #d9dee7; border-radius: 8rpx; background: #fff; }
.picker-field { margin: 14rpx 0; line-height: 64rpx; color: #475569; }
.delete-button, .outline-button { flex: none; margin: 0; color: #c62828; background: #fff1f0; font-size: 24rpx; }
.delete-button[disabled] { opacity: .45; }
.value-input[disabled], .input[disabled], .textarea[disabled] { opacity: .55; }
.input { box-sizing: border-box; width: 100%; margin: 14rpx 0; }
.recipient-input { height: 80rpx; padding: 0 18rpx; border: 1rpx solid #d9dee7; border-radius: 8rpx; font-size: 32rpx; line-height: 80rpx; background: #fff; }
.textarea { box-sizing: border-box; width: 100%; min-height: 120rpx; margin: 14rpx 0; padding: 16rpx; border: 1rpx solid #e5e7eb; border-radius: 8rpx; background: #fff; }
.print-section { padding-bottom: 22rpx; }
.print-option { display: flex; align-items: center; gap: 14rpx; padding: 12rpx 0; }
.print-title { font-size: 27rpx; font-weight: 600; }
.print-hint { margin-top: 6rpx; color: #697386; font-size: 22rpx; }
.empty { padding: 40rpx 0; text-align: center; color: #8a94a6; }
.footer-space { height: 120rpx; }
.panel-footer { z-index: 20; padding: 14rpx 20rpx; background: rgba(255,255,255,.98); box-shadow: 0 -4rpx 16rpx rgba(0,0,0,.08); }
.mode-page .panel-footer { position: fixed; right: 0; bottom: 0; left: 0; }
.mode-embedded .panel-footer { position: sticky; bottom: 0; margin-top: 18rpx; border-radius: 14rpx; }
.mode-embedded .items-section { overflow-x: auto; }
.mode-embedded .cart-table { min-width: 500px; }
.mode-embedded .cart-table-header, .mode-embedded .cart-row {
  display: grid;
  grid-template-columns: minmax(135px, 1.8fr) minmax(70px, .8fr) minmax(65px, .7fr) minmax(210px, 2.3fr);
  align-items: center;
  gap: 10rpx;
}
.mode-embedded .cart-table-header {
  padding: 10rpx 8rpx;
  border-bottom: 1rpx solid #dce2ea;
  color: #64748b;
  background: #eef2f7;
  font-size: 21rpx;
  font-weight: 600;
}
.mode-embedded .edit-table-header, .mode-embedded .edit-row {
  display: grid;
  grid-template-columns: minmax(60px, 1fr) minmax(60px, 1fr) auto;
  align-items: center;
  gap: 8rpx;
}
.mode-embedded .edit-table-header { text-align: center; }
.mode-embedded .cart-row { min-height: 68rpx; padding: 7rpx 8rpx; }
.mode-embedded .cart-product-cell { display: flex; align-items: center; gap: 8rpx; overflow: hidden; }
.mode-embedded .cart-product-cell .product-name,
.mode-embedded .cart-product-cell .meta,
.mode-embedded .package-summary,
.mode-embedded .base-qty-summary {
  overflow: hidden;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mode-embedded .cart-product-cell .product-name { min-width: 0; flex: 1; font-size: 24rpx; }
.mode-embedded .cart-product-cell .meta { max-width: 45%; flex: none; font-size: 20rpx; }
.mode-embedded .package-summary { font-size: 22rpx; }
.mode-embedded .base-qty-summary { text-align: left; }
.mode-embedded .edit-row { margin: 0; }
.mode-embedded .edit-row .field-label, .mode-embedded .narrow-label { display: none; }
.mode-embedded .value-input { height: 54rpx; padding: 0 10rpx; }
.mode-embedded .delete-button { height: 54rpx; padding: 0 12rpx; line-height: 54rpx; }
.summary { margin-bottom: 10rpx; text-align: right; font-weight: 600; }
.button-row { display: flex; gap: 16rpx; }
.button-row button { flex: 1; }
.outline-button { color: #1677ff; background: #eef5ff; }
.submit-button { margin: 0; color: #fff; background: #1677ff; }
.submit-button[disabled] { opacity: .45; }
</style>
