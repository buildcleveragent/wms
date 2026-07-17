<template>
  <view class="cart-page">
    <view v-if="checkingPermission" class="state-card">正在读取代办出库权限…</view>

    <AssistedOrderPanel
      v-else-if="authorized"
      mode="page"
      @continue-selecting="backToProducts"
      @authorization-denied="handleAuthorizationDenied"
    />

    <view v-if="authorized" class="history-navigation">
      <button class="history-button" @click="openAssistedHistory">历史出库单</button>
      <button class="history-button" @click="openAssistedStats">出库统计</button>
    </view>

    <view v-else class="state-card">
      <view>当前账号无代办出库权限，或代办出库单上下文已失效。</view>
      <button class="outline-button" @click="backToStart">返回重新选择</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import AssistedOrderPanel from '@/components/outbound/AssistedOrderPanel.vue'
import { useAuth } from '@/store/auth'
import { useAssistedOutbound } from '@/store/assistedOutbound'

const auth = useAuth()
const draft = useAssistedOutbound()
const checkingPermission = ref(true)
const authorized = ref(false)

function backToProducts() {
  uni.navigateBack({
    fail: () => uni.redirectTo({ url: '/pages/outbound/assisted_products' }),
  })
}

function backToStart() {
  uni.redirectTo({ url: '/pages/outbound/assisted' })
}

function handleAuthorizationDenied() {
  authorized.value = false
}

function openAssistedHistory() {
  uni.navigateTo({ url: '/pages/outbound/assisted_history' })
}

function openAssistedStats() {
  uni.navigateTo({ url: '/pages/outbound/assisted_stats' })
}

async function initialize() {
  checkingPermission.value = true
  try {
    await auth.ensureProfile()
    authorized.value = Boolean(
      auth.canProcessAssistedOutbound && draft.owner?.id && draft.customer?.id,
    )
  } catch (error) {
    console.warn('无法读取代办权限', error)
  } finally {
    checkingPermission.value = false
  }
}

onLoad(initialize)
</script>

<style scoped>
.cart-page {
  box-sizing: border-box;
  min-height: 100vh;
  padding: 18rpx;
  background: #f6f7fb;
}
.state-card {
  padding: 24rpx;
  border-radius: 16rpx;
  background: #fff;
}
.history-navigation {
  position: relative;
  z-index: 30;
  display: flex;
  gap: 12rpx;
  margin-bottom: 130rpx;
}
.history-button {
  flex: 1;
  margin: 0;
  color: #1677ff;
  background: #eef5ff;
}
.outline-button {
  margin: 24rpx 0 0;
  color: #1677ff;
  background: #eef5ff;
}
</style>
