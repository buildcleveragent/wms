<template>
  <view class="page">
    <view class="title">新建盘点</view>
    <view class="hint">先保存草稿，核对范围和明细后再发布。发布才会冻结库存。</view>
    <view v-if="error || localError" class="error">{{ localError || error }}<button size="mini" @click="initialize">重新加载选项</button></view>
    <view v-if="initialLoading" class="hint">加载可选范围…</view>
    <view v-else-if="!manager" class="card">无盘点管理权限<button @click="initialize">重试</button></view>
    <view v-else class="card">
      <text class="label">仓库 *</text>
      <picker :range="metadata.warehouses" range-key="name" @change="chooseWarehouse"><view class="select">{{ selectedName(metadata.warehouses, form.warehouse_id) || '选择仓库' }}</view></picker>
      <text class="label">货主 *</text>
      <picker :disabled="!form.warehouse_id || optionsLoading" :range="metadata.owners" range-key="name" @change="chooseOwner"><view class="select">{{ selectedName(metadata.owners, form.owner_id) || '选择货主' }}</view></picker>
      <text class="label">盘点范围 *</text>
      <picker :range="countScopes" range-key="name" @change="chooseScope"><view class="select">{{ countScopes.find(s => s.id === form.scope)?.name }}</view></picker>
      <text class="label">子仓（可选）</text>
      <picker :range="subwarehouses" range-key="name" @change="chooseSubwarehouse"><view class="select">{{ selectedName(subwarehouses, form.subwarehouse_id) }}</view></picker>
      <template v-if="form.scope === 'ZONE'">
        <text class="label">库区 *</text><picker :range="metadata.zones" range-key="name" @change="form.zone_type = metadata.zones[Number($event.detail.value)].id"><view class="select">{{ selectedName(metadata.zones, form.zone_type) || '选择库区' }}</view></picker>
      </template>
      <template v-if="form.scope === 'SKU' || form.scope === 'LOC'">
        <text class="label">{{ form.scope === 'SKU' ? '选择商品 *' : '指定库位或输入前缀 *' }}</text>
        <view v-if="selectedItem" class="selected">已选：{{ itemLabel(selectedItem) }}<button size="mini" @click="clearSelection">清除</button></view>
        <view class="search"><input v-model="search" :placeholder="form.scope === 'SKU' ? '名称、品牌型号、编码或条码' : '库位编码或名称'" @confirm="loadChoices(false)"/><button size="mini" :disabled="optionsLoading || !form.owner_id" @click="loadChoices(false)">查询</button></view>
        <view v-for="item in choices" :key="item.id" class="choice" @click="chooseItem(item)">{{ itemLabel(item) }}</view>
        <view v-if="searched && !choices.length && !optionsLoading" class="hint">无匹配选项</view>
        <button v-if="nextPage" size="mini" :disabled="optionsLoading" @click="loadChoices(true)">加载更多</button>
        <template v-if="form.scope === 'LOC'"><text class="label">或按库位前缀</text><input v-model="form.location_prefix" class="input" placeholder="例如 A-01-" @input="clearSelection"/></template>
      </template>
      <text class="label">批次（可选）</text><input v-model="form.batch_no" class="input" maxlength="100" placeholder="留空包含所有批次"/>
      <view class="switch-row"><text>盲盘</text><switch :checked="form.blind" @change="form.blind = $event.detail.value"/></view>
      <view class="switch-row"><text>排除零库存</text><switch :checked="form.exclude_zero_onhand" @change="form.exclude_zero_onhand = $event.detail.value"/></view>
      <text class="label">复盘阈值（绝对数量）</text><input v-model="form.recount_threshold" class="input" type="digit"/>
      <text class="label">最多生成行数（1–10000）</text><input v-model="form.max_lines" class="input" type="number"/>
      <text class="label">备注</text><textarea v-model="form.task_remark" class="input notes" maxlength="200" placeholder="可选"/>
      <button class="save" type="primary" :disabled="busy || optionsLoading" @click="save">{{ busy ? '保存中…' : createdTaskId ? '查看已保存草稿' : '保存草稿' }}</button>
    </view>
  </view>
