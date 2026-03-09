<template>
<view class="p-4">
<view class="text-lg font-bold mb-2">我的</view>
<view class="card">
<view class="row"><view>账号</view><view>{{ username || '未登录' }}</view></view>
</view>
<view class="row" style="margin-top:16rpx">
<button class="btn" @click="logout">退出登录</button>
</view>
</view>
</template>
<script setup>
import { computed } from 'vue'
import { useAuth } from '@/store/auth'


const auth = useAuth()
const username = computed(()=> auth.user?.username )
function logout(){ auth.logout(); uni.reLaunch({ url:'/pages/login' }) }
</script>