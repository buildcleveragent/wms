<template>
  <view class="page confirm-page">
    <view class="section">
      <view class="section-title">{{ isPickup ? '自提信息' : '收货信息' }}</view>
      <template v-if="isPickup">
        <view class="pickup-card">
          <view class="pickup-title">{{ pickupTitle }}</view>
          <view class="pickup-sub">下单后平台会尽快备货，客户到约定地点自提。</view>
        </view>
        <view class="inline-address pickup-fields">
          <input class="input" v-model="inline.contact" placeholder="自提联系人" />
          <input class="input" v-model="inline.phone" placeholder="自提联系电话" />
        </view>
      </template>
      <template v-else>
        <AddressCard
          v-if="selectedAddress"
          :address="selectedAddress"
          active
          @select="goAddress"
        />
        <view v-else class="inline-address">
          <input class="input" v-model="inline.contact" placeholder="联系人" />
          <input class="input" v-model="inline.phone" placeholder="联系电话" />
          <input class="input" v-model="inline.ship_to" placeholder="收货地址" />
        </view>
        <button class="link-btn" @click="goAddress">{{ selectedAddress ? '更换地址' : '管理地址' }}</button>
      </template>
    </view>

    <view class="section">
      <view class="section-title">配送方式</view>
      <picker :range="deliveryOptions" range-key="name" :value="deliveryIndex" @change="onDeliveryChange">
        <view class="picker-line">{{ deliveryOptions[deliveryIndex].name }}</view>
      </picker>
    </view>

    <view class="section">
      <view class="section-title">支付方式</view>
      <view v-if="isCombined" class="combined-tip">
        多包裹订单会统一提交，由平台确认付款和备货；当前暂不使用微信支付、优惠券和积分。
      </view>
      <view class="pay-switch">
        <button
          v-for="item in paymentOptions"
          :key="item.code"
          class="pay-option"
          :class="{ active: effectivePaymentMethod === item.code, disabled: isCombined && item.code === 'WECHAT' }"
          :disabled="isCombined && item.code === 'WECHAT'"
          @click="selectPayment(item)"
        >
          {{ item.name }}
        </button>
      </view>
    </view>

    <view class="section">
      <view class="section-title">优惠</view>
      <view v-if="isCombined" class="combined-tip">
        多包裹订单金额由服务端逐包裹校验后合计，优惠权益暂在单包裹订单中使用。
      </view>
      <picker :range="couponOptions" range-key="label" :value="couponIndex" @change="onCouponChange">
        <view class="picker-line between-line">
          <text>优惠券</text>
          <text>{{ selectedCouponLabel }}</text>
        </view>
      </picker>
      <view class="point-line">
        <text>积分</text>
        <view class="point-control">
          <input class="point-input" type="number" v-model="pointsUsed" :disabled="isCombined" @blur="onPointsBlur" />
          <text class="point-balance">/{{ pointInfo.points || 0 }}</text>
        </view>
      </view>
    </view>

    <view class="section">
      <view class="section-title">商品明细</view>
      <view v-for="item in cart.items" :key="item.key" class="line">
        <view>
          <view class="line-name">{{ item.name }}</view>
          <view class="line-meta">{{ item.qty }} {{ item.order_uom }} × ¥{{ money(item.unit_price) }}</view>
          <view v-if="item.quote_message" class="warn">{{ item.quote_message }}</view>
        </view>
        <view class="line-amount">¥{{ money(item.line_amount || Number(item.qty) * Number(item.unit_price)) }}</view>
      </view>
    </view>

    <view class="section">
      <view class="section-title">金额</view>
      <view class="amount-row">
        <text>商品金额</text>
        <text>¥{{ money(previewGoodsAmount) }}</text>
      </view>
      <view v-for="item in adjustmentRows" :key="`${item.type}-${item.source_code}`" class="amount-row discount">
        <text>{{ item.title }}</text>
        <text>{{ Number(item.amount) < 0 ? '-' : '' }}¥{{ money(Math.abs(Number(item.amount || 0))) }}</text>
      </view>
      <view class="amount-row payable">
        <text>应付金额</text>
        <text>¥{{ money(payableAmount) }}</text>
      </view>
    </view>

    <view class="section">
      <view class="section-title">备注</view>
      <textarea class="textarea" v-model="remark" placeholder="选填" />
    </view>

    <view class="bottom">
      <view>
        <view class="state">{{ previewOk ? '已校验' : '待校验' }}</view>
        <view class="amount">¥{{ money(payableAmount) }}</view>
      </view>
      <button class="submit" :loading="loading" @click="submit">{{ submitText }}</button>
    </view>
  </view>
