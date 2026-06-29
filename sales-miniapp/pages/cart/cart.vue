<template>
  <view class="page cart-page">
    <view v-if="groups.length" class="groups">
      <view v-for="group in groups" :key="group.owner_id" class="merchant-group">
        <view class="merchant-head">
          <view>
            <view class="merchant-name">{{ group.owner_name || '商家' }}</view>
            <view class="state">{{ group.ok ? '服务端已校验' : '部分商品需处理' }}</view>
          </view>
          <button class="group-checkout" :loading="loading" @click="goConfirm(group)">结算</button>
        </view>
        <view class="items">
          <view v-for="item in group.items" :key="item.key" class="cart-item">
            <image v-if="item.image_url" class="thumb" :src="item.image_url" mode="aspectFill" />
            <view v-else class="thumb placeholder">货</view>
            <view class="main">
              <view class="between">
                <view class="name">{{ item.name }}</view>
                <button class="remove" @click="remove(item)">删</button>
              </view>
              <view class="meta">{{ item.code }} {{ item.spec }}</view>
              <view v-if="item.quote_message" class="warn">{{ item.quote_message }}</view>
              <view class="row">
                <view>
                  <view class="price">¥{{ money(item.unit_price) }} / {{ item.order_uom }}</view>
                  <view class="base">折合 {{ item.base_qty || baseQty(item) }} {{ item.base_uom }}</view>
                </view>
                <QuantityStepper :model-value="item.qty" :min="0" @change="changeQty(item, $event)" />
              </view>
            </view>
          </view>
        </view>
        <view class="merchant-total">
          <text>{{ group.line_count }} 件</text>
          <text>小计 ¥{{ money(group.total_amount) }}</text>
        </view>
      </view>
    </view>
    <EmptyState v-else text="购物车为空" />

    <view v-if="cart.items.length" class="summary">
      <view>
        <view class="state">{{ groups.length }} 个商家</view>
        <view class="amount">¥{{ money(cart.totalAmount) }}</view>
      </view>
      <view class="actions">
        <button class="refresh" :loading="loading" @click="refresh">刷新</button>
        <button class="checkout" :loading="loading" @click="goFirstConfirm">结算</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import QuantityStepper from '../../components/QuantityStepper.vue'
import { useCartStore } from '../../stores/cart'
import { money } from '../../utils/money'
import { qtyText } from '../../utils/qty'

const cart = useCartStore()
const loading = ref(false)
const groups = computed(() => cart.groups || [])

function baseQty(item) {
  return qtyText(Number(item.qty || 0) * Number(item.qty_in_base || 1))
}

async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    const data = await cart.load()
    if (!data.ok) {
      const failed = data.lines.find((line) => !line.ok)
      uni.showToast({ title: (failed && failed.message) || '购物车校验未通过', icon: 'none' })
    }
  } catch (err) {
    uni.showToast({ title: err.message || '刷新失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

async function changeQty(item, qty) {
  if (loading.value) return
  loading.value = true
  try {
    const data = await cart.setItemQty(item, qty)
    if (data && !data.ok) {
      const failed = data.lines.find((line) => !line.ok)
      uni.showToast({ title: (failed && failed.message) || '购物车校验未通过', icon: 'none' })
    }
  } catch (err) {
    uni.showToast({ title: err.message || '修改失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

async function remove(item) {
  if (loading.value) return
  loading.value = true
  try {
    await cart.removeItem(item)
  } catch (err) {
    uni.showToast({ title: err.message || '删除失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function goConfirm(group) {
  if (!group || !group.owner_id) return
  uni.navigateTo({ url: `/pages/order-confirm/order-confirm?owner_id=${group.owner_id}&cart_id=${group.cart_id || ''}` })
}

function goFirstConfirm() {
  if (!groups.value.length) return
  if (groups.value.length > 1) {
    uni.showToast({ title: '请按商家分别结算', icon: 'none' })
    return
  }
  goConfirm(groups.value[0])
}

onShow(() => refresh())
</script>

<style scoped>
.cart-page {
  padding-bottom: 270rpx;
}

.groups,
.items {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.merchant-group {
  padding: 18rpx;
  background: #fff;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
}

.merchant-head,
.merchant-total {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.merchant-head {
  margin-bottom: 14rpx;
}

.merchant-name {
  color: #17202a;
  font-size: 29rpx;
  font-weight: 850;
}

.merchant-total {
  margin-top: 14rpx;
  color: #334155;
  font-size: 25rpx;
  font-weight: 700;
}

.group-checkout {
  width: 112rpx;
  height: 60rpx;
  line-height: 60rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 24rpx;
  font-weight: 750;
}

.group-checkout::after {
  border: 0;
}

.cart-item {
  display: flex;
  gap: 16rpx;
  padding: 16rpx 0;
  border-top: 1rpx solid #eef2f7;
  background: #fff;
}

.cart-item:first-child {
  border-top: 0;
  padding-top: 0;
}

.cart-item:last-child {
  padding-bottom: 0;
}

.thumb {
  width: 132rpx;
  height: 132rpx;
  border-radius: 8rpx;
  background: #eef2f7;
  flex-shrink: 0;
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0f766e;
  font-weight: 900;
}

.main {
  flex: 1;
  min-width: 0;
}

.between,
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.row {
  margin-top: 14rpx;
}

.name {
  flex: 1;
  min-width: 0;
  color: #17202a;
  font-size: 29rpx;
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remove {
  width: 52rpx;
  height: 48rpx;
  line-height: 48rpx;
  padding: 0;
  border: 1rpx solid #d7dde8;
  border-radius: 8rpx;
  background: #fff;
  color: #64748b;
  font-size: 22rpx;
}

.remove::after {
  border: 0;
}

.meta,
.base,
.state {
  color: #64748b;
  font-size: 23rpx;
}

.meta {
  margin-top: 6rpx;
}

.warn {
  margin-top: 8rpx;
  color: #b45309;
  font-size: 23rpx;
}

.price {
  color: #b42318;
  font-size: 26rpx;
  font-weight: 800;
}

.base {
  margin-top: 4rpx;
}

.summary {
  position: fixed;
  left: 24rpx;
  right: 24rpx;
  bottom: calc(128rpx + env(safe-area-inset-bottom));
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

.actions {
  display: flex;
  gap: 10rpx;
}

.refresh,
.checkout {
  width: 128rpx;
  height: 76rpx;
  line-height: 76rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  font-size: 26rpx;
  font-weight: 750;
}

.refresh {
  background: #eef2f7;
  color: #334155;
}

.checkout {
  background: #0f766e;
  color: #fff;
}

.refresh::after,
.checkout::after {
  border: 0;
}
</style>
