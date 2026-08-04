<template>
  <view class="page">
    <view class="hero">
      <view class="hero-badge">Reports</view>
      <view class="section-title">报表与查询</view>
      <view class="hero-desc">计划、实际收发、库存与对账均按当前货主范围展示。</view>
    </view>

    <button
      v-for="card in cards"
      :key="card.path"
      class="card"
      @click="go(card.path)"
    >
      <view class="card-top">
        <view class="card-icon">{{ card.icon }}</view>
        <view class="card-arrow">›</view>
      </view>
      <view class="card-title">{{ card.title }}</view>
      <view class="card-desc">{{ card.desc }}</view>
    </button>
    <view v-if="!cards.length" class="empty-state">当前账号没有可用报表。</view>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import { useAuth } from '@/store/auth'
import { ownerAccess } from '@/utils/ownerAccess'

const auth = useAuth()
auth.ensureAuth()

const baseCards = [
  {
    title: '入出库履约',
    desc: '按计划量、库存过账和发运实绩查看订单进度与差异',
    path: '/pages/reports/operations',
    icon: '📈',
    requiresOperations: true,
  },
  {
    title: '实时库存',
    desc: '查看当前货主名下商品库存汇总',
    path: '/pages/inventory/index',
    icon: '📦',
  },
  {
    title: '计费总览',
    desc: '查看账期金额、费用构成、每日趋势和当前账单',
    path: '/pages/billing/overview',
    icon: '💳',
    managerOnly: true,
  },
]

const cards = computed(() =>
  baseCards.filter((card) => {
    const access = ownerAccess({ roles: auth.roles, capabilities: auth.capabilities })
    if (!access.ownerRole) return false
    if (card.requiresOperations && !access.canViewOperations) return false
    if (card.managerOnly && !access.manager) return false
    return true
  })
)

function go(path) {
  uni.navigateTo({ url: path })
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: linear-gradient(180deg, #f7f8fc 0%, #eef3ff 100%);
  padding: 24rpx;
  box-sizing: border-box;
}

.card {
  display: block;
  width: 100%;
  min-height: 96rpx;
  margin: 0 0 20rpx;
  padding: 24rpx;
  text-align: left;
  line-height: normal;
  border: 0;
}

.card::after { border: 0; }
.empty-state { padding: 48rpx 24rpx; text-align: center; color: #6b7280; }

.hero {
  padding: 28rpx;
  border-radius: 28rpx;
  margin-bottom: 20rpx;
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.16), transparent 34%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 100%);
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  padding: 8rpx 18rpx;
  margin-bottom: 12rpx;
  border-radius: 999rpx;
  background: #ffffff;
  color: #0b5fff;
  font-size: 20rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.section-title {
  font-size: 40rpx;
  font-weight: 700;
  color: #182033;
}

.hero-desc {
  margin-top: 10rpx;
  font-size: 24rpx;
  line-height: 1.6;
  color: #5f6c88;
}

.card {
  background: #fff;
  border-radius: 24rpx;
  padding: 24rpx;
  margin-bottom: 18rpx;
  box-shadow: 0 10rpx 30rpx rgba(17, 24, 39, 0.06);
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18rpx;
}

.card-icon {
  width: 88rpx;
  height: 88rpx;
  border-radius: 22rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0b5fff, #3ea6ff);
  color: #fff;
  font-size: 40rpx;
}

.card-arrow {
  font-size: 44rpx;
  color: #99a3ba;
}

.card-title {
  font-size: 32rpx;
  font-weight: 700;
  color: #1b2437;
  margin-bottom: 10rpx;
}

.card-desc {
  font-size: 24rpx;
  color: #667389;
  line-height: 1.6;
}
</style>
