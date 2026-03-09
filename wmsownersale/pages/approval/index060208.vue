<template>
  <view class="p-4">
    <view class="tabs row">
      <button class="tab" :class="{active: tab==='PENDING'}" @click="switchTab('PENDING')">待审核</button>
      <button class="tab" :class="{active: tab==='APPROVED'}" @click="switchTab('APPROVED')">已通过</button>
      <button class="tab" :class="{active: tab==='REJECTED'}" @click="switchTab('REJECTED')">已驳回</button>
    </view>

    <view class="bar">
      <input class="input" v-model="q" placeholder="单号 / 客户名 / 客户编码" @confirm="search"/>
      <button class="btn-outline" @click="search">搜索</button>
    </view>

    <view v-for="(o,i) in rows" :key="o?.id ?? i" class="card">
      <view class="row">
        <view class="font-bold">{{ o.order_no || ('#'+o.id) }}</view>
        <view class="badge">{{ statusText(o) }}</view>
      </view>
      <view class="text-gray">客户：{{ o.customer_name || o.customer_id }}</view>
      <view class="text-gray">数量：{{ o.total_qty ?? '—' }} · 金额：¥ {{ o.total_amount ?? 0 }}</view>
      <view class="text-gray">业务日期：{{ o.biz_date }}</view>
      <view class="row mt-2">
        <template v-if="tab==='PENDING'">
          <button class="btn btn-sm" @click="approve(o)">审核通过</button>
          <button class="btn-outline btn-sm" @click="cancel(o)">取消</button>
        </template>
        <template v-else-if="tab==='APPROVED'">
          <!-- <button class="btn btn-sm" @click="unapprove(o)">撤审</button> -->
        </template>
        <template v-else>
          <button class="btn btn-sm" @click="approve(o)">审核通过</button>
        </template>
      </view>
    </view>

    <view class="row" v-if="list.next" style="margin-top:16rpx">
      <button class="btn-outline" @click="loadMore">加载更多</button>
    </view>

    <view v-if="!rows.length && !loading" class="text-gray mt-4">暂无数据</view>
  </view>
</template>

<script setup>
import { ref, computed } from 'vue'
import { onLoad, onPullDownRefresh, onReachBottom, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'

const q = ref('')
const tab = ref('PENDING')
const list = ref({ count:0, next:null, previous:null, results:[] })
const rows = computed(()=> list.value.results || [])
const page = ref(1)
const loading = ref(false)

let alive = true
let reqSeq = 0
onUnload(()=>{ alive=false; reqSeq++ })

function normalize(res){
  return Array.isArray(res)
    ? { count:res.length, next:null, previous:null, results:res }
    : (res?.results ? res : { count:0, next:null, previous:null, results:[] })
}
function queryParams(pageNo=1){
  const statusMap = { PENDING:'OWNER_PENDING', APPROVED:'OWNER_APPROVED', REJECTED:'OWNER_REJECTED' }
  return { page:pageNo, search:q.value || '', approval_status: statusMap[tab.value] }
}
async function fetch(pageNo=1){
  const tag = ++reqSeq
  loading.value = true
  try{
    const res = await api.orders(queryParams(pageNo))
    if (!alive || tag !== reqSeq) return
    const n = normalize(res)
    if (pageNo === 1) list.value = n
    else list.value = { ...n, results:[...(list.value.results||[]), ...n.results] }
  }catch(e){
    uni.showToast({ title: e?.data?.detail || '加载失败', icon:'none' })
  }finally{
    if (alive && tag === reqSeq){ loading.value = false; uni.stopPullDownRefresh && uni.stopPullDownRefresh() }
  }
}
async function search(){ page.value = 1; await fetch(1) }
async function loadMore(){ if(!list.value.next) return; page.value+=1; await fetch(page.value) }
function switchTab(t){ if (tab.value===t) return; tab.value=t; search() }
onLoad(()=>{ fetch(1) })
onPullDownRefresh(()=>{ search() })
onReachBottom(()=>{ loadMore() })

function statusText(o){
  return o.approval_status_display
    || ({OWNER_PENDING:'待审核', OWNER_APPROVED:'已通过', OWNER_REJECTED:'已驳回'}[o.approval_status] || '—')
}
function confirm(title){
  return new Promise((resolve)=>{ uni.showModal({ title, confirmText:'确定', cancelText:'取消', success:r=>resolve(r&&r.confirm) }) })
}
async function approve(o){
  const ok = await confirm('确认审核通过并分配库存？'); if(!ok) return
  try{ await api.ownerApprove(o.id); uni.showToast({ title:'审核成功', icon:'none' }); list.value.results = (list.value.results||[]).filter(x=>x.id!==o.id) }
  catch(e){ const code=e?.statusCode||e?.status; const msg=e?.data?.detail||(code===409?'库存不足/冲突':'审核失败'); uni.showToast({ title:msg, icon:'none' }) }
}
async function unapprove(o){
  const ok = await confirm('确认撤销审核并释放占用？'); if(!ok) return
  try{ await api.ownerUnapprove(o.id); uni.showToast({ title:'已撤审', icon:'none' }); list.value.results=(list.value.results||[]).filter(x=>x.id!==o.id) }
  catch(e){ uni.showToast({ title:e?.data?.detail||'撤审失败', icon:'none' }) }
}
async function cancel(o){
  const ok = await confirm('确认取消订单并释放占用？'); if(!ok) return
  try{ await api.cancelOrder(o.id); uni.showToast({ title:'已取消', icon:'none' }); list.value.results=(list.value.results||[]).filter(x=>x.id!==o.id) }
  catch(e){ uni.showToast({ title:e?.data?.detail||'取消失败', icon:'none' }) }
}
</script>

<style scoped>
.tabs{ gap: 12rpx; margin-bottom: 16rpx; }
.tab{ padding: 10rpx 20rpx; border-radius: 10rpx; border: 1px solid #ddd; background: #fff; }
.tab.active{ border-color: #333; }
.mt-2{ margin-top: 12rpx; }
.btn-sm{ padding: 10rpx 20rpx; font-size: 24rpx; border-radius: 8rpx; }
</style>