</template>

<script setup>
import { onLoad, onShow } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'
import AddressCard from '../../components/AddressCard.vue'
import { addressService } from '../../services/address'
import { benefitService } from '../../services/benefit'
import { paymentService } from '../../services/payment'
import { useCartStore } from '../../stores/cart'
import { money } from '../../utils/money'

const cart = useCartStore()
const addresses = ref([])
const selectedId = ref(null)
const preview = ref(null)
const loading = ref(false)
const remark = ref('')
const deliveryIndex = ref(0)
const paymentMethod = ref('WECHAT')
const coupons = ref([])
const couponIndex = ref(0)
const pointInfo = ref({ points: 0, frozen: 0, exchange_rate: '100.00' })
const pointsUsed = ref('')
const ownerId = ref('')
const cartId = ref('')
const inline = reactive({ contact: '', phone: '', ship_to: '' })
const deliveryOptions = [
  { code: 'OWN_TRUCK', name: '配送' },
  { code: 'PICKUP', name: '客户自提' },
  { code: 'COURIER', name: '快递/小包' },
]
const paymentOptions = [
  { code: 'WECHAT', name: '微信支付' },
  { code: 'OFFLINE', name: '线下付款' },
]

const selectedAddress = computed(() => addresses.value.find((item) => item.id === selectedId.value) || null)
const selectedDelivery = computed(() => deliveryOptions[deliveryIndex.value] || deliveryOptions[0])
const isPickup = computed(() => selectedDelivery.value.code === 'PICKUP')
const isCombined = computed(() => !ownerId.value && (cart.groups || []).length > 1)
const pickupTitle = computed(() => '自提点')
const effectivePaymentMethod = computed(() => (isCombined.value ? 'OFFLINE' : paymentMethod.value))
const submitText = computed(() => (effectivePaymentMethod.value === 'WECHAT' ? '提交并支付' : '提交订单'))
const adjustmentRows = computed(() => (preview.value && preview.value.adjustments) || [])
const payableAmount = computed(() => {
  const data = preview.value || {}
  return data.payable_amount || data.total_amount || cart.totalAmount
})
const previewGoodsAmount = computed(() => {
  const data = preview.value || {}
  return data.goods_amount === undefined || data.goods_amount === null ? cart.totalAmount : data.goods_amount
})
const previewOk = computed(() => Boolean(preview.value && preview.value.ok))
const couponOptions = computed(() => [
  { id: null, label: isCombined.value ? '多包裹订单暂不使用优惠券' : '不使用优惠券' },
  ...coupons.value.map((item) => ({
    id: item.id,
    label: `${item.title} -¥${money(item.discount_amount)}`,
  })),
])
const selectedCoupon = computed(() => couponOptions.value[couponIndex.value] || couponOptions.value[0])
const selectedCouponLabel = computed(() => selectedCoupon.value.label || '不使用优惠券')
const selectedCouponId = computed(() => selectedCoupon.value.id || null)

function orderExtra() {
  const address = isPickup.value ? null : selectedAddress.value
  const fullAddress = address ? (address.full_address || address.detail || '') : ''
  return {
    owner_id: ownerId.value || undefined,
    cart_id: cartId.value || undefined,
    address_id: address && !isCombined.value ? address.id : null,
    delivery_method: selectedDelivery.value.code,
    contact: address && isCombined.value ? address.contact : address ? '' : inline.contact,
    contact_phone: address && isCombined.value ? address.phone : address ? '' : inline.phone,
    ship_to: isPickup.value ? pickupTitle.value : address && isCombined.value ? fullAddress : address ? '' : inline.ship_to,
    remark: remark.value,
    payment_method: effectivePaymentMethod.value,
    coupon_id: isCombined.value ? null : selectedCouponId.value,
    points: isCombined.value ? 0 : Number(pointsUsed.value || 0),
  }
}

async function payOrder(order) {
  const prepay = await paymentService.prepay(order.id)
  if (prepay.paid) return
  await paymentService.requestPayment(prepay.pay_params)
}

async function loadAddresses() {
  const params = ownerId.value ? { owner_id: ownerId.value } : {}
  addresses.value = await addressService.list(params)
  const selectedExists = addresses.value.some((item) => item.id === selectedId.value)
  if (!selectedId.value || !selectedExists) {
    const defaultAddress = addresses.value.find((item) => item.is_default)
    const firstAddress = addresses.value[0]
    selectedId.value = (defaultAddress && defaultAddress.id) || (firstAddress && firstAddress.id) || null
    const source = defaultAddress || firstAddress
    if (source) {
      if (!inline.contact) inline.contact = source.contact || ''
      if (!inline.phone) inline.phone = source.phone || ''
    }
  }
}

