<template>
  <view class="page">
    <view v-if="loading" class="state">正在加载任务…</view>
    <view v-else-if="!task" class="state">任务不存在，或你已无权查看。</view>

    <template v-else>
      <view class="task-card">
        <view class="task-head">
          <view>
            <text class="task-no">{{ task.task_no }}</text>
            <text class="ref-no">来源单：{{ task.ref_no || '-' }}</text>
          </view>
          <text :class="['status', statusClass(task.status)]">{{ task.status_name || task.status }}</text>
        </view>
        <view class="meta">货主：{{ task.owner_name || '-' }}</view>
        <view class="meta">仓库：{{ task.warehouse_name || '-' }}</view>
        <view class="meta">备注：{{ task.remark || '-' }}</view>

        <view class="task-actions">
          <button
            v-if="task.can_claim"
            class="secondary"
            size="mini"
            :loading="actionLoading"
            @click="claimTask"
          >领取任务</button>
          <button
            v-if="task.can_start"
            class="primary"
            size="mini"
            :loading="actionLoading"
            @click="startTask"
          >开始上架</button>
          <text v-if="task.is_assigned_to_me && !task.can_record" class="completed">当前任务已结束</text>
          <text v-else-if="!task.is_assigned_to_me && !task.can_claim" class="notice">请由任务领取人执行上架</text>
        </view>
      </view>

      <view v-if="locationPickerLineId" class="location-picker">
        <view class="picker-title">为 {{ lineName(locationPickerLineId) }} 选择目标库位</view>
        <view class="search-row">
          <input
            v-model="locationSearch"
            class="search-input"
            placeholder="输入库位编码或名称"
            confirm-type="search"
            @confirm="searchLocations"
          />
          <button class="secondary" size="mini" :loading="locationLoading" @click="searchLocations">查询</button>
          <button class="link" size="mini" @click="closeLocationPicker">关闭</button>
        </view>
        <view v-if="locationLoading" class="picker-state">正在查询库位…</view>
        <view v-else-if="locationResults.length" class="location-results">
          <view
            v-for="location in locationResults"
            :key="location.id"
            class="location-item"
            @click="selectLocation(location)"
          >
            <text class="location-code">{{ location.code }}</text>
            <text class="location-name">{{ location.name || '未命名库位' }}</text>
          </view>
        </view>
        <view v-else class="picker-state">未找到可用库位</view>
      </view>

      <view class="section-title">上架明细</view>
      <view v-for="line in task.lines || []" :key="line.id" class="line-card">
        <view class="line-top">
          <view>
            <text class="product-name">{{ line.product_name || line.product_sku || `商品 #${line.product_id}` }}</text>
            <text class="sku">{{ line.product_sku || '-' }}</text>
          </view>
          <text :class="['line-status', line.finished_at ? 'line-done' : '']">
            {{ line.finished_at ? '已完成' : '待上架' }}
          </text>
        </view>

        <view class="quantity-summary">
          计划 {{ formatQty(line.qty_plan) }} · 已上架 {{ formatQty(line.qty_done) }} · 待上架 {{ formatQty(line.qty_pending) }}
        </view>
        <view class="source-location">来源库位：{{ line.from_location_code || '-' }}</view>

        <template v-if="lineForms[line.id]">
          <view class="field-group">
            <text class="field-label">目标库位 *</text>
            <view class="location-current">
              <text>{{ selectedLocationLabel(line.id) || '尚未选择' }}</text>
              <button
                class="link"
                size="mini"
                :disabled="!task.can_record || line.finished_at"
                @click="openLocationPicker(line)"
              >查询库位</button>
            </view>
          </view>

          <view class="field-group">
            <text class="field-label">本次上架数量 *</text>
            <input
              v-model="lineForms[line.id].qty"
              class="number-input"
              type="digit"
              :disabled="!task.can_record || line.finished_at"
              @input="resetRequestId(lineForms[line.id])"
            />
            <text class="field-help">本次输入增量，不能超过待上架数量。</text>
          </view>

          <button
            v-if="task.can_record && !line.finished_at"
            class="submit"
            :loading="savingLineId === line.id"
            :disabled="savingLineId !== null"
            @click="submitPutaway(line)"
          >确认上架</button>
        </template>
      </view>

      <view class="idempotency-note">网络异常后请直接再次提交同一行，系统会使用本次请求编号防止重复上架。</view>
    </template>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { api, createIdempotencyUuid } from '@/utils/request'

const taskId = ref(null)
const task = ref(null)
const loading = ref(false)
const actionLoading = ref(false)
const savingLineId = ref(null)
const lineForms = ref({})
const locationPickerLineId = ref(null)
const locationSearch = ref('')
const locationResults = ref([])
const locationLoading = ref(false)

