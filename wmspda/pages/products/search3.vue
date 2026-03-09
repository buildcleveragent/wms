<template>
  <view class="p-4">
    <view class="text-lg font-bold mb-2">
      选品<span v-if="cart.customer"> - {{ cart.customer.name }}</span>
    </view>

    <view class="bar">
      <input class="input flex-input" v-model="q" placeholder="名称/编码/条码 可输入部分内容"  @confirm="search" />
      <button class="btn-outline" @click="search">搜索</button>
      <button class="btn-outline" @click="scanAdd">扫码</button>
    </view>

    <view v-for="(p,i) in rows" :key="p?.id ?? i" class="card">
      <!-- 名称 + 价格 -->
      <view class="name-price-row">
        <view class="product-name">{{ p?.name }}</view>
        <view class="price-badge">¥ {{ p?.price ?? 0 }}</view>
      </view>

      <!-- 编码/规格/单位/箱规 -->
      <view class="product-info">
        {{ p?.sku }}
        <text v-if="p?.spec"> · 规格：{{ p.spec }}</text>
		 <text v-if="p?.product_image_url"> · 规格：{{ p.product_image_url }}</text>
        <text v-if="p?.base_unit_name"> · 单位：{{ p.base_unit_name }}</text>
        <text v-if="p?.carton_conv && p?.carton_unit">
          · 箱规：{{ p.carton_conv }}{{ p.base_unit || '' }}/{{ p.carton_unit }}
        </text>
      </view>

      <!-- 可用库存 + 数量输入 + 小按钮加入 -->
      <view class="action-row">
        <view class="stock-info">可用：{{ p?.available ?? 0 }}</view>
        <view class="qty-row">
          <input
            class="input qty-input"
            type="number"
            :value="qtyMap[p.id] ?? 1"
            @input="(e:any)=> setQty(p.id, e?.detail?.value ?? e?.target?.value)"
            min="0"
          />
          <button class="btn btn-sm" @click="add(p)">加入</button>
        </view>
      </view>
    </view>

    <view class="row" style="margin-top:16rpx">
      <button class="btn-outline" @click="goCart">查看选中的商品清单 ({{ cart.totalQty }})</button>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { scanOne } from '@/utils/scan'
import { useCart } from '@/store/cart'

