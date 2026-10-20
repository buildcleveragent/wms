<template>
  <view class="address-page">
    <view class="title">客户地址簿</view>
    <button v-if="customerId && !selectMode" class="link" @click="customerId = null; editing = false">更换客户</button>
    <template v-if="!customerId">
      <view class="search"><input v-model="q" placeholder="客户名称 / 编码" @confirm="searchCustomers" /><button @click="searchCustomers">搜索</button></view>
      <view v-if="customerList.error.value" class="error">{{ customerList.error.value }}<button @click="customerList.refresh">重试</button></view>
      <button v-for="customer in customerList.data.value?.results || []" :key="customer.id" class="card"
        @click="customerId = customer.id">{{ customer.name }} · {{ customer.code }}</button>
      <view v-if="customerList.loading.value">正在加载客户…</view>
      <button v-if="customerList.data.value?.next" @click="moreCustomers">更多客户</button>
    </template>
    <template v-else>
      <view class="hint">{{ data?.customer?.name || '客户地址' }} · {{ data?.private ? '仅本业务员可用' : '当前客户的共享地址' }}</view>
      <view v-if="updatedAt" class="hint">最近成功加载：{{ updatedAt }}</view>
      <view class="tools"><button :disabled="loading || busy" @click="refresh">刷新</button>
        <button :disabled="loading || busy || denied" @click="startNew">新增地址</button></view>
      <view v-if="error" class="error">{{ error }}<button :disabled="loading" @click="refresh">重试</button></view>
      <view v-if="actionError" class="error">{{ actionError }}</view>
      <view v-if="loading" class="hint">正在加载地址…</view>
      <view v-if="data && !data.results.length" class="hint">尚无常用地址，可新增后重复使用。</view>
      <view v-for="address in data?.results || []" :key="address.id" class="card">
        <view class="name">{{ address.contact }} · {{ address.phone }} <text v-if="address.is_default" class="badge">默认</text></view>
        <view>{{ address.address }}</view>
        <view class="tools">
          <button v-if="selectMode" :disabled="busy || loading || !!error" @click="select(address)">使用此地址</button>
          <button :disabled="busy || loading" @click="edit(address)">修改</button>
          <button v-if="!address.is_default" :disabled="busy || loading" @click="makeDefault(address)">设为默认</button>
          <button :disabled="busy || loading" @click="remove(address)">停用</button>
        </view>
      </view>
      <view v-if="editing" class="card form">
        <view class="name">{{ form.id ? '修改地址' : '新增地址' }}</view>
        <button v-if="selectMode && !form.id" :disabled="busy" @click="fillOrder">填入本单收件信息</button>
        <label>收件人<input v-model="form.contact" :disabled="busy" maxlength="80" placeholder="收件人" /></label>
        <label>联系电话<input v-model="form.phone" :disabled="busy" maxlength="40" placeholder="联系电话" /></label>
        <label>完整收货地址<textarea v-model="form.address" :disabled="busy" maxlength="200" placeholder="省市区、街道及详细地址" auto-height /></label>
        <label class="default"><switch :checked="form.is_default" :disabled="busy" @change="form.is_default = $event.detail.value" />设为默认地址</label>
        <view class="tools"><button :disabled="busy" @click="editing = false">取消</button><button class="primary" :disabled="busy || loading || denied" @click="save">{{ busy ? '保存中…' : '保存地址' }}</button></view>
      </view>
    </template>
  </view>
