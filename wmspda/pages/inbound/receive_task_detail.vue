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
          >开始收货</button>
          <text v-if="task.is_assigned_to_me && !task.can_record" class="completed">当前任务已结束</text>
          <text v-else-if="!task.is_assigned_to_me && !task.can_claim" class="notice">请由任务领取人执行收货</text>
        </view>
      </view>

      <view v-if="locationPickerLineId" class="location-picker">
        <view class="picker-title">为 {{ lineName(locationPickerLineId) }} 选择收货库位</view>
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

      <view class="section-title">收货明细</view>
      <view v-for="line in task.lines || []" :key="line.id" class="line-card">
        <view class="line-top">
          <view>
            <text class="product-name">{{ line.product_name || line.product_sku || `商品 #${line.product_id}` }}</text>
            <text class="sku">{{ line.product_sku || '-' }}</text>
          </view>
          <text :class="['line-status', line.finished_at ? 'line-done' : '']">
            {{ line.finished_at ? '已完成' : '待收货' }}
          </text>
        </view>
        <view class="quantity-summary">
          计划 {{ formatQty(line.qty_plan) }} · 已登记 {{ formatQty(line.qty_done) }} · 待处理 {{ formatQty(line.qty_pending) }}
        </view>

        <template v-if="lineForms[line.id]">
          <view class="field-group">
            <text class="field-label">收货库位 *</text>
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

          <view class="quantity-grid">
            <view class="field-group">
              <text class="field-label">良品数量 *</text>
              <input
                v-model="lineForms[line.id].qtyOk"
                class="number-input"
                type="digit"
                :disabled="!task.can_record || line.finished_at"
                @input="resetRequestId(lineForms[line.id])"
              />
            </view>
            <view class="field-group">
              <text class="field-label">破损数量</text>
              <input
                v-model="lineForms[line.id].qtyDamage"
                class="number-input"
                type="digit"
                :disabled="!task.can_record || line.finished_at"
                @input="resetRequestId(lineForms[line.id])"
              />
            </view>
            <view class="field-group">
              <text class="field-label">拒收数量</text>
              <input
                v-model="lineForms[line.id].qtyReject"
                class="number-input"
                type="digit"
                :disabled="!task.can_record || line.finished_at"
                @input="resetRequestId(lineForms[line.id])"
              />
            </view>
          </view>

          <view v-if="numeric(lineForms[line.id].qtyDamage) > 0" class="field-group">
            <text class="field-label">破损原因 *</text>
            <input
              v-model="lineForms[line.id].damageReasonCode"
              class="text-input"
              placeholder="例如：包装破损"
              :disabled="!task.can_record || line.finished_at"
              @input="resetRequestId(lineForms[line.id])"
            />
          </view>
          <view v-if="numeric(lineForms[line.id].qtyReject) > 0" class="field-group">
            <text class="field-label">拒收原因 *</text>
            <input
              v-model="lineForms[line.id].rejectReasonCode"
              class="text-input"
              placeholder="例如：规格不符"
              :disabled="!task.can_record || line.finished_at"
              @input="resetRequestId(lineForms[line.id])"
            />
          </view>

          <view class="field-group">
            <text class="field-label">批次号</text>
            <input
              v-model="lineForms[line.id].lotNo"
              class="text-input"
              placeholder="可选"
              :disabled="!task.can_record || line.finished_at"
              @input="resetRequestId(lineForms[line.id])"
            />
          </view>
          <view class="date-grid">
            <view class="field-group">
              <text class="field-label">生产日期</text>
              <input
                v-model="lineForms[line.id].mfgDate"
                class="text-input"
                placeholder="YYYY-MM-DD"
                :disabled="!task.can_record || line.finished_at"
                @input="resetRequestId(lineForms[line.id])"
              />
            </view>
            <view class="field-group">
              <text class="field-label">有效期</text>
              <input
                v-model="lineForms[line.id].expDate"
                class="text-input"
                placeholder="YYYY-MM-DD"
                :disabled="!task.can_record || line.finished_at"
                @input="resetRequestId(lineForms[line.id])"
              />
            </view>
          </view>

          <view class="field-group">
            <text class="field-label">差异原因</text>
            <input
              v-model="lineForms[line.id].varianceReason"
              class="text-input"
              placeholder="数量与计划不一致时，结束该行前必须填写"
              :disabled="!task.can_record || line.finished_at"
              @input="resetRequestId(lineForms[line.id])"
            />
          </view>
          <view class="finalize-row">
            <switch
              :checked="lineForms[line.id].finalize"
              :disabled="!task.can_record || line.finished_at"
              color="#1d70d6"
              @change="toggleFinalize(lineForms[line.id], $event)"
            />
            <text>结束此收货行（数量有差异时需填写差异原因）</text>
          </view>

          <button
            v-if="task.can_record && !line.finished_at"
            class="submit"
            :loading="savingLineId === line.id"
            :disabled="savingLineId !== null"
            @click="submitReceipt(line)"
          >
            {{ lineForms[line.id].finalize ? '保存并结束该行' : '保存收货数量' }}
          </button>
        </template>
      </view>

      <view class="idempotency-note">网络异常后请直接再次提交同一行，系统会使用本次请求编号防止重复入账。</view>
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

