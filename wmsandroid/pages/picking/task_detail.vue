<template> 
  <view class="page">
    <!-- 任务头信息 -->
    <view class="card-first">
      <view class="row-first">
        <text class="title">拣货任务</text>
      </view>
      <view class="row-first">
        <text>任务号：{{ task?.task_no || taskId }}</text>
      </view>
      <view class="row-meta">
        <text>货主：{{ task?.owner_name || '-' }}</text>
        <text style="margin-left: 24rpx;">仓库：{{ task?.warehouse_name || '-' }}</text>
      </view>
      <view class="row-meta">
        <text>状态：{{ task?.status }}</text>
        <text style="margin-left: 24rpx;">复核：{{ task?.review_status || '-' }}</text>
        <text style="margin-left: 24rpx;">过账：{{ task?.posting_status || '-' }}</text>
      </view>
      <view v-if="task?.is_warehouse_assisted" class="assisted-badge">仓库代办出库</view>
    </view>

    <view v-if="pendingScan" class="pending-scan">
      <text>拣货结果待确认：{{ pendingScan.payload.barcode }} × {{ pendingScan.payload.qty }}</text>
      <text>保留原请求编号，确认结果后再继续拣货。</text>
      <button :disabled="scanning" @click="retryScan">继续确认结果</button>
    </view>
    <view v-if="recoveryError" class="pending-scan">{{ recoveryError }}</view>
    <!-- 扫码 + 数量 -->
    <view v-if="isPickable" class="scan-bar">
			    <input
        class="input flex-input"
        v-model="scanBarcode"
        :disabled="scanLocked"
        placeholder="扫描或输入条码"
        @confirm="submitScan()"
      />
      <input
        class="input qty-input"
        type="number"
        v-model="scanQty"
        :disabled="scanLocked"
        @confirm="submitScan()"
      />
      <button class="btn-outline" :disabled="scanLocked || adjustingCount > 0" @click="handleScan">扫码</button>
      <button class="btn-outline" :disabled="scanLocked || adjustingCount > 0" @click="submitScan()">
        {{ scanning ? '录入中…' : '录入' }}
      </button>
    </view>

    <!-- 任务行列表 -->
    <view class="content">
      <view
        v-for="(ln, i) in lines"
        :key="ln.id ?? i"
        :class="['row item', { odd: i % 2 === 0 }]"
      >
        <view class="col-info">
          <text class="name">{{ ln.product_name || ln.product_sku || ln.product_id }}</text>
          <view class="meta">
            <text>货位：{{ ln.from_loc_code || '-' }}</text>
          </view>

          <!-- 原有计划/已拣显示 -->
          <view class="qty-row">
            <text>计划：{{ formatQty(ln.qty_plan) }}</text>
            <text style="margin-left: 24rpx;">已拣：{{ formatQty(ln.qty_done) }}</text>
          </view>

		  <view class="qty-edit-row">
		    <text class="qty-edit-label">当前拣货数：</text>
		    <input
		      class="line-qty-input"
		      type="number"
		      :value="ln._edit_qty_done"
		      placeholder=""
		      @input="(e) => onEditQty(e, ln)"
				  @blur="applyManualQty(ln)"
				  :disabled="!isPickable || scanLocked || isAdjustingLine(ln)"

		    />
		    <text v-if="isAdjustingLine(ln)" class="qty-saving">保存中…</text>
		  </view>
        </view>
      </view>
    </view>

    <!-- 底部完成按钮 -->
    <view class="footer" v-if="task">
      <button
        v-if="isPickable"
        class="btn-primary"
        :disabled="!allDone || submittingReview || scanLocked"
        @click="createReviewTask"
      >
        {{ submittingReview ? '正在保存并提交…' : '完成拣货' }}
      </button>
      <button
        v-else-if="canSelfReview"
        class="btn-danger"
        :disabled="posting || confirmingPost || scanLocked"
        @click="confirmAndPost"
      >
        {{ posting ? '正在复核过账…' : task.review_status === 'APPROVED' ? '重试确认出库' : '复核并确认出库' }}
      </button>
      <view v-else-if="isPosted" class="completed-text">本任务已完成复核并过账</view>
      <view v-else class="completed-text">拣货已提交，等待复核</view>
    </view>
  </view>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { api, createIdempotencyUuid } from '@/utils/request'
