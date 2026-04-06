<template>
  <view class="container">
    <!-- 订单头 -->
    <view class="card-first">
      <view class="row-first">
        <view class="font-bold">客户：{{ cart.customer?.name || '未选择' }} </view>
      </view>
    </view>

    <!-- 明细 -->
   <view class="content" v-if="cart.items.length">
	   <view class="receiver-card">
	     <view class="receiver-title">收件信息</view>
	   
	     <view class="receiver-grid">
	       <view class="receiver-item">
	         <view class="receiver-label">平台单号</view>
	         <input
	           v-model="form.src_bill_no"
	           class="receiver-input"
	           placeholder="可选，平台单号"
	         />
	       </view>
	   
	       <view v-if="isCashCustomer" class="receiver-item">
	         <view class="receiver-label">收件人</view>
	         <input
	           v-model="form.contact"
	           class="receiver-input"
	           placeholder="请输入收件人"
	         />
	       </view>
	   
	       <view v-if="isCashCustomer" class="receiver-item">
	         <view class="receiver-label">联系电话</view>
	         <input
	           v-model="form.contact_phone"
	           class="receiver-input"
	           placeholder="请输入联系电话"
	         />
	       </view>
	   
			<view v-if="isCashCustomer"  class="receiver-item receiver-item-full">
			  <view class="receiver-label">收货地址</view>
			  <input
				v-model="form.ship_to"
				class="receiver-input"
				placeholder="请输入完整收货地址"
			  />
			</view>
	     </view>
	   </view>
	   
	   <view class="section-divider"></view>
	   
      <!-- 商品行 -->
	  <view v-for="(it, i) in cart.items" :key="it.product_id ?? i"  :class="['row item', { 'odd': i % 2 === 0 }]" >
        <!-- 左侧商品图像 -->
        <view class="col-image">
          <image :src="it.product_image_url" mode="aspectFill" class="product-image" />
        </view>

        <!-- 右侧商品信息 -->
        <view class="col-info">
			
          <view class="customer-name"><text class="name">{{ it.name || it.sku || it.product_id }}</text></view>
		  
          <view class="meta-container">
			<text v-if="it.gtin" class="meta">条码: {{ it.gtin }}</text>
            <text v-if="it.aux_uom_name" class="meta">规格: {{ it.aux_uom_name }} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 换算数量: {{ it.aux_qty_in_base }}</text>
          </view>

          <!-- 单价与数量 -->
          <view class="price-qty">
			<!--基本单位 -->
			<view class="col-label-first">				            
			  <text class="label-text">基本单位</text>
              <text class="label-text-name">{{ it.base_unit_name }}</text>
			</view>			  
			  
            <!-- 单价 -->
            <view class="col-label-price">				            
              <text class="label-text">基本单价</text>
              <input
                class="input num-input"
                type="number"
                inputmode="decimal"
                v-model.number="it.price"
                :min="it.min_price ?? 0"
                @blur="enforceMin(it)"
                @change="enforceMin(it)"
			
              />
            </view>
            <!-- 数量 -->
            <view class="col-label-qty">
              <text class="label-text">基本数量</text>
              <input
                class="input qty-input"
                type="number"
                v-model.number="it.qty"
                min="0"
				@blur="enforceAvailable(it)"
				@change="enforceAvailable(it)"
              />
            </view>
            <!-- 金额 -->
            <view class="col-label-last">
              <text class="label-text">金额</text>
              <view class="amount-text">¥ {{ fmt((it.qty || 0) * (it.price || 0)) }}</view>
            </view>
			
          </view>
		  
       </view>
      </view>
	  
	 
	  
    </view>
	

	
  <view class="footer">
    <view class="row total-row">
      <view class="font-bold"></view>
      <view class="font-bold">合计：¥ {{ fmt(cart.totalAmount) }}</view>
    </view>

    <view class="button-row">
      <button class="btn-outline" @click="backToProducts">继续选品</button>
      <button class="btn" :disabled="!canSubmit" @click="submitOrder">提交订单</button>
    </view>
  </view>
</view>
</template>

