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
		<view v-if="quickItems.length" class="quick-grid">
			<button
				v-for="item in quickItems"
				:key="item.key"
				:class="item.key === 'order_create' ? 'btn' : 'btn-outline'"
				@click="openItem(item)"
			>{{ item.text }}</button>
		</view>
		<view v-else class="empty-access">当前账号没有货主端功能权限</view>
		</view>
	</view>
</template>

<script setup>
	import { computed } from 'vue'
	import { downloadAuthenticatedFile } from '@/utils/request.js'
	import { useAuth } from '@/store/auth'
	import { buildOwnerMenu } from '@/utils/ownerAccess'

	const auth = useAuth()
	auth.ensureAuth()
	
	const menu = computed(() => buildOwnerMenu({
		roles: auth.roles,
		capabilities: auth.capabilities,
	}))
	const quickItems = computed(() => [
		...menu.value.orders,
		...menu.value.reports,
		...menu.value.administration,
	])

	function navigationFailed() {
		uni.showToast({ title: '页面暂时无法打开，请稍后重试', icon: 'none' })
	}

	function openItem(item) {
		if (item.action === 'download_template') return downloadDropShipTemplate()
		const options = { url: item.path, fail: navigationFailed }
		if (item.navigation === 'tab') return uni.switchTab(options)
		return uni.navigateTo(options)
	}
	
	async function downloadDropShipTemplate() {
	  try {
	    await downloadAuthenticatedFile(
	      '/api/outbound/orders/import-drop-ship-template/',
	      '一件代发导入模板.xlsx',
	    )
	  } catch (error) {
	    uni.showToast({ title: error?.message || '模板下载失败', icon: 'none' })
	  }
	}
</script>

<style scoped>
.quick-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16rpx; }
.quick-grid button { width: 100%; margin: 0; }
.empty-access { padding: 32rpx 0; color: #6b7280; text-align: center; }
</style>
