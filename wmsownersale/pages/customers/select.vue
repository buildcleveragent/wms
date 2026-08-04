<template>
	<view class="p-4">
		<view class="text-lg font-bold mb-2">点击选中的客户</view>
		<view class="bar">
			<input class="input" v-model="q" placeholder="客户名/编码 可输入部分内容" @confirm="search" />
			<button class="btn-outline" @click="search">搜索</button>
		</view>

		<view v-if="firstLoading" class="customer-state">正在加载客户…</view>
		<view v-else-if="error && !rows.length" class="customer-state error-state">
			<view>{{ error }}</view>
			<button class="btn-outline" @click="search">重试</button>
		</view>
		<view v-else-if="!rows.length" class="customer-state">没有符合条件的客户</view>

		<view v-for="(c,i) in rows" :key="c?.id ?? i" class="card" @click="choose(c)">
			<view class="row">
				<view class="font-bold">{{ c?.name }}</view>
				<view class="badge">ID: {{ c?.id }}</view>
			</view>
			<view class="text-gray">{{ c?.code }}</view>
		</view>
		<view v-if="loadingMore" class="customer-state">正在加载更多…</view>
		<view v-else-if="error && rows.length" class="customer-state error-state">
			<view>{{ error }}</view>
			<button class="btn-outline" @click="loadMore">重试加载更多</button>
		</view>
		<view v-else-if="rows.length && !list.next" class="customer-state">已加载全部客户</view>
	</view>
</template>

<script setup>
import { ref, computed } from 'vue'
// 👇 一定要把 onUnload 引进来（需要的话也可加 onHide）
import { onLoad, onReachBottom, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'       // 注意是 request（单数）
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'

const q = ref('')
const page = ref(1)
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const cart = useCart()
const auth = useAuth()
const firstLoading = ref(false)
const loadingMore = ref(false)
const error = ref('')

// ---- 存活守卫：避免离开页面后回写 UI ----
let alive = true
let reqSeq = 0
onUnload(() => { alive = false; reqSeq++ })   // 页面销毁：让未归来的请求结果作废

function normalize(res){
  return Array.isArray(res)
    ? { count: res.length, next:null, previous:null, results: res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}

async function fetch(pageNo = 1, { reset = false } = {}){
  if (!reset && (firstLoading.value || loadingMore.value || !list.value.next)) return
  const tag = reset ? ++reqSeq : reqSeq
  const searchText = q.value || ''
  if (reset) {
    firstLoading.value = true
    error.value = ''
    list.value = { count:0, next:null, previous:null, results:[] }
  } else {
    loadingMore.value = true
    error.value = ''
  }
  try{
    // 后端已按业务员固定货主过滤，无需传 owner_id
    const res = await api.customers(searchText, pageNo)
    if (!alive || tag !== reqSeq) return   // 页面已销毁或有更新版请求 → 丢弃结果
    const n = normalize(res)
    if (pageNo === 1) list.value = n
    else {
      list.value = {
        ...n,
        results: Array.from(new Map([
          ...(list.value.results || []),
          ...n.results,
        ].map(item => [String(item.id), item])).values()),
      }
    }
    page.value = pageNo
  }catch(e){
    if (alive && tag === reqSeq) error.value = e?.message || '客户加载失败，请稍后重试'
  }finally{
    if (alive && tag === reqSeq) {
      firstLoading.value = false
      loadingMore.value = false
    }
  }
}

async function search(){ await fetch(1, { reset: true }) }
async function loadMore(){ await fetch(page.value + 1) }

// 选中即跳到选品；跳转前标记页面无效，阻止后续回写
function choose(c){
  if (!c || !c.id) return
  const selected = cart.setCustomer({ id: c.id, code: c.code, name: c.name })
  if (!selected) {
    uni.showToast({ title: '客户数据不完整，请刷新重试', icon: 'none' })
    return
  }
  alive = false; reqSeq++
  // 用 redirectTo 可减少历史栈干扰
  uni.redirectTo({ url: '/pages/products/search' })
}

onLoad(() => {
  auth.ensureAuth()
  if (!cart.hasContextForUser(auth.user?.id, auth.user?.owner_id)) {
    cart.resetOrder()
    uni.redirectTo({ url: '/pages/warehouses/select' })
    return
  }
  search()
})
onReachBottom(() => { loadMore() })
</script>

<style scoped>
.customer-state { padding: 36rpx 12rpx; color: #6b7280; text-align: center; }
.error-state { color: #b42318; }
.error-state button { width: auto; margin-top: 18rpx; }
</style>