import { useBarcodeScanner } from '@/utils/useBarcodeScanner'
import { useAuth } from '@/store/auth'
import { businessScope, captureSession, clearPending, definitelyRejected, isCurrentSession, operationKey, readPending, writePending } from '@/utils/pendingOperation'

const taskId = ref<number | null>(null)
const auth = useAuth()
const task = ref<any | null>(null)
const lines = ref<any[]>([])

const scanBarcode = ref('')
const scanQty = ref<string>('1')
const loading = ref(false)
const submittingReview = ref(false)
const posting = ref(false)
const confirmingPost = ref(false)
const scanning = ref(false)
const preparingScan = ref(false)
const pendingScan = ref<any>(null)
const recoveryError = ref('')
const scanLocked = computed(() => scanning.value || preparingScan.value || !!pendingScan.value || !!recoveryError.value || !businessScope(auth))
function pendingScanKey() { return operationKey(auth, 'pick-scan', taskId.value) }
const adjustingLineIds = reactive(new Set<number>())
const adjustingCount = computed(() => adjustingLineIds.size)
const dirtyLineIds = new Set<number>()
const adjustmentPromises = new Map<number, Promise<boolean>>()

// 扫描钩子
const { quickScan } = useBarcodeScanner({ onScan: handleBarcodeScanned })

const allDone = computed(() => {
  if (!lines.value.length) return false
  return lines.value.every((ln: any) =>
    Number(ln.qty_done || 0) >= Number(ln.qty_plan || 0)
  )
})

const isPickable = computed(() => ['RESERVED', 'RELEASED', 'IN_PROGRESS'].includes(task.value?.status))
const isPosted = computed(() => task.value?.posting_status === 'POSTED')
const canSelfReview = computed(() => {
  if (task.value?.status !== 'COMPLETED' || task.value?.can_self_review !== true) return false
  const reviewStatus = task.value?.review_status
  const postingStatus = task.value?.posting_status
  return (
    (reviewStatus === 'PENDING' && ['NOT_READY', 'PENDING'].includes(postingStatus)) ||
    (reviewStatus === 'APPROVED' && ['PENDING', 'FAILED'].includes(postingStatus))
  )
})

// 每次扫描生成一个唯一的 client_seq
function genClientSeq(): string {
  return createIdempotencyUuid()
}

async function loadTask() {
  if (!taskId.value) return
  const session = captureSession(auth)
  try {
    const res: any = await api.pickTaskDetail(taskId.value)
    if (isCurrentSession(auth, session)) task.value = res
  } catch (e) {
    if (!isCurrentSession(auth, session)) return
    console.error(e)
    uni.showToast({ title: '加载任务头失败', icon: 'none' })
  }
}

async function loadLines() {
  if (!taskId.value) return
  const session = captureSession(auth)
  loading.value = true
  try {
    const res: any = await api.pickTaskLines(taskId.value)
    if (!isCurrentSession(auth, session)) return
    const list = Array.isArray(res) ? res : (res.results || [])
    // ✅ 初始化每行的可编辑拣货数
    lines.value = list.map((ln: any) => ({
      ...ln,
      _edit_qty_done: formatQty(ln.qty_done),
    }))
    dirtyLineIds.clear()
  } catch (e) {
    if (!isCurrentSession(auth, session)) return
    console.error(e)
    uni.showToast({ title: '加载任务行失败', icon: 'none' })
  } finally {
    if (isCurrentSession(auth, session)) loading.value = false
  }
}

function isAdjustingLine(ln: any): boolean {
  return adjustingLineIds.has(Number(ln?.id))
}

function restoreConfirmedQty(ln: any) {
  ln._edit_qty_done = formatQty(ln.qty_done)
  if (ln?.id) dirtyLineIds.delete(Number(ln.id))
}