function numeric(value) {
  return toNumber(value)
}

function formatQty(value) {
  return String(Number(toNumber(value).toFixed(3)))
}

function statusClass(status) {
  if (status === 'COMPLETED') return 'status-done'
  if (status === 'IN_PROGRESS') return 'status-progress'
  return 'status-ready'
}

function receiptForm(line) {
  const receive = line.receive || {}
  const recordedTotal = toNumber(receive.qty_ok) + toNumber(receive.qty_damage) + toNumber(receive.qty_reject)
  // ReceiveLineExtra is created together with every task line, so an untouched
  // line also serializes as three zeroes.  Default that case to the planned
  // quantity; retain the real values once an operator has recorded anything.
  const defaultGoodQty = !line.finished_at && toNumber(line.qty_done) === 0 && recordedTotal === 0
    ? line.qty_plan
    : receive.qty_ok
  return {
    locationId: null,
    locationLabel: '',
    qtyOk: formatQty(defaultGoodQty),
    qtyDamage: formatQty(receive.qty_damage),
    qtyReject: formatQty(receive.qty_reject),
    lotNo: receive.lot_no || '',
    mfgDate: receive.mfg_date || '',
    expDate: receive.exp_date || '',
    damageReasonCode: receive.damage_reason_code || '',
    rejectReasonCode: receive.reject_reason_code || '',
    varianceReason: '',
    finalize: Boolean(line.finished_at),
    requestId: createIdempotencyUuid(),
  }
}

function hydrateTask(nextTask, resetLineId = null) {
  task.value = nextTask
  const previous = lineForms.value
  const forms = {}
  for (const line of nextTask?.lines || []) {
    // Preserve unfinished local input when merely refreshing the task header.
    forms[line.id] = previous[line.id] && !line.finished_at && line.id !== resetLineId
      ? previous[line.id]
      : receiptForm(line)
  }
  lineForms.value = forms
}

async function loadTask() {
  if (!taskId.value) return
  loading.value = true
  try {
    hydrateTask(await api.inboundPdaTask(taskId.value))
  } catch (error) {
    console.error('加载收货任务失败', error)
    task.value = null
  } finally {
    loading.value = false
  }
}

function resetRequestId(form) {
  if (savingLineId.value === null) form.requestId = createIdempotencyUuid()
}

function toggleFinalize(form, event) {
  form.finalize = Boolean(event?.detail?.value)
  resetRequestId(form)
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
    console.error('领取收货任务失败', error)
  } finally {
    actionLoading.value = false
  }
}