async function loadBenefits() {
  if (isCombined.value) {
    coupons.value = []
    couponIndex.value = 0
    pointInfo.value = { points: 0, frozen: 0, exchange_rate: '100.00' }
    pointsUsed.value = ''
    return
  }
  const [couponRows, points] = await Promise.all([
    benefitService.coupons({ owner_id: ownerId.value, order_amount: cart.totalAmount }),
    benefitService.points({ owner_id: ownerId.value }),
  ])
  coupons.value = couponRows || []
  pointInfo.value = points || { points: 0, frozen: 0, exchange_rate: '100.00' }
  if (couponIndex.value >= couponOptions.value.length) {
    couponIndex.value = 0
  }
  clampPoints()
}

async function refreshPreview() {
  if (!cart.items.length) return
  preview.value = await cart.preview(orderExtra())
}

async function refreshPreviewQuietly() {
  try {
    await refreshPreview()
  } catch (err) {
    uni.showToast({ title: err.message || '订单预览失败', icon: 'none' })
  }
}

function clampPoints() {
  const max = Number((pointInfo.value && pointInfo.value.points) || 0)
  const value = Math.max(0, Math.floor(Number(pointsUsed.value || 0)))
  pointsUsed.value = value ? String(Math.min(value, max)) : ''
}

async function onCouponChange(event) {
  couponIndex.value = Number(event.detail.value)
  await refreshPreviewQuietly()
}

async function onPointsBlur() {
  clampPoints()
  await refreshPreviewQuietly()
}

async function onDeliveryChange(event) {
  deliveryIndex.value = Number(event.detail.value)
  await refreshPreviewQuietly()
}

function selectPayment(item) {
  if (!item || (isCombined.value && item.code === 'WECHAT')) return
  paymentMethod.value = item.code
}