</template>
<script setup>
import { ref, reactive, watch } from 'vue'
import { onLoad, onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { api } from '@/utils/request'
import { useAuth } from '@/store/auth'
import { useCart } from '@/store/cart'
import { createIdempotencyKey } from '@/utils/idempotency'
import { usePurchaseResource } from '@/composables/usePurchaseResource'

const auth = useAuth(), cart = useCart(), customerId = ref(null), selectMode = ref(false)
const q = ref(''), applied = ref(''), busy = ref(false), editing = ref(false), actionError = ref('')
const form = reactive({ id: null, contact: '', phone: '', address: '', is_default: false, version: null })
let requestKey = createIdempotencyKey(), active = true, generation = 0
const identity = () => `${auth.user?.id}:${auth.user?.owner_id}`
const customerList = usePurchaseResource(
  ({ page = 1 }) => api.customers(applied.value, page),
  () => !customerId.value ? `customers:${applied.value}` : '', identity,
)
const { data, loading, error, denied, updatedAt, refresh } = usePurchaseResource(
  () => api.customerAddresses(customerId.value), () => customerId.value, identity,
)
function searchCustomers() { applied.value = q.value.trim(); customerList.refresh() }
function moreCustomers() {
  const next = customerList.data.value?.next
  const page = typeof next === 'number' ? next : Number(String(next).match(/[?&]page=(\d+)/)?.[1] || 2)
  customerList.more(page)
}
watch([identity, customerId], () => { generation++; editing.value = false; actionError.value = '' })
watch(denied, value => { if (value) { editing.value = false; Object.assign(form, {contact:'',phone:'',address:''}) } })
onShow(() => { active = true })
onHide(() => { active = false; generation++ })
onUnload(() => { active = false; generation++ })
onLoad(query => {
  auth.ensureAuth()
  customerId.value = Number(query?.customer_id || 0) || null
  selectMode.value = query?.select === '1'
})
function startNew() {
  Object.assign(form, { id: null, contact: '', phone: '', address: '', is_default: false, version: null })
  requestKey = createIdempotencyKey(); actionError.value = ''; editing.value = true
}
function fillOrder() {
  if (String(cart.customer?.id) !== String(customerId.value)) return
  Object.assign(form, { contact: cart.order_header.contact, phone: cart.order_header.contact_phone, address: cart.order_header.ship_to })
}
function edit(address) { Object.assign(form, address); editing.value = true; actionError.value = '' }
function select(address) {
  if (!cart.hasContextForUser(auth.user?.id, auth.user?.owner_id) ||
      String(cart.customer?.id) !== String(customerId.value) || cart.pending_submission) return
  Object.assign(cart.order_header, { contact: address.contact, contact_phone: address.phone, ship_to: address.address })
  cart.historical_address = false; cart.default_address_loaded = true
  uni.navigateBack({ delta: 1 })
}
async function mutation(operation) {
  if (busy.value || loading.value || denied.value) return
  busy.value = true; actionError.value = ''
  const tag = generation, actor = identity()
  try {
    await operation()
    if (!active || tag !== generation || actor !== identity()) return
    editing.value = false; requestKey = createIdempotencyKey()
    await refresh()
  } catch (e) {
    if (!active || tag !== generation || actor !== identity()) return
    const status = Number(e?.statusCode || e?.code)
    if ([401,403,404].includes(status)) {
      data.value = null; updatedAt.value = ''; denied.value = true; editing.value = false
      actionError.value = '该地址已不可访问，请返回并重新选择客户'
      return
    }
    actionError.value = status === 409
      ? '地址已变化，请刷新后重新点击“修改”，再核对保存。当前输入已保留。'
      : e?.data?.detail || e?.message || '保存失败，当前输入已保留，请重试'
  } finally { busy.value = false }
}
function save() {
  if (!form.contact.trim() || !form.address.trim() || form.phone.trim().length < 6 || !/\d/.test(form.phone)) {
    actionError.value = '请完整填写收件人、有效联系电话和地址'; return
  }
  const payload = { contact: form.contact.trim(), phone: form.phone.trim(), address: form.address.trim(), is_default: form.is_default }
  if (form.id) payload.version = form.version
  return mutation(() => api.saveCustomerAddress(customerId.value, form.id, payload, requestKey))
}
function makeDefault(address) {
  return mutation(() => api.saveCustomerAddress(customerId.value, address.id, {version: address.version, is_default: true}))
}
function remove(address) {
  const actor = identity(), customer = customerId.value, tag = generation
  uni.showModal({ title: '停用地址', content: '停用后不再出现在常用地址中，历史订单的收件信息保留。', success: result => {
    if (result.confirm && active && tag === generation && actor === identity() && customer === customerId.value) {
      mutation(() => api.removeCustomerAddress(customer, address.id, address.version))
    }
  } })
}
</script>
<style scoped>
.address-page { min-height: 100vh; padding: 28rpx 24rpx; background: #f5f7fa; box-sizing: border-box; }
.title { font-size: 38rpx; font-weight: 700; }
.hint { color: #64748b; margin: 20rpx 0; }
.card { margin: 20rpx 0; padding: 24rpx; background: white; border-radius: 16rpx; line-height: 1.7; overflow-wrap: anywhere; text-align: left; }
.name { font-weight: 600; }
.badge { color: #166534; background: #dcfce7; padding: 4rpx 12rpx; border-radius: 8rpx; }
.tools, .search { display: flex; flex-wrap: wrap; gap: 12rpx; margin-top: 18rpx; }
.tools button, .search button, .link { margin: 0; width: auto; font-size: 26rpx; }
.search input { flex: 1; min-width: 0; }
.error { color: #9a3412; padding: 16rpx; background: #fff7ed; margin: 16rpx 0; overflow-wrap: anywhere; }
.form label { display: block; margin: 18rpx 0; color: #475569; font-size: 26rpx; }
input, textarea { box-sizing: border-box; width: 100%; border: 1px solid #cbd5e1; padding: 18rpx; border-radius: 8rpx; background: #fff; min-height: 76rpx; }
.primary { background: #2563eb; color: white; }
.default { display: flex !important; align-items: center; }
</style>