async function refreshConfirmedLine(lineId: number, fallbackLine: any) {
  const session = captureSession(auth)
  let confirmedQty = fallbackLine?.qty_done
  try {
    const res: any = await api.pickTaskLines(taskId.value)
    const list = Array.isArray(res) ? res : (res?.results || [])
    const serverLine = list.find((item: any) => Number(item?.id) === lineId)
    if (serverLine && typeof serverLine.qty_done !== 'undefined') {
      confirmedQty = serverLine.qty_done
    }
  } catch (refreshError) {
    console.error('刷新拣货行数量失败', refreshError)
  }

  if (!isCurrentSession(auth, session)) return
  const currentLine = lines.value.find((item: any) => Number(item?.id) === lineId)
  if (currentLine) {
    currentLine.qty_done = confirmedQty
    currentLine._edit_qty_done = formatQty(confirmedQty)
  }
  dirtyLineIds.delete(lineId)
}


function applyManualQty(ln: any): Promise<boolean> {
  const lineId = Number(ln?.id)
  if (!lineId || !taskId.value || !isPickable.value || scanning.value || pendingScan.value || recoveryError.value) {
    return Promise.resolve(false)
  }

  const existing = adjustmentPromises.get(lineId)
  if (existing) return existing
  if (!dirtyLineIds.has(lineId)) return Promise.resolve(true)

  const raw = String(ln?._edit_qty_done ?? '').trim()
  if (!raw) {
    uni.showToast({ title: '拣货数量不能为空', icon: 'none' })
    restoreConfirmedQty(ln)
    return Promise.resolve(false)
  }

  const val = Number(raw)
  if (!Number.isFinite(val) || val < 0) {
    uni.showToast({ title: '请输入合法的拣货数量', icon: 'none' })
    restoreConfirmedQty(ln)
    return Promise.resolve(false)
  }

  if (val === Number(ln.qty_done ?? 0)) {
    restoreConfirmedQty(ln)
    return Promise.resolve(true)
  }

  const session = captureSession(auth)
  const request = (async (): Promise<boolean> => {
    adjustingLineIds.add(lineId)
    try {
      const res: any = await api.adjustPickLineQty(taskId.value, {
        line_id: lineId,
        final_qty_done: val,
        client_seq: genClientSeq(),
      })
      if (!isCurrentSession(auth, session)) return false
      ln.qty_done = res.qty_done
      ln._edit_qty_done = formatQty(res.qty_done)
      dirtyLineIds.delete(lineId)
      return true
    } catch (e: any) {
      if (!isCurrentSession(auth, session)) return false
      const msg = e?.data?.detail || e?.data?.message || '调整拣货数量失败'
      uni.showToast({ title: String(msg), icon: 'none' })
      await refreshConfirmedLine(lineId, ln)
      return false
    } finally {
      if (isCurrentSession(auth, session)) { adjustingLineIds.delete(lineId); adjustmentPromises.delete(lineId) }
    }
  })()

  adjustmentPromises.set(lineId, request)
  return request
}

async function flushManualQtyEdits(): Promise<boolean> {
  for (const ln of lines.value) {
    const lineId = Number(ln?.id)
    if (!lineId || (!dirtyLineIds.has(lineId) && !adjustmentPromises.has(lineId))) continue
    if (!await applyManualQty(ln)) return false
  }
  const remaining = Array.from(adjustmentPromises.values())
  if (remaining.length) {
    const results = await Promise.all(remaining)
    if (results.some((saved) => !saved)) return false
  }
  return adjustmentPromises.size === 0
}



async function submitScan(barcodeOverride?: string) {
  if (!taskId.value || !isPickable.value || scanLocked.value) return
  const session = captureSession(auth)
  const code = (barcodeOverride || scanBarcode.value || '').trim()
  if (!code) {
    uni.showToast({ title: '请先扫描或输入条码', icon: 'none' })
    return
  }
  const q = Number(scanQty.value)
  if (!Number.isFinite(q) || q <= 0 || Number(q.toFixed(3)) <= 0) { uni.showToast({ title: '请输入大于零的有效数量', icon: 'none' }); return }
  preparingScan.value = true
  try {
    if (!await flushManualQtyEdits() || !isCurrentSession(auth, session) || !isPickable.value) return
    pendingScan.value = writePending(pendingScanKey(), { barcode: code, qty: q.toFixed(3), client_seq: genClientSeq() }, { task_id: taskId.value })
    await sendPendingScan(session)
  } catch (error: any) { if (isCurrentSession(auth, session)) uni.showToast({ title: error?.message || '拣货结果待确认', icon: 'none' }) }
  finally { if (isCurrentSession(auth, session)) preparingScan.value = false }
}

