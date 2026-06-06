<template>
<view class="p-4">
	<view class="text-lg font-bold mb-2">订单详情</view>
	<view class="card" v-if="order">
	<view class="row"><view class="font-bold">{{ order.order_no }}</view><view class="badge">¥ {{ order.total_amount }}</view></view>
	<view class="text-gray">状态：{{ order.submit_status_name || order.submit_status }}</view>
</view>


<!-- <view class="card" v-if="(order?.lines||[]).length">
<view class="row font-bold"><view style="flex:3">商品</view><view style="flex:2">单价</view><view style="flex:2">数量</view><view style="flex:2;text-align:right">小计</view></view>
<view v-for="(l,i) in order.lines" :key="l?.id ?? i" class="row" style="padding:10rpx 0">
<view style="flex:3">{{ l?.product_name || l?.product }}</view>
<view style="flex:2">¥ {{ l?.base_price }}</view>
<view style="flex:2">{{ l?.base_qty }}</view>
<view style="flex:2;text-align:right">¥ {{ (l?.base_qty||0)*(l?.base_price||0) }}</view> -->

<view class="card" v-if="(order?.lines||[]).length">
<view class="row font-bold"><view style="flex:3">商品</view><view style="flex:2">单价</view><view style="flex:2">数量</view><view style="flex:2;text-align:right">小计</view></view>
<view v-for="(l,i) in order.lines" :key="l?.id ?? i" class="row" style="padding:10rpx 0">
<view style="flex:3">{{ l?.product_name || l?.product }}</view>
<view style="flex:2">¥ {{ l?.base_price }}</view>
<view style="flex:2">{{ fmtQty(l?.base_qty) }}</view>
<view style="flex:2;text-align:right">¥ {{ (fmtQty(l?.base_qty)||0)*(l?.base_price||0) }}</view>


</view>
</view>
</view>
</template>
<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'


const order = ref(null)


onLoad(async (query)=>{
const id = Number(query?.id||0)
if(!id) return
order.value = await api.orderDetail(id)
})

function fmtQty(value) {
  if (value === null || value === undefined || value === '') return '-'

  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)

  return String(Number(n.toFixed(4)))
}

function fmtAmount(line) {
  const qty = Number(line?.base_qty || 0)
  const price = Number(line?.base_price || 0)
  return (qty * price).toFixed(2)
}

</script>