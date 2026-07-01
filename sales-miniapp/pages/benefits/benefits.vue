<template>
  <view class="page benefits-page">
    <view class="member-head">
      <view>
        <view class="head-title">会员资产</view>
        <view class="head-sub">博悦商城</view>
      </view>
    </view>

    <view class="asset-grid">
      <view class="asset">
        <view class="asset-value">{{ pointInfo.points || 0 }}</view>
        <view class="asset-label">可用积分</view>
      </view>
      <view class="asset">
        <view class="asset-value">{{ pointInfo.frozen || 0 }}</view>
        <view class="asset-label">冻结积分</view>
      </view>
      <view class="asset">
        <view class="asset-value">{{ coupons.length }}</view>
        <view class="asset-label">可用优惠券</view>
      </view>
    </view>

    <view class="section-head">
      <text>可用优惠券</text>
      <text class="hint">结算时自动重算</text>
    </view>

    <view v-if="coupons.length" class="coupon-list">
      <view v-for="coupon in coupons" :key="coupon.id" class="coupon">
        <view class="coupon-main">
          <view class="coupon-amount">¥{{ money(coupon.discount_amount) }}</view>
          <view>
            <view class="coupon-title">{{ coupon.title }}</view>
            <view class="coupon-rule">满 ¥{{ money(coupon.threshold_amount) }} 可用</view>
            <view class="coupon-time">{{ couponTime(coupon) }}</view>
          </view>
        </view>
        <button class="use-btn" @click="goList">去使用</button>
      </view>
    </view>
    <EmptyState v-else :text="loading ? '加载中' : '暂无可用优惠券'" />

    <view class="section-head">
      <text>积分说明</text>
    </view>
    <view class="rule-box">
      <view>积分会在结算页按服务端规则折抵。</view>
      <view>优惠券、积分、订单金额都会在后端重新计算，前端展示只作参考。</view>
    </view>
  </view>
</template>

<script setup>
import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { ref } from 'vue'
import EmptyState from '../../components/EmptyState.vue'
import { benefitService } from '../../services/benefit'
import { useSessionStore } from '../../stores/session'
import { money } from '../../utils/money'
import { getToken } from '../../utils/request'

const session = useSessionStore()
const coupons = ref([])
const pointInfo = ref({ points: 0, frozen: 0, exchange_rate: '100.00' })
const loading = ref(false)

function couponTime(coupon) {
  const end = coupon.expires_at || coupon.effective_to || ''
  if (!end) return '长期有效'
  return `有效期至 ${String(end).slice(0, 10)}`
}

async function load() {
  if (!getToken()) {
    uni.navigateTo({ url: '/pages/login/login' })
    return
  }
  if (loading.value) return
  loading.value = true
  try {
    if (!session.profile) await session.fetchProfile()
    const [couponRows, points] = await Promise.all([
      benefitService.coupons(),
      benefitService.points(),
    ])
    coupons.value = couponRows || []
    pointInfo.value = points || { points: 0, frozen: 0, exchange_rate: '100.00' }
  } catch (err) {
    uni.showToast({ title: err.message || '权益加载失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function goList() {
  uni.navigateTo({ url: '/pages/product-list/product-list' })
}

onShow(() => {
  load()
})

onPullDownRefresh(async () => {
  try {
    await load()
  } finally {
    uni.stopPullDownRefresh()
  }
})
</script>

<style scoped>
.benefits-page {
  padding-bottom: 48rpx;
}

.member-head {
  padding: 28rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18rpx;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
}

.head-title {
  font-size: 36rpx;
  font-weight: 900;
}

.head-sub {
  margin-top: 8rpx;
  font-size: 24rpx;
  opacity: 0.9;
}

.switch-btn {
  min-width: 140rpx;
  height: 58rpx;
  padding: 0 18rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1rpx solid rgba(255, 255, 255, 0.62);
  border-radius: 8rpx;
  font-size: 24rpx;
}

.asset-grid {
  margin-top: 18rpx;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12rpx;
}

.asset {
  min-height: 132rpx;
  padding: 20rpx 10rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
  text-align: center;
}

.asset-value {
  color: #b42318;
  font-size: 36rpx;
  font-weight: 900;
}

.asset-label {
  margin-top: 8rpx;
  color: #64748b;
  font-size: 23rpx;
}

.section-head {
  margin: 26rpx 2rpx 14rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #17202a;
  font-size: 29rpx;
  font-weight: 850;
}

.hint {
  color: #64748b;
  font-size: 22rpx;
  font-weight: 400;
}

.coupon-list {
  display: flex;
  flex-direction: column;
  gap: 14rpx;
}

.coupon {
  padding: 20rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14rpx;
  border: 1rpx solid #f0c9c1;
  border-radius: 8rpx;
  background: #fff;
}

.coupon-main {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 18rpx;
}

.coupon-amount {
  min-width: 120rpx;
  color: #b42318;
  font-size: 38rpx;
  font-weight: 950;
}

.coupon-title {
  color: #17202a;
  font-size: 28rpx;
  font-weight: 800;
}

.coupon-rule,
.coupon-time {
  margin-top: 6rpx;
  color: #64748b;
  font-size: 23rpx;
}

.use-btn {
  width: 112rpx;
  height: 58rpx;
  line-height: 58rpx;
  padding: 0;
  border: 0;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  font-size: 24rpx;
}

.use-btn::after {
  border: 0;
}

.rule-box {
  padding: 22rpx;
  border: 1rpx solid #dfe6ef;
  border-radius: 8rpx;
  background: #fff;
  color: #475569;
  font-size: 25rpx;
  line-height: 1.7;
}
</style>
