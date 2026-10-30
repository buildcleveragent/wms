<template>
  <view class="page">
    <view class="header">出库复核</view>
    <view class="review-tabs"><button :type="stage === 'pending' ? 'primary' : 'default'" @click="changeStage('pending')">待复核</button><button :type="stage === 'completed' ? 'primary' : 'default'" @click="changeStage('completed')">已完成</button></view>
    <view class="review-search">
      <input v-model="search" placeholder="搜索订单号／客户" @confirm="load()" />
      <button :disabled="loading" @click="load()">搜索</button>
    </view>
    <view v-if="denied" class="hint warning">当前账号无此仓库的复核权限</view>
    <template v-else>
      <view v-for="t in rows" :key="t.id" class="card review-card" @click="openTask(t)">
        <view class="row-first"><text class="task-no">{{ t.review_task?.task_no || '复核关联异常' }}</text><text class="status">{{ stage === 'completed' ? '已完成出库' : t.can_retry_post ? '待重试过账' : '待复核' }}</text></view>
        <view>来源订单：{{ t.order?.order_no || t.ref_no || '—' }}</view>
        <view>客户：{{ t.order?.customer_name || '—' }}</view>
        <view>拣货任务：{{ t.task_no }}</view>
        <view>货主：{{ t.owner_name }} · 仓库：{{ t.warehouse_name }}</view>
        <view>拣货人：{{ t.picked_by_name || '—' }}</view>
        <view v-if="t.blocked_reason && stage !== 'completed'" class="warning">{{ t.blocked_reason }}</view>
        <button v-if="t.can_print_delivery" @click.stop="openOutboundPrintPage(t.id)">补打送货单</button>
      </view>
      <view v-if="!loading && !error && !rows.length" class="hint">暂无符合条件的任务</view>
      <button v-if="hasMore" :disabled="loading" @click="load({ more: true })">加载更多</button>
    </template>
    <view v-if="loading" class="hint">加载中…</view>
    <view v-if="error" class="warning">{{ error }}</view>
    <button :disabled="loading" @click="load()">刷新列表</button>
  </view>
</template>
<script setup>
import { onShow, onUnload, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { openOutboundPrintPage } from '@/utils/outboundPrint'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { usePhysicalReview } from '@/utils/physicalReview'
const session = usePhysicalReview({ api, auth: useAuth() })
const { rows, loading, denied, error, hasMore, stage, search, load } = session
function changeStage(value) { stage.value = value; load() }
function openTask(task) { uni.navigateTo({ url: `/pages/review/pick_task?task_id=${task.id}` }) }
onShow(() => load())
onPullDownRefresh(async () => { await load(); uni.stopPullDownRefresh() })
onReachBottom(() => load({ more: true }))
onUnload(session.dispose)
</script>
<style scoped src="./review.css"></style>

<style scoped>
.review-tabs { display: flex; gap: 12px; margin: 12px 0; }
.review-tabs button { flex: 1; margin: 0; }
.review-search { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.review-search input { flex: 1; min-width: 0; height: 44px; padding: 0 12px; border: 1px solid #dbe3eb; border-radius: 6px; background: #fff; }
.review-search button { flex: 0 0 auto; margin: 0; }
</style>
