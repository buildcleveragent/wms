<template>
  <view class="page operations-page">
    <view class="intro">
      <text class="title">现场作业</text>
      <text class="intro-copy">选择任务后，按页面提示逐步扫描和确认。</text>
    </view>
    <view v-for="group in visibleGroups" :key="group.title" class="operation-group">
      <text class="group-title">{{ group.title }}</text>
      <view class="group-list">
        <view v-for="item in group.items" :key="item.path" class="group-row" @click="go(item.path)">
          <view :class="['row-icon', item.tone]"><WmsIcon :name="item.icon" /></view>
          <text class="row-title">{{ item.title }}</text>
          <WmsIcon class="chevron" />
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'
import { canAccessPath } from '@/utils/mobileAccess'

const auth = useAuth()
const groups = [
  { title: '入库', items: [
    { title: '有订单收货', icon: 'receive', tone: 'blue', path: '/pages/inbound/receive_task_list' },
    { title: '无订单收货', icon: 'receive', tone: 'teal', path: '/pages/inbound/createwithoutorder/selectowner' },
    { title: '上架作业', icon: 'putaway', tone: 'green', path: '/pages/inbound/putaway_task_list' },
  ]},
  { title: '出库', items: [
    { title: '代货主出库', icon: 'pick', tone: 'blue', path: '/pages/outbound/assisted' },
    { title: '出库历史', icon: 'history', tone: 'teal', path: '/pages/outbound/assisted_history' },
    { title: '拣货作业', icon: 'pick', tone: 'green', path: '/pages/picking/task_list' },
    { title: '复核作业', icon: 'review', tone: 'amber', path: '/pages/review/pick_task_list' },
  ]},
  { title: '库存', items: [
    { title: '库存查询', icon: 'search', tone: 'blue', path: '/pages/inventory/company' },
    { title: '补货作业', icon: 'replenish', tone: 'teal', path: '/pages/inventory/replenish/index' },
    { title: '移库作业', icon: 'move', tone: 'green', path: '/pages/inventory/move/index' },
    { title: '盘点作业', icon: 'stocktake', tone: 'amber', path: '/pages/inventory/stocktake/index' },
    { title: '要货申请', icon: 'replenish', tone: 'blue', path: '/pages/replenishment/request' },
  ]},
]
const visibleGroups = computed(() => groups.map((group) => ({
  ...group,
  items: group.items.filter((item) => canAccessPath(auth, item.path)),
})).filter((group) => group.items.length))
function go(path) { uni.navigateTo({ url: path }) }
onShow(() => auth.loadProfile({ force: true }).catch(() => null))
</script>

<style scoped>
.operations-page { padding-top: 34rpx; }
.intro { margin-bottom: 38rpx; }.intro-copy { display: block; margin-top: 10rpx; color: #64748b; font-size: 25rpx; }
.operation-group { margin-top: 34rpx; }.group-title { display: block; margin-bottom: 12rpx; font-size: 28rpx; font-weight: 720; }
.group-list { border: 1rpx solid #e2e8f0; border-radius: 24rpx; overflow: hidden; }
.group-row { min-height: 112rpx; padding: 0 24rpx; display: flex; align-items: center; gap: 20rpx; border-bottom: 1rpx solid #e2e8f0; }.group-row:last-child { border-bottom: 0; }
.row-icon { width: 64rpx; height: 64rpx; border-radius: 18rpx; display: flex; align-items: center; justify-content: center; font-size: 34rpx; }.row-icon.blue{background:#eff6ff;color:#1677ff}.row-icon.teal{background:#ecfdf5;color:#0f766e}.row-icon.green{background:#edf9f3;color:#159357}.row-icon.amber{background:#fff7ed;color:#e77800}
.row-title { flex: 1; font-size: 29rpx; font-weight: 630; }.chevron { color: #94a3b8; font-size: 30rpx; }
</style>
