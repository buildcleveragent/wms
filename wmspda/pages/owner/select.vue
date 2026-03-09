<template>
	<view class="p-4">
		<view class="text-lg font-bold mb-2">点击选中的货主</view>
		<view class="bar">
			<input class="input" v-model="q" placeholder="货主名/编码 可输入部分内容" @confirm="search" />
			<button class="btn-outline" @click="search">搜索</button>
		</view>

		<view v-for="(c,i) in rows" :key="c?.id ?? i" class="card" @click="choose(c)">
			<view class="row">
				<view class="font-bold">{{ c?.name }}</view>
				<view class="badge">ID: {{ c?.id }}</view>
			</view>
			<view class="text-gray">{{ c?.code }}</view>
		</view>


		<view class="mt-6">
			<button class="btn" :disabled="!cart.customer" >下一步：选品</button>
		</view>
	</view>
</template>

<script setup>
import { ref, computed } from 'vue'
// 👇 一定要把 onUnload 引进来（需要的话也可加 onHide）
import { onLoad, onReachBottom, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'       // 注意是 request（单数）
import { useCart } from '@/store/cart'

const q = ref('')
const page = ref(1)
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const cart = useCart()

// ---- 存活守卫：避免离开页面后回写 UI ----
let alive = true
let reqSeq = 0
onUnload(() => { alive = false; reqSeq++ })   // 页面销毁：让未归来的请求结果作废

function normalize(res){
  return Array.isArray(res)
    ? { count: res.length, next:null, previous:null, results: res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}

async function fetch(pageNo = 1){
  const tag = ++reqSeq
  try{
    // 后端已按业务员固定货主过滤，无需传 owner_id
    const res = await api.customers(q.value || '', pageNo)
    if (!alive || tag !== reqSeq) return   // 页面已销毁或有更新版请求 → 丢弃结果
    const n = normalize(res)
    if (pageNo === 1) list.value = n
    else list.value = { ...n, results: [ ...(list.value.results || []), ...n.results ] }
  }catch(e){
    // 页面销毁后返回的错误直接忽略
  }
}

async function search(){ page.value = 1; await fetch(1) }
async function loadMore(){ if (!list.value.next) return; page.value += 1; await fetch(page.value) }

// 选中即跳到选品；跳转前标记页面无效，阻止后续回写
function choose(c){
  if (!c || !c.id) return
  cart.setCustomer({ id: c.id, name: c.name })
  alive = false; reqSeq++
  // 用 redirectTo 可减少历史栈干扰
  uni.redirectTo({ url: '/pages/products/search' })
}

onLoad(() => { search() })
onReachBottom(() => { loadMore() })
</script>
