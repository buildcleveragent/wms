<template>
  <view class="page home-today">
    <BossNav active="home" />
    <view class="account-row"><text @click="showSync = !showSync">{{ error ? '同步失败 · ' : '' }}{{ updatedAt ? `更新于 ${shortTime(updatedAt)}` : '等待同步' }} ⓘ</text><text class="refresh-now" @click="refreshAll">立即刷新</text><text @click="accountMenu">{{ auth.user?.display_name || auth.user?.username || '账号' }} ▾</text></view>
    <BossScopeFilter compact hide-dates :disabled="!ready" />
    <view v-if="contextError" class="warning" @click="initialize">{{ contextError }} · 点击重试</view>
    <view v-if="error" class="warning" @click="refresh">{{ error.statusCode === 404 ? '今日接口尚未上线，请先更新后端。' : '同步失败，保留上次结果，点击重试。' }}</view>
    <view class="section-heading"><text>今日业务</text><text class="date">{{ data?.business_today || scope.businessToday }} <text class="link" @click="openStatistics">周期统计 ›</text></text></view>
    <view class="order-grid">
      <view v-for="item in directions" :key="item.key" class="order-card" :class="item.key">
        <view @click="openMetric(item.key)"><view class="label">{{ item.label }} ›</view><view class="order-number">{{ data?.orders?.[item.key]?.count ?? '—' }} <text>单</text></view><view class="value-label">本日{{ item.key === 'inbound' ? '实收' : '实发' }}货值</view><view class="goods-value">{{ goodsValue(item.key) }}</view></view>
        <view class="submitted" @click="openMetric(`${item.key}_submitted`)">今日新增 {{ data?.orders?.[item.key]?.submitted ?? '—' }} 单 ›</view>
        <view v-if="data?.orders?.[item.key]?.unlinked_records" class="unlinked" @click="openMetric(`${item.key}_unlinked`)">{{ data.orders[item.key].unlinked_records }} 条未关联订单 ›</view>
      </view>
    </view>
    <view class="money-grid"><view v-for="item in financialLabels" :key="item.key" class="money-card" @click="openMetric(item.key)"><view class="label">{{ item.label }} ›</view><view class="money-value">{{ financialValue(item.key) }}</view></view></view>
    <view class="backlog"><view @click="openMetric('inbound_pending')">待收货 <text class="backlog-count">{{ data?.backlog?.inbound ?? '—' }}</text> ›</view><view @click="openMetric('outbound_pending')">待发货 <text class="backlog-count">{{ data?.backlog?.outbound ?? '—' }}</text> ›</view><view class="overdue" @click="openMetric('overdue')">超期 <text class="backlog-count">{{ data?.backlog?.overdue ?? '—' }}</text> ›</view></view>
    <view v-if="showSync" class="sync-details"><view>今日指标查询时间：{{ data?.meta?.generated_at || '未知' }}</view><view>单数为今日实际收发涉及的订单数，部分收发不代表整单完成；POS 单列。</view><view v-for="notice in data?.notices || []" :key="notice">{{ notice }}</view></view>
    <view class="live-columns"><BossFocusPanel ref="focus" :ready="ready" :reading="reading" :show-sync="showSync" /><BossActivityFeed ref="activity" compact :ready="ready" :reading="reading" :show-sync="showSync" /></view>
  </view>
