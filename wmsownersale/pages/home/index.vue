<template>
  <view class="home-page">
    <view v-if="quickItems.length" class="quick-grid">
      <view
        v-for="item in quickItems"
        :key="item.key"
        class="quick-card"
        hover-class="quick-card--pressed"
        role="button"
        :aria-label="item.text"
        @click="openItem(item)"
      >
        <view class="quick-icon" :class="`quick-icon--${item.color || 'blue'}`">
          <text class="quick-icon__emoji">{{ item.emoji }}</text>
        </view>
        <text class="quick-title">{{ item.text }}</text>
      </view>
    </view>

    <view v-else class="empty-access">
      <view class="empty-icon">🔒</view>
      <text class="empty-title">暂无可用功能</text>
      <text class="empty-hint">当前账号没有货主端功能权限</text>
    </view>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import { useAuth } from '@/store/auth'
import { buildOwnerMenu } from '@/utils/ownerAccess'
import { downloadAuthenticatedFile } from '@/utils/request.js'

const auth = useAuth()
auth.ensureAuth()

const menu = computed(() => buildOwnerMenu({
  roles: auth.roles,
  capabilities: auth.capabilities,
}))

const quickItems = computed(() => [
  ...menu.value.orders,
  ...menu.value.reports,
  ...menu.value.administration,
])

function navigationFailed() {
  uni.showToast({ title: '页面暂时无法打开，请稍后重试', icon: 'none' })
}

function openItem(item) {
  if (item.action === 'download_template') return downloadDropShipTemplate()

  const options = { url: item.path, fail: navigationFailed }
  if (item.navigation === 'tab') return uni.switchTab(options)
  return uni.navigateTo(options)
}

async function downloadDropShipTemplate() {
  try {
    await downloadAuthenticatedFile(
      '/api/outbound/orders/import-drop-ship-template/',
      '一件代发导入模板.xlsx',
    )
  } catch (error) {
    uni.showToast({ title: error?.message || '模板下载失败', icon: 'none' })
  }
}
</script>

<style scoped>
.home-page {
  min-height: 100vh;
  padding: 24rpx;
  background: #f6f7f9;
  box-sizing: border-box;
}

.quick-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22rpx;
}

.quick-card {
  display: flex;
  min-width: 0;
  min-height: 176rpx;
  padding: 28rpx 26rpx 24rpx;
  flex-direction: column;
  align-items: flex-start;
  justify-content: space-between;
  border: 1rpx solid #e8ebef;
  border-radius: 24rpx;
  background: #fff;
  box-shadow: 0 10rpx 28rpx rgba(15, 23, 42, 0.055);
  box-sizing: border-box;
  transition: transform 120ms ease, box-shadow 120ms ease, background-color 120ms ease;
}

.quick-card--pressed {
  background: #f8fafc;
  box-shadow: 0 4rpx 12rpx rgba(15, 23, 42, 0.08);
  transform: scale(0.98);
}

.quick-icon {
  display: flex;
  width: 70rpx;
  height: 70rpx;
  align-items: center;
  justify-content: center;
  border-radius: 18rpx;
  box-sizing: border-box;
}

.quick-icon--blue {
  background: #dbeafe;
}

.quick-icon--green {
  background: #dcfce7;
}

.quick-icon--orange {
  background: #ffedd5;
}

.quick-icon__emoji {
  font-size: 40rpx;
  line-height: 1;
}

.quick-title {
  display: block;
  width: 100%;
  margin-top: 22rpx;
  color: #101828;
  font-size: 30rpx;
  font-weight: 700;
  line-height: 1.35;
  word-break: break-all;
}

.empty-access {
  display: flex;
  min-height: 56vh;
  padding: 40rpx;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #667085;
  text-align: center;
}

.empty-icon {
  margin-bottom: 20rpx;
  font-size: 58rpx;
}

.empty-title {
  color: #344054;
  font-size: 30rpx;
  font-weight: 700;
}

.empty-hint {
  margin-top: 10rpx;
  font-size: 25rpx;
}

@media screen and (min-width: 900px) {
  .home-page {
    padding: 30rpx;
  }

  .quick-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 28rpx;
  }

  .quick-card {
    min-height: 190rpx;
    padding: 32rpx;
  }
}
</style>
