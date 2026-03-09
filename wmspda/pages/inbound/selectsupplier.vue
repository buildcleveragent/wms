<template>
	<view class="p-4">
		<view class="text-lg font-bold mb-2">点击选中的供应商</view>
		
		<view class="bar">
			<input class="input" v-model="q" placeholder="供应商名/编码 可输入部分内容" @confirm="search" />
			<button class="btn-outline" @click="search">搜索</button>
			<button class="btn-outline" @click="quickScan">扫码</button>
		</view>
		
<!-- 		  <view>
		    <picker mode="selector" :range="options" :value="index" @change="onPickerChange">
		      <view class="picker">
		        当前选择：{{ options[index] }}
		      </view>
		    </picker>
		  </view> -->

		<view v-for="(c,i) in rows" :key="c?.id ?? i" class="card" @click="choose(c)">
			<view class="row">
				<view class="font-bold">{{ c?.name }}</view>
				<view class="badge">ID: {{ c?.id }}</view>
			</view>
			<view class="text-gray">{{ c?.code }}</view>
		</view>


		<view class="mt-6">
			<button class="btn" :disabled="!cart.supplier" >下一步：录入收货商品</button>
		</view>
	</view>
</template>

<script setup>
// import { ref, computed } from 'vue'
import { ref, computed,onMounted, onUnmounted, watch } from 'vue'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
// 👇 一定要把 onUnload 引进来（需要的话也可加 onHide）
import { onLoad, onReachBottom, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'     
import { useCart } from '@/store/cart'

const q = ref('')
const page = ref(1)
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])

const cart = useCart()

// ---- 存活守卫：避免离开页面后回写 UI ----
let alive = true
let reqSeq = 0
onUnload(() => { alive = false; reqSeq++ })   // 页面销毁：让未归来的请求结果作废

function normalize(res){
  return Array.isArray(res)
    ? { count: res.length, next:null, previous:null, results: res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}

async function fetch(pageNo = 1){
  const tag = ++reqSeq
  try{
    // 后端已按业务员固定货主过滤，无需传 owner_id
    const res = await api.suppliers(q.value || '', pageNo,cart.owner.id)
    if (!alive || tag !== reqSeq) return   // 页面已销毁或有更新版请求 → 丢弃结果
    const n = normalize(res)
    if (pageNo === 1) list.value = n
    else list.value = { ...n, results: [ ...(list.value.results || []), ...n.results ] }
  }catch(e){
    // 页面销毁后返回的错误直接忽略
  }
}

async function search(){ page.value = 1; await fetch(1) }

async function loadMore(){ if (!list.value.next) return; page.value += 1; await fetch(page.value) }

// 选中即跳到选品；跳转前标记页面无效，阻止后续回写
function choose(c){
  if (!c || !c.id) return
  cart.setSupplier({ id: c.id, name: c.name })
  alive = false; reqSeq++
  // 用 redirectTo 可减少历史栈干扰
  uni.redirectTo({ url: '/pages/products/search' })
}

onLoad(() => { search() })

onReachBottom(() => { loadMore() })


// 扫描相关
const { lastScan, canScan, quickScan, setScanCallback, initScanner, unRegisterBroadcast } = useBarcodeScanner()

// 业务数据
const scannedProduct = ref(null)

// 监听扫描结果变化
watch(lastScan, (newBarcode) => {
  if (newBarcode) {
    // 将扫描结果设置到输入框
    q.value = newBarcode
    // 自动触发搜索
    search()
  }
})

// 设置扫描回调
setScanCallback((barcode) => {
  console.log('入库页面收到条码:', barcode)
  handleBarcodeScanned(barcode)
})

// 初始化扫描
onMounted(() => {
  initScanner()
})

// 清理扫描
onUnmounted(() => {
  unRegisterBroadcast()
})

// 手动触发扫描
const handleScan = () => {
  quickScan()
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
  padding-top:40rpx;
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
  padding-top: 5rpx; /* 为顶部固定区域留出空间 */
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
  font-size: 20rpx;
  color: #666;
  line-height: 1.4;
}

.metabar {
  font-size: 20rpx;
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
  height:32rpx;
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



/* 输入框样式 */
.flex-input {
  flex: 1;
  height: 62rpx;
  background: #f8f8f8;
  flex-grow: 1;  /* 让输入框占据剩余空间 */
  padding: 8rpx 12rpx;  /* 内边距 */
  font-size: 26rpx;  /* 字体大小 */
  border: 1rpx solid #ccc;  /* 边框样式 */
  border-radius: 4rpx;  /* 圆角 */
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
    padding: 10rpx 10rpx;
  }
}

</style>
