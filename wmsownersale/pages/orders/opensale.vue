<template>
  <view class="p-4">
    <view class="title">开销售单</view>

    <!-- 步骤条 -->
    <view class="steps">
      <text :class="{ on: step===1 }">1 选择客户</text>
      <text :class="{ on: step===2 }">2 选品下单</text>
    </view>

    <!-- Step 1：选择货主 + 客户 -->
    <view v-if="step===1">
      <view class="card">
        <view class="row">
          <view class="font-bold">所属货主</view>
          <picker mode="selector" :range="owners" range-key="name" @change="onPickOwner">
            <view>{{ currentOwnerName || '请选择' }}</view>
          </picker>
        </view>
      </view>

      <view class="bar">
        <input class="input" v-model="custQ" placeholder="客户编号 / 名称" @confirm="searchCustomers" />
        <button class="btn-outline" @click="searchCustomers">搜索</button>
      </view>

      <view v-for="(c,i) in customers" :key="c?.id ?? i" class="card" @click="chooseCustomer(c)">
        <view class="row">
          <view class="font-bold">{{ c?.name }}</view>
          <view class="badge">ID: {{ c?.id }}</view>
        </view>
        <view class="text-gray">{{ c?.code }}</view>
      </view>

      <view style="margin-top:16rpx">
        <button class="btn" :disabled="!customer" @click="toStep2">下一步：选品</button>
      </view>
    </view>

    <!-- Step 2：搜索商品 + 明细 + 提交 -->
    <view v-else>
      <view class="card">
        <view class="row"><view class="font-bold">货主</view><view>{{ currentOwnerName || '-' }}</view></view>
        <view class="row" style="margin-top:8rpx"><view class="font-bold">客户</view><view>{{ customer?.name || '-' }}</view></view>
      </view>

      <view class="bar">
        <input class="input" v-model="prodQ" placeholder="商品名 / 编码 / 条码" @confirm="searchProducts" />
        <button class="btn-outline" @click="searchProducts">搜索</button>
      </view>

      <view v-for="(p,i) in products" :key="p?.id ?? i" class="card">
        <view class="row">
          <view class="font-bold">{{ p?.name }}</view>
          <view class="badge">¥ {{ Number(p?.price ?? 0) }}</view>
        </view>
        <view class="text-gray">{{ p?.sku }}</view>
        <view class="row" style="margin-top:12rpx">
          <view class="text-gray">可用：{{ p?.stock ?? '—' }}</view>
          <button class="btn" @click="addItem(p)">加入</button>
        </view>
      </view>

      <view class="card" v-if="items.length">
        <view class="row font-bold">
          <view style="flex:3">商品</view>
          <view style="flex:2">单价</view>
          <view style="flex:2">数量</view>
          <view style="flex:2;text-align:right">小计</view>
        </view>
        <view v-for="(it,idx) in items" :key="it.product_id ?? idx" class="row" style="padding:10rpx 0">
          <view style="flex:3">{{ it.name || it.sku || it.product_id }}</view>
          <view style="flex:2">¥ <input class="input" style="margin:0" type="number" v-model.number="it.price" min="0" /></view>
          <view style="flex:2">
            <input class="input" style="margin:0" type="number" v-model.number="it.qty" min="0" @input="normalizeQty(idx)" />
          </view>
          <view style="flex:2;text-align:right">¥ {{ (it.qty||0) * (it.price||0) }}</view>
        </view>
        <view class="row" style="margin-top:10rpx">
          <view class="font-bold">合计</view>
          <view class="font-bold">¥ {{ totalAmount }}</view>
        </view>
      </view>

      <view class="row" style="margin-top:16rpx">
        <button class="btn-outline" @click="step=1">返回选客户</button>
        <button class="btn" :disabled="!canSubmit" @click="submitOrder">提交订单</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, computed } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

// 步骤控制
const step = ref(1)

// --------- 货主 ----------
const owners = ref([])           // [{id,name}]
const ownerIndex = ref(-1)
const currentOwnerId   = computed(()=> owners.value?.[ownerIndex.value]?.id ?? null)
const currentOwnerName = computed(()=> owners.value?.[ownerIndex.value]?.name ?? '')

