<template>
	<view class="uni-container">
		<view class="uni-hello-text">
			<text class="hello-text">uni原生插件示例 SDK</text>
		</view>
		<view class="uni-panel" v-for="(item, index) in list" :key="item.id">
			<view class="uni-panel-h" :class="item.open ? 'uni-panel-h-on' : ''" @click="triggerCollapse(index)">
				<text class="uni-panel-text">{{item.name}}</text>
			</view>
			<view class="uni-panel-c" v-if="item.open">
				<view class="uni-navigate-item" v-for="(item2,key) in item.pages" :key="key" @click="goDetailPage(item2.url)">
					<text class="uni-navigate-text">{{item2.name ? item2.name : item2}}</text>
					<text class="uni-navigate-icon uni-icon">&#xe470;</text>
				</view>
			</view>
		</view>
	</view>
</template>
<script>
	
	export default {
		data() {
			return {
				list: [
				{
					id: 'ext-module',
					name: 'Device Scan Sdk module',
					open: false,
					url: '/pages/sample/ext-module-device-scan'
				},
				
				],
				navigateFlag: false
			}
		},
		onLoad() {
					const systemInfo = uni.getSystemInfoSync();
			       var model = systemInfo.model;//型号
					console.log('model: ' + model);
					
					var Build = plus.android.importClass("android.os.Build");
					 var serial = Build.getSerial()
					console.log('serial' + serial);
					
		},
		onShow() {
		},
		onHide() {
		},
		methods: {
			triggerCollapse(e) {
							if (!this.list[e].pages) {
								this.goDetailPage(this.list[e].url);
								return;
							}
							for (var i = 0; i < this.list.length; ++i) {
								if (e === i) {
									this.list[i].open = !this.list[e].open;
								} else {
									this.list[i].open = false;
								}
							}
						},
						goDetailPage(e) {
							if (this.navigateFlag) {
								return;
							}
							this.navigateFlag = true;
							uni.navigateTo({
								url: e
							});
							setTimeout(() => {
								this.navigateFlag = false;
							}, 200)
							return false;
						}
			
		}
	}
</script>

<style>
</style>
