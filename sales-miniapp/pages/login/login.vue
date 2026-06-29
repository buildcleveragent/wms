<template>
  <view class="page login-page">
    <view class="brand">
      <view class="brand-mark">悦</view>
      <view>
        <view class="title">博悦商城</view>
        <view class="subtle">多商家商品选购</view>
      </view>
    </view>

    <view class="panel form">
      <view class="field">
        <text class="label">服务地址</text>
        <input class="input" v-model="serverUrl" placeholder="http://192.168.1.6:8001" />
      </view>
      <view class="field">
        <text class="label">账号</text>
        <input class="input" v-model="username" placeholder="请输入账号" />
      </view>
      <view class="field">
        <text class="label">密码</text>
        <input class="input" v-model="password" password placeholder="请输入密码" />
      </view>
      <button class="button wechat" :loading="wechatLoading" @click="doWechatLogin">微信登录</button>
      <button class="button secondary" :loading="loading" @click="doLogin">账号登录</button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { getBaseUrl } from '../../utils/request'
import { useSessionStore } from '../../stores/session'

const session = useSessionStore()
const serverUrl = ref(getBaseUrl())
const username = ref('')
const password = ref('')
const loading = ref(false)
const wechatLoading = ref(false)

async function doLogin() {
  if (loading.value) return
  if (!username.value || !password.value) {
    uni.showToast({ title: '请输入账号和密码', icon: 'none' })
    return
  }
  loading.value = true
  try {
    await session.login(username.value, password.value, serverUrl.value)
    uni.switchTab({ url: '/pages/index/index' })
  } catch (err) {
    uni.showToast({ title: err.message || '登录失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function uniLogin() {
  return new Promise((resolve, reject) => {
    uni.login({
      provider: 'weixin',
      success: resolve,
      fail: reject,
    })
  })
}

async function doWechatLogin() {
  if (wechatLoading.value) return
  wechatLoading.value = true
  try {
    const loginRes = await uniLogin()
    if (!loginRes.code) {
      throw new Error('微信登录未返回 code')
    }
    await session.wechatLogin(loginRes.code, serverUrl.value)
    uni.switchTab({ url: '/pages/index/index' })
  } catch (err) {
    uni.showToast({ title: err.message || '微信登录失败', icon: 'none' })
  } finally {
    wechatLoading.value = false
  }
}
</script>

<style scoped>
.login-page {
  padding-top: 96rpx;
}

.brand {
  display: flex;
  align-items: center;
  gap: 20rpx;
  margin-bottom: 36rpx;
}

.brand-mark {
  width: 88rpx;
  height: 88rpx;
  border-radius: 8rpx;
  background: #0f766e;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40rpx;
  font-weight: 800;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.wechat {
  background: #0f766e;
}

.secondary {
  background: #334155;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
}

.label {
  color: #334155;
  font-size: 24rpx;
}
</style>
