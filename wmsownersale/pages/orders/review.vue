<template>
<view></view>
</template>
<script setup>
import { ref, computed } from 'vue'
import { onLoad, onReachBottom } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const q = ref('')
const page = ref(1)
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const hasMore = computed(()=> !!list.value.next)
const approvingId = ref(null)

function normalize(res){
return Array.isArray(res)
? { count: res.length, next: null, previous: null, results: res }
: (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}


async function fetch(pageNo=1){
const res = await api.pendingOrders(pageNo, q.value)
if(pageNo===1){
list.value = normalize(res)
}else{
const n = normalize(res)
list.value = { ...n, results: [...list.value.results, ...n.results] }
}
}

async function refresh(){ page.value = 1; await fetch(1) }
async function loadMore(){ if(!hasMore.value) return; page.value += 1; await fetch(page.value) }

async function approve(o){
if(!o?.id) return
approvingId.value = o.id
try{
await api.ownerApprove(o.id)
uni.showToast({ title:'审核通过', icon:'none' })
list.value.results = list.value.results.filter(x=> x.id !== o.id)
}catch(e){ console.error(e) }
finally{ approvingId.value = null }
}

function goDetail(o){ if(!o?.id) return; uni.navigateTo({ url:'/pages/orders/detail?id='+o.id }) }

onLoad(()=>{ refresh() })
onReachBottom(()=>{ loadMore() })
</script>