</template>
<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { onShow, onHide, onUnload } from '@dcloudio/uni-app'
import { useAuth } from '@/store/auth'
import { api } from '@/utils/request'
import { countScopes, countPayload, countError, useCountSession } from '@/utils/counting'
const auth = useAuth(), session = useCountSession({ api, auth })
const { manager, error, busy } = session
const initialLoading = ref(true), optionsLoading = ref(false), localError = ref('')
const createdTaskId = ref(null)
const metadata = ref({ warehouses: [], owners: [], subwarehouses: [], zones: [] })
const form = reactive({ warehouse_id: null, owner_id: null, scope: 'ALL', subwarehouse_id: null, zone_type: null, location_id: null, location_prefix: '', product_id: null, batch_no: '', blind: true, exclude_zero_onhand: true, max_lines: '1000', recount_threshold: '0', task_remark: '' })
const choices = ref([]), nextPage = ref(null), search = ref(''), selectedItem = ref(null), searched = ref(false)
const subwarehouses = computed(() => [{ id: null, name: '全部子仓' }, ...metadata.value.subwarehouses])
let sequence = 0, visible = false
const selectedName = (rows, id) => rows.find(row => row.id === id)?.name
const itemLabel = item => `${item.code} · ${item.product_display_name || item.name}${item.spec ? ' · ' + item.spec : ''}`
function clearSelection() { form.product_id = null; form.location_id = null; selectedItem.value = null }
function clearChoices() { sequence++; optionsLoading.value = false; choices.value = []; nextPage.value = null; search.value = ''; searched.value = false; clearSelection() }
function currentParams() { return { warehouse_id: form.warehouse_id || undefined, owner_id: form.owner_id || undefined, subwarehouse_id: form.subwarehouse_id || undefined } }
async function options(params, apply) {
  const seq = ++sequence, account = auth.access
  optionsLoading.value = true; localError.value = ''
  try {
    const result = await api.countOptions(params)
    if (seq === sequence && visible && account === auth.access) apply(result)
  } catch (err) {
    if (seq === sequence && visible && account === auth.access) {
      localError.value = countError(err)
      if (Number(err?.statusCode) === 403) { session.denied.value = true; metadata.value = { warehouses: [], owners: [], subwarehouses: [], zones: [] }; choices.value = [] }
    }
  } finally { if (seq === sequence) optionsLoading.value = false }
}
async function initialize() {
  visible = true; initialLoading.value = true; localError.value = ''
  const account = auth.access
  try {
    await session.authorize()
    if (!visible || account !== auth.access) return
    if (manager.value) await options(currentParams(), result => { metadata.value = result })
  } catch (err) { if (visible && account === auth.access) localError.value = countError(err) }
  finally { initialLoading.value = false }
}
async function chooseWarehouse(event) {
  form.warehouse_id = metadata.value.warehouses[Number(event.detail.value)]?.id
  form.owner_id = null; form.subwarehouse_id = null; form.zone_type = null; form.location_prefix = ''
  clearChoices(); metadata.value.owners = []; metadata.value.subwarehouses = []
  await options(currentParams(), result => { metadata.value = result })
}
function chooseOwner(event) { form.owner_id = metadata.value.owners[Number(event.detail.value)]?.id; clearChoices(); if (['SKU', 'LOC'].includes(form.scope)) loadChoices(false) }
function chooseScope(event) {
  form.scope = countScopes[Number(event.detail.value)].id
  form.zone_type = null; form.location_prefix = ''; clearChoices()
  if (['SKU', 'LOC'].includes(form.scope) && form.owner_id) loadChoices(false)
}
function chooseSubwarehouse(event) { form.subwarehouse_id = subwarehouses.value[Number(event.detail.value)].id; clearChoices(); if (form.scope === 'LOC' && form.owner_id) loadChoices(false) }
async function loadChoices(more = false) {
  if (!form.warehouse_id || !form.owner_id) { localError.value = '请先选择仓库和货主'; return }
  await options({ ...currentParams(), kind: form.scope === 'SKU' ? 'products' : 'locations', search: search.value, page: more ? nextPage.value : 1 }, result => {
    choices.value = more ? [...choices.value, ...result.results] : result.results
    nextPage.value = result.next; searched.value = true
  })
}
function chooseItem(item) { selectedItem.value = item; if (form.scope === 'SKU') form.product_id = item.id; else { form.location_id = item.id; form.location_prefix = '' } }
async function save() {
  if (busy.value) return
  if (createdTaskId.value) {
    uni.redirectTo({ url: `/pages/inventory/stocktake/detail?task_id=${createdTaskId.value}` })
    return
  }
  localError.value = ''
  try {
    const result = await session.act('create', countPayload(form))
    if (!result) return
    createdTaskId.value = result.id
    uni.redirectTo({ url: `/pages/inventory/stocktake/detail?task_id=${result.id}${result.truncated ? '&truncated=1' : ''}` })
  } catch (err) { localError.value = countError(err) }
}
watch(search, () => { sequence++; optionsLoading.value = false; choices.value = []; nextPage.value = null; searched.value = false }, { flush: 'sync' })
watch(() => auth.access, () => { createdTaskId.value = null; sequence++; clearChoices(); metadata.value = { warehouses: [], owners: [], subwarehouses: [], zones: [] }; form.warehouse_id = null; form.owner_id = null; form.subwarehouse_id = null; form.location_prefix = ''; form.batch_no = ''; form.task_remark = '' })
onShow(initialize)
onHide(() => { visible = false; sequence++; optionsLoading.value = false; session.invalidate() })
onUnload(session.dispose)
</script>
<style scoped>
.page{padding:24rpx;background:#f5f7fa;min-height:100vh;box-sizing:border-box}.title{font-size:36rpx;font-weight:700}.hint{color:#64748b;font-size:26rpx;margin:18rpx 0;line-height:1.6}.card{padding:24rpx;background:white;border-radius:16rpx}.label{display:block;font-size:27rpx;font-weight:600;margin:24rpx 0 12rpx}.input,.select{border:1rpx solid #cbd5e1;border-radius:10rpx;padding:16rpx;font-size:28rpx;min-height:44rpx}.input{height:48rpx}.switch-row,.search{display:flex;align-items:center;justify-content:space-between;gap:12rpx;margin-top:24rpx}.search input{min-width:0;flex:1;border-bottom:1rpx solid #cbd5e1;padding:12rpx 0;font-size:25rpx}.search button{margin:0;flex-shrink:0}.choice{padding:20rpx 0;border-bottom:1rpx solid #e2e8f0;font-size:26rpx;overflow-wrap:anywhere}.selected{background:#eff6ff;padding:16rpx;line-height:1.6;font-size:26rpx}.notes{width:100%;box-sizing:border-box;height:140rpx}.save{margin-top:32rpx}.error{color:#b91c1c;background:#fff1f2;padding:20rpx;line-height:1.5;margin:16rpx 0}
</style>
