<template>
  <view class="workbench page">
    <view class="topline">
      <view>
        <text class="eyebrow">{{ greeting }}</text>
        <text class="page-title">仓库工作台</text>
      </view>
      <view class="avatar"><WmsIcon name="user" /></view>
    </view>

    <view v-if="canUsePos" class="pos-hero" @click="go('/pages/pos/index')">
      <view class="scan-mark"><WmsIcon name="scan" /></view>
      <view class="hero-copy">
        <text class="hero-title">扫码收银</text>
        <text class="hero-sub">拿起手机，扫描商品后直接登记销售</text>
      </view>
      <WmsIcon class="hero-chevron" />
    </view>

    <view v-if="canUsePos" class="shift-strip">
      <view class="shift-state">
        <view :class="['status-dot', { active: shiftOpen }]" />
        <text>{{ shiftLoading ? '正在读取班次' : shiftOpen ? shiftText : '当前未开班' }}</text>
      </view>
      <text class="shift-link" @click="go('/pages/pos/index')">{{ shiftOpen ? '进入收银' : '去开班' }}</text>
    </view>

    <view v-for="group in visibleGroups" :key="group.title" class="operation-section">
      <view class="section-heading">
        <text>{{ group.title }}</text>
        <text class="section-note">{{ group.note }}</text>
      </view>
      <view class="operation-list">
        <view v-for="item in group.items" :key="item.path" class="operation-row" @click="go(item.path)">
          <view :class="['operation-icon', item.tone]"><WmsIcon :name="item.icon" /></view>
          <view class="operation-copy">
            <text class="operation-title">{{ item.title }}</text>
            <text class="operation-desc">{{ item.desc }}</text>
          </view>
          <WmsIcon class="chevron" />
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import WmsIcon from '@/components/WmsIcon.vue'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { canAccessPath, hasPermission } from '@/utils/mobileAccess'

const auth = useAuth()
const currentShift = ref(null)
const shiftLoading = ref(false)

const canUsePos = computed(() => hasPermission(auth, 'pos.add_possale'))
const shiftOpen = computed(() => ['OPEN', 'REOPENED'].includes(currentShift.value?.status))
const shiftText = computed(() => `${currentShift.value?.shift_no || '当前班次'} · 已开班`)
const greeting = computed(() => auth.user?.display_name || auth.user?.username || '仓库伙伴')

const groups = [
  { title: '入库作业', note: '收货与上架', items: [
    { title: '有订单收货', desc: '按到货任务验收并登记', icon: 'receive', tone: 'blue', path: '/pages/inbound/receive_task_list' },
    { title: '无订单收货', desc: '选择货主后扫码创建入库单', icon: 'receive', tone: 'teal', path: '/pages/inbound/createwithoutorder/selectowner' },
    { title: '上架', desc: '确认来源库存并放入目标库位', icon: 'putaway', tone: 'green', path: '/pages/inbound/putaway_task_list' },
  ]},
  { title: '出库作业', note: '拣选与复核', items: [
    { title: '代货主出库', desc: '代货主选品并完成出库', icon: 'pick', tone: 'blue', path: '/pages/outbound/assisted' },
    { title: '拣货', desc: '按任务扫描库位和商品', icon: 'pick', tone: 'teal', path: '/pages/picking/task_list' },
    { title: '复核', desc: '检查拣货结果并确认出库', icon: 'review', tone: 'green', path: '/pages/review/pick_task_list' },
  ]},
  { title: '库存作业', note: '移动与盘点', items: [
    { title: '库存查询', desc: '查询当前仓库商品库存', icon: 'search', tone: 'blue', path: '/pages/inventory/company' },
    { title: '补货', desc: '从存储区补充作业库存', icon: 'replenish', tone: 'teal', path: '/pages/inventory/replenish/index' },
    { title: '移库', desc: '扫描来源、商品和目标库位', icon: 'move', tone: 'green', path: '/pages/inventory/move/index' },
    { title: '盘点', desc: '按盘点任务扫描并记录实盘数', icon: 'stocktake', tone: 'amber', path: '/pages/inventory/stocktake/index' },
    { title: '要货申请', desc: '提交现场补货需求', icon: 'replenish', tone: 'blue', path: '/pages/replenishment/request' },
  ]},
]

