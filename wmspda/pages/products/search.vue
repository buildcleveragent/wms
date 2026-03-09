<template>
  <view class="container">
    <view class="card-first">
      <view class="row-first">
        <view class="font-bold">货主：{{ cart.owner?.name || '未选择' }} </view>
      </view>
    </view>
  
    <view class="bar">
      <input class="input flex-input" v-model="q" placeholder="名称/编码/条码 可输入部分内容"  @confirm="search" />
      <button class="btn-outline" @click="search">搜索</button>
      <button class="btn-outline" @click="quickScan">扫码</button>
    </view>

    <view class="content">
      <view v-for="(p, i) in rows" :key="p?.id ?? i" :class="['row item', { 'odd': i % 2 === 0 }]">
        <!-- 商品图片 -->
        <view class="col-image">
          <image :src="p.product_image_url" mode="aspectFill" class="product-image" />
        </view> 

        <!-- 商品信息 -->
        <view class="col-info">
          <view class="name-price-row">
            <view class="product-name">{{ p?.name }}</view>  
          </view>

          <view class="meta-container">
            <view class="baradd">
              <text class="metabar">条码: {{ p.gtin }} </text>    
              <button class="btnnew" @click="add(p)">添加</button>    
            </view>  
          </view>

			<!-- 收货单位选择（单选按钮） -->
		 <view v-if="p.unitOptions && p.unitOptions.length" class="pkg-radio-block">
		  <text class="pkg-label">收货单位</text>
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

          <!-- 数量与价格 -->
          <view class="price-qty">
			  	  
				 <view class="col-label-qty">
					<text class="label-text">收货数量</text>
					<input class="input qty-input-sh"  
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
						<text class="label-text">收货单位</text>
						<text class="label-text-name">{{ p.unitOptions[p.selectedUnitIndex].label }}</text>
				 </view>  
				    

				<view class="col-label-qty">
				  <text class="label-text">基本数量</text>
				  <!-- <input class="input qty-input" type="number" :value="qtyMap[p.id] ?? 0" @input="(e) => setQty(p.id, e?.detail?.value ?? e?.target?.value)" min="0" /> -->
				  <text class="qty-input-text" type="number" >{{ (p.unitOptions[p.selectedUnitIndex].multiplier * qtyMap[p.id])||0}}</text>
				</view>
				
				<view class="col-label-first">
				  <text class="label-text">基本单位</text>
				  <text class="label-text-name">{{ p.base_unit_name }}</text>
				</view>   
          </view>
		  <!-- 批次号 -->
		  <view class="col-label-batch">
		    <text class="label-text-batch">商品批次</text>
		    <input class="input qty-input-sh" type="text" :value="batchMap[p.id] ?? ''" @input="(e) => setBatch(p.id, e?.detail?.value ?? e?.target?.value)" placeholder="输入商品批次号" />
		<!-- 	<button class="btn-outline-sm" @click="batchScan(p.id)">扫码</button> -->
		  </view>
		  
		  <!-- 生产日期 -->
		<!-- 生产日期 -->
		<view class="col-label-batch">
		  <text class="label-text-batch">生产日期</text>
		  
		  <view class="date-input-group">
		    <input class="input date-input" type="text" 
		           :value="productionDateMap[p.id] || ''" 
		           @input="(e) => setProductionDate(p.id, getInputValue(e))" 
		           placeholder="YYYY-MM-DD" />
		    <picker mode="date" :value="productionDateMap[p.id] || ''" 
		            @change="(e) => setProductionDate(p.id, e.detail.value)" 
		            class="picker-button">
		      <view class="picker-button-text">选择</view>
		    </picker>
		  </view>
		  
	<!-- 	  <button class="btn-outline-sm" @click="productScan">扫码</button> -->
		</view>
		
		<!-- 有效截止日期 -->
		<view class="col-label-batch">
		  <text class="label-text-batch">有效截止</text>
		  <view class="date-input-group">
		    <input class="input date-input" type="text" 
		           :value="expiryDateMap[p.id] || ''" 
		           @input="(e) => setExpiryDate(p.id, getInputValue(e))" 
		           placeholder="YYYY-MM-DD" />
		    <picker mode="date" :value="expiryDateMap[p.id] || ''" 
		            @change="(e) => setExpiryDate(p.id, e.detail.value)" 
		            class="picker-button">
		      <view class="picker-button-text">选择</view>
		    </picker>
		  </view>
