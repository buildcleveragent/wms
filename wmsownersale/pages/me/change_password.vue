<template>
  <view class="page-wrap">
    <view class="title">修改密码</view>

    <view class="card form-card">
      <view class="field">
        <view class="label">原密码</view>
        <view class="password-box">
          <input
            class="input password-input"
            v-model="oldPassword"
            :password="!showOldPassword"
            placeholder="请输入原密码"
          />
          <text class="toggle" @click="showOldPassword = !showOldPassword">
            {{ showOldPassword ? '隐藏' : '显示' }}
          </text>
        </view>
      </view>

      <view class="field">
        <view class="label">新密码</view>
        <view class="password-box">
          <input
            class="input password-input"
            v-model="newPassword1"
            :password="!showNewPassword"
            placeholder="请输入新密码"
          />
          <text class="toggle" @click="showNewPassword = !showNewPassword">
            {{ showNewPassword ? '隐藏' : '显示' }}
          </text>
        </view>
      </view>

      <view class="field">
        <view class="label">确认新密码</view>
        <view class="password-box">
          <input
            class="input password-input"
            v-model="newPassword2"
            :password="!showConfirmPassword"
            placeholder="请再次输入新密码"
          />
          <text class="toggle" @click="showConfirmPassword = !showConfirmPassword">
            {{ showConfirmPassword ? '隐藏' : '显示' }}
          </text>
        </view>
      </view>

      <button class="btn submit-btn" :disabled="submitting" @click="submit">
        {{ submitting ? '保存中...' : '保存新密码' }}
      </button>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '@/utils/request'

const oldPassword = ref('')
const newPassword1 = ref('')
const newPassword2 = ref('')
const showOldPassword = ref(false)
const showNewPassword = ref(false)
const showConfirmPassword = ref(false)
const submitting = ref(false)

function showMessage(title) {
  uni.showToast({ title, icon: 'none' })
}

async function submit() {
  if (submitting.value) return

  if (!oldPassword.value) {
    showMessage('请输入原密码')
    return
  }
  if (!newPassword1.value) {
    showMessage('请输入新密码')
    return
  }
  if (newPassword1.value !== newPassword2.value) {
    showMessage('两次输入的新密码不一致')
    return
  }

  submitting.value = true
  try {
    await api.changePassword(oldPassword.value, newPassword1.value, newPassword2.value)
    uni.showToast({ title: '密码修改成功', icon: 'success' })
    oldPassword.value = ''
    newPassword1.value = ''
    newPassword2.value = ''
    setTimeout(() => {
      uni.navigateBack()
    }, 800)
  } catch (e) {
    // request.js 已统一展示后端错误信息。
  } finally {
    submitting.value = false
  }
}
</script>

<style>
.page-wrap {
  padding: 24rpx;
}
.form-card {
  padding: 24rpx;
}
.field {
  margin-bottom: 22rpx;
}
.label {
  color: #374151;
  font-size: 28rpx;
  font-weight: 600;
  margin-bottom: 8rpx;
}
.password-box {
  position: relative;
}
.password-input {
  margin: 0;
  padding-right: 100rpx;
}
.toggle {
  position: absolute;
  right: 18rpx;
  top: 50%;
  transform: translateY(-50%);
  color: #2563eb;
  font-size: 26rpx;
}
.submit-btn {
  margin-top: 10rpx;
  width: 100%;
}
</style>
