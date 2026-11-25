<template>
  <view class="p-4">
    <view class="title">当前账号无操作权限</view>
    <view class="card">货主财务可以查看本货主全部业务和导出数据，不能下单、审批或修改业务数据。</view>
    <button class="btn" @click="goHome">返回首页查看数据</button>
  </view>
</template>
<script setup>
function goHome() { uni.switchTab({url:'/pages/home/index'}) }
</script>