async function loadOwners(){
  try{
    // 需要后端提供：GET /api/catalog/owners?mine=1&search=
    const res = await (api.myOwners ? api.myOwners('') : Promise.resolve([]))
    const arr = Array.isArray(res?.results) ? res.results : (Array.isArray(res)?res:[])
    owners.value = arr
    if(arr.length === 1){ ownerIndex.value = 0 }
  }catch(e){ owners.value = [] }
}
function onPickOwner(e){ ownerIndex.value = Number(e?.detail?.value || 0); searchCustomers() }

// --------- 客户 ----------
const custQ = ref('')
const customer = ref(null)       // {id,name}
const customerList = ref({ results:[] })
const customers = computed(()=> customerList.value.results || [])

async function searchCustomers(){
  // 需要支持：/api/catalog/customers?mine=1&owner_id=&search=
  const res = await api.customers(custQ.value || '', 1, currentOwnerId.value || undefined, true)
  customerList.value = Array.isArray(res)
    ? { results: res }
    : (res?.results ? res : { results: [] })
}
function chooseCustomer(c){
  if(!c?.id) return
  customer.value = { id: c.id, name: c.name, code: c.code }
  uni.showToast({ title: '客户已选择：' + (c.name || c.code), icon: 'none' })
}
function toStep2(){ if(!customer.value) return; step.value = 2; searchProducts() }

// --------- 商品 ----------
const prodQ = ref('')
const productList = ref({ results:[] })
const products = computed(()=> productList.value.results || [])

async function searchProducts(){
  // /api/catalog/products?search=
  const res = await api.products(prodQ.value || '', 1)
  productList.value = Array.isArray(res)
    ? { results: res }
    : (res?.results ? res : { results: [] })
}

const items = ref([]) // [{product_id, sku, name, price, qty}]
function addItem(p){
  if(!p?.id) return
  const exist = items.value.find(x=> x.product_id === p.id)
  if(exist){ exist.qty += 1; return }
  items.value.push({ product_id: p.id, sku: p.sku, name: p.name, price: Number(p.price||0), qty: 1 })
}
function normalizeQty(i){
  const it = items.value[i]; if(!it) return
  it.qty = Math.max(0, Number(it.qty)||0)
}
const totalAmount = computed(()=> items.value.reduce((a,b)=> a + (b.qty||0)*(b.price||0), 0))
const canSubmit   = computed(()=> !!customer.value && items.value.some(x=> x.qty>0))

// --------- 提交 ----------
async function submitOrder(){
  try{
    const payload = {
      owner_id: currentOwnerId.value,
      customer_id: customer.value?.id,
      remark: '业务员下单',
      items: items.value.map(it => ({ product_id: it.product_id, qty: it.qty, price: it.price }))
    }
    const res = await api.createOutboundOrder(payload)
    uni.showToast({ title: '下单成功：' + (res?.order_no || res?.id || ''), icon:'none' })
    // 清空并返回第1步
    items.value = []
    step.value = 1
  }catch(e){ console.error(e) }
}

onLoad(()=>{ loadOwners(); searchCustomers() })
</script>

<style>
.title{ font-weight:700; font-size:36rpx; margin:16rpx 0 24rpx }
.steps{ display:flex; gap:20rpx; margin-bottom:16rpx; color:#9ca3af }
.steps .on{ color:#111; font-weight:600 }
.app .btn{ padding:10rpx 20rpx; background:#3b82f6; color:#fff; border-radius:8rpx }
.btn-outline{ padding:10rpx 20rpx; border:1rpx solid #cbd5e1; border-radius:8rpx; color:#111; background:#fff }
.input{ border:1rpx solid #e5e7eb; border-radius:8rpx; padding:16rpx; margin:16rpx 0; background:#fff }
.card{ background:#fff; border:1rpx solid #e5e7eb; border-radius:12rpx; padding:20rpx; margin:16rpx 0 }
.text-gray{ color:#6b7280 }
.bar{ display:flex; gap:16rpx; align-items:center }
.row{ display:flex; justify-content:space-between; align-items:center }
.badge{ background:#eef2ff; color:#3730a3; border-radius:9999rpx; padding:6rpx 14rpx; font-size:24rpx }
</style>
