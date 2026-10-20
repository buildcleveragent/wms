<template>
  <view class="page">
    <view class="header">待复核任务</view>
    <view v-if="denied" class="hint warning">当前账号无此仓库的复核权限</view>
    <template v-else>
      <view v-for="t in rows" :key="t.id" class="card review-card" @click="openTask(t)">
        <view class="row-first"><text class="task-no">{{ t.review_task?.task_no || '复核关联异常' }}</text><text class="status">{{ t.can_retry_post ? '待重试过账' : '待复核' }}</text></view>
        <view>来源订单：{{ t.ref_no || '—' }}</view>
        <view>拣货任务：{{ t.task_no }}</view>
        <view>货主：{{ t.owner_name }} · 仓库：{{ t.warehouse_name }}</view>
        <view>拣货人：{{ t.picked_by_name || '—' }}</view>
        <view v-if="t.blocked_reason" class="warning">{{ t.blocked_reason }}</view>
      </view>
      <view v-if="!loading && !error && !rows.length" class="hint">暂无可复核任务</view>
      <button v-if="hasMore" :disabled="loading" @click="load({ more: true })">加载更多</button>
    </template>
    <view v-if="loading" class="hint">加载中…</view>
    <view v-if="error" class="warning">{{ error }}</view>
    <button :disabled="loading" @click="load()">刷新列表</button>
  </view>
</template>
<script setup>
import { onShow, onUnload, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { usePhysicalReview } from '@/utils/physicalReview'
const session = usePhysicalReview({ api, auth: useAuth() })
const { rows, loading, denied, error, hasMore, load } = session
function openTask(task) { uni.navigateTo({ url: `/pages/review/pick_task?task_id=${task.id}` }) }
onShow(() => load())
onPullDownRefresh(async () => { await load(); uni.stopPullDownRefresh() })
onReachBottom(() => load({ more: true }))
onUnload(session.dispose)
</script>
<style scoped src="./review.css"></style>
