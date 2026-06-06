<template>
	<view class="p-4">
		<view class="text-lg font-bold mb-3">登录</view>
		
		<input class="input" v-model="username" placeholder="用户名" />
		<input class="input" v-model="password" password placeholder="密码" />
		<button class="btn" @click="doLogin">登录</button>
	</view>
</template>

<script setup>
	import { ref } from 'vue'
	import { useAuth } from '@/store/auth'

	const username = ref('cz1')
	const password = ref('qwezxc123')
	const auth = useAuth()

	async function doLogin(){
	try{
		await auth.login(username.value, password.value)
			uni.showToast({ title:'登录成功', icon:'none' })
			uni.switchTab({ url:'/pages/index/index' })
			// uni.navigateTo({
			//   url: '/pages/home/index'  // 替换为你需要跳转的页面路径
			// });
	}catch(e){ 
		console.error(e) }
		uni.showToast({ title:'未能登录：检查用户名，密码，网络连通性', icon:'none' })
	}
</script>