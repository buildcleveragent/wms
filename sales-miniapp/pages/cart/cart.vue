<template>
  <view class="page cart-page">
    <view v-if="groups.length" class="groups">
      <view v-for="(group, index) in groups" :key="group.owner_id" class="package-group">
        <view class="package-head">
          <view>
            <view class="package-name">配送包裹 {{ index + 1 }}</view>
            <view class="state">{{ group.ok ? '价格库存已校验' : '部分商品需处理' }}</view>
          </view>
          <view class="package-badge">{{ group.line_count }} 件</view>
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
              <view v-if="item.spec || item.order_uom" class="meta">{{ [item.spec, item.order_uom].filter(Boolean).join(' · ') }}</view>
              <view v-if="item.quote_message" class="warn">{{ item.quote_message }}</view>
              <view class="row">
                <view>
                  <view class="price">¥{{ money(item.unit_price) }} / {{ item.order_uom }}</view>
                  <view class="base">{{ item.qty }} {{ item.order_uom }}</view>
                </view>
                <QuantityStepper :model-value="item.qty" :min="0" @change="changeQty(item, $event)" />
              </view>
            </view>
          </view>
        </view>
        <view class="package-total">
          <text>{{ group.line_count }} 件</text>
          <text>小计 ¥{{ money(group.total_amount) }}</text>
        </view>
      </view>
    </view>
    <EmptyState v-else text="购物车为空" />

    <view v-if="cart.items.length" class="summary">
      <view>
        <view class="state">{{ groups.length }} 个配送包裹</view>
        <view class="amount">¥{{ money(cart.totalAmount) }}</view>
      </view>
      <view class="actions">
        <button class="refresh" :loading="loading" @click="refresh">刷新</button>
        <button class="checkout" :loading="loading" @click="goCheckout">统一结算</button>
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

const cart = useCartStore()
const loading = ref(false)
const groups = computed(() => cart.groups || [])

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

function goSinglePackageCheckout(group) {
  if (!group || !group.cart_id) return
  uni.navigateTo({ url: `/pages/order-confirm/order-confirm?cart_id=${group.cart_id}` })
}

function goCheckout() {
  if (!groups.value.length) return
  if (groups.value.length > 1) {
    uni.navigateTo({ url: '/pages/order-confirm/order-confirm' })
    return
  }
  goSinglePackageCheckout(groups.value[0])
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

.package-group {
  padding: 18rpx;
  background: #fff;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
}

.package-head,
.package-total {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}

.package-head {
  margin-bottom: 14rpx;
}

.package-name {
  color: #17202a;
  font-size: 29rpx;
  font-weight: 850;
}

.package-total {
  margin-top: 14rpx;
  color: #334155;
  font-size: 25rpx;
  font-weight: 700;
}

.package-badge {
  width: 112rpx;
  height: 60rpx;
  line-height: 60rpx;
  padding: 0;
  text-align: center;
  border-radius: 8rpx;
  background: #ecfdf5;
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 750;
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
