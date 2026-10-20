<template>
  <view class="reorder-page">
    <view class="title">再次下单</view>
    <view class="hint">先核对商品、当前价格及收件信息，确认后进入新订单。</view>
    <view v-if="updatedAt" class="hint">最近成功加载：{{ updatedAt }}</view>
    <button class="secondary" :disabled="loading || denied" @click="refresh">刷新预览</button>
    <view v-if="error" class="error"><text>{{ error }}</text><button @click="refresh">重试</button></view>
    <view v-if="loading" class="hint">正在核对当前价格和库存…</view>
    <template v-if="data">
      <view class="card">
        <view>来源：{{ data.source_order_no }} · {{ data.source_status }}</view>
        <view>客户：{{ data.customer?.name || '请选择当前可用客户' }}</view>
        <view>仓库：{{ data.warehouse?.name || '未选择' }}</view>
        <button class="secondary" @click="chooseContext = !chooseContext">更换客户或仓库</button>
        <view v-if="chooseContext">
          <view v-if="contextError" class="error">{{ contextError }}<button @click="loadOptions">重试</button></view>
          <picker :range="warehouses" range-key="name" @change="pickWarehouse">
            <view class="picker">选择出库仓库 ›</view>
          </picker>
          <input v-model="customerSearch" placeholder="搜索客户名称或编码" @confirm="searchCustomers" />
          <button @click="searchCustomers">搜索客户</button>
          <button v-for="customer in customers" :key="customer.id" @click="pickCustomer(customer)">{{ customer.name }}</button>
          <button v-if="customerNext" @click="loadCustomers(customerNext)">更多客户</button>
        </view>
        <view class="hint">{{ data.copy_notice }}</view>
        <view v-for="message in data.context_issues" :key="message" class="error">{{ message }}</view>
      </view>
      <view class="card">
        <view>来自历史订单，请确认</view>
        <view>{{ data.header.contact || '收件人未记录' }} {{ data.header.contact_phone }}</view>
        <view>{{ data.header.ship_to || '收货地址未记录' }}</view>
      </view>
      <view v-for="item in data.items" :key="item.line_key" class="card">
        <view class="name">{{ item.name }}<text class="hint"> · 原行 {{ item.source_line_no }}</text></view>
        <view>{{ item.sku }} {{ item.spec }}</view>
        <view>单位：{{ item.base_unit_name || '待核对' }} · 计划数量：{{ item.qty }}</view>
        <view v-if="item.current_base_unit_name && item.current_base_unit_name !== item.historical_unit_name" class="error">
          当前单位：{{ item.current_base_unit_name }}；原数量按历史单位保留，请重新选品。
        </view>
        <view>历史价：¥{{ item.historical_price }} → 当前参考价：{{ item.price == null ? '未配置' : '¥' + item.price }}</view>
        <view v-if="item.price !== item.historical_price" class="changed">价格与原订单不同，请确认。</view>
        <view>当前可用：{{ item.available ?? '待核对' }} {{ item.current_base_unit_name }}</view>
        <view v-for="message in item.issues" :key="message" class="error">{{ message }}</view>
      </view>
      <view class="hint">商品问题可在新订单中调整或移除。{{ data.notice }}</view>
    </template>
    <view class="footer">
      <button @click="cancel">取消</button>
      <button class="primary" :disabled="!data?.can_copy || loading || !!error || copying" @click="copy">
        确认复制并编辑
      </button>
    </view>
  </view>
</template>
<script setup>
import { ref, watch } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'
import { usePurchaseResource } from '@/composables/usePurchaseResource'

