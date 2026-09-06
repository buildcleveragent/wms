<template>
  <view class="page receive-cart-page">
    <view class="owner-head"><view><text>无订单收货</text><text>货主：{{cart.owner?.name||'未选择'}}</text></view><text>{{cart.items.length}} 种商品</text></view>
    <view v-if="!cart.items.length" class="empty">购物车为空，请返回扫码录入商品。</view>
    <view v-for="(item,index) in cart.items" :key="`${item.id}:${index}`" class="item-card">
      <view class="item-head"><view><text>{{item.name||item.sku||item.id}}</text><text>{{item.spec||'无规格'}} · {{item.gtin||item.sku||'-'}}</text></view><text @click="removeItem(index)">移除</text></view>
      <view class="field-row"><text>基本数量</text><input v-model="item.base_quantity" type="digit" @blur="normalizeQty(item)"/><text>{{item.base_unit_name||''}}</text></view>
      <view class="batch-grid"><input v-model.trim="item.lot_no" placeholder="批次号（可选）"/><input v-model.trim="item.mfg_date" placeholder="生产日期 YYYY-MM-DD"/><input v-model.trim="item.exp_date" placeholder="有效期 YYYY-MM-DD"/></view>
    </view>
    <view class="footer safe-bottom"><button class="continue" @click="backToProducts">继续录入</button><button class="submit" :disabled="submitting||!cart.items.length" @click="submitOrder">{{submitting?'正在提交…':'确认收货'}}</button></view>
  </view>
</template>

<script setup>
import{ref}from'vue'
import{useCart}from'@/store/cart'
import{api,createIdempotencyUuid}from'@/utils/request'
const cart=useCart(),submitting=ref(false),requestId=ref('')
function backToProducts(){uni.navigateBack({fail:()=>uni.redirectTo({url:'/pages/products/search'})})}
function normalizeQty(item){const number=Number(item.base_quantity);item.base_quantity=Number.isFinite(number)&&number>0?Number(number.toFixed(4)):0}
function removeItem(index){cart.remove(index)}
function compact(item){const result={product_id:item.id,qty:Number(item.base_quantity)};const lot=String(item.lot_no||item.batch_number||'').trim().toUpperCase(),mfg=String(item.mfg_date||item.production_date||'').trim(),exp=String(item.exp_date||item.expiry_date||'').trim();if(lot){result.lot_no=lot;result.batch_number=lot}if(mfg){result.mfg_date=mfg;result.production_date=mfg}if(exp){result.exp_date=exp;result.expiry_date=exp}return result}
async function submitOrder(){if(submitting.value)return;const invalid=cart.items.find((item)=>!(Number(item.base_quantity)>0));if(invalid){uni.showToast({title:'存在 0 数量明细，请删除或改正',icon:'none'});return}requestId.value=requestId.value||createIdempotencyUuid();submitting.value=true;try{const result=await api.submitReceiveWithoutOrder({request_id:requestId.value,owner_id:cart.owner?.id,remark:'仓库操作员手机收货',items:cart.items.map(compact)});uni.showModal({title:'收货已创建',content:`任务号：${result?.task_no||result?.task_id||'-'}`,showCancel:false});cart.clear();requestId.value='';setTimeout(()=>uni.switchTab({url:'/pages/index/index'}),500)}catch(error){uni.showToast({title:error?.message||'收货提交失败，请直接重试',icon:'none'})}finally{submitting.value=false}}
</script>

<style scoped>
.receive-cart-page{padding-bottom:160rpx}.owner-head{display:flex;align-items:flex-start;justify-content:space-between;padding-bottom:24rpx;border-bottom:1rpx solid #e2e8f0}.owner-head view text{display:block}.owner-head view text:first-child{font-size:34rpx;font-weight:750}.owner-head view text:last-child{margin-top:6rpx;color:#64748b;font-size:23rpx}.owner-head>text{color:#0f766e;font-size:23rpx}.empty{padding:100rpx 0;color:#94a3b8;text-align:center}.item-card{margin-top:20rpx;padding:22rpx;border:1rpx solid #e2e8f0;border-radius:20rpx}.item-head{display:flex;align-items:flex-start;gap:18rpx}.item-head view{flex:1}.item-head view text{display:block}.item-head view text:first-child{font-size:28rpx;font-weight:680}.item-head view text:last-child{margin-top:5rpx;color:#64748b;font-size:22rpx}.item-head>text{color:#e5484d;font-size:23rpx}.field-row{min-height:82rpx;margin-top:14rpx;border-top:1rpx solid #f1f5f9;display:flex;align-items:center;gap:12rpx;font-size:24rpx}.field-row input{width:160rpx;height:62rpx;margin-left:auto;border:1rpx solid #cbd5e1;border-radius:12rpx;text-align:center}.batch-grid{display:grid;gap:12rpx}.batch-grid input{height:72rpx;padding:0 18rpx;border:1rpx solid #dbe3ed;border-radius:12rpx;font-size:23rpx}.footer{position:fixed;left:0;right:0;bottom:0;min-height:132rpx;padding:18rpx 28rpx;border-top:1rpx solid #e2e8f0;background:#fff;display:flex;gap:14rpx}.footer button{height:86rpx;border-radius:15rpx;display:flex;align-items:center;justify-content:center;font-size:27rpx;font-weight:680}.continue{width:35%;border:1rpx solid #94a3b8;background:#fff}.submit{flex:1;background:#0f766e;color:#fff}
</style>