const canRecord = computed(() => Boolean(task.value?.can_record))

function toNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number : 0
}

function formatQty(value) {
  return String(Number(toNumber(value).toFixed(3)))
}

function statusClass(status) {
  if (status === 'COMPLETED') return 'status-done'
  if (status === 'IN_PROGRESS') return 'status-progress'
  return 'status-ready'
}

function putawayForm(line) {
  const putaway = line.putaway || {}
  const locationId = putaway.to_location_id || line.to_location_id || null
  const locationCode = putaway.to_location_code || line.to_location_code || ''
  return {
    locationId,
    locationLabel: locationCode,
    qty: formatQty(line.qty_pending),
    requestId: createIdempotencyUuid(),
  }
}

function hydrateTask(nextTask, resetLineId = null) {
  task.value = nextTask
  const previous = lineForms.value
  const forms = {}
  for (const line of nextTask?.lines || []) {
    const existing = previous[line.id]
    if (existing && !line.finished_at && line.id !== resetLineId) {
      forms[line.id] = existing
    } else {
      forms[line.id] = putawayForm(line)
    }
  }
  lineForms.value = forms
}

async function loadTask() {
  if (!taskId.value) return
  loading.value = true
  try {
    hydrateTask(await api.inboundPdaTask(taskId.value))
  } catch (error) {
    console.error('加载上架任务失败', error)
    task.value = null
  } finally {
    loading.value = false
  }
}

function resetRequestId(form) {
  if (savingLineId.value === null) form.requestId = createIdempotencyUuid()
}

function lineName(lineId) {
  const line = (task.value?.lines || []).find((item) => item.id === lineId)
  return line?.product_name || line?.product_sku || '当前商品'
}

function selectedLocationLabel(lineId) {
  return lineForms.value[lineId]?.locationLabel || ''
}

async function claimTask() {
  if (!task.value || actionLoading.value) return
  actionLoading.value = true
  try {
    hydrateTask(await api.claimInboundPdaTask(task.value.id))
    uni.showToast({ title: '任务已领取', icon: 'success' })
  } catch (error) {
    console.error('领取上架任务失败', error)
  } finally {
    actionLoading.value = false
  }
}

async function startTask() {
  if (!task.value || actionLoading.value) return
  actionLoading.value = true
  try {
    hydrateTask(await api.startInboundPdaTask(task.value.id))
    uni.showToast({ title: '已开始上架', icon: 'success' })
  } catch (error) {
    console.error('开始上架失败', error)
  } finally {
    actionLoading.value = false
  }
}

async function openLocationPicker(line) {
  if (!canRecord.value || line.finished_at) return
  locationPickerLineId.value = line.id
  locationSearch.value = ''
  locationResults.value = []
  await searchLocations()
}

function closeLocationPicker() {
  locationPickerLineId.value = null
  locationResults.value = []
}

async function searchLocations() {
  if (!task.value || !locationPickerLineId.value || locationLoading.value) return
  locationLoading.value = true
  try {
    const response = await api.inboundPdaLocations(task.value.id, locationSearch.value.trim())
    locationResults.value = Array.isArray(response) ? response : (response?.results || [])
  } catch (error) {
    console.error('查询目标库位失败', error)
    locationResults.value = []
  } finally {
    locationLoading.value = false
  }
}

function selectLocation(location) {
  const form = lineForms.value[locationPickerLineId.value]
  if (!form) return
  form.locationId = location.id
  form.locationLabel = location.name ? `${location.code} · ${location.name}` : location.code
  resetRequestId(form)
  closeLocationPicker()
}

function validatePutaway(line, form) {
  const qty = toNumber(form.qty)
  const pending = toNumber(line.qty_pending)
  if (!form.locationId) return '请选择目标库位'
  if (Number(form.locationId) === Number(line.from_location_id)) return '目标库位不能与来源库位相同'
  if (qty <= 0) return '请输入大于零的上架数量'
  if (qty > pending) return '本次上架数量不能超过待上架数量'
  return ''
}

async function submitPutaway(line) {
  if (!task.value || savingLineId.value !== null) return
  const form = lineForms.value[line.id]
  const message = validatePutaway(line, form)
  if (message) {
    uni.showToast({ title: message, icon: 'none' })
    return
  }

  savingLineId.value = line.id
  try {
    const response = await api.recordInboundPutaway(task.value.id, {
      request_id: form.requestId,
      line_id: line.id,
      to_location_id: Number(form.locationId),
      qty: toNumber(form.qty),
    })
    hydrateTask(response?.task || response, line.id)
    uni.showToast({ title: response?.idempotent ? '已确认此前上架记录' : '上架已保存', icon: 'success' })
  } catch (error) {
    // Preserve form.requestId for a safe retry of an uncertain network write.
    console.error('保存上架失败', error)
  } finally {
    savingLineId.value = null
  }
}