async function sendPendingScan(session: any) {
  const pending = pendingScan.value
  const key = pendingScanKey()
  if (!pending || !isCurrentSession(auth, session)) return
  scanning.value = true
  try {
    const res: any = await api.scanPick(pending.display.task_id, pending.payload)
    if (!isCurrentSession(auth, session)) return
    if (!res?.line_id || (res.qty_done == null && res.line?.qty_done == null)) throw new Error('拣货响应不完整，请继续确认原请求')

    const lineId = res.line_id
    if (lineId) {
      const ln = lines.value.find((x: any) => x.id === lineId)
      // 优先用 res.line.qty_done
      let newQtyDone: number | undefined
      if (res.line && typeof res.line.qty_done !== 'undefined') {
        newQtyDone = Number(res.line.qty_done)
      } else if (typeof res.qty_done !== 'undefined') {
        newQtyDone = Number(res.qty_done)
      }

      if (ln && !Number.isNaN(newQtyDone as number)) {
        ln.qty_done = newQtyDone
        ln._edit_qty_done = formatQty(newQtyDone) // ✅ 同步到输入框
        dirtyLineIds.delete(Number(ln.id))
      }
    }

    uni.showToast({ title: '已记录拣货', icon: 'none' })
    clearPending(key); pendingScan.value = null
    scanBarcode.value = ''
    await loadTask()
  } catch (err: any) {
    if (!isCurrentSession(auth, session)) return
    if (definitelyRejected(err)) { clearPending(key); pendingScan.value = null }
    console.error(err)
    const msg = err?.message || err?.data?.detail || err?.data?.message || '拣货结果待确认，请继续确认原请求'
    uni.showToast({ title: String(msg), icon: 'none' })
  } finally {
    if (isCurrentSession(auth, session)) scanning.value = false
  }
}

async function retryScan() {
  if (!pendingScan.value || scanning.value || preparingScan.value) return
  await sendPendingScan(captureSession(auth))
}

// 点击“扫码”按钮
async function handleScan() {
  if (!isPickable.value || scanLocked.value) return
  if (!await flushManualQtyEdits()) return
  quickScan()
}

// 注册扫描回调：扫码→直接提交
function handleBarcodeScanned(barcode: string) {
  if (scanLocked.value) return
  console.log('拣货页面收到条码:', barcode)
  scanBarcode.value = barcode
  submitScan(barcode)
}

async function postTask() {
  if (!taskId.value || posting.value || scanLocked.value) return
  posting.value = true
  const session = captureSession(auth)
  try {
    const res: any = await api.postPickTask(taskId.value)
    if (!isCurrentSession(auth, session)) return
    uni.showToast({
      title: res?.message || '拣货已过账',
      icon: 'none',
    })
    setTimeout(() => {
      if (isCurrentSession(auth, session)) uni.navigateBack()
    }, 800)
  } catch (err: any) {
    if (!isCurrentSession(auth, session)) return
    console.error(err)
    if (Number(err?.statusCode || err?.code) === 403 && task.value?.is_warehouse_assisted) {
      auth.invalidateAssistedCapability()
    }
    const msg = err?.data?.detail || err?.data?.message || '过账失败'
    uni.showToast({ title: String(msg), icon: 'none' })
  } finally {
    if (isCurrentSession(auth, session)) posting.value = false
  }
}