async function submit() {
  if (loading.value) return
  if (!cart.items.length) {
    uni.showToast({ title: '购物车为空', icon: 'none' })
    return
  }
  loading.value = true
  try {
    const order = await cart.checkout(orderExtra())
    const isBatchOrder = order && order.orders && order.orders.length > 1
    if (isBatchOrder) {
      uni.setStorageSync('sale_mini_pending_order_status', 'WAIT_SHIP')
      uni.redirectTo({
        url: `/pages/order-result/order-result?id=${order.id}&result=batch_offline&count=${order.order_count}&amount=${order.payable_amount}`,
      })
      return
    }
    let result = effectivePaymentMethod.value === 'OFFLINE' ? 'offline' : 'created'
    if (effectivePaymentMethod.value === 'WECHAT') {
      try {
        await payOrder(order)
        result = 'paid'
        uni.showToast({ title: '支付成功', icon: 'none' })
      } catch (payErr) {
        result = 'wait_pay'
        uni.showToast({ title: '订单已提交，待支付', icon: 'none' })
      }
    }
    uni.redirectTo({ url: `/pages/order-result/order-result?id=${order.id}&result=${result}` })
  } catch (err) {
    uni.showToast({ title: err.message || '提交失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function goAddress() {
  if (ownerId.value) {
    uni.setStorageSync('sale_mini_address_owner_id', ownerId.value)
  } else {
    uni.removeStorageSync('sale_mini_address_owner_id')
  }
  uni.navigateTo({ url: '/pages/address/address?select=1' })
}

function backToCart() {
  uni.switchTab({ url: '/pages/cart/cart' })
}

onLoad((query = {}) => {
  ownerId.value = ''
  cartId.value = query.cart_id || ''
})

onShow(async () => {
  try {
    if (cartId.value && !ownerId.value) {
      await cart.load({ cart_id: cartId.value })
      if (cart.groups.length === 1) {
        ownerId.value = String(cart.groups[0].owner_id)
      }
    } else if (!ownerId.value) {
      await cart.load()
      if (cart.groups.length === 1) {
        ownerId.value = String(cart.groups[0].owner_id)
        cartId.value = String(cart.groups[0].cart_id || '')
      }
    }
    const loadParams = cartId.value ? { cart_id: cartId.value } : ownerId.value ? { owner_id: ownerId.value } : {}
    await cart.load(loadParams)
    if (!ownerId.value && cart.groups.length === 1) {
      ownerId.value = String(cart.groups[0].owner_id)
    }
    if (!cartId.value && cart.cartId) cartId.value = String(cart.cartId)
  } catch (err) {
    uni.showToast({ title: err.message || '购物车同步失败', icon: 'none' })
  }
  if (!cart.items.length) {
    backToCart()
    return
  }
  const picked = uni.getStorageSync('sale_mini_selected_address')
  if (picked) {
    selectedId.value = picked.id
    uni.removeStorageSync('sale_mini_selected_address')
  }
  try {
    await loadAddresses()
    await loadBenefits()
    await refreshPreview()
  } catch (err) {
    uni.showToast({ title: err.message || '订单预览失败', icon: 'none' })
  }
})
</script>

<style scoped>
.confirm-page {
  padding-bottom: 150rpx;
}

.section {
  margin-bottom: 18rpx;
  padding: 22rpx;
  background: #fff;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
}

.section-title {
  margin-bottom: 16rpx;
  color: #17202a;
  font-size: 28rpx;
  font-weight: 800;
}

.inline-address {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.pickup-card {
  padding: 18rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #f8fafc;
}

.pickup-title {
  color: #17202a;
  font-size: 28rpx;
  font-weight: 850;
}

.pickup-sub {
  margin-top: 8rpx;
  color: #64748b;
  font-size: 24rpx;
  line-height: 1.45;
}

.pickup-fields {
  margin-top: 14rpx;
}

.link-btn {
  width: 168rpx;
  height: 56rpx;
  line-height: 56rpx;
  margin: 16rpx 0 0;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #0f766e;
  font-size: 24rpx;
}

.link-btn::after {
  border: 0;
}

.picker-line {
  height: 72rpx;
  display: flex;
  align-items: center;
  color: #334155;
  font-size: 27rpx;
}

.between-line {
  justify-content: space-between;
  gap: 20rpx;
}

.point-line {
  min-height: 72rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20rpx;
  color: #334155;
  font-size: 27rpx;
}

.point-control {
  min-width: 220rpx;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6rpx;
}

.point-input {
  width: 132rpx;
  height: 56rpx;
  padding: 0 12rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  box-sizing: border-box;
  text-align: right;
  color: #17202a;
  font-size: 26rpx;
}

.point-balance {
  color: #64748b;
  font-size: 24rpx;
}

.combined-tip {
  margin-bottom: 14rpx;
  padding: 14rpx 16rpx;
  border-radius: 8rpx;
  background: #f8fafc;
  color: #64748b;
  font-size: 24rpx;
  line-height: 1.45;
}

.pay-switch {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12rpx;
}

.pay-option {
  height: 72rpx;
  line-height: 72rpx;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #334155;
  font-size: 26rpx;
  font-weight: 750;
}

.pay-option.active {
  border-color: #0f766e;
  background: #ecfdf5;
  color: #0f766e;
}

.pay-option.disabled {
  background: #f1f5f9;
  color: #94a3b8;
}

.pay-option::after {
  border: 0;
}

.line {
  min-height: 88rpx;
  display: flex;
  justify-content: space-between;
  gap: 16rpx;
  padding: 14rpx 0;
  border-bottom: 1rpx solid #eef2f7;
}

.line:last-child {
  border-bottom: 0;
}

.line-name {
  color: #17202a;
  font-size: 27rpx;
  font-weight: 700;
}

.line-meta,
.state {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
}

.warn {
  margin-top: 6rpx;
  color: #b45309;
  font-size: 23rpx;
}

.line-amount {
  color: #b42318;
  font-size: 27rpx;
  font-weight: 850;
  white-space: nowrap;
}

.amount-row {
  min-height: 52rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  color: #475569;
  font-size: 26rpx;
}

.amount-row.discount {
  color: #0f766e;
}

.amount-row.payable {
  margin-top: 8rpx;
  padding-top: 12rpx;
  border-top: 1rpx solid #eef2f7;
  color: #17202a;
  font-size: 30rpx;
  font-weight: 850;
}

.textarea {
  width: 100%;
  min-height: 120rpx;
  padding: 16rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  box-sizing: border-box;
  font-size: 26rpx;
}

.bottom {
  position: fixed;
  left: 24rpx;
  right: 24rpx;
  bottom: 24rpx;
  padding: 16rpx;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  box-shadow: 0 10rpx 30rpx rgba(15, 23, 42, 0.08);
}

.amount {
  margin-top: 4rpx;
  color: #b42318;
  font-size: 34rpx;
  font-weight: 900;
}

.submit {
  width: 220rpx;
  height: 76rpx;
  line-height: 76rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 27rpx;
  font-weight: 800;
}

.submit::after {
  border: 0;
}
</style>
