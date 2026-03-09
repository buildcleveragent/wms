<template>
  <view class="page">
    <view class="section" v-for="grp in groups" :key="grp.title">
      <view class="section-title">{{ grp.title }}</view>
      <view class="grid">
        <view class="tile" v-for="it in grp.items" :key="it.text" @click="go(it.path)">
          <view class="tile-inner">
            <view class="icon" :class="it.color">{{ it.emoji }}</view>
            <text class="tile-text"> {{ it.text }}</text>
          </view>
        </view>
      </view>
    </view>

    <view v-if="isAdmin" class="section">
      <view class="section-title">管理员</view>
      <view class="grid">
        <view class="tile" v-for="it in adminItems" :key="it.text" @click="go(it.path)">
          <view class="tile-inner">
            <view class="icon" :class="it.color">{{ it.emoji }}</view>
            <text class="tile-text"> {{ it.text }}</text>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>
<script setup>
import { ref } from 'vue'
const isAdmin = ref(!!uni.getStorageSync('isAdmin')) // 同步读取标志，保证页面稳定渲染

const groups = ref([
  { title: '常用功能', items: [
    { text:'访销下单', path:'/pages/customers/select',        emoji:'📝', color:'blue' },
    { text:'车销下单', path:'/pages/vansales/create',      emoji:'🚚', color:'orange' },
    { text:'客户拜访', path:'/pages/visit/index',          emoji:'👥', color:'blue' },
    { text:'客户新增', path:'/pages/customer/create',      emoji:'➕', color:'orange' },
  ]},
  { title: '访销管理', items: [
    // { text:'访销下单', path:'/pages/orders/create',        emoji:'📝', color:'blue' },
	// { key:'so_create', dom:'sales', text:'开单', icon:'/static/icons/create.png', to:'/pages/customers/select', perm:'so_create' },
	{ text:'访销下单', path:'/pages/customers/select',        emoji:'📝', color:'blue' },
    { text:'访销订单', path:'/pages/orders/list',          emoji:'📄', color:'blue' },
    { text:'客户拜访', path:'/pages/visit/index',          emoji:'👥', color:'blue' },
    { text:'客户新增', path:'/pages/customer/create',      emoji:'➕', color:'blue' },
    { text:'陈列记录', path:'/pages/visit/display',        emoji:'🧾', color:'green' },
    { text:'临期仓销售', path:'/pages/orders/near_expiry_sale', emoji:'🏷️', color:'blue' },
  ]},
  { title: '车销管理', items: [
    { text:'车销下单', path:'/pages/vansales/create',      emoji:'🚚', color:'blue' },
    { text:'车销订单', path:'/pages/vansales/list',        emoji:'📄', color:'blue' },
    { text:'要货申请', path:'/pages/replenishment/request', emoji:'📦', color:'orange' },
    { text:'车销返仓', path:'/pages/vansales/return',      emoji:'↩️', color:'orange' },
    { text:'车销库存', path:'/pages/vansales/inventory',   emoji:'📊', color:'green' },
    { text:'调拨记录', path:'/pages/transfer/records',     emoji:'🧾', color:'blue' },
  ]},
  { title: '统计分析', items: [
    { text:'销售统计', path:'/pages/analytics/sales',      emoji:'📈', color:'green' },
    { text:'客户分析', path:'/pages/analytics/customers',  emoji:'👤', color:'green' },
    { text:'销售报表', path:'/pages/analytics/reports',    emoji:'🗂️', color:'blue' },
  ]},
  { title: '其他', items: [
    { text:'通讯录', path:'/pages/contacts/index',         emoji:'📇', color:'green' },
  ]},
])

const adminItems = [
  { text:'订单审批', path:'/pages/admin/pending', emoji:'✅', color:'orange' },
]
function go(url){ uni.navigateTo({ url }) }
</script>
