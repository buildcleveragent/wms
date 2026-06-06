<template>
<view class="app">
<slot />
</view>
</template>
<script setup>
</script>
<style>
.app{flex:1;width:100%;background-color:#f6f7fb;color:#111}
.btn{padding:10rpx 20rpx;background-color:#3b82f6;color:#fff;border-radius:8rpx}
.btn-disabled{opacity:.5}
.btn-outline{padding:5rpx 20rpx;border:1rpx solid #cbd5e1;border-radius:8rpx;color:#111;background-color:#fff}
.input{flex:1;border:1rpx solid #e5e7eb;border-radius:8rpx;padding:16rpx;margin-top:16rpx;margin-bottom:16rpx;background-color:#fff}
.card{background-color:#fff;border:1rpx solid #eee;border-radius:12rpx;padding:18rpx;margin-top:12rpx;margin-bottom:12rpx}
.text-gray{color:#6b7280}
.bar{display:flex;flex-direction:row;align-items:center;justify-content:flex-start}
.row{display:flex;flex-direction:row;justify-content:space-between;align-items:center}
.badge{background-color:#eef2ff;color:#3730a3;border-radius:9999rpx;padding:6rpx 14rpx;font-size:24rpx}
.table{width:100%}
.table-cell{border-bottom:1rpx solid #e5e7eb;padding:12rpx}
.title{font-weight:700;font-size:36rpx;margin-top:16rpx;margin-bottom:24rpx}
.seg{display:flex;flex-direction:row;background-color:#f3f4f6;border-radius:12rpx;overflow:hidden}
.seg-item{flex:1;text-align:center;padding:20rpx 0;color:#6b7280}
.seg-item-active{background-color:#fff;color:#111;font-weight:600}
.cell{position:relative;display:flex;flex-direction:column;align-items:center;background-color:#fff;border:1rpx solid #e5e7eb;border-radius:16rpx;padding:24rpx 0}
.icon{width:92rpx;height:92rpx;border-radius:20rpx}
.icon-box{display:flex;align-items:center;justify-content:center;font-size:44rpx;color:#fff}
.icon-blue{background-color:#26A4FF}
.icon-orange{background-color:#FF8A65}
.icon-green{background-color:#2ECC71}
.icon-gray{background-color:#BDBDBD}
.mask{position:absolute;top:0;right:0;bottom:0;left:0;background-color:rgba(255,255,255,.7);color:#6b7280;font-size:24rpx;display:flex;align-items:center;justify-content:center;border-radius:16rpx}
.page{width:100%;padding:16rpx}
.section{margin-top:28rpx;background-color:#fff;border-radius:16rpx;padding:18rpx;box-shadow:0 4rpx 16rpx rgba(0,0,0,.03)}
.section-title{font-weight:700;font-size:32rpx;margin-bottom:12rpx}
.grid{display:flex;flex-direction:row;flex-wrap:wrap;margin-left:-12rpx;margin-right:-12rpx}
.tile{width:25%;padding-left:12rpx;padding-right:12rpx;margin-bottom:16rpx}
.tile-inner{background-color:#fff;border:1rpx solid #eee;border-radius:20rpx;padding:18rpx 0;display:flex;flex-direction:column;align-items:center}
.tile-text{margin-top:12rpx;font-size:26rpx;color:#333}
.sub{color:#666;font-size:24rpx}
</style>
