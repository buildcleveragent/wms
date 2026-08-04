<template>
  <view class="page">
    <view class="section" v-for="grp in groups" :key="grp.title">
      <view class="section-title">{{ grp.title }}</view>
      <view class="grid">
        <view class="tile" v-for="it in grp.items" :key="it.key" @click="go(it)">
          <view class="tile-inner">
            <view class="feature-icon" :class="it.color">{{ it.emoji }}</view>
            <text class="tile-text"> {{ it.text }}</text>
          </view>
        </view>
      </view>
    </view>

    <view v-if="adminItems.length" class="section">
      <view class="section-title">管理功能</view>
      <view class="grid">
        <view class="tile" v-for="it in adminItems" :key="it.key" @click="go(it)">
          <view class="tile-inner">
            <view class="feature-icon" :class="it.color">{{ it.emoji }}</view>
            <text class="tile-text"> {{ it.text }}</text>
          </view>
        </view>
      </view>
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

const groups = computed(() => {
  const menu = buildOwnerMenu({ roles: auth.roles, capabilities: auth.capabilities })

  return [
    { title: '订单业务', items: menu.orders },
    { title: '库存报表', items: menu.reports },
  ].filter((group) => group.items.length)
})

const adminItems = computed(() => buildOwnerMenu({
  roles: auth.roles,
  capabilities: auth.capabilities,
}).administration)

function navigationFailed() {
  uni.showToast({ title: '页面暂时无法打开，请稍后重试', icon: 'none' })
}

async function go(item) {
  if (item.action === 'download_template') {
    try {
      await downloadAuthenticatedFile(
        '/api/outbound/orders/import-drop-ship-template/',
        '一件代发导入模板.xlsx',
      )
    } catch (error) {
      uni.showToast({ title: error?.message || '模板下载失败', icon: 'none' })
    }
    return
  }
  const options = { url: item.path, fail: navigationFailed }
  if (item.navigation === 'tab') {
    uni.switchTab(options)
    return
  }
  uni.navigateTo(options)
}
</script>

<style scoped>
.page { min-height: 100vh; padding: 24rpx; background: #f5f7fa; box-sizing: border-box; }
.section { margin-bottom: 28rpx; }
.section-title { margin-bottom: 14rpx; color: #374151; font-size: 30rpx; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16rpx; }
.tile { padding: 22rpx; border-radius: 14rpx; background: #fff; box-shadow: 0 2rpx 8rpx rgba(0, 0, 0, .05); }
.tile-inner { display: flex; align-items: center; gap: 16rpx; }
.feature-icon { display: flex; width: 64rpx; height: 64rpx; align-items: center; justify-content: center; border-radius: 16rpx; font-size: 34rpx; }
.feature-icon.blue { background: #dbeafe; }
.feature-icon.green { background: #dcfce7; }
.feature-icon.orange { background: #ffedd5; }
.tile-text { color: #111827; font-size: 28rpx; }
</style>