const visibleGroups = computed(() => groups.map((group) => ({
  ...group,
  items: group.items.filter((item) => canAccessPath(auth, item.path)),
})).filter((group) => group.items.length))

function go(path) { uni.navigateTo({ url: path }) }

async function loadShift() {
  if (!canUsePos.value || shiftLoading.value) return
  shiftLoading.value = true
  try {
    const result = await api.posShiftCurrent()
    currentShift.value = result?.shift || null
  } catch (error) {
    currentShift.value = null
  } finally {
    shiftLoading.value = false
  }
}

onShow(async () => {
  await auth.loadProfile({ force: true }).catch(() => null)
  await loadShift()
})
</script>

<style scoped>
.workbench { padding-top: 34rpx; }
.topline { display: flex; align-items: center; justify-content: space-between; margin-bottom: 30rpx; }
.eyebrow { display: block; margin-bottom: 2rpx; color: #64748b; font-size: 24rpx; }
.page-title { display: block; font-size: 44rpx; font-weight: 780; line-height: 1.2; }
.avatar { width: 76rpx; height: 76rpx; border-radius: 50%; background: #f1f5f9; color: #334155; display: flex; align-items: center; justify-content: center; font-size: 38rpx; }
.pos-hero { min-height: 184rpx; padding: 28rpx; display: flex; align-items: center; gap: 24rpx; border-radius: 28rpx; background: #1677ff; color: #fff; box-shadow: 0 18rpx 44rpx rgba(22,119,255,.2); }
.scan-mark { width: 86rpx; height: 86rpx; flex: 0 0 auto; border-radius: 22rpx; background: rgba(255,255,255,.16); display: flex; align-items: center; justify-content: center; font-size: 54rpx; }
.hero-copy { flex: 1; min-width: 0; }
.hero-title, .hero-sub { display: block; }
.hero-title { font-size: 38rpx; font-weight: 750; }
.hero-sub { margin-top: 8rpx; color: rgba(255,255,255,.86); font-size: 24rpx; line-height: 1.45; }
.hero-chevron { flex: 0 0 auto; font-size: 34rpx; }
.shift-strip { margin: 18rpx 4rpx 34rpx; display: flex; align-items: center; justify-content: space-between; color: #475569; font-size: 24rpx; }
.shift-state { display: flex; align-items: center; gap: 12rpx; }
.status-dot { width: 14rpx; height: 14rpx; border-radius: 50%; background: #94a3b8; }
.status-dot.active { background: #159357; box-shadow: 0 0 0 8rpx #edf9f3; }
.shift-link { color: #0f766e; font-weight: 650; }
.operation-section { margin-top: 34rpx; }
.section-heading { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 14rpx; font-size: 30rpx; font-weight: 730; }
.section-note { color: #94a3b8; font-size: 22rpx; font-weight: 450; }
.operation-list { border-top: 1rpx solid #e2e8f0; }
.operation-row { min-height: 132rpx; display: flex; align-items: center; gap: 22rpx; border-bottom: 1rpx solid #e2e8f0; }
.operation-icon { width: 72rpx; height: 72rpx; flex: 0 0 auto; border-radius: 20rpx; display: flex; align-items: center; justify-content: center; font-size: 38rpx; }
.operation-icon.blue { background: #eff6ff; color: #1677ff; }.operation-icon.teal { background: #ecfdf5; color: #0f766e; }.operation-icon.green { background: #edf9f3; color: #159357; }.operation-icon.amber { background: #fff7ed; color: #e77800; }
.operation-copy { flex: 1; min-width: 0; }.operation-title, .operation-desc { display: block; }.operation-title { font-size: 29rpx; font-weight: 680; }.operation-desc { margin-top: 4rpx; color: #64748b; font-size: 23rpx; }
.chevron { color: #94a3b8; font-size: 30rpx; }
</style>
