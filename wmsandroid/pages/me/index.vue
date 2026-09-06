<template>
  <view class="page me-page">
    <view class="profile-head">
      <view class="avatar"><WmsIcon name="user" /></view>
      <view><text class="profile-name">{{ displayName }}</text><text class="profile-meta">{{ warehouseText }}</text></view>
    </view>
    <view class="menu-list">
      <view class="menu-row" @click="goChangePassword">
        <view class="menu-icon"><WmsIcon name="lock" /></view><text>修改密码</text><WmsIcon class="chevron" />
      </view>
      <view class="menu-row danger" @click="logout">
        <view class="menu-icon"><WmsIcon name="logout" /></view><text>退出登录</text><WmsIcon class="chevron" />
      </view>
    </view>
    <text class="version">金桥融通仓库手机版 · 1.0.0</text>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'

const auth = useAuth()
const displayName = computed(() => auth.user?.display_name || auth.user?.username || '未登录')
const warehouseText = computed(() => auth.user?.warehouse_id ? `仓库编号 ${auth.user.warehouse_id}` : '仓库现场账号')
function goChangePassword() { uni.navigateTo({ url: '/pages/me/change_password' }) }
async function logout() {
  const result = await new Promise((resolve) => uni.showModal({ title: '退出登录', content: '确定退出当前账号？', success: (res) => resolve(res.confirm), fail: () => resolve(false) }))
  if (!result) return
  await auth.logout()
  uni.reLaunch({ url: '/pages/login' })
}
</script>

<style scoped>
.me-page { padding-top: 54rpx; }.profile-head { display: flex; align-items: center; gap: 24rpx; padding-bottom: 42rpx; border-bottom: 1rpx solid #e2e8f0; }.avatar { width: 104rpx; height: 104rpx; border-radius: 50%; background: #eff6ff; color: #1677ff; display: flex; align-items: center; justify-content: center; font-size: 52rpx; }.profile-name,.profile-meta { display: block; }.profile-name { font-size: 38rpx; font-weight: 740; }.profile-meta { margin-top: 6rpx; color: #64748b; font-size: 24rpx; }
.menu-list { margin-top: 34rpx; border: 1rpx solid #e2e8f0; border-radius: 24rpx; overflow: hidden; }.menu-row { min-height: 116rpx; padding: 0 24rpx; display: flex; align-items: center; gap: 20rpx; border-bottom: 1rpx solid #e2e8f0; font-size: 29rpx; }.menu-row:last-child { border-bottom: 0; }.menu-row>text { flex: 1; }.menu-icon { color: #0f766e; font-size: 34rpx; }.menu-row.danger,.menu-row.danger .menu-icon { color: #e5484d; }.chevron { color: #94a3b8; font-size: 30rpx; }.version { display: block; margin-top: 44rpx; color: #94a3b8; font-size: 22rpx; text-align: center; }
</style>