<script setup>
import { computed, onMounted,reactive } from 'vue'
import { useCart } from '@/store/cart'
import { api } from '@/utils/request'

const form = reactive({
  src_bill_no: '',
  contact: '',
  contact_phone: '',
  ship_to: '',
})

const isCashCustomer = computed(() => {
  const code = String(cart.customer?.code || '').toUpperCase()
  const name = String(cart.customer?.name || '')
  return code === 'CASH' || name.includes('一件代发')
})

const cart = useCart()
const canSubmit = computed(()=> !!cart.customer && cart.items.length>0 )

// 数字格式化为两位小数
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


// 数量不能大于可用库存
function enforceAvailable(it){
  ensureItemGuard(it)
  const val = Number(it.qty)
  const  max_available = Number(it.available || 0)
  
  if (!Number.isFinite(val)) {
    uni.showToast({ title:`输入一个数值`, icon:'none' })
    return
  }
  
  let diffavailable=val - max_available
  
  
  if (diffavailable>0) {
    it.qty = max_available
    uni.showToast({ title:`订购数量不能超过可用库存, 已超出数量：`+diffavailable, icon:'none' })
  } 
  
}


onMounted(()=>{
  // 页面加载时给每一行补齐 orig_price/min_price
  cart.items.forEach(ensureItemGuard)
})

function backToProducts(){ uni.navigateTo({ url:'/pages/products/search' }) }

async function submitOrder() {
  if (!cart.customer?.id) {
    uni.showToast({ title: '请先选择客户', icon: 'none' })
    return
  }

  if (!cart.items?.length) {
    uni.showToast({ title: '请先添加商品', icon: 'none' })
    return
  }

  // 提交前兜底校验：不允许低于最低价
  const bad = cart.items.find(it => {
    ensureItemGuard(it)
    return typeof it.orig_price === 'number' && it.price < it.min_price
  })
  if (bad) {
    uni.showToast({ title: '存在价格低于系统最低价的商品，请修正后再提交', icon: 'none' })
    return
  }

  // 一件代发客户：收件信息必填
  if (isCashCustomer.value) {
    if (!String(form.contact || '').trim()) {
      uni.showToast({ title: '请填写收件人', icon: 'none' })
      return
    }
    const phone = String(form.contact_phone || '').trim()
    if (!phone) {
      uni.showToast({ title: '请填写联系电话', icon: 'none' })
      return
    }
    if (phone.length < 6 || !/\d/.test(phone)) {
      uni.showToast({ title: '联系电话格式不正确', icon: 'none' })
      return
    }
    if (!String(form.ship_to || '').trim()) {
      uni.showToast({ title: '请填写收货地址', icon: 'none' })
      return
    }
  }

  try {
    const payload = {
      customer_id: cart.customer?.id,
      remark: '业务员下单',
      src_bill_no: String(form.src_bill_no || '').trim(),
      contact: String(form.contact || '').trim(),
      contact_phone: String(form.contact_phone || '').trim(),
      ship_to: String(form.ship_to || '').trim(),
      items: cart.items.map(it => ({
        product_id: it.product_id,
        qty: it.qty,
        price: it.price
      }))
    }
    console.log('createOutboundOrder payload=', payload)
    const res = await api.createOutboundOrder(payload)

    uni.showToast({
      title: '已创建：' + (res?.order_no || res?.id),
      icon: 'none'
    })

    cart.clear()
    form.src_bill_no = ''
    form.contact = ''
    form.contact_phone = ''
    form.ship_to = ''

    uni.switchTab({ url: '/pages/features/index' })
 } catch (e) {
  console.error(e)

  const data = e?.data || {}
  const duplicateMsg =
    data?.src_bill_no ||
    data?.message ||
    data?.detail ||
    ''

  const existingOrderId = Number(data?.existing_order_id || 0)
  const existingOrderNo = String(data?.existing_order_no || '').trim()

  // 命中“平台单号重复”
  if (duplicateMsg && String(duplicateMsg).includes('平台单号重复')) {
    uni.showModal({
      title: '平台单号重复',
      content: existingOrderNo
        ? `该平台单号已存在，对应订单：${existingOrderNo}。是否查看原订单？`
        : '该平台单号已存在。是否查看原订单？',
      confirmText: '查看原单',
      cancelText: '返回修改',
      success: (res) => {
        if (res.confirm && existingOrderId) {
          uni.navigateTo({
            url: '/pages/orders/detail?id=' + existingOrderId
          })
        }
      }
    })
    return
  }

  uni.showToast({
    title: e?.message || data?.detail || data?.message || '创建订单失败',
    icon: 'none'
  })
}
}


