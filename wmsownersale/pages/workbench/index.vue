<template>
<view class="p-4">
<view class="title">工作台</view>
<view class="seg">
<view :class="['seg-item', domain==='purchase' && 'active']" @click="domain='purchase'">入库订单</view>
<view :class="['seg-item', domain==='sales' && 'active']" @click="domain='sales'">销售订单</view>
</view>


<view class="grid">
<view v-for="m in visibleMenus" :key="m.key" class="cell" @click="go(m)">
<image class="icon" :src="m.icon" mode="aspectFit" />
<view class="text">{{ m.text }}</view>
<view v-if="m.badge && badges[m.badge] > 0" class="badge">{{ badges[m.badge] }}</view>
<view v-if="m.disabled" class="mask">敬请期待</view>
</view>
</view>
</view>
</template>
<script setup>
import { ref, computed } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { api } from '@/utils/request'


const domain = ref('sales')
const MENUS = [
{ key:'po_create', dom:'purchase', text:'开单', icon:'/static/icons/create.png', to:'/pages/purchase/create', perm:'po_create' , disabled:true },
{ key:'po_review', dom:'purchase', text:'审核', icon:'/static/icons/review.png', to:'/pages/purchase/review', perm:'po_review' , badge:'poPending', disabled:true },
{ key:'po_query', dom:'purchase', text:'查询', icon:'/static/icons/query.png', to:'/pages/purchase/search', perm:'po_query' , disabled:true },
{ key:'so_create', dom:'sales', text:'开单', icon:'/static/icons/create.png', to:'/pages/customers/select', perm:'so_create' },
{ key:'so_review', dom:'sales', text:'审核', icon:'/static/icons/review.png', to:'/pages/approval/index', perm:'so_review' , badge:'soPending' },
{ key:'so_query', dom:'sales', text:'查询', icon:'/pages/orders/index', perm:'so_query' },
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
function go(m){ if(m.disabled) return uni.showToast({ title:'功能开发中', icon:'none' }); uni.navigateTo({ url: m.to }) }


onShow(()=> loadBadges())
</script>