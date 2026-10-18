<template>
  <view class="login-page">
    <view class="brand-mark"><WmsIcon name="scan" /></view>
    <text class="brand-title">金桥融通仓库手机版</text>
    <text class="brand-sub">现场作业与手机收银</text>

    <view class="login-form">
      <text class="field-label">账号</text>
      <input v-model.trim="username" class="login-input" placeholder="请输入用户名" confirm-type="next" />
      <text class="field-label password-label">密码</text>
      <view class="password-field">
        <input v-model="password" class="login-input password-input" :password="!showPassword" placeholder="请输入密码" confirm-type="done" @confirm="doLogin" />
        <text class="password-toggle" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</text>
      </view>
      <button class="login-button" :disabled="loading || !username || !password" @click="doLogin">
        {{ loading ? '正在登录…' : '登录' }}
      </button>
      <text class="network-note">应用仅连接仓库局域网，请确认手机已接入仓库 Wi-Fi。</text>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'
import { getToken } from '@/utils/request'

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const loading = ref(false)
const auth = useAuth()

async function doLogin() {
  if (!username.value || !password.value || loading.value) return
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    uni.showToast({ title: '登录成功', icon: 'success' })
    uni.switchTab({ url: '/pages/index/index' })
  } catch (error) {
    uni.showToast({ title: error?.message || '用户名、密码或网络不正确', icon: 'none' })
  } finally {
    loading.value = false
  }
}

onShow(() => {
  // 请求层清除过期 token 后，Pinia 仍可能保留上一次的登录资料。
  auth.access = getToken()
  if (!auth.access) {
    auth.clearProfile()
    return
  }
  uni.switchTab({ url: '/pages/index/index' })
})
</script>

<style scoped>
.login-page { min-height: 100vh; padding: 150rpx 48rpx 60rpx; background: #fff; display: flex; flex-direction: column; align-items: center; }
.brand-mark { width: 112rpx; height: 112rpx; border-radius: 28rpx; background: #1677ff; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 66rpx; box-shadow: 0 20rpx 50rpx rgba(22,119,255,.2); }
.brand-title { margin-top: 34rpx; font-size: 42rpx; font-weight: 780; }.brand-sub { margin-top: 8rpx; color: #64748b; font-size: 25rpx; }
.login-form { width: 100%; margin-top: 76rpx; }.field-label { display: block; margin: 0 0 12rpx 4rpx; color: #334155; font-size: 25rpx; font-weight: 650; }.password-label { margin-top: 28rpx; }
.login-input { width: 100%; height: 96rpx; padding: 0 26rpx; border: 1rpx solid #cbd5e1; border-radius: 18rpx; background: #fff; font-size: 29rpx; }.password-field { position: relative; }.password-input { padding-right: 110rpx; }.password-toggle { position: absolute; right: 26rpx; top: 30rpx; color: #1677ff; font-size: 25rpx; }
.login-button { width: 100%; height: 96rpx; margin-top: 42rpx; border-radius: 18rpx; background: #0f766e; color: #fff; font-size: 31rpx; font-weight: 720; display: flex; align-items: center; justify-content: center; }.network-note { display: block; margin-top: 26rpx; color: #94a3b8; font-size: 22rpx; line-height: 1.55; text-align: center; }
</style>