const auth = useAuth(), cart = useCart(), orderId = ref(0), warehouseId = ref(null), customerId = ref(null)
const identity = () => `${auth.user?.id}:${auth.user?.owner_id}`
const { data, error, loading, denied, updatedAt, refresh } = usePurchaseResource(
  () => api.reorderContext(orderId.value, { warehouse_id: warehouseId.value, customer_id: customerId.value }),
  () => orderId.value ? `${orderId.value}:${warehouseId.value}:${customerId.value}` : '', identity,
)
const copying = ref(false), chooseContext = ref(false), warehouses = ref([]), customers = ref([])
const customerSearch = ref(''), customerNext = ref(null), contextError = ref('')
let optionBusy = false, optionEpoch = 0, active = true, pendingCustomers = null
onShow(() => { active = true })
onHide(() => { active = false; optionEpoch++; pendingCustomers = null })
onUnload(() => { active = false; optionEpoch++; pendingCustomers = null })
watch(identity, () => { optionEpoch++; pendingCustomers = null; warehouses.value = []; customers.value = [] })
watch(chooseContext, value => { if (value) { active = true; loadOptions() } })
async function loadOptions() {
  if (optionBusy) return
  optionBusy = true
  const tag = ++optionEpoch, actor = identity()
  try {
    const result = await api.warehouses()
    if (active && tag === optionEpoch && actor === identity()) warehouses.value = result
  } catch (e) { if (tag === optionEpoch) contextError.value = e?.message || '仓库加载失败' }
  finally { optionBusy = false }
  if (active && actor === identity()) { pendingCustomers = null; await loadCustomers(1) }
}
async function loadCustomers(page = 1) {
  if (optionBusy) { optionEpoch++; pendingCustomers = page; return }
  optionBusy = true; contextError.value = ''
  const tag = ++optionEpoch, actor = identity(), query = customerSearch.value.trim()
  try {
    const result = await api.customers(query, page)
    if (!active || tag !== optionEpoch || actor !== identity()) return
    customers.value = page === 1 ? result.results : [...customers.value, ...result.results]
    customerNext.value = result.next ? page + 1 : null
  } catch (e) { if (tag === optionEpoch) contextError.value = e?.message || '客户加载失败' }
  finally {
    optionBusy = false
    if (pendingCustomers && active) {
      const next = pendingCustomers; pendingCustomers = null; loadCustomers(next)
    }
  }
}
function searchCustomers() { return loadCustomers(1) }
function pickWarehouse(event) { warehouseId.value = warehouses.value[Number(event.detail.value)]?.id }
function pickCustomer(customer) { customerId.value = customer.id; chooseContext.value = false }
function cancel() { uni.navigateBack({ delta: 1 }) }
async function copy() {
  if (copying.value || !data.value?.can_copy || error.value || loading.value) return
  copying.value = true
  const actor = identity(), snapshot = data.value
  if (cart.pending_submission) {
    uni.showToast({ title: '当前订单提交结果待确认，请先返回订单重试确认结果', icon: 'none' })
    copying.value = false; return
  }
  if (cart.items.length || cart.editing_order_id || cart.customer ||
      cart.order_header.contact || cart.order_header.ship_to || cart.order_header.src_bill_no) {
    const confirmed = await new Promise(resolve => uni.showModal({
      title: '替换正在填写的订单',
      content: '确认后将使用本次复制内容替换当前待提交内容。已保存的后台草稿不会被删除。',
      confirmText: '确认替换', success: result => resolve(result.confirm), fail: () => resolve(false),
    }))
    if (!confirmed) { copying.value = false; return }
  }
  if (actor === identity() && snapshot === data.value && active && cart.beginReorder({
    user_id: auth.user?.id, owner_id: auth.user?.owner_id, context: snapshot,
  })) uni.redirectTo({ url: '/pages/orders/cart' })
  copying.value = false
}
onLoad(query => { auth.ensureAuth(); orderId.value = Number(query?.id || 0) })
</script>
<style scoped>
.reorder-page { padding: 28rpx 24rpx 180rpx; background: #f5f7fa; min-height: 100vh; box-sizing: border-box; }
.title { font-size: 38rpx; font-weight: 700; }
.hint { color: #64748b; font-size: 25rpx; margin: 14rpx 0; }
.card { background: white; padding: 24rpx; margin: 22rpx 0; border-radius: 16rpx; line-height: 1.8; overflow-wrap: anywhere; }
.name { font-weight: 600; }
.error, .changed { color: #9a3412; background: #fff7ed; padding: 10rpx; margin-top: 12rpx; overflow-wrap: anywhere; }
.picker, input { padding: 18rpx; border: 1px solid #dbe3ee; margin-top: 14rpx; border-radius: 10rpx; }
.footer { position: fixed; bottom: 0; left: 0; right: 0; background: #fff; display: flex; gap: 16rpx; padding: 18rpx 24rpx calc(18rpx + env(safe-area-inset-bottom)); border-top: 1px solid #e2e8f0; z-index: 10; }
.footer button { flex: 1; margin: 0; font-size: 28rpx; }
.primary { color: white; background: #2563eb; }
.secondary { margin-top: 16rpx; color: #2563eb; font-size: 26rpx; }
</style>
