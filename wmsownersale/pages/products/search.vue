<template>
  <view class="container">
	  
    <view class="card-first">
      <view class="row-first">
        <view class="font-bold">客户：{{ cart.customer?.name || '未选择' }} </view>
      </view>
	  
	  <view class="bar">
	    <input class="input flex-input" v-model="q" placeholder="名称/编码/条码 可输入部分内容"  @confirm="search" />
	    <button class="btn-outline" @click="search">搜索</button>
	    <button class="btn-outline" @click="scanAdd">扫码</button>
	  </view>
    </view>


	<view class="content">
			<view v-for="(p,i) in rows" :key="p?.id ?? i"  :class="['row item', { 'odd': i % 2 === 0 }]">
				
			  <view class="col-image">
				<image :src="p.product_image_url" mode="aspectFill" class="product-image" />
			  </view>	
				
			  <view class="col-info"><!-- 名称 + 价格 -->
				  <view class="name-price-row">
					<view class="product-name">{{ p?.name }}</view>	
				  </view>
				  		  
				  <!-- 编码/规格/单位/箱规 -->
				  <view class="meta-container">
					<view class="baradd">
						<text  class="metabar">条码: {{ p.gtin }}   </text>		
						<button class="btnnew" @click="add(p)">加入</button>		
					</view>  				 	
					
					<text  class="meta"> 库存可用数量: {{ p.available }}   </text>		
				  </view>
				  
				  
				  <!-- 出货单位选择（单选按钮） -->
				  <view v-if="p.unitOptions && p.unitOptions.length" class="pkg-radio-block">
				   <text class="pkg-label">选择单位</text>
				  			  <radio-group class="pkg-radio-group" @change="(e) => onUnitChange(p, e.detail.value)">
				  				<label v-for="(opt, idx2) in p.unitOptions" :key="opt.key" class="pkg-radio-row">
				  				  <radio
				  					:value="String(idx2)"
				  					:checked="getUnitIndex(p) === idx2"
				  					class="pkg-radio-input"
				  				  />
				  				  <text class="pkg-radio-text">
				  					{{ opt.label }}：换算数量={{ opt.multiplier }}{{ p.base_unit_name }}
				  				  </text>
				  				</label>
				  			  </radio-group>
				  </view>
				  

				  <view class="price-qty-ch">
						<view class="col-label-qty">
											<text class="label-text">出货数量</text>
											<input class="input num-input"  
											       :id="'input_' + p.id"
											       type="number" 
												   :value="qtyMap[p.id] ?? ''" 
												   @input="(e) => setQty(p.id, e?.detail?.value ?? e?.target?.value)" 
												   min="0" 
												  :ref="el => { if (el) qtyInputRefs[p.id] = el }" 
												  @focus="() => handleTap(p.id)"                
												   />
						</view>
						 
						 <view class="col-label-first">
								<text class="label-text-chdw">出货单位</text>
								<text class="label-text-sh">{{ p.unitOptions[p.selectedUnitIndex].label }}</text>
						 </view> 
				  </view>


                  <!-- 单价与数量 -->
                  <view class="price-qty">

				<view class="col-label-qty">
				  <text class="label-text">基本数量</text>
				  <!-- <input class="input qty-input" type="number" :value="qtyMap[p.id] ?? 0" @input="(e) => setQty(p.id, e?.detail?.value ?? e?.target?.value)" min="0" /> -->
				  <text class="qty-input-text" type="number" >{{ (p.unitOptions[p.selectedUnitIndex].multiplier * qtyMap[p.id])||0}}</text>
				</view>
					  
					  
                  			<!--基本单位 -->
                  			<view class="col-label-jbdw">				            
                  			    <text class="label-text">基本单位</text>
								<text class="label-text-name">{{ p.base_unit_name }}</text>
                  			</view>			  
                  			  
                    <!-- 单价 -->
                    <view class="col-label-price">				            
                      <text class="label-text">基本单价</text>
                      <input
                        class="input num-input"
                        type="number"
                        inputmode="decimal"
                        v-model.number="p.price"
                        :min="p.min_price ?? 0"
                        @blur="enforceMin(p)"
                        @change="enforceMin(p)"                  			
                      />
                    </view>

                    <!-- 金额 -->
                    <view class="col-label-last">
                      <text class="label-text">金额</text>
                      <view class="amount-text">¥ {{ fmt( ((p.unitOptions[p.selectedUnitIndex].multiplier * qtyMap[p.id])||0)* (p.price || 0)) }}</view>
                    </view>
                  			
                  </view>
				  
				</view>
			  </view>	
	</view>
	
    <view class="footer">
      <button class="btn-outline" @click="goCart">
		 <text>查看、提交订单：数量:{{cart.totalQty}} ￥{{cart.totalAmount}}</text> 
		  
	</button>
    </view>
  </view>
