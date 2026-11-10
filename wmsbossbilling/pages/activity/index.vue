<template>
  <view class="page activity-page">
    <BossNav active="activity" />
    <BossScopeFilter v-if="!digestToken" hide-dates />
    <view v-if="!digestToken" class="dates">
      <picker mode="date" :value="dates.date_from || today" :end="dates.date_to || today" @change="changeDate('date_from', $event)"><view>开始：{{ dates.date_from || '今天' }}</view></picker>
      <picker mode="date" :value="dates.date_to || today" :start="dates.date_from || undefined" :end="today" @change="changeDate('date_to', $event)"><view>结束：{{ dates.date_to || '今天' }}</view></picker>
      <button v-for="item in datePresets" :key="item.key" size="mini" @click="dates = presetRange(item.key, today)">{{ item.label }}</button>
    </view>
    <view v-if="contextError">范围加载失败。<button @click="initialize">重试</button></view>
    <BossActivityFeed :ready="ready" :reading="reading" :dates="dates" :digest-token="digestToken" :initial-category="initialCategory" :initial-action="initialAction" />
  </view>
</template>
<script setup>
import { ref, computed } from 'vue'
import { onLoad, onPageScroll } from '@dcloudio/uni-app'
import BossNav from '@/components/boss-nav.vue'
import BossScopeFilter from '@/components/boss-scope-filter.vue'
import BossActivityFeed from '@/components/boss-activity-feed.vue'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
import { datePresets, presetRange, validRange } from '@/utils/date-ranges'
const auth = useAuth()
const scope = useBossScope()
const ready = ref(false)
const initialOptions = ref({})
const contextError = ref(false)
const reading = ref(false)
// Activity dates are independent of the saved financial reporting interval.
// Omitted dates follow the server's business day, including midnight rollover.
const dates = ref({})
const digestToken = ref('')
const initialCategory = ref('')
const initialAction = ref('')
const today = computed(() => scope.businessToday)
function changeDate(field, event) {
  const next = { date_from: today.value, date_to: today.value, ...dates.value, [field]: event.detail.value }
  if (!validRange(next.date_from, next.date_to, today.value)) { uni.showToast({ title: '请选择有效日期范围', icon: 'none' }); return }
  dates.value = next
}
async function initialize() {
  if (!auth.ensureAuth()) { uni.reLaunch({ url: '/pages/login' }); return }
  contextError.value = false
  try {
    const options = initialOptions.value
    const hasWarehouse = Object.prototype.hasOwnProperty.call(options, 'warehouse')
    const loaded = await scope.loadContext(auth.user, hasWarehouse, hasWarehouse ? options.warehouse : undefined)
    if (!loaded) return
    if (hasWarehouse && options.warehouse && !scope.warehouseOptions.some(row => String(row.id) === options.warehouse)) throw new Error('仓库范围不可用')
    if (Object.prototype.hasOwnProperty.call(options, 'owner')) {
      if (options.owner && !scope.ownerOptions.some(row => String(row.id) === options.owner)) throw new Error('货主范围不可用')
      scope.selectOwner(options.owner)
    }
    if (options.date_from || options.date_to) {
      if (!validRange(options.date_from, options.date_to, scope.businessToday)) throw new Error('活动日期无效')
      dates.value = { date_from: options.date_from, date_to: options.date_to }
    }
    ready.value = true
  } catch { contextError.value = true }
}
onLoad(options => { initialOptions.value = options; digestToken.value = options.token || ''; initialCategory.value = options.category || ''; initialAction.value = options.action || ''; initialize() })
onPageScroll(event => { reading.value = event.scrollTop > 120 })
</script>
<style scoped>
.activity-page { padding: 24rpx; max-width: 1500px; margin: auto; }
.dates { display: flex; flex-wrap: wrap; gap: 20rpx; align-items: center; margin-top: 22rpx; font-size: 25rpx; }
.dates picker { background: white; padding: 20rpx; border-radius: 12rpx; }
</style>