</template>
<script setup>
import { ref } from 'vue'
import { onLoad, onPullDownRefresh, onPageScroll } from '@dcloudio/uni-app'
import BossNav from '@/components/boss-nav.vue'
import BossScopeFilter from '@/components/boss-scope-filter.vue'
import BossFocusPanel from '@/components/boss-focus-panel.vue'
import BossActivityFeed from '@/components/boss-activity-feed.vue'
import { useAuth } from '@/store/auth'
import { useBossScope } from '@/store/bossScope'
import { useLiveResource } from '@/utils/use-live-resource'
import { api, buildQuery } from '@/utils/request'
import { money } from '@/utils/billing'
const auth = useAuth(), scope = useBossScope()
const ready = ref(false), contextError = ref(''), reading = ref(false), showSync = ref(false)
const focus = ref(null), activity = ref(null)
const directions = [{ key: 'inbound', label: '今日实际入库' }, { key: 'outbound', label: '今日实际出库' }]
const financialLabels = [{ key: 'pos_sales', label: 'POS 销售' }, { key: 'pos_returns', label: '销售退款' }, { key: 'accruals', label: '仓储应计收入' }, { key: 'receipts', label: '回款净额' }]
const params = () => ({ warehouse: scope.warehouse, owner: scope.owner })
const { data, error, updatedAt, refresh } = useLiveResource(() => api.bossToday(params()), {
  key: () => JSON.stringify([auth.user?.id, params()]), enabled: () => ready.value && !!auth.access,
})
function shortTime(value) { const stamp = data.value?.meta?.generated_at; return stamp ? new Date(stamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) : value }
function moneyText(values) { return (values || []).map(row => money(row.amount, row.currency)).join(' / ') }
function goodsValue(key) { const row = data.value?.orders?.[key]; return !row ? '等待同步' : row.money?.length ? `已知 ${moneyText(row.money)}` : row.execution_records ? '金额未完整记录' : '暂无收发记录' }
function financialValue(key) { const row = data.value?.financial?.find(item => item.key === key); return !row ? '—' : row.unknown_currency ? `${row.count} 笔 · 币种未记录` : moneyText(row.money) || '暂无记录' }
function openMetric(metric) {
  if (!data.value) return
  const day = data.value.business_today
  uni.navigateTo({ url: `/pages/cockpit/details?${buildQuery({ ...params(), date_from: day, date_to: day, type: 'today', metric }, { keepEmpty: ['warehouse', 'owner'] })}` })
}
function openStatistics() { uni.reLaunch({ url: '/pages/cockpit/index?tab=periods' }) }
function accountMenu() { uni.showActionSheet({ itemColor: '#172b4d', itemList: ['刷新与检查设置', '退出登录'], success: async ({ tapIndex }) => { if (tapIndex === 0) return uni.navigateTo({ url: '/pages/settings/refresh' }); if (tapIndex !== 1) return; ready.value = false; await auth.logout(); scope.clear(); uni.reLaunch({ url: '/pages/login' }) } }) }
async function initialize() {
  if (!auth.ensureAuth()) return uni.reLaunch({ url: '/pages/login' })
  contextError.value = ''
  try { ready.value = !!await scope.loadContext(auth.user) } catch (err) { contextError.value = err.message || '筛选范围加载失败' }
}
onLoad(initialize)
onPageScroll(event => { reading.value = event.scrollTop > 120 })
async function refreshAll() { if (!ready.value) await initialize(); await Promise.allSettled([refresh(), focus.value?.refresh(), activity.value?.refresh()]) }
onPullDownRefresh(async () => { try { await refreshAll() } finally { uni.stopPullDownRefresh() } })
</script>
<style scoped>
.page { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 1400px; margin: auto; padding: 16rpx 24rpx 32rpx; color: #172b4d; background: #f4f7fb; min-height: 100vh; box-sizing: border-box; }
.account-row { display: flex; justify-content: space-between; gap: 16rpx; font-size: 23rpx; color: #64748b; margin: 6rpx 0 12rpx; }.account-row text { padding: 8rpx 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; font-size: 30rpx; font-weight: 700; margin: 18rpx 0 14rpx; }.date { font-size: 22rpx; font-weight: 400; color: #64748b; }.link { color: #1656a4; margin-left: 14rpx; }
.order-grid, .money-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14rpx; }
.order-card { border-radius: 18rpx; background: #fff; padding: 20rpx; border-top: 5rpx solid #26877b; }.outbound { border-top-color: #2b65bb; }
.label { font-size: 25rpx; color: #526581; }.order-number { font-size: 56rpx; font-weight: 750; line-height: 1.2; margin: 8rpx 0; }.order-number text { font-size: 24rpx; font-weight: 400; }
.value-label { color: #728199; font-size: 21rpx; }.goods-value { font-size: 24rpx; color: #42536b; margin-top: 4rpx; overflow-wrap: anywhere; }
.submitted { font-size: 24rpx; color: #1656a4; border-top: 1px solid #edf0f5; padding-top: 14rpx; margin-top: 14rpx; }.unlinked { color: #9b5d18; font-size: 22rpx; padding-top: 10rpx; }
.money-grid { margin-top: 14rpx; }.money-card { background: white; padding: 18rpx 20rpx; border-radius: 16rpx; }.money-value { font-size: 27rpx; font-weight: 650; margin-top: 8rpx; overflow-wrap: anywhere; }
.backlog { display: flex; justify-content: space-between; gap: 8rpx; background: #eaf0f7; border-radius: 14rpx; padding: 20rpx 16rpx; margin-top: 14rpx; font-size: 24rpx; }.overdue { color: #a35a19; }
.warning, .sync-details { font-size: 23rpx; padding: 16rpx; margin-top: 12rpx; background: #fff3da; line-height: 1.6; color: #805a20; }.sync-details { background: white; color: #64748b; }
@media (min-width: 900px) { .money-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }.live-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }.order-number { font-size: 42px; } }
</style>