</template>

<script setup lang="ts">
// import { ref, computed } from 'vue'
import { ref, computed, onMounted, onUnmounted, watch,reactive } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { scanOne } from '@/utils/scan'
import { useCart } from '@/store/cart'

const qtyInputRefs = reactive<Record<string | number, any>>({})
const lastQtyMap = reactive<Record<string | number, string>>({})

const q = ref('')
const list = ref<{count:number; next:string|null; previous:string|null; results:any[]}>({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const cart = useCart()
const fmt = (n)=> Number(n||0).toFixed(2)

// 兼容老数据：补齐原价与最低价
function ensureItemGuard(it){
  // 原价：优先使用已有字段，其次用当前价兜底
  if (typeof it.orig_price !== 'number' || !(it.orig_price >= 0)) {
    const orig = Number(it.price ?? 0)
    it.orig_price = Number.isFinite(orig) ? orig : 0
  }
  
    // 确保 max_discount 和 product_min_price 是有效的数字
    const product_min_price = +Number(it.product_min_price || 0);
    const max_discount = +Number(it.max_discount || 0); // 默认最大折扣为0
    const orig_price = it.orig_price;
  
    // 输出中间值
    console.log("orig_price:", orig_price);
    console.log("product_min_price:", product_min_price);
    console.log("max_discount:", max_discount);
  
  
  // 最低可售价 = 原价 * 0.9（保留两位）
 //    const product_min_price=+Number(it.product_min_price || 0)
	// const max_discount=+Number(it.max_discount || 1)
	const min=Math.max(product_min_price,max_discount*it.orig_price)
	
	
  if (typeof it.min_price !== 'number' || !(it.min_price >= 0)) {
		
    it.min_price = +min.toFixed(2)
  }
}

// 把用户输入价钳制到 >= 最低价，并统一两位小数
function enforceMin(it){
  ensureItemGuard(it)
  const val = Number(it.price)
  const min = Number(it.min_price || 0)

  
  if (!Number.isFinite(val)) {
    it.price = min
    return
  }
  
  if (val < min) {
    it.price = min
    uni.showToast({ title:`单价不得低于 ¥${fmt(min)}`, icon:'none' })
  } else {
    it.price = +val.toFixed(2)
  }
}



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
  console.log("res=",res)
  list.value = Array.isArray(res)
    ? { count: res.length, next:null, previous:null, results: res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}

const unitSelIndexMap = reactive({}) 

function onUnitChange(p, newIndex) {
  const idx = Number(newIndex)
  console.log("idx=",idx)
  p.selectedUnitIndex = idx
}

function getUnitIndex(p) {
  const opts = p.unitOptions || []
  const idx  =p.selectedUnitIndex 
  if (idx == null || idx < 0 || idx >= opts.length) return 0
  return idx
}

function add(p:any){
  if(!p?.id) return
  const desired = getDesiredQty(p.id)
  const idx = cart.items.findIndex(x=> x.product_id === p.id)
  
  let curabc = 0
  
  if (idx > -1){
	  curabc = Number(cart.items[idx].qty || 0)
  }

  const diffavailabe=curabc + desired-p.available
  
  if (diffavailabe>0){
  	uni.showToast({ title:'加上之前选的，已超出可用库存，超出数量：'+diffavailabe, icon:'none' })
	return
  }	
  
  
  if (idx > -1) {
    const cur = Number(cart.items[idx].qty || 0)
	cart.setQty(idx, cur + desired)
  } else {
	cart.addItem({ id:p.id, 
	              sku:p.sku, 
				 name:p.name, 
			    price:Number(p.price||0),
    product_image_url:p.product_image_url,
	             gtin:p.gtin,
       base_unit_name:p.base_unit_name,
	     aux_uom_name:p.aux_uom_name,
	  aux_qty_in_base:p.aux_qty_in_base,	
    product_min_price:Number(p.product_min_price||0),
         max_discount:Number(p.max_discount||0), 
		 	available:p.available,
	      unitOptions: p.unitOptions,
	selectedUnitIndex: p.selectedUnitIndex,
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


function handleTap(id: string | number) {
  const wrapper = qtyInputRefs[id]
  if (!wrapper) return

  // H5：正常全选
  // #ifdef H5
  const realInput =
    wrapper.$el?.querySelector?.('input') ||
    wrapper.$el ||
    wrapper
  if (realInput && realInput.select) {
    realInput.select()
  }
  // #endif

  // APP 端：退而求其次，清空，让用户直接输入新值，相当于“全选后覆盖”
  // #ifdef APP-PLUS
  lastQtyMap[id] = String(qtyMap[id] ?? '')
  qtyMap[id] = ''   // 清空
  // #endif
}

// 选做：如果失焦时还是空的，就恢复原值
const handleBlur = (id: string | number) => {
  // #ifdef APP-PLUS
  if (!qtyMap[id] && lastQtyMap[id] != null) {
    qtyMap[id] = lastQtyMap[id]
  }
  // #endif
}


</script>


<style scoped>
/* 搜索栏样式 */

.multi-line {
display: inline-flex;
flex-direction: column;
align-items: center;
justify-content: center;
/* 如果按钮高度不够，可以适当增加padding */
padding: 20rpx;
}

.btn-multiline-1 text {
    display: block;
}

.baradd{
	display:flex;
	flex-direction:row;
	justify-content: space-between;
	            width: 100%;
}

.btnnew {
	background: #3498db;
	color: white;
	border: none;
	border-radius: 16rpx;
	font-size: 25rpx;
	font-weight: 500;
	cursor: pointer;
	transition: all 0.3s ease;
	display: flex;
	justify-content: center;
	align-items: center;
	box-shadow: 0 4px 6px rgba(50, 150, 230, 0.2);
	transition: all 0.3s ease;
	padding: 20rpx 20rpx;
	box-shadow: 0 4rpx 6rpx rgba(50, 150, 230, 0.2);
	white-space: nowrap;	        
	width: 100rpx;
	height: 30rpx;
	margin-right:20rpx;
	
				
}
       

.bar .btn-outline {
  flex: none;
  width: auto;
  padding: 0 24rpx;
  height: 62rpx;
  line-height: 62rpx;
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


.qty-input-text {
  width: 80%;
  height:50rpx;
  padding: 5rpx;
  font-size: 30rpx;
  /* border: 1rpx solid #ccc; */
  border-radius: 5rpx;
  text-align: center;
  margin-top:5rpx;
/*  color:red; */
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
  align-items: stretch;  /* 垂直居中对齐 */
  justify-content: flex-start;  /* 水平排列，从左开始 */
  gap: 10rpx;  /* 设置按钮和输入框之间的间距 */
  padding-top:1rpx;
/*  top:200rpx; */
}

/* 输入框样式 */
.flex-input {
  flex: 1;
  height: 62rpx;
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

.container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding-top: 200rpx; 
  overflow: hidden;
  margin-left:2rpx;
   margin-right:2rpx;
}

/* 顶部固定 */
.card-first {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  background-color: white;
  z-index: 100;
  padding: 0rpx;
  box-shadow: 0 2rpx 10rpx rgba(0, 0, 0, 0.1);
  /* border-bottom: 1rpx solid #f0f0f0; */
  height: 100rpx;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

/* 中间可滚动区域 */
.content {
  flex: 1;
  overflow-y: auto;
  padding-top: 110rpx; /* 为顶部固定区域留出空间 */
  padding-bottom: 80rpx; /* 为底部footer留出空间 */
  padding-left: 2rpx;
  padding-right: 2rpx;
}

/* 底部固定区域 */
.footer {
  position: fixed;
  bottom: 0;
  left: 0;
  width: 100%;
  background-color: white;
  box-shadow: 0 -2rpx 10rpx rgba(0, 0, 0, 0.1);
  border-top: 1rpx solid #a6a6a6;
  z-index: 100;
  padding-right: 50rpx;
  box-sizing: border-box;
}

/* 行样式 */
.row {
  display: flex;
  align-items: flex-start;
  width: 100%;
  margin-bottom: 10rpx;
}

.row-first {
  display: flex;
  align-items: center;
  width: 100%;
  height: 60rpx;
}

/* 商品行样式 */
.row.item {
  padding: 5rpx 0;
  border-bottom: 1rpx solid #d8d8d8;
/*  min-height: 140rpx; */
  align-items: flex-start;
  margin-left:2rpx;
   margin-right:2rpx;
}

.item {
  margin-bottom: 5rpx;
  padding: 5rpx;
  border: 1rpx solid #d8d8d8;
  border-radius: 8rpx;
  display: flex;
  flex-direction: row;
  margin-left:2rpx;
   margin-right:2rpx;
}

.odd {
  background-color: #f5f5f5;
}

/* 合计行样式 */
.total-row {
  justify-content: flex-end;
  margin-bottom: 20rpx;

  padding-bottom: 1rpx;
  border-bottom: 1rpx solid #eaeaea;
  align-items: center;
  font-size: 36rpx;
  color:red;
  padding: 1rpx;
}

/* 按钮行样式 */
.button-row {
  display: flex;
  gap: 20rpx;
  padding-bottom: 1rpx;
}

/* 按钮样式 */
.btn, .btn-outline {
  flex: 1;
  height: 80rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32rpx;
  border-radius: 10rpx;
  border: none;
}

.btn {
  background: #007AFF;
  color: white;
}

.btn-outline {
  background: transparent;
  border: 1rpx solid #007AFF !important;
  color: #007AFF;
}

.btn:disabled {
  background: #ccc;
  color: #999;
  border: none;
}

/* 商品显示信息 */
.col-image {
  flex: 0 0 160rpx;
  margin-right: 30rpx;
}

.product-image {
  width: 160rpx;
  height: 160rpx;
  object-fit: cover;
  border-radius: 10rpx;
}

.col-info {
  flex: 1;
}

/* 商品信息样式 */
.name {
  font-weight: 500;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 8rpx;
  font-size: 30rpx;
}

.meta-container {
  display: flex;
  flex-direction: column;
  gap: 3rpx;
  margin-bottom: 2rpx;
}

.meta {
  font-size: 30rpx;
  color: #666;
  line-height: 1.4;
}

.metabar {
  font-size: 30rpx;
  color: #666;
  line-height: 1.4;
  flex:1;
}

/* 价格和数量区域 */
.price-qty {
  display: flex;
  justify-content: space-between;
/*  margin-top: 5rpx; */
}

.price-qty-ch {
  display: flex;
  justify-content: flex-start;
/*  margin-top: 5rpx; */
}



.col-label {
  width: 25%;
  text-align: center;

}

.col-label-first {
  width: 20%; /* 第一列宽度稍小 */
  text-align: left; /* 左对齐 */
}

.col-label-jbdw {
  width: 23%; /* 第一列宽度稍小 */
  text-align: center; /* 左对齐 */
}

.col-label-price {
  display: flex;
  flex-direction: column;
  align-items: center; /* 水平居中 */
  width: 28%; /* 第一列宽度稍小 */
  text-align: center; /* 左对齐 */
  
}

.col-label-qty {
  display: flex;
  flex-direction: column;
  align-items: center; /* 水平居中 */	
  width: 30%; /* 第一列宽度稍小 */
  text-align: left; /* 左对齐 */
}

.col-label-last {
  display: flex;
  flex-direction: column;
  align-items: right; /* 水平居中 */
  width: 32%;
  text-align: right;
  margin-right:20rpx;  
}

.col-label-first .label-text-name {
  display: block;
  font-size: 30rpx;
  color: #777;
  /* padding-left:30rpx; */
   text-align: center;
  margin-top:5rpx;
}

.col-label-jbdw .label-text-name {
  display: block;
  font-size: 30rpx;
/*  color: #777; */
  /* padding-left:30rpx; */
   text-align: center;
  margin-top:5rpx;
}



.label-text-chdw {
	/* border: 2rpx solid #ddd; */
  display: block;
  width:200rpx;
  font-size: 30rpx;
  color: #777;
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
/*  margin-bottom: 6rpx; */
}


.label-text-sh {
  display: block;
  font-size: 35rpx;
  width:200rpx;
  color: red;
  text-align: center;
/*    border: 2rpx solid #ddd; */
	  box-sizing: border-box;
}



.label-text {
  display: block;
  font-size: 30rpx;
  color: #777;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
/*  margin-bottom: 6rpx; */
}


.label-text-first {
  display: block;
  font-size: 20rpx;
  color: #777;
  margin-bottom: 12rpx;
}


/* 输入框样式 */
.input {
  width: 80%;
  padding: 12rpx;
  border-radius: 8rpx;
  border: 2rpx solid #ddd;
  box-sizing: border-box;
  font-size: 20rpx;
/*  margin-right: 16px; */
}

.num-input {
  width: 80%;
  height:50rpx;
  padding: 5rpx;
  font-size: 30rpx;
  border: 1rpx solid #ccc;
  border-radius: 5rpx;
  text-align: right;
  margin-top:5rpx;
  color:red;
/*    margin-right: 16px; */
}

.qty-input-text, .num-input {
  box-sizing: border-box;
}

.qty-input {
  width: 100%;
  height:50rpx;
  padding: 5rpx;
  font-size: 30rpx;
  border: 1rpx solid #ccc;
  border-radius: 5rpx;
  text-align: right;
  margin-top:5rpx;
/*  margin-right: 16px; */
}


/* 金额文本样式 */
.amount-text {
  font-size: 30rpx;
  font-weight: bold;
    height:50rpx;
  color: #e74c3c;
  display: flex;
  align-items: right;
  justify-content: right;
    margin-top:5rpx;
/*  padding-right:5rpx; */
}

.font-bold {
  font-weight: bold;
}

/* 通用卡片样式（保留但可能不需要） */
.card {
  background: white;
  border-radius: 12rpx;
  padding: 24rpx;
  margin-bottom: 10rpx;
  /* box-shadow: 0 2rpx 3rpx rgba(0,0,0,0.06); */
  display: flex;
  flex-direction: column;
}


/* 包装选择区域 */
.pkg-select-block {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 12rpx;
  margin: 8rpx 0 12rpx 0;
}

.pkg-label {
  font-size: 30rpx;
  color: #777;
}

.pkg-picker-display {
  min-width: 220rpx;
  padding: 10rpx 16rpx;
  border: 1rpx solid #ccc;
  border-radius: 8rpx;
  font-size: 30rpx;
  background-color: #fff;
  color: #333;
  line-height: 1.4;
}

.pkg-base-hint {
  font-size: 30rpx;
  color: #999;
  margin-left: 8rpx;
}

/* 单选按钮版本的样式（如果启用radio-group时用） */
.pkg-radio-block {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  margin: 8rpx 0 12rpx 0;
}
.pkg-radio-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
  font-size: 24rpx;
  color: #333;
}
.pkg-radio-text {
  font-size: 30rpx;
  color: #333;
}


/* 针对电脑浏览器的额外调整 */
@media (min-width: 768px) {
  .container {
    padding-top: 0px; /* 电脑上可能需要更多顶部空间 */
  }
  
  .card-first {
	top:80rpx;
    height: 70px; /* 电脑上增加高度 */
    padding: 10px;
  }
}

/* 针对移动设备的优化 */
@media (max-width: 767px) {
  .container {
    padding-top: 0px;
  }
  
  .card-first {	  
    height: 60rpx;
    padding: 10rpx 10rpx;
  }
}
</style>