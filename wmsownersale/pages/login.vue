<template>
  <view class="p-4">
    <view class="text-lg font-bold mb-3">登录</view>

    <input class="input" v-model="username" :disabled="submitting" placeholder="用户名" />

    <view class="password-box">
      <input
        class="input pr-60"
        v-model="password"
        :password="!showPassword"
        :disabled="submitting"
        placeholder="密码"
      />
      <text class="eye-icon" @click="togglePassword">
        {{ showPassword ? '🚫' : '👁️' }}
      </text>
    </view>

    <view v-if="errorMessage" class="login-error" role="alert">{{ errorMessage }}</view>
    <button class="btn" :disabled="submitting" @click="doLogin">
      {{ submitting ? '登录中…' : '登录' }}
    </button>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { useAuth } from '@/store/auth'

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const errorMessage = ref('')

const auth = useAuth()

function togglePassword() {
  showPassword.value = !showPassword.value
}

async function doLogin() {
  if (submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    await auth.login(username.value, password.value)
    uni.showToast({ title: '登录成功', icon: 'none' })
    uni.switchTab({ url: '/pages/home/index' })
  } catch (e) {
    const retryAfter = Number(e?.data?.retry_after || 0)
    errorMessage.value = e?.statusCode === 429
      ? `登录尝试过于频繁，请${Math.max(1, retryAfter)}秒后重试`
      : (e?.message || '登录失败')
    uni.showToast({ title: errorMessage.value, icon: 'none' })
  } finally {
    submitting.value = false
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

.login-error {
  margin: 20rpx 0;
  color: #b42318;
  font-size: 26rpx;
}
</style>
