<template>
  <view class="container">
    <!-- 订单头 -->
    <view class="card-first">
      <view class="row-first">
        <view class="font-bold">货主：{{ cart.owner?.name || '未选择' }} </view>
      </view>
    </view>

    <!-- 明细 -->
   <view class="content" v-if="cart.items.length">
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
  
			  
            <!-- 数量 -->
            <view class="col-label-qty">
              <text class="label-text">基本数量</text>
              <input
                class="input qty-input"
                type="number"
                v-model.number="it.base_quantity"
                min="0"
				@blur="enforceAvailable(it)"
				@change="enforceAvailable(it)"
              />
            </view>
			
			<!--基本单位 -->
			<view class="col-label-first">				            
			  <text class="label-text">基本单位</text>
			  <text class="label-text-name">{{ it.base_unit_name }}</text>
			</view>			

          </view>
		  
       </view>
      </view>
    </view>
	
	<view class="footer">
	  <!-- 操作 -->
	  <view class="button-row">
		<button class="btn-outline" @click="backToProducts">继续录入商品</button>
		<!-- <button class="btn" :disabled="!canSubmit" @click="submitOrder">提交订单</button> -->
				<button class="btn"  @click="submitOrder">提交订单</button>
	  </view>
	</view>
  </view>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useCart } from '@/store/cart'
import { api } from '@/utils/request'
import { BASE_URL } from '@/utils/request'

const cart = useCart()
const canSubmit = computed(()=> !!cart.customer && cart.items.length>0 )
console.log("cart vue111111111111111",cart.items)

// 数字格式化为两位小数
const fmt = (n)=> Number(n||0).toFixed(2)



onMounted(()=>{
  // 页面加载时给每一行补齐 orig_price/min_price
  // cart.items.forEach(console.log(p.id))
})



function printReceiveTask(taskId){
	console.log("3 printReceiveTask printReceiveTask ")
  // 后端打印页 URL，如 http://192.168.1.10:8000/console/receive_task/58/print/
  const base = BASE_URL.replace(/\/$/, '')
  // const url = `${base}/inbound/receive_task/${taskId}/print/`
  const url = `${BASE_URL}/api/inbound/receive_task/${taskId}/print/`
  
  // 在 H5（PC 浏览器）里：新开一个标签页，里面会自动 window.print()
  // #ifdef H5
  window.open(url, '_blank')
  // #endif

  // 在 APP-PLUS（打成 Android App）里：调起系统浏览器打开这个地址
  // 这样如果设备有系统级打印功能，也可以打印
  // #ifdef APP-PLUS
  try {
    plus.runtime.openURL(url)
  } catch (e) {
    uni.showToast({ title: '当前环境不支持直接打印', icon: 'none' })
  }
  // #endif
}

function backToProducts(){ uni.navigateTo({ url:'/pages/products/search' }) }

async function submitOrder(){
  // 提交前兜底校验：不允许低于
  // const bad = cart.items.find(it => {
  //   ensureItemGuard(it)
  //   return typeof it.orig_price === 'number' && it.price < it.min_price
  // })
  // if (bad) {
  //   uni.showToast({ title:'存在价格低于系统最低价的商品，请修正后再提交', icon:'none' })
  //   return
  // }

  try{
    const payload = {
      owner_id: cart.owner?.id,
      remark: '仓库操作员入库',
      items: cart.items.map(it=> ({
        product_id: it.id,
        qty: it.base_quantity,		
      }))
    }
	
	console.log("aaaaaaaaaa111222 payload=",payload)
	
    const res = await api.submitReceiveWithoutOrder(payload)
    // uni.showToast({ title: '已创建：'+ (res?.order_no||res?.id), icon:'none' })
	// const msg = res.data?.detail || res.data?.message || JSON.stringify(res.data)
	// uni.showToast({ title: msg, icon: 'none' })
	console.log("bbbbbaweqrewqrew111111111111111111 ")
	console.log("bbbbbbbbbbb3333333311111111111111111111111 res=",res)
	uni.showToast({ title: "aaaaaabbb123", icon: 'none' })

	// 拿后端返回的数据
	// const data = res.data || {}
	const taskId = res.task_id       // ⭐ 当前入库任务ID
	const taskNo = res.task_no

	// 提示信息
	const msg = `收货成功，任务号：${taskNo || taskId}`
	uni.showToast({ title: msg, icon: 'none' })

	// 如果有 taskId，就导出 Excel
	if (taskId) {
	  const exportUrl = `${BASE_URL}/api/inbound/receive_task/${taskId}/export_excel/`

	  // H5：直接打开下载
	  // #ifdef H5
	  // window.open(exportUrl, '_blank')
printReceiveTask(taskId)
		console.log("excel url ",url)
	  console.log("excel 12311111111111111 H5H5H5H5H5H5 ")
	  // #endif

	  // App：下载到本地后用系统应用打开
	  // #ifdef APP-PLUS
	  uni.downloadFile({
		url: exportUrl,
		header: {
		  'Authorization': 'Bearer ' + TOKEN,   // 如果你的导出接口需要鉴权
		},
		success: (dlRes) => {
		  if (dlRes.statusCode === 200) {
			const filePath = dlRes.tempFilePath
			 console.log("excel APP-PLUS APP-PLUS APP-PLUS ")
			uni.openDocument({
			  filePath,
			  fileType: 'xlsx',
			  success: () => {
				console.log('open excel success')
			  }
			})
		  } else {
			uni.showToast({ title: 'Excel 下载失败', icon: 'none' })
		  }
		},
		fail: () => {
		  uni.showToast({ title: 'Excel 下载失败', icon: 'none' })
		}
	  })
	  // #endif
	}
	
	console.log("1 printReceiveTask printReceiveTask ")

	printReceiveTask(taskId)
	
	console.log("2 printReceiveTask printReceiveTask ")
	
    cart.clear()
    uni.switchTab({ url:'/pages/index/index' })
  }catch(e){
    console.error(e)
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
  padding-top: 60rpx; /* 为顶部固定区域留出空间 */
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