function confirmAndPost() {
  if (!canSelfReview.value || posting.value || confirmingPost.value || scanLocked.value) return
  const session = captureSession(auth)
  confirmingPost.value = true
  uni.showModal({
    title: '确认单人复核并出库',
    content: '你将以同一仓库操作员身份完成复核和库存过账。请确认商品、数量和库位均已核对无误。',
    confirmText: '确认出库',
    confirmColor: '#c62828',
    success: (result) => {
      if (result.confirm && isCurrentSession(auth, session)) postTask()
    },
    complete: () => {
      if (isCurrentSession(auth, session)) confirmingPost.value = false
    },
  })
}

async function createReviewTask() {
  if (
    !taskId.value ||
    !isPickable.value ||
    submittingReview.value ||
    scanLocked.value
  ) return

  submittingReview.value = true
  const session = captureSession(auth)
  try {
    // 点击按钮会先触发当前数量输入框的 blur。等待该保存请求，或者
    // 主动保存尚未失焦的编辑，避免第一次点击只保存数量、第二次才提交。
    if (!await flushManualQtyEdits() || !isCurrentSession(auth, session)) return
    if (!allDone.value) {
      uni.showToast({ title: '还有未拣完的行，不能提交复核', icon: 'none' })
      return
    }

    const res: any = await api.createPickReviewTask(taskId.value)
    if (!isCurrentSession(auth, session)) return
    uni.showToast({
      title: res?.message || '拣货完成，已创建复核任务',
      icon: 'none',
    })
    await loadTask()
    await loadLines()
  } catch (err: any) {
    if (!isCurrentSession(auth, session)) return
    console.error(err)
    const msg = err?.data?.detail || err?.data?.message || '提交复核任务失败'
    uni.showToast({ title: String(msg), icon: 'none' })
    // 请求超时但服务端已成功时，刷新后可直接进入第二阶段。
    await loadTask()
    await loadLines()
  } finally {
    if (isCurrentSession(auth, session)) submittingReview.value = false
  }
}

// function formatQty(val: any): string {
//   if (val === null || val === undefined || val === '') return ''

//   const n = Number(val)
//   if (Number.isNaN(n)) {
//     // 非数字，就原样返回字符串
//     return String(val)
//   }

//   // 是整数，就只显示整数部分
//   if (Number.isInteger(n)) {
//     return n.toString()
//   }

//   // 有小数，就正常显示（会自动去掉多余的 0，比如 10.50 -> 10.5）
//   return n.toString()
// }


function formatQty(value) {
  if (value === null || value === undefined || value === '') return '0'

  const n = Number(value)
  if (!Number.isFinite(n)) return String(value)

  return String(Number(n.toFixed(4)))
}

function onEditQty(e: any, ln: any) {
  if (scanLocked.value) return
  // uni-app 的 input 事件里，值在 e.detail.value
  const raw = e?.detail?.value ?? e?.target?.value ?? ''
  // 输入过程中保留原始字符串（包括空字符串），只在失焦/提交时转成数值。
  ln._edit_qty_done = String(raw)
  if (ln?.id) dirtyLineIds.add(Number(ln.id))
}

onLoad((opts: any) => {
  const id = Number(opts?.task_id)
  if (!id) {
    uni.showToast({ title: '缺少任务ID', icon: 'none' })
    return
  }
  taskId.value = id
})
let restoringTask = false
async function restoreTaskState() {
  if (!businessScope(auth) || !taskId.value || restoringTask) return
  const session = captureSession(auth)
  restoringTask = true
  try {
    pendingScan.value = readPending(pendingScanKey()); recoveryError.value = ''
    await loadTask()
    if (isCurrentSession(auth, session)) await loadLines()
  } catch (error: any) { if (isCurrentSession(auth, session)) recoveryError.value = error.message }
  finally { if (isCurrentSession(auth, session)) restoringTask = false }
}
watch(() => businessScope(auth), (scope) => {
  pendingScan.value = null; task.value = null; lines.value = []; scanning.value = false
  preparingScan.value = false; recoveryError.value = ''; scanBarcode.value = ''; scanQty.value = '1'
  posting.value = false; confirmingPost.value = false; submittingReview.value = false; loading.value = false
  dirtyLineIds.clear(); adjustmentPromises.clear(); adjustingLineIds.clear()
  restoringTask = false
  if (scope) void restoreTaskState()
}, { flush: 'sync' })
onShow(async () => {
  await auth.loadProfile().catch(() => null)
  await restoreTaskState()
})