</script>

<style scoped>
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
  padding: 20rpx;
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
  padding-top: 100rpx; /* 为顶部固定区域留出空间 */
  padding-bottom: 180rpx; /* 为底部footer留出空间 */
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
  padding: 10rpx;
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
  font-size: 20rpx;
  color: #666;
  line-height: 1.4;
}

/* 价格和数量区域 */
.price-qty {
  display: flex;
  justify-content: space-between;
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
  font-size: 25rpx;
  color: #777;
  padding-left:30rpx;
  margin-top:5rpx;
}



.label-text {
  display: block;
  font-size: 20rpx;
  color: #777;
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
/*    margin-right: 16px; */
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

.receiver-card {
  margin: 16rpx 20rpx 220rpx;
  padding: 20rpx;
  background: #fff;
  border-radius: 12rpx;
  box-sizing: border-box;
}

.receiver-title {
  font-size: 30rpx;
  font-weight: 600;
  color: #111;
  margin-bottom: 20rpx;
}

.receiver-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
  gap: 16rpx 16rpx;
  align-items: start;
}

.receiver-item {
  min-width: 0;
}

.receiver-item-full {
  grid-column: 1 / -1;
}

.receiver-label {
  font-size: 26rpx;
  color: #333;
  margin-bottom: 8rpx;
  line-height: 1.4;
}

.receiver-input,
.receiver-textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 12rpx 16rpx;
  border: 1rpx solid #dcdfe6;
  border-radius: 8rpx;
  background: #fff;
  font-size: 28rpx;
  color: #333;
}

.receiver-input {
  height: 72rpx;
}

.receiver-textarea {
  min-height: 140rpx;
}

.receiver-card {
  margin: 16rpx 20rpx 12rpx;
  padding: 20rpx;
  background: #fff;
  border-radius: 12rpx;
  box-sizing: border-box;
}

.receiver-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
  gap: 16rpx;
  align-items: start;
}

.receiver-item {
  min-width: 0;
}

.receiver-item-full {
  grid-column: 1 / -1;
}

.receiver-title {
  font-size: 30rpx;
  font-weight: 600;
  color: #111;
  margin-bottom: 20rpx;
}

.receiver-label {
  font-size: 26rpx;
  color: #333;
  margin-bottom: 8rpx;
}

.receiver-input {
  width: 100%;
  height: 72rpx;
  box-sizing: border-box;
  padding: 0 16rpx;
  border: 1rpx solid #dcdfe6;
  border-radius: 8rpx;
  background: #fff;
  font-size: 28rpx;
  color: #333;
}

.section-divider {
  height: 1rpx;
  margin: 0 20rpx 16rpx;
  background: #e5e7eb;
}


/* 窄屏时自动改成两列 */
@media (max-width: 1200px) {
  .receiver-grid {
    grid-template-columns: 1fr 1fr;
  }

  .receiver-item-full {
    grid-column: 1 / -1;
  }
}

/* 更窄时改成一列 */
@media (max-width: 768px) {
  .receiver-grid {
    grid-template-columns: 1fr;
  }

  .receiver-item,
  .receiver-item-full {
    grid-column: auto;
  }
}

/* 针对电脑浏览器的额外调整 */
@media (min-width: 768px) {
  .container {
    padding-top: 0px; /* 电脑上可能需要更多顶部空间 */
  }
  
  .card-first {
	top:80rpx;
    height: 60px; /* 电脑上增加高度 */
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
    padding: 15rpx 20rpx;
  }
}


</style>