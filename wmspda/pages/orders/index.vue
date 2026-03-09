<template>
<view class="p-4">
<view class="text-lg font-bold mb-2">我的订单</view>
<input class="input" v-model="q" placeholder="单号/客户" @confirm="search"/>
<button class="btn-outline" @click="search">搜索</button>


<view v-for="(o,i) in rows" :key="o?.id ?? i" class="card" @click="goDetail(o)">
<view class="row"><view class="font-bold">{{ o?.order_no || ('订单#'+o?.id) }}</view><view class="badge">¥ {{ o?.total_amount ?? 0 }}</view></view>
<view class="text-gray">状态：{{ o?.submit_status_name || o?.submit_status }}</view>
</view>
</view>
</template>
<script setup>
import { ref, computed } from 'vue'
import { api } from '@/utils/request'


const q = ref('')
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])


async function search(){
const res = await api.orders(q.value)
list.value = Array.isArray(res) ? { count: res.length, next:null, previous:null, results: res } : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}
function goDetail(o){ if(!o?.id) return; uni.navigateTo({ url:'/pages/orders/detail?id='+o.id }) }


search()
</script>