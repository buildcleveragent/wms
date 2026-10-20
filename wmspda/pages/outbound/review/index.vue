<template>
  <view class="review-page">
    <view class="title">出库确认</view>
    <view v-if="denied" class="card">正在核验权限或当前账号无出库审核权限</view>
    <template v-else>
      <view class="tabs">
        <button v-for="stage in reviewStages" :key="stage.key" :class="{ active: filters.stage === stage.key }" @click="switchStage(stage.key)">{{ stage.label }}</button>
      </view>
      <view class="card">
        <view class="search-row"><input v-model="filters.search" placeholder="单号 / 客户名称" @confirm="load()" /><button @click="load()">搜索</button></view>
        <view class="filters">
          <picker :range="owners" range-key="name" :value="ownerIndex" @change="selectOwner"><view class="filter">货主：{{ owners[ownerIndex].name }}</view></picker>
          <picker :range="warehouses" range-key="name" :value="warehouseIndex" @change="selectWarehouse"><view class="filter">仓库：{{ warehouses[warehouseIndex].name }}</view></picker>
        </view>
      </view>
      <view v-for="row in rows" :key="row.id" class="card order-row" @click="open(row.id)">
        <view class="row"><text class="strong">{{ row.order_no }}</text><text class="status">{{ reviewStatus(row.approval_status) }}</text></view>
        <view>货主：{{ row.owner_name }} · 仓库：{{ row.warehouse_name }}</view>
        <view>客户：{{ row.customer_name || '—' }}</view>
        <view>数量：{{ formatQuantity(row.total_qty) }} · {{ amountSummaryLabel(row) }}</view>
        <view v-if="row.warehouse_reject_reason" class="warning">退回原因：{{ row.warehouse_reject_reason }}</view>
        <view class="muted">{{ row.biz_date }} · 查看详情 ›</view>
      </view>
      <view v-if="!loading && !rows.length && !error" class="card">暂无订单</view>
      <button v-if="hasMore" :disabled="loading" @click="load({ more: true })">加载更多</button>
    </template>
    <view v-if="loading" class="hint">正在加载…</view>
    <view v-if="error" class="card warning">{{ error }}<button :disabled="loading" @click="load({ authorize: true })">重试</button></view>
  </view>
</template>
<script setup>
import { computed } from 'vue'
import { onShow, onUnload, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { useWarehouseReview, reviewStages, reviewStatus, formatQuantity, amountSummaryLabel } from '@/utils/warehouseReview'
const session = useWarehouseReview({ api, auth: useAuth() })
const { rows, filters, options, hasMore, loading, error, denied, load } = session
const owners = computed(() => [{ id: '', name: '全部' }, ...options.value.owners])
const warehouses = computed(() => [{ id: '', name: '全部' }, ...options.value.warehouses])
const ownerIndex = computed(() => Math.max(0, owners.value.findIndex(row => row.id === filters.owner_id)))
const warehouseIndex = computed(() => Math.max(0, warehouses.value.findIndex(row => row.id === filters.warehouse_id)))
function selectOwner(e) { filters.owner_id = owners.value[Number(e.detail.value)].id; load() }
function selectWarehouse(e) { filters.warehouse_id = warehouses.value[Number(e.detail.value)].id; load() }
function switchStage(stage) { filters.stage = stage; load() }
function open(id) { uni.navigateTo({ url: `/pages/outbound/review/detail?id=${id}` }) }
onShow(() => load({ authorize: true }))
onPullDownRefresh(async () => { await load({ authorize: true }); uni.stopPullDownRefresh() })
onReachBottom(() => { if (hasMore.value) load({ more: true }) })
onUnload(session.dispose)
</script>
<style src="./review.css"></style>
