<template>
	<view class="p-4">
<!-- 		<view class="text-lg font-bold mb-2">首页</view> -->
		
<!-- 		<view class="card">
			<view class="row">
				<view>今日下单</view> <view class="badge">--</view></view>
			<view class="row"><view>待审核</view><view class="badge">--</view></view>
			<view class="row"><view>本月金额</view><view class="badge">--</view></view>
		</view> -->
	
		<view class="card">
<!-- 		<view class="font-bold" style="margin-bottom:8rpx">快捷入口</view> -->
		
		<view class="row" style="gap:16rpx">
			<button class="btn" @click="goto('/pages/customers/select')">开单</button>
			<button class="btn-outline" @click="goto('/pages/approval/index')">审核</button>
			<button class="btn-outline" @click="goto('/pages/orders/index')">查询</button>
			<button class="btn-outline" @click="goto('/pages/orders/import_drop_ship')">批量导入一件代发</button>			
			<button class="btn-outline" @click="downloadDropShipTemplate()">下载一件代发模板</button>					
		</view>
		

		</view>
	</view>
</template>

<script setup>
	import { BASE_URL } from '@/utils/request.js'
	
	function goto(url){ uni.navigateTo({ url }) }
	
	function downloadDropShipTemplate() {
	  const url = `${BASE_URL}/api/outbound/orders/import-drop-ship-template/`
	
	  if (typeof window !== 'undefined') {
	    window.open(url, '_blank')
	    return
	  }
	
	  // #ifdef APP-PLUS
	  plus.runtime.openURL(url)
	  // #endif
	}
</script>