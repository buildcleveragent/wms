<template>
  <view class="page user-page">
    <view class="profile">
      <view class="avatar">{{ initials }}</view>
      <view>
        <view class="name">{{ profileName }}</view>
        <view class="muted">{{ accountSubtitle }}</view>
      </view>
    </view>
    <view v-if="merchantOptions.length > 1" class="merchant-switch">
      <view>
        <view class="switch-label">当前资产商家</view>
        <view class="switch-name">{{ assetMerchantName }}</view>
      </view>
      <picker :range="merchantOptions" range-key="name" :value="merchantIndex" @change="changeMerchant">
        <view class="switch-btn">切换</view>
      </picker>
    </view>
    <view class="assets">
      <view class="asset" @click="goBenefits">
        <view class="asset-value">{{ pointsText }}</view>
        <view class="asset-label">积分</view>
      </view>
      <view class="asset" @click="goBenefits">
        <view class="asset-value">{{ couponText }}</view>
        <view class="asset-label">优惠券</view>
      </view>
      <view class="asset" @click="go('/pages/merchants/merchants')">
        <view class="asset-value">{{ merchantCount }}</view>
        <view class="asset-label">商家</view>
      </view>
    </view>
    <view class="order-panel">
      <view class="panel-head">
        <text>我的订单</text>
        <text class="more" @click="go('/pages/order-list/order-list')">全部</text>
      </view>
      <view class="order-shortcuts">
        <view class="shortcut" @click="go('/pages/order-list/order-list?status=WAIT_PAY')">待付款</view>
        <view class="shortcut" @click="go('/pages/order-list/order-list?status=WAIT_SHIP')">待发货</view>
        <view class="shortcut" @click="go('/pages/order-list/order-list?status=COMPLETED')">已完成</view>
        <view class="shortcut" @click="go('/pages/after-sales/after-sales')">售后</view>
      </view>
    </view>
    <view class="menu">
      <view class="menu-item" @click="go('/pages/benefits/benefits')">优惠券与积分</view>
      <view class="menu-item" @click="go('/pages/favorites/favorites')">我的收藏</view>
      <view class="menu-item" @click="go('/pages/history/history')">浏览足迹</view>
      <view class="menu-item" @click="go('/pages/merchants/merchants')">全部商家</view>
      <view class="menu-item" @click="go('/pages/after-sales/after-sales')">售后服务</view>
      <view class="menu-item" @click="go('/pages/address/address')">收货地址</view>
      <view class="menu-item" @click="go('/pages/cart/cart')">购物车</view>
      <view class="menu-item danger" @click="logout">退出登录</view>
    </view>
  </view>
</template>

