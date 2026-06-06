<template>
<view class="p-4">
<view class="title">{{ labels.title }}</view>
<view class="seg">
<view :class="['seg-item', domain==='purchase' && 'seg-item-active']" @click="domain='purchase'">{{ labels.purchase }}</view>
<view :class="['seg-item', domain==='sales' && 'seg-item-active']" @click="domain='sales'">{{ labels.sales }}</view>
</view>

<view class="grid">
<view v-for="m in visibleMenus" :key="m.key" class="cell" @click="go(m)">
<image class="icon" :src="m.icon" mode="aspectFit" />
<view class="text">{{ m.text }}</view>
<view v-if="m.badge && badges[m.badge] > 0" class="badge">{{ badges[m.badge] }}</view>
<view v-if="m.disabled" class="mask">{{ labels.comingSoon }}</view>
</view>
</view>
</view>
</template>
<script setup>
import { ref, computed } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const labels = {
  title:'\u5de5\u4f5c\u53f0',
  purchase:'\u5165\u5e93\u8ba2\u5355',
  sales:'\u9500\u552e\u8ba2\u5355',
  create:'\u5f00\u5355',
  review:'\u5ba1\u6838',
  query:'\u67e5\u8be2',
  comingSoon:'\u656c\u8bf7\u671f\u5f85',
  inDevelopment:'\u529f\u80fd\u5f00\u53d1\u4e2d',
}

const domain = ref('sales')
const MENUS = [
{ key:'po_create', dom:'purchase', text:labels.create, icon:'/static/icons/create.png', to:'/pages/purchase/create', perm:'po_create', disabled:true },
{ key:'po_review', dom:'purchase', text:labels.review, icon:'/static/icons/review.png', to:'/pages/purchase/review', perm:'po_review', badge:'poPending', disabled:true },
{ key:'po_query', dom:'purchase', text:labels.query, icon:'/static/icons/query.png', to:'/pages/purchase/search', perm:'po_query', disabled:true },
{ key:'so_create', dom:'sales', text:labels.create, icon:'/static/icons/create.png', to:'/pages/customers/select', perm:'so_create' },
{ key:'so_review', dom:'sales', text:labels.review, icon:'/static/icons/review.png', to:'/pages/approval/index', perm:'so_review', badge:'soPending' },
{ key:'so_query', dom:'sales', text:labels.query, icon:'/static/icons/query.png', to:'/pages/orders/index', perm:'so_query' },
]
const perms = ref(['so_create','so_review','so_query'])
const visibleMenus = computed(()=> MENUS.filter(m => m.dom===domain.value && perms.value.includes(m.perm)))
const badges = ref({ soPending: 0, poPending: 0 })

async function loadBadges(){
try{
const res = await api.pendingOrders(1)
const arr = Array.isArray(res?.results) ? res.results : []
badges.value.soPending = arr.length
}catch(e){}
}

function go(m){
if(m.disabled) return uni.showToast({ title:labels.inDevelopment, icon:'none' })
uni.navigateTo({ url: m.to })
}

onShow(()=> loadBadges())
</script>
