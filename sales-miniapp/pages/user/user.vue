<template>
  <view class="page user-page">
    <view class="profile">
      <view class="avatar">{{ initials }}</view>
      <view>
        <view class="name">{{ profileName }}</view>
        <view class="muted">{{ accountSubtitle }}</view>
      </view>
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
      <view class="asset" @click="go('/pages/favorites/favorites')">
        <view class="asset-value">{{ favoriteText }}</view>
        <view class="asset-label">收藏</view>
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
      <view class="menu-item" @click="go('/pages/product-list/product-list')">全部商品</view>
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
const profile = computed(() => session.profile)
const profileName = computed(() => {
  const current = profile.value || {}
  if (current.buyer && current.buyer.nickname) return current.buyer.nickname
  if (session.user && session.user.username) return session.user.username
  return '我的账户'
})
const accountSubtitle = computed(() => '会员账户 · 统一商城服务')
const initials = computed(() => profileName.value.slice(0, 1) || '我')
const pointsText = computed(() => String(pointInfo.value.points || 0))
const couponText = computed(() => String(coupons.value.length || 0))
const favoriteText = computed(() => '查看')

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
  try {
    const [couponRows, points] = await Promise.all([
      benefitService.coupons(),
      benefitService.points(),
    ])
    coupons.value = couponRows || []
    pointInfo.value = points || { points: 0, frozen: 0 }
  } catch (err) {
    coupons.value = []
  }
}

function goBenefits() {
  go('/pages/benefits/benefits')
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