<!-- 		 <button class="btn-outline-sm" @click="expireScan">扫码</button> -->
		</view>

        </view>
      </view>
    </view>

    <view class="footer">
      <button class="btn-outline" @click="goCart">
        <text>查看、提交入库单：数量:{{cart.totalQty}} </text> 
      </button>
    </view>
  </view>
</template>


<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch,reactive } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
import { api } from '@/utils/request'
import { scanOne } from '@/utils/scan'
import { useCart } from '@/store/cart'

const q = ref('')
const qtyInputRefs = reactive<Record<string | number, any>>({})
const lastQtyMap = reactive<Record<string | number, string>>({})

const list = ref<{count:number; next:string|null; previous:string|null; results:any[]}>({
  count:0,
  next:null,
  previous:null,
  results:[]
})
const rows = computed(()=> list.value.results || [])
console.log("rows",rows.value)

const cart = useCart()
console.log("cart1111111112222222222222222",cart)

const fmt = (n:any)=> Number(n||0).toFixed(2)

// =========================
// 数量输入：按商品 id 记录期望数量
// =========================
const qtyMap = ref<Record<number, number>>({})
function setQty(pid:number, v:any){
  const n = Math.max(0, Number(v) || 0)
  qtyMap.value = { ...qtyMap.value, [pid]: n }
}

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



function getBasicQty(pid:number){
  const n = Number(qtyMap.value[pid])
  return Number.isFinite(n) && n > 0 ? n : 1
}


// =========================
// 包装选择：每个商品一组选中的包装index
// =========================

function getDesiredQty(pid:number){
  const n = Number(qtyMap.value[pid])
  return Number.isFinite(n) && n > 0 ? n : 1
}

const pkgSelIndexMap = ref<Record<number, number>>({})

function initPkgSelectionForProducts(products:any[]){
  const nextMap:Record<number, number> = { ...pkgSelIndexMap.value }
  products.forEach((prod:any)=>{
    if (!prod || !prod.id) return
    if (!('packaging' in prod) || !Array.isArray(prod.packaging) || !prod.packaging.length) return
    // 如果这个商品还没初始化选中项，则默认选第0个包装
    if (nextMap[prod.id] === undefined) {
      nextMap[prod.id] = 0
    }
  })
  pkgSelIndexMap.value = nextMap
}

function getPkgIndex(pid:number, pkgs:any[]){
  const idx = pkgSelIndexMap.value[pid]
  if (idx === undefined || idx === null) return 0
  if (idx >= pkgs.length) return 0
  return idx
}

// picker / radio 改变选项
function onPkgPickerChange(pid:number, newIndex:any){
  const idx = Number(newIndex)
  pkgSelIndexMap.value = { ...pkgSelIndexMap.value, [pid]: idx }
}

// 取当前选中包装对象
function getSelectedPkg(pid:number, pkgs:any[]){
  const idx = getPkgIndex(pid, pkgs)
  return pkgs[idx] || null
}

// 用于展示 picker 里“当前已选包装”的文字
function displaySelectedPkg(pid:number, pkgs:any[]){
  const sel:any = getSelectedPkg(pid, pkgs)
  if(!sel) return ''
  // 例: "箱 ×12"
  return `${sel.uom_type} ×${sel.quantity_in_base}`
}