onLoad((options) => {
  const id = Number(options?.task_id)
  if (!Number.isInteger(id) || id <= 0) {
    uni.showToast({ title: '缺少有效的任务编号', icon: 'none' })
    return
  }
  taskId.value = id
  loadTask()
})
</script>

<style scoped>
.page { min-height: 100vh; padding: 24rpx; padding-bottom: 52rpx; background: #f6f8fb; box-sizing: border-box; }
.state { padding: 100rpx 24rpx; color: #718096; text-align: center; font-size: 28rpx; }
.task-card, .line-card, .location-picker { margin-bottom: 20rpx; padding: 24rpx; border-radius: 16rpx; background: #fff; box-shadow: 0 4rpx 18rpx rgba(15, 23, 42, .06); }
.task-head, .line-top, .location-current { display: flex; justify-content: space-between; gap: 16rpx; align-items: flex-start; }
.task-no { display: block; color: #172033; font-size: 30rpx; font-weight: 700; }
.ref-no, .sku { display: block; margin-top: 8rpx; color: #718096; font-size: 23rpx; }
.status, .line-status { flex-shrink: 0; padding: 6rpx 12rpx; border-radius: 999rpx; font-size: 22rpx; }
.status-ready { color: #9a6700; background: #fff6d8; }
.status-progress { color: #0c6b54; background: #dcf8ed; }
.status-done, .line-done { color: #475569; background: #edf1f5; }
.meta { margin-top: 12rpx; color: #536274; font-size: 25rpx; }
.task-actions { display: flex; align-items: center; gap: 14rpx; margin-top: 20rpx; }
.task-actions button { margin: 0; }
.primary { color: #fff; background: #1d70d6; }
.secondary { color: #175db7; background: #e7f0ff; }
.link { color: #475569; background: #f2f4f7; }
.completed { color: #0c6b54; font-size: 24rpx; }
.notice { color: #9a6700; font-size: 24rpx; }
.section-title { margin: 30rpx 0 16rpx; color: #172033; font-size: 30rpx; font-weight: 700; }
.product-name { display: block; max-width: 460rpx; color: #172033; font-size: 28rpx; font-weight: 650; }
.quantity-summary { margin: 16rpx 0 8rpx; color: #175db7; font-size: 24rpx; }
.source-location { margin-top: 9rpx; color: #536274; font-size: 24rpx; }
.field-group { margin-top: 20rpx; }
.field-label { display: block; margin-bottom: 9rpx; color: #536274; font-size: 24rpx; }
.number-input, .search-input { width: 100%; height: 68rpx; padding: 0 14rpx; box-sizing: border-box; border: 1rpx solid #d7dfeb; border-radius: 10rpx; background: #fff; color: #172033; font-size: 26rpx; }
.location-current { min-height: 68rpx; padding: 0 0 0 14rpx; align-items: center; border: 1rpx solid #d7dfeb; border-radius: 10rpx; color: #334155; font-size: 25rpx; }
.location-current text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.location-current button { margin: 0; }
.field-help { display: block; margin-top: 8rpx; color: #718096; font-size: 22rpx; }
.submit { margin-top: 26rpx; color: #fff; background: #1d70d6; font-size: 27rpx; }
.location-picker { border: 2rpx solid #9cc8ff; background: #fafdff; }
.picker-title { color: #1d4f91; font-size: 27rpx; font-weight: 650; }
.search-row { display: flex; align-items: center; gap: 12rpx; margin-top: 18rpx; }
.search-input { flex: 1; width: auto; }
.search-row button { margin: 0; }
.picker-state { padding: 28rpx 0 8rpx; color: #718096; font-size: 24rpx; text-align: center; }
.location-results { margin-top: 16rpx; border-top: 1rpx solid #e5ebf3; }
.location-item { display: flex; justify-content: space-between; gap: 16rpx; padding: 17rpx 4rpx; border-bottom: 1rpx solid #e5ebf3; }
.location-code { color: #172033; font-size: 26rpx; font-weight: 650; }
.location-name { color: #718096; font-size: 23rpx; text-align: right; }
.idempotency-note { margin: 28rpx 8rpx 0; color: #718096; font-size: 22rpx; line-height: 1.55; }
</style>