</script>

<style scoped>
.page {
  padding: 16rpx;
}
.card-first {
  background: #fff;
  border-radius: 16rpx;
  padding: 16rpx;
  margin-bottom: 16rpx;
  box-shadow: 0 2rpx 6rpx rgba(15, 23, 42, 0.08);
}
.row-first {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8rpx;
}
.row-meta {
  font-size: 24rpx;
  color: #475569;
  margin-top: 4rpx;
}
.title {
  font-size: 32rpx;
  font-weight: 600;
}
.scan-bar {
  flex-direction: row;
  align-items: center;
  display: flex;
  gap: 8rpx;
  margin-bottom: 16rpx;
}
.input {
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  padding: 8rpx 12rpx;
  background: #fff;
}
.flex-input {
  flex: 1;
}
.qty-input {
  width: 120rpx;
  text-align: center;
}
.btn-outline {
  padding: 8rpx 16rpx;
  border-radius: 8rpx;
  border: 1rpx solid #cbd5e1;
  background: #fff;
  font-size: 24rpx;
}
.content {
  margin-top: 8rpx;
}
.row.item {
  flex-direction: row;
  display: flex;
  padding: 12rpx;
  border-radius: 12rpx;
  background: #fff;
  margin-bottom: 8rpx;
}
.row.item.odd {
  background: #f8fafc;
}
.col-info {
  flex: 1;
}
.name {
  font-size: 28rpx;
  font-weight: 500;
}
.meta {
  font-size: 24rpx;
  color: #64748b;
  margin-top: 4rpx;
}
.qty-row {
  margin-top: 4rpx;
  font-size: 24rpx;
}

/* ✅ 新增样式：行内手工输入拣货数 */
.qty-edit-row {
  margin-top: 6rpx;
  font-size: 24rpx;
  display: flex;
  align-items: center;
}
.qty-edit-label {
  margin-right: 8rpx;
  color: #64748b;
}
.line-qty-input {
  flex: 0 0 180rpx;
  border: 1rpx solid #e5e7eb;
  border-radius: 8rpx;
  padding: 4rpx 8rpx;
  background: #fff;
  text-align: center;
}
.line-qty-input[disabled] {
  color: #64748b;
  background: #f1f5f9;
}
.qty-saving {
  margin-left: 10rpx;
  color: #64748b;
  font-size: 22rpx;
}

.footer {
  margin-top: 24rpx;
}
.btn-primary {
  width: 100%;
  padding: 12rpx 0;
  border-radius: 12rpx;
  background: #0f766e;
  color: #fff;
  text-align: center;
  font-size: 28rpx;
}
.btn-primary:disabled {
  background: #94a3b8;
}
.assisted-badge {
  display: inline-block;
  margin-top: 10rpx;
  padding: 5rpx 12rpx;
  border-radius: 999rpx;
  color: #9a3412;
  background: #ffedd5;
  font-size: 22rpx;
}
.btn-danger {
  width: 100%;
  padding: 12rpx 0;
  border-radius: 12rpx;
  color: #fff;
  background: #c62828;
  text-align: center;
  font-size: 28rpx;
}
.btn-danger:disabled {
  background: #94a3b8;
}
.completed-text {
  padding: 20rpx;
  border-radius: 12rpx;
  color: #475569;
  background: #f1f5f9;
  text-align: center;
}

.pending-scan{margin:12px 0;padding:14px;background:#fff7ed;border:1px solid #f5c16c;border-radius:12px;font-size:14px;overflow-wrap:anywhere}.pending-scan text{display:block;margin-bottom:8px}.pending-scan button{min-height:48px;background:#e77800;color:white}
.scan-bar{flex-wrap:wrap}.flex-input{min-width:50%;box-sizing:border-box}.scan-bar .input,.scan-bar button,.line-qty-input,.footer button{min-height:48px;box-sizing:border-box}.scan-bar button{flex:1}.name,.row-meta{overflow-wrap:anywhere}.footer{padding-bottom:max(12px,env(safe-area-inset-bottom))}
</style>
