<template>
  <view class="page login-page">
    <view class="login-card">
 <!--     <view class="app-tag">仓储经营分析中心</view> -->
      <view class="app-title">仓储经营分析中心</view>
      <view class="base-url">接口地址：{{ baseUrl }}</view>
   <!--   <view class="app-desc">登录后先看首页总览，再进入库存与库容、收入与计费、预警中心继续追问。</view> -->

      <input
        v-model="username"
        class="field-input"
        placeholder="用户名"
        confirm-type="next"
      />

      <view class="password-box">
        <input
          v-model="password"
          class="field-input password-input"
          :password="!showPassword"
          placeholder="密码"
          confirm-type="done"
          @confirm="doLogin"
        />
        <text class="toggle-btn" @click="togglePassword">
          {{ showPassword ? '隐藏' : '显示' }}
        </text>
      </view>

      <button class="btn-primary submit-btn" :disabled="submitting" @click="doLogin">
        {{ submitting ? '登录中...' : '登录' }}
      </button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { BASE_URL } from '@/utils/request'

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const baseUrl = BASE_URL

const auth = useAuth()

function enterDashboard() {
  uni.reLaunch({
    url: '/pages/home/index',
  })
}

function togglePassword() {
  showPassword.value = !showPassword.value
}

async function doLogin() {
  if (!username.value || !password.value) {
    uni.showToast({
      title: '请输入用户名和密码',
      icon: 'none',
    })
    return
  }

  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    uni.showToast({
      title: '登录成功',
      icon: 'none',
    })
    enterDashboard()
  } catch (error) {
    console.error('boss billing login failed:', error)
  } finally {
    submitting.value = false
  }
}

onLoad(() => {
  auth.restore()
})
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32rpx;
}

.login-card {
  width: 100%;
  max-width: 720rpx;
  background:
    radial-gradient(circle at top right, rgba(11, 95, 255, 0.14), transparent 38%),
    linear-gradient(135deg, #ffffff 0%, #eef5ff 55%, #fff8ef 100%);
  border-radius: 28rpx;
  padding: 36rpx 30rpx;
  box-shadow: 0 16rpx 50rpx rgba(18, 36, 84, 0.08);
}

.app-tag {
  display: inline-flex;
  align-items: center;
  padding: 10rpx 18rpx;
  border-radius: 999rpx;
  background: #fff;
  color: #0b5fff;
  font-size: 22rpx;
  font-weight: 700;
}

.app-title {
  margin-top: 18rpx;
  font-size: 46rpx;
  font-weight: 700;
  color: #162034;
    text-align: center;
}

.app-desc {
  margin-top: 14rpx;
  font-size: 26rpx;
  line-height: 1.6;
  color: #66748d;
}

.base-url {
  margin-top: 12rpx;
  font-size: 22rpx;
  line-height: 1.6;
  color: #7a879b;
  text-align: center;
  word-break: break-all;
}

.field-input {
  width: 100%;
  min-height: 92rpx;
  margin-top: 22rpx;
  padding: 0 24rpx;
  border-radius: 18rpx;
  background: rgba(255, 255, 255, 0.96);
  border: 1rpx solid #d9e3f2;
  font-size: 28rpx;
}

.password-box {
  position: relative;
}

.password-input {
  padding-right: 110rpx;
}

.toggle-btn {
  position: absolute;
  top: 50%;
  right: 28rpx;
  transform: translateY(-50%);
  color: #0b5fff;
  font-size: 24rpx;
  font-weight: 600;
}

.submit-btn {
  margin-top: 28rpx;
}
</style>