const q = ref('')
const list = ref<{count:number; next:string|null; previous:string|null; results:any[]}>({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const cart = useCart()

// 数量输入：按商品 id 记录期望数量（默认 1）
const qtyMap = ref<Record<number, number>>({})
function setQty(pid:number, v:any){
  const n = Math.max(0, Number(v) || 0)
  qtyMap.value = { ...qtyMap.value, [pid]: n }
}
function getDesiredQty(pid:number){
  const n = Number(qtyMap.value[pid])
  return Number.isFinite(n) && n > 0 ? n : 1
}

async function search(){
  // 注：后端按登录用户的 owner 限定；如需按仓库收窄，可继续传 warehouse_id
  const res = await api.products(q.value, 1, cart.warehouse_id||undefined)
  list.value = Array.isArray(res)
    ? { count: res.length, next:null, previous:null, results: res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}

function add(p:any){
  if(!p?.id) return
  const desired = getDesiredQty(p.id)
  const idx = cart.items.findIndex(x=> x.product_id === p.id)
  if (idx > -1) {
    const cur = Number(cart.items[idx].qty || 0)
    cart.setQty(idx, cur + desired)
  } else {
	  
    cart.addItem({ id:p.id, 
	              sku:p.sku, 
				 name:p.name, 
			    price: Number(p.price||0),
    product_image_url:p.product_image_url,
	             gtin:p.gtin,
       base_unit_name:p.base_unit_name,
	     aux_uom_name:p.aux_uom_name,
	  aux_qty_in_base:p.aux_qty_in_base,	
		   product_min_price:Number(p.product_min_price||0),
		   max_discount:Number(p.max_discount||0), 
		 })
		 
	console.log("cart.addItem",cart.items[0].gtin)
    const newIndex = cart.items.findIndex(x => x.product_id === p.id)
    if (newIndex > -1) cart.setQty(newIndex, desired)
  }
  uni.showToast({ title:'已加入：'+(p.name||p.sku)+' × '+desired, icon:'none' })
}

function goCart(){ uni.navigateTo({ url:'/pages/orders/cart' }) }

async function scanAdd(){ const code = await scanOne(); if(!code) return; q.value = code; await search() }

onLoad(()=>{
  if(!cart.customer){
    uni.redirectTo({ url: '/pages/customers/select' })
    return
  }
  search()
})
</script>


<style scoped>
/* 搜索栏样式 */

.bar .btn-outline {
  flex: none;
  width: auto;
  padding: 0 24rpx;
  height: 72rpx;
  line-height: 72rpx;
  border: 1rpx solid #007AFF;
  color: #007AFF;
  border-radius: 8rpx;
  background: transparent;
  font-size: 28rpx;
}

/* 卡片样式 */
.card {
  background: white;
  border-radius: 12rpx;
  padding: 24rpx;
  margin-bottom: 20rpx;
  box-shadow: 0 2rpx 8rpx rgba(0,0,0,0.06);
}

/* 商品名称和价格行 */
.name-price-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 12rpx;
}

.product-name {
  flex: 1;
  font-weight: bold;
  font-size: 32rpx;
  line-height: 1.4;
  padding-right: 16rpx;
}

.price-badge {
  flex: none;
  background: #f8f8f8;


  padding: 8rpx 16rpx;
  border-radius: 20rpx;
  font-size: 26rpx;
  font-weight: bold;
  white-space: nowrap;
}

/* 商品信息 */
.product-info {
  font-size: 26rpx;
  color: #666;
  line-height: 1.5;
  margin-bottom: 16rpx;
}

/* 操作行 */
.action-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stock-info {
  font-size: 26rpx;
  color: #999;
}

/* 数量输入和按钮 */
.qty-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.qty-input {
  width: 160rpx;
  height: 64rpx;
  border: 1rpx solid #e0e0e0;
  border-radius: 8rpx;
  padding: 0 16rpx;
  font-size: 28rpx;
  background: #f8f8f8;
  text-align: center;
}

.btn-sm {
  padding: 6rpx 14rpx;
  font-size: 24rpx;
  border-radius: 8rpx;
  height: 64rpx;
  line-height: 64rpx;
  background: #007AFF;
  color: white;
  border: none;
}

/* 底部按钮 */
.btn-outline {
  width: 100%;
  background: transparent;
  border: 1rpx solid #007AFF;
  color: #007AFF;
  border-radius: 10rpx;
  padding: 20rpx 0;
  font-size: 32rpx;
  margin-top: 16rpx;
}

/* 整体容器 */
.bar {
  display: flex;  /* 启用 Flexbox 布局 */
  align-items: center;  /* 垂直居中对齐 */
  justify-content: flex-start;  /* 水平排列，从左开始 */
  gap: 10px;  /* 设置按钮和输入框之间的间距 */
}

/* 输入框样式 */
.flex-input {
  flex: 1;
  height: 72rpx;
  background: #f8f8f8;
  flex-grow: 1;  /* 让输入框占据剩余空间 */
  padding: 8px 12px;  /* 内边距 */
  font-size: 16px;  /* 字体大小 */
  border: 1px solid #ccc;  /* 边框样式 */
  border-radius: 4px;  /* 圆角 */
}

/* 按钮样式 */
.btn-outline {
  padding: 8px 16px;  /* 按钮内边距 */
  font-size: 16px;  /* 字体大小 */
  border: 1px solid #007aff;  /* 按钮边框 */
  border-radius: 4px;  /* 圆角 */
  background-color: white;  /* 背景色 */
  cursor: pointer;  /* 鼠标样式 */
}

/* 按钮对齐 */
.btn-outline:first-of-type {
  margin-left: 10px; /* 如果需要左边距，可以加此行 */
}


</style>