<script setup>
import { onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'
import { benefitService } from '../../services/benefit'
import { useSessionStore } from '../../stores/session'
import { getToken } from '../../utils/request'

const session = useSessionStore()
const pointInfo = ref({ points: 0, frozen: 0 })
const coupons = ref([])
const selectedOwnerId = ref('')
const profile = computed(() => session.profile)
const merchantOptions = computed(() => {
  const current = profile.value || {}
  const bindings = Array.isArray(current.bindings) ? current.bindings : []
  const rows = bindings
    .map((item) => item.owner)
    .filter((owner) => owner && owner.id)
  if (!rows.length && current.owner && current.owner.id) rows.push(current.owner)
  const seen = new Set()
  return rows.filter((owner) => {
    const key = String(owner.id)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})
const selectedMerchant = computed(() => {
  const rows = merchantOptions.value
  if (!rows.length) return null
  return rows.find((owner) => String(owner.id) === String(selectedOwnerId.value)) || rows[0]
})
const merchantIndex = computed(() => {
  const index = merchantOptions.value.findIndex((owner) => String(owner.id) === String(ownerId.value))
  return index >= 0 ? index : 0
})
const profileName = computed(() => {
  const current = profile.value || {}
  if (current.customer && current.customer.name) return current.customer.name
  if (current.buyer && current.buyer.nickname) return current.buyer.nickname
  if (current.owner && current.owner.name) return current.owner.name
  return '我的账户'
})
const accountSubtitle = computed(() => {
  const current = profile.value || {}
  const bindings = Array.isArray(current.bindings) ? current.bindings : []
  if (bindings.length > 1) return `已开通 ${bindings.length} 个商家`
  if (selectedMerchant.value && selectedMerchant.value.name) return `可购买 ${selectedMerchant.value.name} 商品`
  return '账号资料待完善'
})
const initials = computed(() => profileName.value.slice(0, 1) || '我')
const merchantCount = computed(() => merchantOptions.value.length)
const ownerId = computed(() => (selectedMerchant.value && selectedMerchant.value.id) || '')
const assetMerchantName = computed(() => (selectedMerchant.value && selectedMerchant.value.name) || '当前商家')
const pointsText = computed(() => String(pointInfo.value.points || 0))
const couponText = computed(() => String(coupons.value.length || 0))

function ensureSelectedOwner() {
  const rows = merchantOptions.value
  if (!rows.length) {
    selectedOwnerId.value = ''
    return
  }
  const exists = rows.some((owner) => String(owner.id) === String(selectedOwnerId.value))
  if (!selectedOwnerId.value || !exists) selectedOwnerId.value = String(rows[0].id)
}

function go(url) {
  if (url.startsWith('/pages/order-list/order-list')) {
    const [, query = ''] = url.split('?')
    const statusPair = query.split('&').find((item) => item.indexOf('status=') === 0)
    if (statusPair) uni.setStorageSync('sale_mini_pending_order_status', decodeURIComponent(statusPair.split('=')[1] || ''))
    uni.switchTab({ url: '/pages/order-list/order-list' })
    return
  }
  if (url === '/pages/cart/cart') {
    uni.switchTab({ url })
    return
  }
  uni.navigateTo({ url })
}

async function loadAssets() {
  ensureSelectedOwner()
  const params = ownerId.value ? { owner_id: ownerId.value } : {}
  try {
    const [couponRows, points] = await Promise.all([
      benefitService.coupons(params),
      benefitService.points(params),
    ])
    coupons.value = couponRows || []
    pointInfo.value = points || { points: 0, frozen: 0 }
  } catch (err) {
    coupons.value = []
  }
}

async function changeMerchant(event) {
  const row = merchantOptions.value[Number(event.detail.value)]
  selectedOwnerId.value = row && row.id ? String(row.id) : ''
  await loadAssets()
}

function goBenefits() {
  const suffix = ownerId.value ? `?owner_id=${ownerId.value}` : ''
  go(`/pages/benefits/benefits${suffix}`)
}

function logout() {
  session.logout()
}

onShow(() => {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  session.fetchProfile().then(loadAssets).catch(() => {})
})
</script>

<style scoped>
.profile {
  padding: 26rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.avatar {
  width: 88rpx;
  height: 88rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 38rpx;
  font-weight: 900;
}

.name {
  color: #17202a;
  font-size: 32rpx;
  font-weight: 850;
}

.muted {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 24rpx;
}

.merchant-switch {
  margin-top: 18rpx;
  min-height: 92rpx;
  padding: 16rpx 20rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.switch-label {
  color: #64748b;
  font-size: 22rpx;
}

.switch-name {
  margin-top: 6rpx;
  max-width: 420rpx;
  color: #17202a;
  font-size: 28rpx;
  font-weight: 850;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switch-btn {
  min-width: 104rpx;
  height: 56rpx;
  padding: 0 18rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1rpx solid #b6d8d2;
  border-radius: 8rpx;
  background: #edf8f5;
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 750;
}

.assets {
  margin-top: 18rpx;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12rpx;
}

.asset {
  min-height: 112rpx;
  padding: 18rpx 8rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
  text-align: center;
}

.asset-value {
  color: #0f766e;
  font-size: 34rpx;
  font-weight: 900;
}

.asset-label {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
}

.order-panel {
  margin-top: 18rpx;
  padding: 20rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #17202a;
  font-size: 28rpx;
  font-weight: 850;
}

.more {
  color: #0f766e;
  font-size: 24rpx;
  font-weight: 500;
}

.order-shortcuts {
  margin-top: 18rpx;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10rpx;
}

.shortcut {
  height: 64rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8rpx;
  background: #f8fafc;
  color: #334155;
  font-size: 24rpx;
}

.menu {
  margin-top: 18rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  overflow: hidden;
  background: #fff;
}

.menu-item {
  min-height: 88rpx;
  padding: 0 24rpx;
  display: flex;
  align-items: center;
  border-bottom: 1rpx solid #eef2f7;
  color: #17202a;
  font-size: 28rpx;
}

.menu-item:last-child {
  border-bottom: 0;
}

.danger {
  color: #b42318;
}
</style>