// =========================
// 搜索
// =========================
async function search(){
  // 后端按owner过滤；传 owner.id
  const res:any = await api.receive_products(q.value, 1, cart.owner.id||undefined)

  // res 可能是分页结构，也可能就是数组
  list.value = Array.isArray(res)
    ? { count: res.length, next:null, previous:null, results: res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}

function getInputValue(e: any): string {
    return e?.detail?.value ?? e?.target?.value ?? ''
}

function add(p: any) {
  if (!p?.id) return;
  console.log("function add p?.id =",p.id)
  const desired = getDesiredQty(p.id);
  const batch = batchMap.value[p.id] || ''; // Get the batch from batchMap
  const productionDate = productionDateMap.value[p.id] || ''; // Get the production date
  const expiryDate = expiryDateMap.value[p.id] || ''; // Get the expiry date
  p.base_quantity = p.unitOptions[p.selectedUnitIndex]?.multiplier * desired || 0; // Calculate base quantity
  console.log("1 p.base_quantity",p.base_quantity)

  // Check if the item is already in the cart
  const idx = cart.items.findIndex((x: any) => x.id === p.id);

  if (idx > -1) {
    // Accumulate base quantity if the item is already in the cart
    const curBaseQty = cart.items[idx].base_quantity || 0; // Get current base quantity in cart
	console.log("curBaseQty curBaseQty=",curBaseQty)
    cart.setbase_quantity(idx, curBaseQty + p.base_quantity); // Accumulate base quantity instead of regular quantity
	  console.log("idx > -1 idx > -1idx > -1idx > -12 cart.items[idx].base_quantity",cart.items[idx].base_quantity)
	  console.log('=== 购物车状态 ===');
	  console.log('物品列表:', JSON.stringify(cart.items, null, 2));
	    // console.log('base_quantity:', JSON.stringify(cart.items[0].base_quantity, null, 2));
	  console.log('总数量（收货）:', cart.totalQty);
	  console.log('总金额:', cart.totalAmount);
	  console.log('================');
  } else {
    // Add item to the cart
	console.log("idx<=-1,idx<=-1idx<=-1idx<=-1: 3 baseQuantity",p.base_quantity)
    cart.addItem({
      id: p.id,
      sku: p.sku,
      name: p.name,
      qty: desired,
      product_image_url: p.product_image_url,
      gtin: p.gtin,
      aux_uom_name: p.aux_uom_name,
      base_unit_name: p.base_unit_name,
      aux_qty_in_base: p.aux_qty_in_base,
      packaging: p.packaging,
      unitOptions: p.unitOptions,
      selectedUnitIndex: p.selectedUnitIndex,
      batch_number: batch, // Add batch number to the cart item
      production_date: productionDate, // Add production date
      expiry_date: expiryDate, // Add expiry date
	  base_quantity: p.base_quantity,
    });
		// console.log("4 cart.baseQuantity=",cart[1].owner)
  console.log('=== 购物车状态 ===');
  console.log('物品列表:', JSON.stringify(cart.items, null, 2));
    // console.log('base_quantity:', JSON.stringify(cart.items[0].base_quantity, null, 2));
  console.log('总数量（收货）:', cart.totalQty);
  console.log('总金额:', cart.totalAmount);
  console.log('================');
  }

  uni.showToast({
    title: '已加入：' + (p.name || p.sku) + ' × ' + desired,
    icon: 'none'
  });
}


function goCart(){
  uni.navigateTo({ url:'/pages/orders/cart' })
}

// =========================
// 扫描相关
// =========================
let currentScan="q"

const { lastScan, canScan, quickScan, setScanCallback, initScanner, unRegisterBroadcast } = useBarcodeScanner()
const scannedProduct = ref(null)

// 监听扫描结果变化：自动用条码搜索
watch(lastScan, (newBarcode) => {
  if (newBarcode) {
   if (currentScan=="q"){
	   q.value = newBarcode
	   search()
   }if (currentScan=="batch") {
   	   batch.value = newBarcode
   } else {
   	
   }
	
  }
})

// 手动触发扫描
const handleScan = () => {
  quickScan()
}

function batchScan()
{
	currentScan="batch"
	quickScan()
}


// 扫描回调（也可在这里直接add）
setScanCallback((barcode) => {
  console.log('入库页面收到条码:', barcode)
  handleBarcodeScanned(barcode)
})
async function handleBarcodeScanned(code:string){
  q.value = code
  await search()
}

// =========================
// 生命周期
// =========================
onLoad(()=>{
  if(!cart.owner){
    uni.redirectTo({ url: '/pages/inbound/createwithoutorder/selectowner' })
    return
  }
  search()
})

onMounted(() => {
  initScanner()
})

onUnmounted(() => {
  unRegisterBroadcast()
})
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

// 方式二：更简洁的写法（推荐）
const batchMap = ref<Record<number, string>>({})
const productionDateMap = ref<Record<number, string>>({})
const expiryDateMap = ref<Record<number, string>>({})

// 设置批次号
function setBatch(pid: number, value: any) {
  if (pid == null) return
  batchMap.value = { 
    ...batchMap.value, 
    [pid]: String(value || '') 
  }
}

// 设置生产日期
function setProductionDate(pid: number, value: any) {
  if (pid == null) return
  productionDateMap.value = { 
    ...productionDateMap.value, 
    [pid]: String(value || '') 
  }
}

// 设置有效截止日期
function setExpiryDate(pid: number, value: any) {
  if (pid == null) return
  expiryDateMap.value = { 
    ...expiryDateMap.value, 
    [pid]: String(value || '') 
  }
}

// 获取商品的生产日期（带默认值）
function getProductionDate(pid: number): string {
  return productionDateMap.value[pid] || ''
}

// 获取商品的有效日期（带默认值）
function getExpiryDate(pid: number): string {
  return expiryDateMap.value[pid] || ''
}

// 获取商品的批次号（带默认值）
function getBatch(pid: number): string {
  return batchMap.value[pid] || ''
}



</script>
<style scoped>
/* 搜索栏样式 */

.multi-line {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
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
  padding: 20rpx 20rpx;
  box-shadow: 0 4rpx 6rpx rgba(50, 150, 230, 0.2);
  white-space: nowrap;
  width: 100rpx;
  height: 30rpx;
  margin-right:20rpx;
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

.btn-outline-sm {
  flex: none;
  width: 200rpx;
  padding: 0 24rpx;
  height: 62rpx;
  line-height: 62rpx;
  border: 1rpx solid #007AFF;
  color: #007AFF;
  border-radius: 8rpx;
  background: transparent;
  font-size: 28rpx;
  margin-left:20rpx;
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
/* .bar {
  display: flex;
  align-items: stretch;
  justify-content: flex-start;
  gap: 10rpx;
  padding-top:40rpx;
} */

/* 搜索栏：固定在顶部信息(card-first)下面，避免被遮挡 */


/* 输入框样式 */
.flex-input {
  flex: 1;
  height: 62rpx;
  background: #f8f8f8;
  flex-grow: 1;
  padding: 8rpx 12rpx;
  font-size: 30rpx;
  border: 1rpx solid #ccc;
  border-radius: 4rpx;
}

/* 按钮样式 */
.btn-outline {
  padding: 8px 16px;
  font-size: 16px;
  border: 1px solid #007aff;
  border-radius: 4px;
  background-color: white;
  cursor: pointer;
}

/* 按钮对齐 */
.btn-outline:first-of-type {
  margin-left: 10px;
}

.container {
  display: flex;
  flex-direction: column;
  height: 100vh;
/*  padding-top: 200rpx; */
  overflow: hidden;
  margin-left:2rpx;
  margin-right:2rpx;
}

/* 顶部固定 */
/* .card-first {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  background-color: white;
  z-index: 100;
  padding: 0rpx;
  box-shadow: 0 2rpx 10rpx rgba(0, 0, 0, 0.1);
  height: 100rpx;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  justify-content: center;
} */

/* 中间可滚动区域 */
/* .content {
  flex: 1;
  overflow-y: auto;
  padding-top: 5rpx;
  padding-bottom: 80rpx;
  padding-left: 2rpx;
  padding-right: 2rpx;
} */
.content {
  flex: 1;
  overflow-y: auto;

  /* 100rpx(card-first) + 96rpx(搜索条大概高度) */
  padding-top: 200rpx;

  padding-bottom: 80rpx;
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
  font-size: 30rpx;
  color: #666;
  line-height: 1.4;
  flex:1;
}

/* 价格和数量区域 */
.price-qty {
  display: flex;
  justify-content: space-between;
}

/* 列布局 */
.col-label-first {
  width: 20%;
  text-align: left;
}

.col-label-price {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 28%;
  text-align: center;
}

.col-label-qty {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 30%;
  text-align: left;
}

.col-label-batch {
  display: flex;
  flex-direction: row;
  align-items: center;
  width: 80%;
  text-align: left;
}

.col-label-last {
  display: flex;
  flex-direction: column;
  align-items: right;
  width: 32%;
  text-align: right;
  margin-right:20rpx;  
}

.col-label-first .label-text-name {
  display: block;
  font-size: 35rpx;
  color: #777;
  padding-left:30rpx;
  margin-top:5rpx;
}

.label-text {
  display: block;
  font-size: 26rpx;
  color: #777;
}


.label-text-batch {
  display: block;
  font-size: 30rpx;
  width: 180rpx;
  color: #777;
}


.label-text-sh {
  display: block;
  font-size: 35rpx;
  color: red;
  text-align: center;
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
}

.qty-input {
  width: 100%;
  height:50rpx;
  padding: 5rpx;
  font-size: 35rpx;
  border: 1rpx solid #ccc;
  border-radius: 5rpx;
  text-align: right;
  margin-top:5rpx;
}

.qty-input-text {
  width: 90%;
  height:50rpx;
  padding: 5rpx;
  font-size: 35rpx;

  border-radius: 5rpx;
  text-align: center;
  margin-top:5rpx;
}


.qty-input-sh {
  color:red;
  width: 100%;
  height:50rpx;
  padding: 5rpx;
  font-size: 35rpx;
  border: 1rpx solid #ccc;
  border-radius: 5rpx;
  text-align: right;
  margin-top:5rpx;
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
  display: flex;
  flex-direction: column;
}


/* ✅ 统一用变量控制布局（手机/电脑都不遮挡） */
.container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding-top: 0;     /* ✅ 不要再顶 200rpx */
  overflow: hidden;
  margin-left: 2rpx;
  margin-right: 2rpx;
}


/* 顶部固定：货主信息 */
.card-first {
  position: fixed;
  top: 2;           /* ✅ 不要再 top:80rpx */
  left: 0;
  right: 0;
  width: 100%;
  height: 60rpx;    /* ✅ 更矮 */
  background-color: white;
  z-index: 1000;
  padding: 6rpx 10rpx;  /* ✅ 更小 */
  box-shadow: 0 2rpx 10rpx rgba(0, 0, 0, 0.1);
  box-sizing: border-box;
  display: flex;
  align-items: center;
}
.row-first {
  display: flex;
  align-items: center;
  width: 100%;
  height: 60rpx;   /* ✅ 去掉 60rpx */
}


/* ✅ 搜索栏固定在 header 下面（关键） */
.bar {
  position: fixed;
  top: 160rpx;        /* ✅ 紧贴 header 下面（对应 card-first 高度） */
  left: 0;
  right: 0;
  z-index: 101;
  background: #fff;

  display: flex;
  align-items: center;
  gap: 10rpx;

  padding: 6rpx 10rpx;   /* ✅ 更小 */
  box-sizing: border-box;
}


/* ✅ 中间滚动区：顶部让位给 header + bar */
.content {
  flex: 1;
  overflow-y: auto;
  padding-top: 200rpx;   /* ✅ 64 + ~74 + 10 */
  padding-bottom: 80rpx;
  padding-left: 2rpx;
  padding-right: 2rpx;
}


/* 针对电脑浏览器的额外调整 */
/* @media (min-width: 768px) { */
/*  .container {
    padding-top: 0px;
  }
  */
/*  .card-first {
    top:80rpx;
    height: 60px;
    padding: 10px;
  }
} */

/* 针对移动设备的优化 */
/* @media (max-width: 767px) { */
/*  .container {
    padding-top: 0px;
  }
  */
/*  .card-first {	  
    height: 60rpx;
    padding: 10rpx 10rpx;
  }
} */

@media (min-width: 768px) {
  .container {
    --header-top: 80rpx;
    --header-h: 44px;
    --bar-h: 52px;
  }
}


/* 手机：保持默认即可；如果你想更高/更低，在这里改变量 */
@media (max-width: 767px) {
  .container {
    --header-top: 0px;
    --header-h: 60rpx;
    --bar-h: 86rpx;
  }
}



/* 日期输入组 */
.date-input-group {
  display: flex;
  width: 100%;
  margin-top: 5rpx;
}

.date-input {
  flex: 1;
  height: 50rpx;
  padding: 5rpx;
  font-size: 26rpx;
  border: 1rpx solid #ccc;
  border-radius: 5rpx 0 0 5rpx;
  text-align: center;
}

.picker-button {
  width: auto;
  
  background: #007AFF;
  color: white;
  border-radius: 0 5rpx 5rpx 0;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left:15rpx;
}

.picker-button-text {
  padding: 0 20rpx;
  font-size: 24rpx;
  line-height: 50rpx;
  height: 50rpx;
}
</style>