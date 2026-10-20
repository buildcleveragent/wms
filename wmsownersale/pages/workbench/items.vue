<template>
  <view class="workbench-list">
    <view class="list-heading"><text class="list-title">{{ labels[category] || '待办清单' }}</text><button size="mini" :disabled="loading" @click="reload">刷新</button></view>
    <text v-if="data" class="scope">{{ data.scope_label }} · {{ data.count }} 单</text>
    <view class="search-row"><input v-model="search" placeholder="单号 / 客户" @confirm="reload" /><button size="mini" @click="reload">搜索</button></view>
    <text v-if="category === 'overdue'" class="hint">不含未设置预计发货时间的订单。</text>
    <text v-if="data" class="hint">最近更新：{{ formatTime(data.as_of) }}</text>
    <view v-if="error" class="error"><text>{{ error }}</text><button v-if="!denied" size="mini" @click="fetchPage(failedPage)">重试</button></view>
    <text v-if="loading && !data" class="empty">正在加载待办…</text>
    <text v-else-if="data && !rows.length" class="empty">暂无符合条件的订单</text>
    <view v-for="row in rows" :key="row.id" class="todo-order" role="link" @click="openOrder(row)">
      <view class="order-heading"><text class="order-number">{{ row.order_no }}</text><text class="status">{{ row.status_label }}</text></view>
      <text>客户：{{ row.customer_name || '未记录' }}</text>
      <text>仓库：{{ row.warehouse_name || '未记录' }}</text>
      <text>预计发货：{{ row.etd ? formatTime(row.etd) : '未设置' }}</text>
      <text class="reason">{{ row.reason }}</text>
      <view v-for="(item, index) in row.shortages" :key="index" class="shortage-item">
        <text>{{ item.product_name }} · 缺口 {{ qty(item.quantity) }} {{ item.unit }}</text><text>{{ item.reason }}</text>
      </view>
      <text class="open-order">{{ row.action === 'review' ? '前往审核' : '查看订单与履约进度' }} ›</text>
    </view>
    <button v-if="data?.next_page && !denied" class="load-more" :disabled="loading" @click="fetchPage(data.next_page)">{{ loading ? '加载中…' : '加载更多' }}</button>
    <text v-else-if="data && rows.length" class="empty">已加载全部订单</text>
  </view>
</template>
<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { onLoad, onShow, onHide, onUnload, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
const labels = { pending: '待审核', returned: '被退回', overdue: '超期未发货', shortage: '库存不足' }
const auth = useAuth()
const category = ref('')
const search = ref('')
const rows = ref([])
const data = ref(null)
const loading = ref(false)
const error = ref('')
const denied = ref(false)
const failedPage = ref(1)
let active = false
let alive = true
let generation = 0
let appliedSearch = ''
function suspend() { active = false; generation++; loading.value = false }
function dispose() { alive = false; suspend() }
async function fetchPage(page = 1) {
  if (!alive || !active || denied.value || loading.value || !labels[category.value]) return
  const tag = ++generation
  const key = category.value
  loading.value = true; error.value = ''
  try {
    const result = await api.ownerWorkbenchItems({ category: key, search: appliedSearch, page, page_size: 20 })
    if (!alive || !active || tag !== generation) return
    if (result?.category !== key || !Array.isArray(result.results)) throw new Error('数据不完整')
    rows.value = page === 1 ? result.results : Array.from(new Map([...rows.value, ...result.results].map(r => [r.id, r])).values())
    data.value = result
  } catch (e) {
    if (!alive || !active || tag !== generation) return
    failedPage.value = page
    const status = Number(e?.statusCode || e?.status)
    if ([401, 403, 404, 409].includes(status)) {
      denied.value = true; rows.value = []; data.value = null
      error.value = status === 404 ? '待办清单暂不可用' : '登录状态或待办权限已失效'
    } else error.value = data.value ? (page === 1 ? '更新失败，当前为上次数据' : '加载失败，请重试') : '待办清单暂不可用，请重试'
  } finally {
    if (tag === generation) loading.value = false
    uni.stopPullDownRefresh?.()
  }
}
function reload() {
  if (appliedSearch !== search.value.trim()) { data.value = null; rows.value = [] }
  appliedSearch = search.value.trim()
  generation++; loading.value = false
  return fetchPage(1)
}
function openOrder(row) { uni.navigateTo({ url: `/pages/${row.action === 'review' ? 'approval/approvedetail' : 'orders/detail'}?id=${row.id}` }) }
function qty(value) { return String(value).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') }
function formatTime(value) { return (value || '').replace('T', ' ').replace(/\.\d+/, '').replace(/\+08:00$/, '（北京时间）') }
onLoad(options => { category.value = options.category || ''; if (!labels[category.value]) error.value = '待办类别不正确' })
onShow(() => { active = true; reload() })
onHide(suspend)
onUnload(dispose)
onUnmounted(dispose)
onPullDownRefresh(reload)
onReachBottom(() => { if (data.value?.next_page) fetchPage(data.value.next_page) })
watch(() => `${auth.user?.id || ''}:${auth.roles.join(',')}`, () => {
  generation++; loading.value = false; data.value = null; rows.value = []; denied.value = false
  if (active) reload()
})
</script>
<style scoped>
.workbench-list { box-sizing: border-box; max-width: 960px; min-height: 100vh; margin: auto; padding: 16px; background: #f6f7f9; color: #1f2937; }
.list-heading, .order-heading, .search-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.list-title { font-size: 20px; font-weight: 700; }.list-heading button, .search-row button { margin: 0; flex-shrink: 0; }
.scope, .hint { display: block; font-size: 13px; color: #667085; margin: 8px 0; line-height: 1.6; }
.search-row { margin: 16px 0; }.search-row input { flex: 1; min-width: 0; height: 40px; padding: 0 10px; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; }
.todo-order { display: flex; flex-direction: column; gap: 8px; margin: 14px 0; padding: 16px; border: 1px solid #e5e7eb; border-radius: 12px; background: #fff; font-size: 14px; overflow-wrap: anywhere; }
.order-heading { flex-wrap: wrap; }.order-number { font-weight: 700; font-size: 16px; }.status { color: #475569; font-size: 12px; background: #f1f5f9; border-radius: 6px; padding: 3px 6px; }
.reason { color: #92400e; line-height: 1.7; }.shortage-item { display: flex; flex-direction: column; gap: 6px; padding: 10px; background: #fff7ed; border-radius: 8px; line-height: 1.6; }
.open-order { color: #2563eb; margin-top: 5px; }.empty { display: block; text-align: center; color: #667085; padding: 24px 0; font-size: 14px; }
.error { padding: 12px; color: #b42318; background: #fff7ed; border-radius: 8px; }.error button { margin-top: 8px; }.load-more { font-size: 14px; }
</style>
