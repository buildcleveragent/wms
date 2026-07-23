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
            <view
              class="select-control"
              :class="{ selected: cart.isSelected(item) }"
              role="checkbox"
              :aria-checked="cart.isSelected(item)"
              :aria-label="cart.isSelected(item) ? `取消选择${item.name}` : `选择${item.name}`"
              @click.stop="cart.toggleSelection(item)"
            >
              <view v-if="cart.isSelected(item)" class="check-mark" aria-hidden="true" />
            </view>
            <view class="product-content">
              <image v-if="item.image_url" class="thumb" :src="item.image_url" mode="aspectFill" />
              <view v-else class="thumb placeholder">货</view>
              <view class="main">
                <view class="between">
                  <view class="name">{{ item.name }}</view>
                  <button class="remove" @click="remove(item)">删</button>
                </view>
                <view v-if="item.spec || item.order_uom_name" class="meta">{{ [item.spec, item.order_uom_name].filter(Boolean).join(' · ') }}</view>
                <view v-if="item.quote_message" class="warn">{{ item.quote_message }}</view>
                <view class="row">
                  <view>
                    <view class="price">¥{{ money(item.unit_price) }} / {{ item.order_uom_name }}</view>
                    <view class="base">{{ item.qty }} {{ item.order_uom_name }}</view>
                  </view>
                  <QuantityStepper
                    :model-value="item.qty"
                    :min="quantityMin(item)"
                    :step="quantityStep(item)"
                    @change="changeQty(item, $event)"
                  />
                </view>
              </view>
            </view>
          </view>
        </view>
        <view class="package-total">
          <text>已选 {{ groupSelectedCount(group) }}/{{ group.line_count }} 件</text>
          <text>已选小计 ¥{{ money(groupSelectedAmount(group)) }}</text>
        </view>
      </view>
    </view>
    <EmptyState v-else text="购物车为空" />

    <view v-if="cart.items.length" class="summary">
      <view class="summary-main">
        <view
          class="all-toggle"
          role="checkbox"
          :aria-checked="cart.allSelected"
          :aria-label="cart.allSelected ? '取消全选' : '全选商品'"
          @click="toggleAll"
        >
          <view class="select-control" :class="{ selected: cart.allSelected }">
            <view v-if="cart.allSelected" class="check-mark" aria-hidden="true" />
          </view>
          <text>全选</text>
        </view>
        <view class="summary-amount">
          <view class="state">已选 {{ cart.selectedItemCount }} 件 · {{ cart.selectedGroups.length }} 个配送包裹</view>
          <view class="amount">合计 ¥{{ money(cart.selectedTotalAmount) }}</view>
        </view>
        <button class="checkout" :disabled="cart.noneSelected" :loading="loading" @click="goCheckout">
          结算{{ cart.selectedItemCount ? `(${cart.selectedItemCount})` : '' }}
        </button>
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

function quantityMin(item) {
  return item.rules && item.rules.enabled ? Number(item.rules.min_order_qty || 1) : 1
}

function quantityStep(item) {
  return item.rules && item.rules.enabled ? Number(item.rules.multiple_qty || 1) : 1
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

function groupSelectedItems(group) {
  return group.items.filter((item) => cart.isSelected(item))
}

function groupSelectedCount(group) {
  return groupSelectedItems(group).length
}

function groupSelectedAmount(group) {
  return groupSelectedItems(group).reduce(
    (sum, item) => sum + Number(item.line_amount || Number(item.qty) * Number(item.unit_price) || 0),
    0,
  )
}

function toggleAll() {
  if (cart.allSelected) cart.selectNone()
  else cart.selectAll()
}

function goCheckout() {
  if (cart.noneSelected) {
    uni.showToast({ title: '请先选择需要结算的商品', icon: 'none' })
    return
  }
  uni.navigateTo({ url: '/pages/order-confirm/order-confirm' })
}

onShow(() => refresh())
</script>

<style scoped>
.cart-page {
  padding-bottom: 240rpx;
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
  align-items: center;
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

.select-control {
  width: 44rpx;
  height: 44rpx;
  box-sizing: border-box;
  flex: 0 0 44rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2rpx solid #94a3b8;
  border-radius: 50%;
  background: #fff;
  color: #fff;
  font-size: 29rpx;
  font-weight: 900;
  line-height: 1;
}

.select-control.selected {
  border-color: #1677ff;
  background: #1677ff;
}

.check-mark {
  width: 10rpx;
  height: 18rpx;
  box-sizing: border-box;
  border-right: 4rpx solid #fff;
  border-bottom: 4rpx solid #fff;
  transform: translateY(-2rpx) rotate(45deg);
  transform-origin: center;
}

.product-content {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 16rpx;
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
  flex-direction: column;
  gap: 16rpx;
  box-shadow: 0 10rpx 30rpx rgba(15, 23, 42, 0.08);
}

.summary-main {
  width: 100%;
  display: flex;
  align-items: center;
}

.all-toggle {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10rpx;
  color: #475569;
  font-size: 24rpx;
}

.summary-main {
  justify-content: space-between;
  gap: 16rpx;
}

.summary-amount {
  flex: 1;
  min-width: 0;
  text-align: right;
}

.amount {
  margin-top: 4rpx;
  color: #b42318;
  font-size: 34rpx;
  font-weight: 900;
}

.checkout {
  width: 176rpx;
  height: 76rpx;
  line-height: 76rpx;
  flex: 0 0 176rpx;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  font-size: 26rpx;
  font-weight: 750;
}

.checkout {
  background: #0f766e;
  color: #fff;
}

.checkout[disabled] {
  background: #cbd5e1;
  color: #fff;
}

.checkout::after {
  border: 0;
}
</style>
