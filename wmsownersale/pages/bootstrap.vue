<template>
  <view class="bootstrap-page">
    <view v-if="checking" class="status">正在验证登录状态…</view>
    <view v-else-if="offline" class="card">
      <view class="title">暂时无法连接服务器</view>
      <view class="text-gray">登录信息已保留，请检查网络后重试。</view>
      <button class="btn" @click="start">重试</button>
      <button class="btn-outline" @click="exit">退出登录</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'

const auth = useAuth()
const checking = ref(true)
const offline = ref(false)

async function start() {
  checking.value = true
  offline.value = false
  const result = await auth.bootstrap()
  checking.value = false
  if (result.status === 'authenticated') {
    uni.switchTab({ url: '/pages/home/index' })
  } else if (result.status === 'offline') {
    offline.value = true
  } else {
    uni.reLaunch({ url: '/pages/login' })
  }
}

async function exit() {
  await auth.logout()
  uni.reLaunch({ url: '/pages/login' })
}

onLoad(start)
</script>

<style>
.bootstrap-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 32rpx; }
.status, .card { width: 100%; text-align: center; }
.btn, .btn-outline { margin-top: 24rpx; }
</style>