async function startTask() {
  if (!task.value || actionLoading.value) return
  actionLoading.value = true
  try {
    hydrateTask(await api.startInboundPdaTask(task.value.id))
    uni.showToast({ title: '已开始收货', icon: 'success' })
  } catch (error) {
    console.error('开始收货失败', error)
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
    console.error('查询收货库位失败', error)
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

function validateReceipt(line, form) {
  const qtyOk = numeric(form.qtyOk)
  const qtyDamage = numeric(form.qtyDamage)
  const qtyReject = numeric(form.qtyReject)
  const total = qtyOk + qtyDamage + qtyReject
  const planned = numeric(line.qty_plan)

  if (!form.locationId) return '请选择收货库位'
  if (qtyOk < 0 || qtyDamage < 0 || qtyReject < 0) return '收货数量不能小于零'
  if (total === 0 && !form.finalize) return '数量均为零时，请明确结束该差异行'
  if (qtyDamage > 0 && !form.damageReasonCode.trim()) return '请填写破损原因'
  if (qtyReject > 0 && !form.rejectReasonCode.trim()) return '请填写拒收原因'
  if ((total > planned || (form.finalize && total !== planned)) && !form.varianceReason.trim()) {
    return '数量与计划不一致时，请填写差异原因'
  }
  return ''
}

async function submitReceipt(line) {
  if (!task.value || savingLineId.value !== null) return
  const form = lineForms.value[line.id]
  const message = validateReceipt(line, form)
  if (message) {
    uni.showToast({ title: message, icon: 'none' })
    return
  }

  savingLineId.value = line.id
  try {
    const response = await api.recordInboundReceipt(task.value.id, {
      request_id: form.requestId,
      line_id: line.id,
      location_id: Number(form.locationId),
      qty_ok: numeric(form.qtyOk),
      qty_damage: numeric(form.qtyDamage),
      qty_reject: numeric(form.qtyReject),
      lot_no: form.lotNo.trim(),
      mfg_date: form.mfgDate || null,
      exp_date: form.expDate || null,
      damage_reason_code: form.damageReasonCode.trim(),
      reject_reason_code: form.rejectReasonCode.trim(),
      finalize: Boolean(form.finalize),
      variance_reason: form.varianceReason.trim(),
    })
    hydrateTask(response?.task || response, line.id)
    uni.showToast({ title: response?.idempotent ? '已确认此前收货记录' : '收货已保存', icon: 'success' })
  } catch (error) {
    // Keep form.requestId intact: retrying this exact payload remains safe if
    // the network failed after the server committed it.
    console.error('保存收货失败', error)
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
.task-head, .line-top, .location-current, .finalize-row { display: flex; justify-content: space-between; gap: 16rpx; align-items: flex-start; }
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
.field-group { margin-top: 18rpx; }
.field-label { display: block; margin-bottom: 9rpx; color: #536274; font-size: 24rpx; }
.quantity-grid, .date-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14rpx; }
.date-grid { grid-template-columns: repeat(2, 1fr); }
.number-input, .text-input, .search-input { height: 66rpx; padding: 0 14rpx; box-sizing: border-box; border: 1rpx solid #d7dfeb; border-radius: 10rpx; background: #fff; color: #172033; font-size: 25rpx; }
.location-current { min-height: 66rpx; padding: 0 0 0 14rpx; align-items: center; border: 1rpx solid #d7dfeb; border-radius: 10rpx; color: #334155; font-size: 25rpx; }
.location-current text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.location-current button { margin: 0; }
.finalize-row { align-items: center; margin-top: 20rpx; color: #475569; font-size: 24rpx; }
.submit { margin-top: 24rpx; color: #fff; background: #1d70d6; font-size: 27rpx; }
.location-picker { border: 2rpx solid #9cc8ff; background: #fafdff; }
.picker-title { color: #1d4f91; font-size: 27rpx; font-weight: 650; }
.search-row { display: flex; align-items: center; gap: 12rpx; margin-top: 18rpx; }
.search-input { flex: 1; }
.search-row button { margin: 0; }
.picker-state { padding: 28rpx 0 8rpx; color: #718096; font-size: 24rpx; text-align: center; }
.location-results { margin-top: 16rpx; border-top: 1rpx solid #e5ebf3; }
.location-item { display: flex; justify-content: space-between; gap: 16rpx; padding: 17rpx 4rpx; border-bottom: 1rpx solid #e5ebf3; }
.location-code { color: #172033; font-size: 26rpx; font-weight: 650; }
.location-name { color: #718096; font-size: 23rpx; text-align: right; }
.idempotency-note { margin: 28rpx 8rpx 0; color: #718096; font-size: 22rpx; line-height: 1.55; }
</style>
