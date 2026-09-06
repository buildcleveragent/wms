<template>
  <view class="page inventory-page">
    <view class="search-panel">
      <ScanInput v-model="search" placeholder="扫描或输入商品条码、名称、SKU" @scan="runSearch" @confirm="runSearch" />
      <view class="scope-line"><text>查询范围：当前仓库</text><button :disabled="loading" @click="runSearch">查询</button></view>
    </view>
    <view v-if="loading && !rows.length" class="state">正在查询库存…</view>
    <view v-else-if="!rows.length" class="state">暂无库存数据</view>
    <view v-for="(item,index) in rows" :key="rowKey(item,index)" class="inventory-card">
      <view class="product-head"><view><text>{{item.product_name||'-'}}</text><text>{{item.product_code||item.product_sku||'-'}} · {{item.product_spec||'无规格'}}</text></view><text>{{item.base_unit||''}}</text></view>
      <view class="owner-line">{{item.owner_name||'-'}} · {{item.warehouse_name||'当前仓库'}}</view>
      <view class="quantity-grid"><view><text>可用</text><strong>{{fmtQty(item.available_qty_display||item.available_qty)}}</strong></view><view><text>现有</text><strong>{{fmtQty(item.onhand_qty_display||item.onhand_qty)}}</strong></view><view><text>分配</text><strong>{{fmtQty(item.allocated_qty_display||item.allocated_qty)}}</strong></view><view><text>锁定</text><strong>{{fmtQty(item.locked_qty_display||item.locked_qty)}}</strong></view></view>
      <text v-if="Number(item.damaged_qty||0)>0" class="damaged">残次数量 {{fmtQty(item.damaged_qty_display||item.damaged_qty)}}</text>
    </view>
    <button v-if="hasMore" class="load-more" :disabled="loading" @click="load(true)">{{loading?'加载中…':'加载更多'}}</button>
  </view>
</template>

<script setup>
import{ref}from'vue'
import{onShow}from'@dcloudio/uni-app'
import ScanInput from '@/components/ScanInput.vue'
import{useAuth}from'@/store/auth'
import{api}from'@/utils/request'
const auth=useAuth(),search=ref(''),rows=ref([]),page=ref(1),loading=ref(false),hasMore=ref(false)
function rowKey(item,index){return`${item.warehouse_id||''}:${item.owner_id||''}:${item.product_id||''}:${index}`}
function fmtQty(value){const number=Number(value);return Number.isFinite(number)?String(Number(number.toFixed(4))):'-'}
async function load(append=false){if(loading.value)return;loading.value=true;const target=append?page.value+1:1;try{const result=await api.companyInventorySummary({mode:'warehouse',warehouse_id:auth.user?.warehouse_id||'',owner_id:'',search:search.value,page:target,page_size:20});const list=Array.isArray(result?.results)?result.results:[];rows.value=append?[...rows.value,...list]:list;page.value=target;hasMore.value=Boolean(result?.next)}catch(error){uni.showToast({title:error?.message||'库存查询失败',icon:'none'})}finally{loading.value=false}}
function runSearch(){load(false)}
onShow(async()=>{await auth.loadProfile().catch(()=>null);if(!rows.value.length)await load(false)})
</script>

<style scoped>
.inventory-page{padding-top:24rpx}.search-panel{position:sticky;top:0;z-index:2;padding:4rpx 0 18rpx;background:#fff}.scope-line{margin-top:12rpx;display:flex;align-items:center;justify-content:space-between;color:#64748b;font-size:22rpx}.scope-line button{min-height:64rpx;padding:0 24rpx;border-radius:13rpx;background:#0f766e;color:#fff;font-size:23rpx;display:flex;align-items:center}.state{padding:100rpx 0;color:#94a3b8;text-align:center}.inventory-card{margin-top:18rpx;padding:22rpx;border:1rpx solid #e2e8f0;border-radius:20rpx}.product-head{display:flex;align-items:flex-start;justify-content:space-between;gap:18rpx}.product-head view{flex:1}.product-head view text{display:block}.product-head view text:first-child{font-size:29rpx;font-weight:700}.product-head view text:last-child{margin-top:5rpx;color:#64748b;font-size:22rpx}.product-head>text{color:#0f766e;font-size:23rpx}.owner-line{margin-top:14rpx;padding-top:14rpx;border-top:1rpx solid #f1f5f9;color:#64748b;font-size:22rpx}.quantity-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10rpx;margin-top:18rpx}.quantity-grid view{padding:14rpx 4rpx;border-radius:12rpx;background:#f8fafc;text-align:center}.quantity-grid text,.quantity-grid strong{display:block}.quantity-grid text{color:#64748b;font-size:20rpx}.quantity-grid strong{margin-top:3rpx;font-size:27rpx}.quantity-grid view:first-child strong{color:#0f766e}.damaged{display:block;margin-top:14rpx;color:#e5484d;font-size:22rpx}.load-more{width:100%;height:84rpx;margin-top:20rpx;border:1rpx solid #cbd5e1;border-radius:15rpx;background:#fff;color:#0f766e;display:flex;align-items:center;justify-content:center;font-size:24rpx}
</style>
