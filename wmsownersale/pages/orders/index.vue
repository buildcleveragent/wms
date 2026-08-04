<template>
<view class="p-4">
<view class="text-lg font-bold mb-2">我的订单</view>
<input class="input" v-model="q" placeholder="单号/客户" @confirm="search"/>
<button class="btn-outline" @click="search">搜索</button>


<view v-for="(o,i) in rows" :key="o?.id ?? i" class="card" @click="goDetail(o)">
<view class="row"><view class="font-bold">{{ o?.order_no || ('订单#'+o?.id) }}</view><view class="badge">¥ {{ o?.total_amount ?? 0 }}</view></view>
<view class="text-gray">状态：{{ o?.submit_status_name || o?.submit_status }}</view>
</view>
<view v-if="loading" class="text-gray">加载中…</view>
<view v-else-if="!list.next && rows.length" class="text-gray">已加载全部订单</view>
</view>
</template>
<script setup>
import { ref, computed } from 'vue'
import { onLoad, onReachBottom } from '@dcloudio/uni-app'
import { api } from '@/utils/request'


const q = ref('')
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const loading = ref(false)
const currentPage = ref(0)
let generation = 0

async function loadOrders({ reset = false } = {}){
if (loading.value) return
if (!reset && currentPage.value > 0 && !list.value.next) return
const requestGeneration = reset ? ++generation : generation
const page = reset ? 1 : currentPage.value + 1
if (reset) { list.value = { count:0, next:null, previous:null, results:[] }; currentPage.value = 0 }
loading.value = true
try {
  const res = await api.orders(q.value, page)
  if (requestGeneration !== generation) return
  const normalized = Array.isArray(res) ? { count:res.length, next:null, previous:null, results:res } : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
  const merged = reset ? normalized.results : [...list.value.results, ...normalized.results]
  list.value = { ...normalized, results:Array.from(new Map(merged.map(row => [String(row.id), row])).values()) }
  currentPage.value = page
} finally {
  if (requestGeneration === generation) loading.value = false
}
}
function search(){ return loadOrders({ reset:true }) }
function goDetail(o){ if(!o?.id) return; uni.navigateTo({ url:'/pages/orders/detail?id='+o.id }) }

onLoad(search)
onReachBottom(() => loadOrders())
</script>
