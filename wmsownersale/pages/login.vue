<template>
  <view class="p-4">
    <view class="text-lg font-bold mb-3">登录</view>

    <input class="input" v-model="username" placeholder="用户名" />

    <view class="password-box">
      <input
        class="input pr-60"
        v-model="password"
        :password="!showPassword"
        placeholder="密码"
      />
      <text class="eye-icon" @click="togglePassword">
        {{ showPassword ? '🚫' : '👁️' }}
      </text>
    </view>

    <button class="btn" @click="doLogin">登录</button>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { useAuth } from '@/store/auth'

const username = ref('ay1')
const password = ref('qwezxc123')
const showPassword = ref(false)

const auth = useAuth()

function togglePassword() {
  showPassword.value = !showPassword.value
}

async function doLogin() {
  try {
    await auth.login(username.value, password.value)
    uni.showToast({ title: '登录成功', icon: 'none' })
    uni.switchTab({ url: '/pages/home/index' })
  } catch (e) {
    console.error(e)
    uni.showToast({ title: '登录失败', icon: 'none' })
  }
}
</script>
<style>
.password-box {
  position: relative;
}

.pr-60 {
  padding-right: 60rpx;
}

.eye-icon {
  position: absolute;
  right: 20rpx;
  top: 50%;
  transform: translateY(-50%);
  font-size: 32rpx;
}
</style>
